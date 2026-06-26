"""
test_oca_helpers.py — Unit tests for IBKR order management helper functions.

Covers:
  - place_oca_bracket(): correct order types, params, OCA group, Power Hold mode
  - cancel_ticker_sell_orders(): cancels only active SELL orders for the target ticker
  - execute_sell(): cancel_ticker_sell_orders() called BEFORE placing MarketOrder

ARCHITECTURE CONTEXT:
  These helpers were introduced in the OCA bracket refactor.  At buy time,
  place_oca_bracket() places a GTC TrailingStopOrder + LimitOrder in the same
  OCA group so IBKR cancels one when the other fills.  Before any explicit sell
  (stale rotation, ETF liquidation), cancel_ticker_sell_orders() cleans up the
  existing OCA orders to prevent duplicate fills.
"""

import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position
)
import execution_agent


# ── place_oca_bracket() unit tests ────────────────────────────────────────────

class TestPlaceOcaBracket:
    """
    place_oca_bracket() places two GTC orders in the same OCA group:
      1. TrailingStopOrder  (orderType='TRAIL', trailingPercent=stop_loss_pct*100)
      2. LimitOrder         (lmtPrice = buy_price * (1 + profit_target_pct))

    During Power Hold (is_power_hold=True), only the trailing stop is placed.
    """

    def test_places_trailing_stop_and_limit_sell(self):
        """Normal case: two placeOrder calls — one TRAIL, one LMT — same OCA group."""
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "NVDA"

        execution_agent.place_oca_bracket(
            ib, contract, shares=100, buy_price=200.0,
            profit_target_pct=0.25, stop_loss_pct=0.07,
            submit_limit_order=True
        )

        assert ib.placeOrder.call_count == 2, (
            "Expected exactly 2 placeOrder calls: TrailingStop + LimitSell"
        )
        orders = [c.args[1] for c in ib.placeOrder.call_args_list]

        # ── Trailing stop ──
        trail = next((o for o in orders if getattr(o, 'orderType', '') == 'TRAIL'), None)
        assert trail is not None, "TrailingStopOrder (orderType='TRAIL') must be placed"
        assert trail.action == 'SELL'
        assert trail.totalQuantity == 100
        assert abs(trail.trailingPercent - 7.0) < 0.01, (
            f"trailingPercent should be 7.0 (0.07 * 100), got {trail.trailingPercent}"
        )
        assert trail.tif == 'GTC'

        # ── Limit sell (profit target) ──
        # LimitOrder from ib_insync sets orderType='LMT'
        limit = next((o for o in orders if getattr(o, 'orderType', '') == 'LMT'), None)
        assert limit is not None, "LimitOrder (profit target) must be placed"
        assert limit.action == 'SELL'
        assert limit.totalQuantity == 100
        assert abs(limit.lmtPrice - 250.0) < 0.01, (
            f"lmtPrice should be 250.0 (200 * 1.25), got {limit.lmtPrice}"
        )
        assert limit.tif == 'GTC'

    def test_both_orders_share_same_oca_group(self):
        """Both orders must have the same ocaGroup so IBKR cancels one when the other fills."""
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "TSLA"

        execution_agent.place_oca_bracket(
            ib, contract, shares=50, buy_price=300.0,
            profit_target_pct=0.25, stop_loss_pct=0.07
        )

        orders = [c.args[1] for c in ib.placeOrder.call_args_list]
        oca_groups = {getattr(o, 'ocaGroup', None) for o in orders}
        assert len(oca_groups) == 1, "Both orders must share one OCA group"
        assert None not in oca_groups, "ocaGroup must be set on both orders"
        oca_group = oca_groups.pop()
        assert oca_group.startswith("OCA_TSLA_"), (
            f"OCA group should start with 'OCA_TSLA_', got: {oca_group}"
        )

    def test_power_hold_places_trailing_stop_only(self):
        """During Power Hold, only the TrailingStopOrder is placed (no 25% limit sell)."""
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "MSFT"

        execution_agent.place_oca_bracket(
            ib, contract, shares=80, buy_price=400.0,
            profit_target_pct=0.25, stop_loss_pct=0.07,
            submit_limit_order=False
        )

        assert ib.placeOrder.call_count == 1, (
            "During Power Hold, exactly 1 placeOrder call (trailing stop only)"
        )
        only_order = ib.placeOrder.call_args.args[1]
        assert getattr(only_order, 'orderType', '') == 'TRAIL', (
            "The only order placed during Power Hold must be a TrailingStopOrder"
        )
        # Confirm no LimitOrder (orderType='LMT') was placed at all
        limit_placed = any(
            getattr(c.args[1], 'orderType', '') == 'LMT'
            for c in ib.placeOrder.call_args_list
        )
        assert not limit_placed, (
            "No LimitOrder (profit target, orderType='LMT') should be placed during Power Hold"
        )

    def test_profit_target_price_computed_correctly(self):
        """Profit target = buy_price * (1 + profit_target_pct), rounded to 2dp."""
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "AAPL"

        # $150 buy, 25% target → $187.50
        execution_agent.place_oca_bracket(
            ib, contract, shares=30, buy_price=150.0,
            profit_target_pct=0.25, stop_loss_pct=0.07,
            submit_limit_order=True
        )

        # Select limit order by orderType='LMT' (all Order objects have lmtPrice attr
        # set to float_max by default — cannot use hasattr).
        limit = next(
            (o for o in [c.args[1] for c in ib.placeOrder.call_args_list]
             if getattr(o, 'orderType', '') == 'LMT'),
            None
        )
        assert limit is not None
        assert abs(limit.lmtPrice - 187.50) < 0.01, (
            f"Expected lmtPrice=187.50, got {limit.lmtPrice}"
        )


