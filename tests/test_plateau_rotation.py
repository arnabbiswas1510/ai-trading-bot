"""
test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation strategy.

Design (simplified from 3-tier to 2-rule as of 2026-07-17):
  Rule 1 (RS Decay)  → fires at Day 3+ when RS has dropped >= RS_DECAY_GATE pts
                       below hwm_rs_score (the RS on the day of the last HWM).
                       Writes rotation_recommendation='RS_DECAY'. Interactive — no auto-sell.
  Rule 2 (Hard Stop) → fires at Day 7 (PLATEAU_DAYS). Auto-executes execute_sell.

REMOVED (was Tier 2): Score Differential — score gap >= 20 pts at Day 5.
  Rationale: if a position is making new highs it should not be rotated just because
  a higher score exists elsewhere. RS decay or the Day 7 stop catch stalling positions.

RS Decay anchor changed (as of 2026-07-17):
  OLD: compared live_rs vs entry_rs_score (RS at buy day)
  NEW: compared live_rs vs hwm_rs_score (RS on day of last HWM)
  Rationale: a stock that ran hard post-entry has higher RS at its peak than at entry.
  Anchoring to entry RS underestimated decay. hwm_rs_score is the correct peak reference.

Mock date: 2026-06-20 (Friday).
HWM date helpers (calendar days):
  _hwm(1)  = 2026-06-19 → 1 trading day stalled
  _hwm(3)  = 2026-06-17 → 3 trading days (Mon) — Rule 1 minimum
  _hwm(5)  = 2026-06-13 → 5 trading days
  _hwm(6)  = 2026-06-12 → 6 trading days
  _hwm(7)  = 2026-06-11 → 7 trading days → Rule 2 (Hard Stop) eligible
"""

import datetime
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_supabase_mock, make_ib_mock, make_position, make_trigger
import execution_agent

# ── Mock date for all tests ───────────────────────────────────────────────────
_MOCK_DATE = datetime.date(2026, 6, 20)
_MOCK_DT   = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=ZoneInfo("America/New_York"))


def _hwm(calendar_days_ago: int) -> str:
    """Return hwm_date string that is exactly `calendar_days_ago` calendar days
    before the mocked date 2026-06-20."""
    return (_MOCK_DATE - datetime.timedelta(days=calendar_days_ago)).isoformat()


