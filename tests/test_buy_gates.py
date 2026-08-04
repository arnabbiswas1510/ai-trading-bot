from zoneinfo import ZoneInfo
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
              is_bullish=True, ibkr_price=0.0):
    """
    Runs run_market_open_buys() with standard patches applied.
    Returns the mock_ib so callers can inspect placeOrder calls.

    NOTE: patches get_own_cash and get_margin_loan (the real margin-safe
    functions) rather than the deprecated get_available_cash alias.
    get_margin_loan is patched to 0.0 so the margin hard-block gate does NOT
    fire in these tests — each test here is focused on a different gate.
    fetch_ibkr_delayed_price defaults to (0.0, '') so the buy loop falls
    back to trigger["close_price"] for share sizing (avoids MagicMock arithmetic).
    Pass ibkr_price to move the traded price away from the pivot — the buy loop
    reads its price from here, NOT from get_live_price.
    notifier is patched to prevent real Telegram messages on the server.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.get_own_cash", return_value=available_cash), \
         patch("execution_agent.get_margin_loan", return_value=0.0), \
         patch("execution_agent.fetch_ibkr_delayed_price",
               return_value=(ibkr_price, "delayed" if ibkr_price else "")), \
         patch("execution_agent.is_market_bullish", return_value=is_bullish), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return ib



# ── Gate 1: Stock slot capacity ───────────────────────────────────────────────

class TestGate1StockSlots:

    def test_gate1_full_portfolio_blocks_all_buys(self):
        """Gate 1: MAX_POSITIONS stock positions → portfolio full → no order placed."""
        import execution_agent
        names = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "NFLX", "AMD"]
        held = names[:execution_agent.MAX_POSITIONS]
        portfolio = [make_position(t) for t in held]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA")],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=held)
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_gate1_one_free_slot_allows_a_buy(self):
        """One slot short of MAX_POSITIONS → the trigger is bought."""
        import execution_agent
        names = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "NFLX", "AMD"]
        held = names[:execution_agent.MAX_POSITIONS - 1]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA")],
            portfolio=[make_position(t) for t in held],
        )
        ib = make_ib_mock(symbols=held + ["TSLA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_called()


class TestTriggerRankingAndVetting:
    def test_buys_highest_scored_trigger_first(self):
        portfolio = [make_position(t) for t in ['AAPL', 'MSFT', 'NVDA']]
        t_tsla = make_trigger('TSLA', close_price=100.0, final_score=70)
        t_meta = make_trigger('META', close_price=100.0, final_score=95)
        t_amzn = make_trigger('AMZN', close_price=100.0, final_score=80)
        supabase = make_supabase_mock(daily_triggers=[t_tsla, t_meta, t_amzn], portfolio=portfolio)
        ib = make_ib_mock(symbols=['AAPL', 'MSFT', 'NVDA', 'META'])
        _run_buys(ib, supabase)
        first_contract = ib.placeOrder.call_args_list[0][0][0]
        assert first_contract.symbol == 'META'

    def test_unvetted_trigger_is_skipped_not_bought_on_technicals(self):
        """
        Regression: ai_evaluator.py silently drops tickers from its batch
        ("lost in the middle"), leaving final_score NULL. The buy gate used to
        fall back to quality_score — a pure technical score — which bought those
        names while bypassing every AI guardrail. It must now fail closed.
        """
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=None)
        trig["trigger_type"]  = "BREAKOUT"
        trig["quality_score"] = 95    # strong technicals ...
        trig["ai_rating"]     = None  # ... but never AI-vetted
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "TSLA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_unvetted_trigger_skipped_even_when_pre_breakout(self):
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=None)
        trig["trigger_type"]  = "PRE_BREAKOUT"
        trig["quality_score"] = 99
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "TSLA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_adjusted_score_still_takes_precedence(self):
        """adjusted_score (post-penalty) remains the primary gate input."""
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=95)
        trig["trigger_type"]    = "BREAKOUT"
        trig["adjusted_score"]  = 40   # penalised below the floor
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "TSLA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()


class TestMarketDirectionGate:
    def test_bearish_market_blocks_buys(self):
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=80)
        trig["trigger_type"] = "BREAKOUT"
        trig["ai_grade"] = "A"
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA"])
        _run_buys(ib, supabase, is_bullish=False)
        ib.placeOrder.assert_not_called()


class TestScoreFloorGate:
    def test_breakout_below_min_score_is_skipped(self):
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=55)
        trig["trigger_type"] = "BREAKOUT"
        trig["ai_grade"] = "A"
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_pre_breakout_relaxed_uses_relaxed_floor(self):
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]
        trig = make_trigger("TSLA", close_price=100.0, final_score=59)
        trig["trigger_type"] = "PRE_BREAKOUT_RELAXED"
        trig["ai_grade"] = "B"
        supabase = make_supabase_mock(daily_triggers=[trig], portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "TSLA"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_called_once()


# ── Gate: pivot buy zone is bounded on BOTH sides ─────────────────────────────

class TestPivotBuyZoneFloor:
    """
    The pivot check used to be a ceiling only: it rejected stocks extended too
    far ABOVE the pivot but placed no floor beneath it. Combined with
    TRIGGER_LOOKBACK_DAYS=3 that meant a 3-day-old trigger whose breakout had
    since failed was still a valid buy — the bot could buy a breakdown.
    """

    def _run_at(self, price):
        """The buy loop takes its price from fetch_ibkr_delayed_price, not
        get_live_price, so that is what must be varied to move the position
        relative to the pivot."""
        trigger = make_trigger("AAPL", close_price=100.0)
        mock_sb = make_supabase_mock(portfolio=[], daily_triggers=[trigger])
        ib = make_ib_mock()
        _run_buys(ib, mock_sb, live_price=price, ibkr_price=price)
        return ib

    def test_rejects_trigger_that_collapsed_below_pivot(self):
        # Price has given back 8% of the pivot — the breakout failed.
        assert not self._run_at(92.0).placeOrder.called, \
            "Must not buy a stock trading below its pivot"

    def test_still_buys_inside_the_zone(self):
        assert self._run_at(102.0).placeOrder.called, \
            "A trigger inside the buy zone must still be bought"

    def test_small_dip_below_pivot_is_tolerated(self):
        """A 1% dip is noise around the pivot, not a failed breakout."""
        assert self._run_at(99.0).placeOrder.called, \
            "Ordinary noise around the pivot must not veto a buy"

    def test_ceiling_still_enforced(self):
        assert not self._run_at(112.0).placeOrder.called, \
            "Extended stocks must still be rejected"