# ── cancel_ticker_sell_orders() unit tests ────────────────────────────────────

class TestCancelTickerSellOrders:
    """
    cancel_ticker_sell_orders(ib, ticker) iterates ib.openTrades() and cancels
    any active SELL order for the given ticker.  It must NOT cancel:
      - BUY orders for the ticker
      - SELL orders for different tickers
      - Already-filled or cancelled orders
    """

    def _make_trade(self, symbol, action, status='Submitted'):
        t = MagicMock()
        t.contract.symbol = symbol
        t.order.action = action
        t.orderStatus.status = status
        return t

    def test_cancels_active_sell_for_ticker(self):
        """Active SELL order for the target ticker → ib.cancelOrder called."""
        ib = make_ib_mock()
        trade = self._make_trade("AAPL", "SELL", "Submitted")
        ib.openTrades.return_value = [trade]

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        ib.cancelOrder.assert_called_once_with(trade.order)
        assert cancelled == 1

    def test_does_not_cancel_buy_orders(self):
        """BUY orders for the same ticker must NOT be cancelled."""
        ib = make_ib_mock()
        buy_trade = self._make_trade("AAPL", "BUY", "Submitted")
        ib.openTrades.return_value = [buy_trade]

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_does_not_cancel_different_ticker(self):
        """SELL order for a different ticker must NOT be cancelled."""
        ib = make_ib_mock()
        other_trade = self._make_trade("NVDA", "SELL", "Submitted")
        ib.openTrades.return_value = [other_trade]

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_does_not_cancel_already_filled_order(self):
        """Filled orders must be skipped (no double-cancel)."""
        ib = make_ib_mock()
        filled_trade = self._make_trade("AAPL", "SELL", "Filled")
        ib.openTrades.return_value = [filled_trade]

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_cancels_multiple_oca_orders(self):
        """Both a trailing stop and a limit sell (OCA pair) must both be cancelled."""
        ib = make_ib_mock()
        trail_trade = self._make_trade("AAPL", "SELL", "Submitted")
        limit_trade = self._make_trade("AAPL", "SELL", "Submitted")
        ib.openTrades.return_value = [trail_trade, limit_trade]

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        assert ib.cancelOrder.call_count == 2
        assert cancelled == 2

    def test_returns_zero_when_no_open_orders(self):
        """Empty openTrades → returns 0, no error."""
        ib = make_ib_mock()
        ib.openTrades.return_value = []

        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")

        assert cancelled == 0
        ib.cancelOrder.assert_not_called()


# ── execute_sell() cancels OCA before market sell ─────────────────────────────

class TestExecuteSellCancelsOcaFirst:
    """
    execute_sell() must call cancel_ticker_sell_orders() BEFORE placing the
    MarketOrder.  If the OCA trailing stop fires at the same time, this prevents
    a duplicate fill (selling the same position twice).
    """

    def test_cancel_called_before_market_order(self):
        """cancel_ticker_sell_orders() must be invoked before ib.placeOrder(MarketOrder)."""
        pos = make_position("CRWD", buy_price=200.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["CRWD"])
        # Simulate confirmed sell: CRWD gone from portfolio after the order
        ib.portfolio.return_value = []

        call_order = []

        def _track_cancel(ib_, ticker_):
            call_order.append(f"cancel:{ticker_}")
            return 0  # nothing to cancel, but call was made

        def _track_place(contract, order):
            call_order.append(f"place:{getattr(order, 'action', '?')}")
            trade_mock = MagicMock()
            trade_mock.orderStatus.status = "Filled"
            trade_mock.orderStatus.avgFillPrice = 200.0
            return trade_mock

        ib.placeOrder.side_effect = _track_place

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.cancel_ticker_sell_orders", side_effect=_track_cancel):
            execution_agent.execute_sell(
                ib, supabase, "CRWD",
                shares=100, buy_price=200.0,
                buy_date=datetime.datetime.now(datetime.timezone.utc),
                buy_reason="daily_triggers", current_price=210.0,
                reason="Stale Rotation"
            )

        cancel_idx = next((i for i, s in enumerate(call_order) if "cancel:CRWD" in s), -1)
        sell_idx   = next((i for i, s in enumerate(call_order) if "place:SELL"  in s), -1)

        assert cancel_idx >= 0, "cancel_ticker_sell_orders was never called for CRWD"
        assert sell_idx >= 0,   "MarketOrder SELL was never placed for CRWD"
        assert cancel_idx < sell_idx, (
            f"cancel_ticker_sell_orders() must fire BEFORE placeOrder(SELL). "
            f"Got cancel at index {cancel_idx}, sell at {sell_idx}. "
            f"Full sequence: {call_order}"
        )

    def test_supabase_not_updated_if_sell_not_confirmed(self):
        """If CRWD is still in ib.portfolio() after the order, Supabase must NOT be touched.
        This guards against phantom deletions when a market order is rejected."""
        pos = make_position("CRWD", buy_price=200.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["CRWD"])
        # Position still present after sell attempt → sell not confirmed
        ib.portfolio.return_value = [
            make_portfolio_item("CRWD", position=100, avg_cost=200.0)
        ]

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.cancel_ticker_sell_orders", return_value=0):
            execution_agent.execute_sell(
                ib, supabase, "CRWD",
                shares=100, buy_price=200.0,
                buy_date=datetime.datetime.now(datetime.timezone.utc),
                buy_reason="daily_triggers", current_price=210.0,
                reason="Stale Rotation"
            )

        # Supabase portfolio_positions must NOT be deleted
        supabase.table("portfolio_positions").delete.assert_not_called()