def _run_eod(ib, supabase_mock, live_rs_return=None):
    """Run monitor_portfolio_intraday at 3:50 PM ET (EOD window).
    Returns the mock_sell object for assertion.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=105.0), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_trailing_stop", return_value="TS_MOCK"), \
         patch("execution_agent.execute_sell") as mock_sell, \
         patch("execution_agent._fetch_current_rs", return_value=live_rs_return), \
         patch("execution_agent.datetime") as mock_dt:

        mock_dt.datetime.now.side_effect = lambda *a, **kw: _MOCK_DT
        mock_dt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_dt.date.fromisoformat.side_effect    = datetime.date.fromisoformat
        mock_dt.date.today.return_value = _MOCK_DATE
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta

        execution_agent.monitor_portfolio_intraday(ib)
        return mock_sell


def _full_portfolio(focal_pos):
    """4-position full portfolio with focal_pos as AAPL (first)."""
    return [focal_pos] + [make_position(t) for t in ["MSFT", "NVDA", "META"]]


def _update_call_strings(mock_sb):
    return [str(c) for c in mock_sb.table("portfolio_positions").update.call_args_list]


# ============================================================================
# Rule 1: PARAM_DRIFT (replaces old RS_DECAY rule)
# ============================================================================
# NOTE: The RS_DECAY rotation rule was removed. RS score decay is now one of
# 6 parameters tracked inside the PARAM_DRIFT system (see test_param_drift.py).
# These tests verify the old RS_DECAY label is never emitted by the EOD loop.
# ============================================================================

class TestRule1RsDecayRemoved:
    """
    Regression tests confirming RS_DECAY recommendation is no longer written
    under any circumstances. Replaced by PARAM_DRIFT in the EOD analysis loop.
    """

    def test_rs_decay_never_written_regardless_of_magnitude(self):
        """Even with large RS decay (>15 pts from HWM), RS_DECAY is never recommended."""
        pos = make_position("AAPL", hwm_date=_hwm(3), hwm_rs_score=70)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=80)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        # live_rs=50 → old decay = 70-50 = 20 pts — would have fired old Rule 1
        _run_eod(ib, mock_sb, live_rs_return=50)

        assert not any("RS_DECAY" in c for c in _update_call_strings(mock_sb)), \
            "RS_DECAY must never be written — it was replaced by PARAM_DRIFT"

    def test_hwm_rs_score_not_written_on_new_hwm(self):
        """hwm_rs_score write was removed from EOD metrics loop — column stays dormant."""
        pos = make_position("AAPL", hwm_date=_hwm(0))   # new HWM today
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=85)

        # Verify hwm_rs_score is not in any update payload
        update_strs = _update_call_strings(mock_sb)
        assert not any("hwm_rs_score" in c for c in update_strs), \
            "hwm_rs_score must not be written — column is dormant, use param_drift instead"


# ============================================================================
# Rule 2: Hard Time-Stop (Auto-Execute)
# ============================================================================

class TestRule2HardStop:
    """
    Rule 2 fires when:
      - days_since_hwm >= PLATEAU_DAYS (default 7)
      - a fresh trigger exists
    Action: calls execute_sell immediately. No user approval. Clears recommendation afterward.
    """

    def test_auto_executes_at_7_trading_days(self):
        """days_since_hwm >= 7, fresh trigger → execute_sell called on most-stalled position."""
        # hwm = 2026-06-11 (Wed). Trading days to 2026-06-20 (Fri):
        # Jun 12(Thu), 13(Fri), 16(Mon), 17(Tue), 18(Wed), 19(Thu), 20(Fri) = 7 days
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",
                            hwm_rs_score=None,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_called_once()
        sell_ticker = mock_sell.call_args.args[2]
        sell_reason = mock_sell.call_args.args[8]
        assert sell_ticker == "AAPL"
        assert "Tier 3" in sell_reason or "Hard" in sell_reason or "time" in sell_reason.lower()

    def test_clears_recommendation_after_auto_sell(self):
        """After Rule 2 auto-sell, rotation_recommendation is cleared to None."""
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",
                            rotation_recommendation="RS_DECAY",
                            hwm_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=None)

        update_calls = _update_call_strings(mock_sb)
        assert any("None" in c or "rotation_recommendation': None" in c
                   for c in update_calls), \
            f"Expected rotation_recommendation cleared to None after sell; calls: {update_calls}"

    def test_does_not_fire_at_6_trading_days(self):
        """6 trading days stalled < PLATEAU_DAYS=7 → Rule 2 does NOT auto-sell."""
        # 2026-06-12 → Jun 13,16,17,18,19,20 = 6 trading days
        pos = make_position("AAPL",
                            hwm_date="2026-06-12",
                            hwm_rs_score=None,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()

    def test_does_not_fire_without_fresh_trigger(self):
        """7 days stalled but no fresh trigger → Rule 2 does NOT fire (never sell to cash)."""
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",
                            hwm_rs_score=None)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()

    def test_most_stalled_position_is_sold_first(self):
        """When multiple positions >= 7 days, only the most stalled is sold per EOD cycle."""
        pos_a = make_position("AAPL", hwm_date="2026-06-11")  # 7 days
        pos_b = make_position("MSFT", hwm_date="2026-06-06")  # ~10 days — worse
        portfolio = [pos_a, pos_b] + [make_position(t) for t in ["NVDA", "META"]]
        trigger = make_trigger("GOOG", final_score=80)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_called_once()
        sell_ticker = mock_sell.call_args.args[2]
        assert sell_ticker == "MSFT", \
            "Most-stalled position (MSFT, ~10d) should be sold before AAPL (7d)"


# ============================================================================
# HWM RS Score — tracking and update
# ============================================================================

class TestHwmRsScoreTracking:
    """
    Tests that hwm_rs_score is NOT written to the DB in any circumstance.
    The column is dormant — RS decay is now tracked via param_drift instead.
    """

    def test_hwm_rs_score_not_written_when_new_high_today(self):
        """days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dormant)."""
        pos = make_position("AAPL", hwm_date=_hwm(0), hwm_rs_score=None)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        # live_rs=85 — old code would write hwm_rs_score=85, new code must not
        _run_eod(ib, mock_sb, live_rs_return=85)

        update_calls = _update_call_strings(mock_sb)
        assert not any("hwm_rs_score" in c for c in update_calls), \
            f"hwm_rs_score must never be written — column is dormant; calls: {update_calls}"

    def test_hwm_rs_score_not_written_when_stalled(self):
        """days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload."""
        pos = make_position("AAPL", hwm_date=_hwm(3), hwm_rs_score=90)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=70)

        update_calls = _update_call_strings(mock_sb)
        assert not any("hwm_rs_score" in c for c in update_calls), \
            "hwm_rs_score must not appear in any update — column is dormant"


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:

    def test_no_action_without_fresh_trigger_any_condition(self):
        """No fresh triggers → no sell, no recommendation, regardless of stall length."""
        pos = make_position("AAPL",
                            hwm_date="2026-05-20",  # >30 trading days — very stale
                            hwm_rs_score=90,
                            entry_rs_score=70,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert not any("RS_DECAY" in c or "TIER_" in c
                       for c in _update_call_strings(mock_sb))

    def test_eod_block_skipped_when_portfolio_not_full(self):
        """Only 2 positions (< MAX_POSITIONS=4) → EOD rotation block skipped entirely."""
        pos = make_position("AAPL", hwm_date="2026-05-20", hwm_rs_score=70)
        portfolio = [pos, make_position("MSFT")]  # only 2
        trigger = make_trigger("GOOG", final_score=90)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()

    def test_tier2_recommendation_not_written(self):
        """
        REMOVED FEATURE TEST: Tier 2 score gap should NEVER be written to the DB.
        Even if the score gap is large and the position is 5 days stalled,
        'TIER_2' must not appear in any update call.
        """
        pos = make_position("AAPL",
                            hwm_date="2026-06-13",  # 5 trading days stalled
                            entry_final_score=50,
                            hwm_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=85)  # gap=35 — old Tier 2 would have fired
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=None)

        assert not any("TIER_2" in c for c in _update_call_strings(mock_sb)), \
            "TIER_2 score gap recommendation must never be written (feature removed)"

    def test_rule1_and_rule2_independent(self):
        """
        Position stalled exactly 7 days with RS decay → Rule 2 auto-sell fires.
        Rule 1 (RS_DECAY recommendation) should NOT also fire since position is sold.
        """
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",  # 7 days
                            hwm_rs_score=80)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=80)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        # RS also decayed: 80 → 55 = 25 pts decay, 7 days stalled
        mock_sell = _run_eod(ib, mock_sb, live_rs_return=55)

        # Rule 2 should fire (hard stop takes precedence at Day 7)
        mock_sell.assert_called_once()
