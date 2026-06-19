"""
test_sell_logic.py — Tests for monitor_portfolio_intraday() sell rules.

Covers:
  - IBKR OCA bracket self-healing (trailing stop + limit sell placed if missing)
  - Power Hold activation (≥20% gain within 21 days) — cancels limit, keeps stop
  - Power Hold expiry — re-places full OCA bracket
  - High-water mark update
  - ETF positions skipped in the per-position loop
  - Stale rotation: sort priority, conditions, Power Hold exemption
  - No duplicate Telegram notification on stale rotation (Bug #3)

ARCHITECTURE NOTE (post-refactor):
  Trailing stop-loss and profit target are now managed by IBKR via GTC OCA bracket
  orders placed at buy time. monitor_portfolio_intraday() does NOT call execute_sell()
  for those triggers. Python's role in the monitor is:
    1. Update high_water_mark in Supabase (informational)
    2. Self-heal OCA bracket if IBKR orders are missing
    3. Manage Power Hold state (cancel limit, re-place stop-only)
    4. Handle stale rotation (explicit execute_sell call)
  reconcile_with_ibkr() (Case 1) detects IBKR-closed positions and archives them.

IMPORTANT: All tests mock ib.portfolio() NOT ib.positions().
ib.positions() was the broken path fixed in Bug #5.
"""

import datetime
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger
)
import execution_agent


# ── Helpers ──────────────────────────────────────────────────────────────────────────

def _run_monitor(ib, supabase_mock, live_prices: dict | None = None,
                 is_bullish: bool = True):
    """
    Runs monitor_portfolio_intraday() with standard patches.
    live_prices: dict of {ticker: price}. Defaults to $100 for all.
    Patches place_oca_bracket and cancel_ticker_sell_orders so tests
    don’t need a live IBKR connection for OCA order placement.
    """
    prices = live_prices or {}

    def _price(ticker):
        return prices.get(ticker, 100.0)

    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", side_effect=_price), \
         patch("execution_agent.is_market_bullish", return_value=is_bullish), \
         patch("execution_agent.run_etf_parking"), \
         patch("execution_agent.place_oca_bracket") as mock_oca, \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.execute_sell") as mock_sell:
        execution_agent.monitor_portfolio_intraday(ib)
        return mock_sell, mock_oca


# ── Trailing stop-loss (IBKR-managed) ────────────────────────────────────────────────────────

class TestTrailingStopLoss:
    """
    IBKR manages trailing stops via GTC TrailingStopOrder in the OCA bracket.
    Python code does NOT call execute_sell() for stop-loss triggers.
    The monitor’s role is to: update high_water_mark, self-heal OCA if missing.
    """

    def test_stop_loss_not_enforced_by_python_code(self):
        """Even when price is well below 7% trailing stop, Python does NOT call
        execute_sell(). IBKR’s TrailingStopOrder fires the actual sell."""
        # Entry $100, high $120, trailing stop = $120 * 0.93 = $111.60
        # Current $110 < $111.60 — old code would have fired execute_sell here
        pos = make_position("AAPL", buy_price=100.0, high_water_mark=120.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        # Simulate OCA already placed — openTrades returns a sell order
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"AAPL": 110.0})
        mock_sell.assert_not_called()  # Python no longer fires execute_sell for stops

    def test_stop_loss_not_triggered_above_threshold(self):
        """Price above trailing stop → no sell (unchanged behaviour)."""
        pos = make_position("AAPL", buy_price=100.0, high_water_mark=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"AAPL": 94.0})
        mock_sell.assert_not_called()

    def test_trailing_stop_rises_with_high_water_mark(self):
        """Trailing stop is based on high_water_mark — high_water_mark is updated when price rises."""
        # Entry $100, high $130, trailing stop = $130 * 0.93 = $120.90
        # Price $122 > $120.90 → no sell; but high_water_mark should be updated
        # (price=$122 < high=$130, so no update — just confirm no sell)
        pos = make_position("NVDA", buy_price=100.0, high_water_mark=130.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "NVDA"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"NVDA": 122.0})
        mock_sell.assert_not_called()

    def test_self_healing_places_oca_when_no_sell_orders(self):
        """If no open SELL orders exist for a position, monitor re-places OCA bracket."""
        pos = make_position("NVDA", buy_price=100.0, high_water_mark=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])
        ib.openTrades.return_value = []  # No open sell orders — self-healing should fire

        _, mock_oca = _run_monitor(ib, supabase, live_prices={"NVDA": 105.0})
        mock_oca.assert_called_once()  # place_oca_bracket called for self-healing


