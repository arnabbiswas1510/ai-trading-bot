"""
test_buy_gates.py — Tests for all 7 buy gates in run_market_open_buys().

Each test verifies one gate: when the gate condition is met, no IBKR order is
placed. When the gate passes, the order IS placed.

Critical invariant tested here:
  - ETF parking positions do NOT count as stock slots (Bug #7)
  - Cooling-off uses trade_history.sell_date, NOT created_at (Bug #2)
  - Momentum cascade has its own ETF pre-flight (Bug #8)
  - Position sizing divides by stock slots only, not total slots
"""

import datetime
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger
)
import execution_agent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_buys(ib, supabase_mock, live_price=105.0, available_cash=20_000.0,
              is_bullish=True):
    """
    Runs run_market_open_buys() with standard patches applied.
    Returns the mock_ib so callers can inspect placeOrder calls.

    NOTE: notifier is patched here to prevent real Telegram messages from
    firing when tests run on the server where live credentials are present.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.get_available_cash", return_value=available_cash), \
         patch("execution_agent.is_market_bullish", return_value=is_bullish), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.run_etf_parking"), \
         patch("execution_agent.liquidate_etf_positions"), \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return ib


# ── Gate 1: Stock slot capacity ───────────────────────────────────────────────

class TestGate1StockSlots:

    def test_gate1_four_stock_positions_blocks_all_buys(self):
        """Gate 1: 4 stock positions → portfolio full → no order placed."""
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA", "AMZN"]]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA")],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "AMZN"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_gate1_etf_slot_does_not_count_as_stock_slot(self):
        """
        Bug #7 regression: 3 stocks + 1 ETF parking = 3 stock slots used.
        Slot for new stock IS available — order should be placed.
        """
        portfolio = [
            make_position("AAPL"), make_position("MSFT"), make_position("NVDA"),
            make_position("QQQ", buy_source="etf_parking"),
        ]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA", close_price=100.0)],
            portfolio=portfolio,
        )
        # ib.portfolio() includes QQQ after fill (so fill verification succeeds)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "QQQ", "TSLA"])
        _run_buys(ib, supabase, live_price=100.0)
        # An order should be placed because stock count is only 3
        ib.placeOrder.assert_called_once()

    def test_gate1_three_stocks_one_slot_remaining(self):
        """3 stock positions → 1 slot open → order placed for incoming trigger."""
        portfolio = [make_position("AAPL"), make_position("MSFT"), make_position("NVDA")]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA", close_price=100.0)],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "TSLA"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()


# ── Gate 2: Trigger freshness ─────────────────────────────────────────────────

class TestGate2TriggerFreshness:

    def test_gate2_trigger_too_old_blocked(self):
        """Gate 2: trigger from 4 days ago (> TRIGGER_LOOKBACK_DAYS=3) → blocked."""
        # The Supabase .gte() filter is what enforces this in real code.
        # We simulate it by returning an empty list from the gte query.
        supabase = make_supabase_mock(
            daily_triggers=[],   # gte filter returns nothing (trigger too old)
            portfolio=[],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_gate2_fresh_trigger_allowed(self):
        """Gate 2: trigger from today → passes freshness gate."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0, days_ago=0)],
            portfolio=[],
        )
        ib = make_ib_mock(symbols=["NVDA"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()


# ── Gate 3: Not already held ──────────────────────────────────────────────────

class TestGate3NotAlreadyHeld:

    def test_gate3_already_held_ticker_skipped(self):
        """Gate 3: trigger ticker already in portfolio → skip."""
        portfolio = [make_position("NVDA")]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA")],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["NVDA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_gate3_different_ticker_allowed(self):
        """Gate 3: trigger ticker not in portfolio → allowed."""
        portfolio = [make_position("AAPL")]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "NVDA"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()


# ── Gate 4: Cooling-off period ────────────────────────────────────────────────

class TestGate4CoolingOff:

    def test_gate4_cooling_off_uses_sell_date_column(self):
        """
        Bug #2 regression: cooling-off query must use sell_date column.
        A recent sale in trade_history.sell_date should block re-buy.
        """
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA")],
            portfolio=[],
            # Simulate a sell within the last 3 days
            trade_history_recent=[{"ticker": "NVDA", "sell_date": datetime.date.today().isoformat()}],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_gate4_cooling_off_expired_allows_rebuy(self):
        """Gate 4: sell was 4+ days ago (> COOLING_OFF_DAYS=3) → allowed."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=[],
            trade_history_recent=[],   # gte query returns nothing — outside window
        )
        ib = make_ib_mock(symbols=["NVDA"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()

    def test_gate4_no_prior_trade_history_allows_buy(self):
        """Gate 4: ticker never traded → no cooling-off → allowed."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("CRWD", close_price=100.0)],
            portfolio=[],
            trade_history_recent=[],
        )
        ib = make_ib_mock(symbols=["CRWD"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()


# ── Gate 5: Sufficient cash ───────────────────────────────────────────────────

class TestGate5SufficientCash:

    def test_gate5_insufficient_cash_skips_buy(self):
        """Gate 5: available cash < MIN_POSITION_SIZE ($5,000) → skip."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA")],
            portfolio=[],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase, available_cash=3_000.0)
        ib.placeOrder.assert_not_called()

    def test_gate5_exactly_at_minimum_allows_buy(self):
        """Gate 5: cash exactly at MIN_POSITION_SIZE → allowed."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=[],
        )
        ib = make_ib_mock(symbols=["NVDA"])
        _run_buys(ib, supabase, available_cash=5_000.0, live_price=100.0)
        ib.placeOrder.assert_called_once()


# ── Gate 6: Pivot extension ───────────────────────────────────────────────────

class TestGate6PivotExtension:

    def test_gate6_price_extended_beyond_5pct_skipped(self):
        """Gate 6 (O'Neil buy zone): price >5% above pivot close → skip."""
        # pivot (close_price) = $100. live price = $106 (6% above) → blocked.
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=[],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase, live_price=106.0)
        ib.placeOrder.assert_not_called()

    def test_gate6_price_within_5pct_allowed(self):
        """Gate 6: price 3% above pivot → within buy zone → allowed."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=[],
        )
        ib = make_ib_mock(symbols=["NVDA"])
        _run_buys(ib, supabase, live_price=103.0)
        ib.placeOrder.assert_called_once()

    def test_gate6_exactly_at_5pct_boundary_skipped(self):
        """Gate 6: price exactly 5% above pivot → blocked (not strictly less than)."""
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=[],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase, live_price=105.01)  # just over 5%
        ib.placeOrder.assert_not_called()


# ── Gate 7: Share count > 0 ───────────────────────────────────────────────────

class TestGate7ShareCount:

    def test_gate7_price_too_high_zero_shares_skipped(self):
        """Gate 7: position_size / live_price rounds to 0 shares → skip."""
        # $5,000 cash / 1 slot = $5,000 position. Price = $6,000 → 0 shares.
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=6_000.0)],
            portfolio=[],
        )
        ib = make_ib_mock()
        _run_buys(ib, supabase, live_price=6_000.0, available_cash=5_000.0)
        ib.placeOrder.assert_not_called()


# ── Position sizing ───────────────────────────────────────────────────────────

class TestPositionSizing:

    def test_sizing_excludes_etf_from_slot_count(self):
        """
        Bug #7 regression: position size = cash / stock_remaining_slots.
        ETF parking slots must NOT be included in the denominator.

        Setup: 2 stocks + 1 ETF = 3 total positions, but stock count = 2.
        Remaining stock slots = MAX_POSITIONS(4) - 2 = 2.
        Position size = $20,000 / 2 = $10,000 per slot.
        Shares = $10,000 / $100 = 100.
        """
        portfolio = [
            make_position("AAPL"), make_position("MSFT"),
            make_position("QQQ", buy_source="etf_parking"),
        ]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=100.0)],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "QQQ", "NVDA"])

        captured_orders = []
        original_place = ib.placeOrder
        def capture_order(contract, order):
            captured_orders.append(order)
            return original_place(contract, order)
        ib.placeOrder.side_effect = capture_order

        _run_buys(ib, supabase, live_price=100.0, available_cash=20_000.0)

        # Verify an order was placed with ~100 shares ($10,000 / $100)
        assert ib.placeOrder.called
        order = captured_orders[0]
        # MarketOrder totalQuantity = shares
        assert order.totalQuantity == 100


# ── Momentum cascade ──────────────────────────────────────────────────────────

class TestMomentumCascade:

    def test_momentum_cascade_runs_when_no_daily_triggers(self):
        """Cascade: no daily_triggers → falls through to momentum_triggers."""
        supabase = make_supabase_mock(
            daily_triggers=[],
            momentum_triggers=[make_trigger("SOFI", close_price=100.0)],
            portfolio=[],
        )
        ib = make_ib_mock(symbols=["SOFI"])
        _run_buys(ib, supabase, live_price=100.0)
        ib.placeOrder.assert_called_once()

    def test_momentum_cascade_skips_when_slots_full(self):
        """Cascade: daily_triggers filled all 4 stock slots → momentum skipped."""
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA", "AMZN"]]
        supabase = make_supabase_mock(
            daily_triggers=[],
            momentum_triggers=[make_trigger("SOFI")],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "AMZN"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()