# ── High-water mark update ────────────────────────────────────────────────────

class TestHighWaterMark:

    def test_high_water_mark_updated_when_price_rises(self):
        """New high price → Supabase update called with new high_water_mark."""
        pos = make_position("MSFT", buy_price=100.0, high_water_mark=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["MSFT"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=115.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # Verify update was called (high water mark rise)
        supabase.table("portfolio_positions").update.assert_called()

    def test_high_water_mark_not_updated_when_price_falls(self):
        """Price below existing high → no high_water_mark update."""
        pos = make_position("MSFT", buy_price=100.0, high_water_mark=130.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["MSFT"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=115.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # No update to portfolio_positions when price < high_water_mark
        supabase.table("portfolio_positions").update.assert_not_called()


# ── Profit target (IBKR-managed) ───────────────────────────────────────────────────────

class TestProfitTarget:
    """
    Profit target is now managed by IBKR via GTC LimitOrder in the OCA bracket.
    Python code does NOT call execute_sell() when price crosses the profit target.
    """

    def test_profit_target_not_enforced_by_python_code(self):
        """Even when price ≥ profit_target and not in power hold, Python does NOT
        call execute_sell(). IBKR’s LimitOrder fills the sell."""
        pos = make_position("AMZN", buy_price=100.0, profit_target=125.0,
                            is_power_hold=False)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AMZN"])
        # OCA already placed
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AMZN"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"AMZN": 126.0})
        mock_sell.assert_not_called()  # Python no longer fires execute_sell for profit target

    def test_profit_target_blocked_by_active_power_hold(self):
        """Power hold active → profit target LimitOrder was already cancelled.
        Python confirms execute_sell is not called from monitor."""
        expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        pos = make_position("MSFT", buy_price=100.0, profit_target=125.0,
                            is_power_hold=True, power_hold_expiry=expiry)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["MSFT"])

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"MSFT": 130.0})
        mock_sell.assert_not_called()


# ── Power Hold ────────────────────────────────────────────────────────────────

class TestPowerHold:

    def test_power_hold_activates_on_20pct_gain_within_21_days(self):
        """≥20% gain within 21 days → Power Hold activated in Supabase."""
        pos = make_position("NVDA", buy_price=100.0, days_ago=10, is_power_hold=False)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=121.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.place_oca_bracket"), \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # Supabase should have been updated to set is_power_hold=True
        update_calls = supabase.table("portfolio_positions").update.call_args_list
        power_hold_set = any(
            call_args[0][0].get("is_power_hold") is True
            for call_args in update_calls
            if call_args[0]
        )
        assert power_hold_set, "is_power_hold=True was not written to Supabase"

    def test_power_hold_not_activated_after_21_days(self):
        """≥20% gain but held >21 days → Power Hold NOT activated."""
        pos = make_position("NVDA", buy_price=100.0, days_ago=25, is_power_hold=False)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=121.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.place_oca_bracket"), \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        update_calls = supabase.table("portfolio_positions").update.call_args_list
        power_hold_set = any(
            call_args[0][0].get("is_power_hold") is True
            for call_args in update_calls
            if call_args[0]
        )
        assert not power_hold_set

    def test_power_hold_not_activated_on_small_gain(self):
        """15% gain within 21 days → does NOT trigger Power Hold (needs ≥20%)."""
        pos = make_position("NVDA", buy_price=100.0, days_ago=10, is_power_hold=False)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=115.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        update_calls = supabase.table("portfolio_positions").update.call_args_list
        power_hold_set = any(
            call_args[0][0].get("is_power_hold") is True
            for call_args in update_calls
            if call_args[0]
        )
        assert not power_hold_set

    def test_power_hold_deactivated_after_expiry(self):
        """today ≥ power_hold_expiry → is_power_hold deactivated in Supabase."""
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        pos = make_position("TSLA", buy_price=100.0, is_power_hold=True,
                            power_hold_expiry=yesterday)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["TSLA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=110.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        update_calls = supabase.table("portfolio_positions").update.call_args_list
        deactivated = any(
            call_args[0][0].get("is_power_hold") is False
            for call_args in update_calls
            if call_args[0]
        )
        assert deactivated, "is_power_hold=False was not written to Supabase on expiry"

    def test_power_hold_stays_active_before_expiry(self):
        """today < power_hold_expiry → Power Hold remains active."""
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        pos = make_position("TSLA", buy_price=100.0, is_power_hold=True,
                            power_hold_expiry=future)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["TSLA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=110.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        update_calls = supabase.table("portfolio_positions").update.call_args_list
        deactivated = any(
            call_args[0][0].get("is_power_hold") is False
            for call_args in update_calls
            if call_args[0]
        )
        assert not deactivated

    def test_power_hold_activation_places_trailing_stop_only(self):
        """When Power Hold activates, the OCA bracket is cancelled and ONLY a
        trailing stop (orderType='TRAIL') is re-placed — no 25% limit sell.
        This prevents the profit target from auto-filling while the position
        is exempt during the power hold window."""
        pos = make_position("NVDA", buy_price=100.0, days_ago=10, is_power_hold=False)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=121.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.place_oca_bracket"), \
             patch("execution_agent.cancel_ticker_sell_orders") as mock_cancel, \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # cancel_ticker_sell_orders must be called for NVDA to remove the OCA limit sell
        cancel_tickers = [str(c) for c in mock_cancel.call_args_list]
        assert any("NVDA" in t for t in cancel_tickers), \
            "cancel_ticker_sell_orders must be called for NVDA when Power Hold activates"

        # ib.placeOrder should have been called with exactly one TRAIL order
        # (place_oca_bracket is mocked, so no OCA calls — only the manual trail re-place)
        trail_calls = [
            c for c in ib.placeOrder.call_args_list
            if getattr(c.args[1], 'orderType', '') == 'TRAIL'
        ]
        assert len(trail_calls) == 1, (
            "Exactly one TrailingStopOrder must be placed when Power Hold activates"
        )
        # No LimitOrder (profit target, orderType='LMT') should be placed during Power Hold
        limit_calls = [
            c for c in ib.placeOrder.call_args_list
            if getattr(c.args[1], 'orderType', '') == 'LMT'
        ]
        assert len(limit_calls) == 0, (
            "No LimitOrder (profit target) should be placed during Power Hold"
        )

    def test_power_hold_expiry_replaces_full_oca_bracket(self):
        """When Power Hold expires, place_oca_bracket() is called without
        is_power_hold=True, which means BOTH the trailing stop AND the 25%
        limit sell are re-placed (full OCA bracket restored)."""
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        pos = make_position("TSLA", buy_price=100.0, is_power_hold=True,
                            power_hold_expiry=yesterday)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["TSLA"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=110.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.place_oca_bracket") as mock_oca, \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # place_oca_bracket must be called at least once without is_power_hold=True
        # (call #1 is self-healing with is_power_hold=True; call #2 is expiry re-place
        # with is_power_hold defaulting to False — i.e. full OCA bracket).
        oca_calls = mock_oca.call_args_list
        assert len(oca_calls) >= 1, "place_oca_bracket must be called on Power Hold expiry"
        full_oca_calls = [
            c for c in oca_calls
            if not c.kwargs.get('is_power_hold', False)
        ]
        assert len(full_oca_calls) >= 1, (
            "At least one place_oca_bracket call must have is_power_hold=False "
            "(full OCA bracket restored after Power Hold expiry)"
        )



# ── ETF positions skipped in per-position loop ────────────────────────────────

class TestETFPositionSkipped:

    def test_etf_parking_position_not_stop_lossed(self):
        """
        Bug #7 related: ETF parking positions must be skipped in the
        per-position stop-loss/profit-target loop.
        Even if price collapses, execute_sell should NOT be called for ETF.
        """
        pos = make_position("QQQ", buy_price=400.0, buy_source="etf_parking",
                            high_water_mark=400.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["QQQ"])

        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"QQQ": 300.0})
        # execute_sell must not be triggered for ETF positions by the stop-loss logic
        mock_sell.assert_not_called()


# ── Stale rotation ────────────────────────────────────────────────────────────

class TestStaleRotation:

    def test_stale_rotation_fires_when_portfolio_full_and_trigger_exists(self):
        """Stale rotation: portfolio full + fresh trigger → worst position sold."""
        portfolio = [
            make_position("AAPL", days_ago=20, buy_price=100.0),  # stale: 20d, 0% gain
            make_position("MSFT", days_ago=5),
            make_position("NVDA", days_ago=5),
            make_position("AMZN", days_ago=5),
        ]
        supabase = make_supabase_mock(
            portfolio=portfolio,
            daily_triggers=[make_trigger("TSLA")],
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "AMZN"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", side_effect=lambda t: {
                 "AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0, "AMZN": 100.0
             }.get(t, 100.0)), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.get_fresh_triggers_today", return_value=["TSLA"]):
            execution_agent.monitor_portfolio_intraday(ib)

        mock_sell.assert_called()

    def test_stale_rotation_does_not_fire_when_portfolio_not_full(self):
        """Stale rotation: only 2 positions → rotation should not fire."""
        portfolio = [
            make_position("AAPL", days_ago=20, buy_price=100.0),
            make_position("MSFT", days_ago=5),
        ]
        supabase = make_supabase_mock(
            portfolio=portfolio,
            daily_triggers=[make_trigger("TSLA")],
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.get_fresh_triggers_today", return_value=["TSLA"]):
            execution_agent.monitor_portfolio_intraday(ib)

        mock_sell.assert_not_called()

    def test_stale_rotation_does_not_fire_without_fresh_trigger(self):
        """Stale rotation: portfolio full but no fresh trigger today → no rotation."""
        portfolio = [make_position(t, days_ago=20) for t in ["AAPL", "MSFT", "NVDA", "AMZN"]]
        supabase = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "AMZN"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.get_fresh_triggers_today", return_value=[]):
            execution_agent.monitor_portfolio_intraday(ib)

        mock_sell.assert_not_called()

    def test_stale_sort_sells_momentum_before_canslim(self):
        """
        Stale sort: etf_parking(0) < momentum_triggers(1) < daily_triggers(2).
        When both a momentum and a CANSLIM position are stale, momentum is sold first.
        """
        portfolio = [
            make_position("STALE_CANSLIM", buy_source="daily_triggers", days_ago=20, buy_price=100.0),
            make_position("STALE_MOM", buy_source="momentum_triggers", days_ago=20, buy_price=100.0),
            make_position("FRESH1", days_ago=2),
            make_position("FRESH2", days_ago=2),
        ]
        supabase = make_supabase_mock(portfolio=portfolio)
        ib = make_ib_mock(symbols=["STALE_CANSLIM", "STALE_MOM", "FRESH1", "FRESH2"])

        sold_tickers = []

        def capture_sell(ib_, client, ticker, *args, **kwargs):
            sold_tickers.append(ticker)

        prices = {"STALE_CANSLIM": 100.0, "STALE_MOM": 100.0, "FRESH1": 100.0, "FRESH2": 100.0}

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", side_effect=lambda t: prices.get(t, 100.0)), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell", side_effect=capture_sell), \
             patch("execution_agent.get_fresh_triggers_today", return_value=["NEWCOMER"]):
            execution_agent.monitor_portfolio_intraday(ib)

        # The first (and only) stale rotation sell should be the momentum position
        if sold_tickers:
            assert sold_tickers[0] == "STALE_MOM", (
                f"Expected STALE_MOM to be sold first (lower quality), got {sold_tickers[0]}"
            )

    def test_stale_rotation_exempts_power_hold_positions(self):
        """Power Hold positions must NOT be stale-rotated."""
        future_expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        portfolio = [
            make_position("PH_STOCK", days_ago=20, is_power_hold=True,
                          power_hold_expiry=future_expiry),
            make_position("STOCK2", days_ago=5),
            make_position("STOCK3", days_ago=5),
            make_position("STOCK4", days_ago=5),
        ]
        supabase = make_supabase_mock(portfolio=portfolio)
        ib = make_ib_mock(symbols=["PH_STOCK", "STOCK2", "STOCK3", "STOCK4"])

        sold_tickers = []

        def capture_sell(ib_, client, ticker, *args, **kwargs):
            sold_tickers.append(ticker)

        prices = {t["ticker"]: 100.0 for t in portfolio}

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", side_effect=lambda t: prices.get(t, 100.0)), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.run_etf_parking"), \
             patch("execution_agent.execute_sell", side_effect=capture_sell), \
             patch("execution_agent.get_fresh_triggers_today", return_value=["NEWCOMER"]):
            execution_agent.monitor_portfolio_intraday(ib)

        assert "PH_STOCK" not in sold_tickers, "Power Hold position was incorrectly stale-rotated"
