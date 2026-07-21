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


def _run_eod(ib, supabase_mock, live_rs_return=None, live_price=105.0):
    """Run monitor_portfolio_intraday at 3:50 PM ET (EOD window).
    Returns the mock_sell object for assertion.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_trailing_stop", return_value=("TS_MOCK", 0.07)), \
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

    def test_no_action_without_triggers_if_within_6_days(self):
        """Within 3-6 days, even with param drift, no swap occurs if there are no fresh triggers."""
        pos = make_position("AAPL",
                            buy_price=100.0,
                            buy_date="2026-06-14T12:00:00+00:00",
                            entry_rs_score=90,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        with patch("execution_agent._fetch_ohlcv", return_value=[]):
            mock_sell = _run_eod(ib, mock_sb, live_rs_return=70, live_price=100.0)

        mock_sell.assert_not_called()

    def test_eod_block_skipped_when_portfolio_not_full(self):
        """Only 2 positions (< MAX_POSITIONS=4) -> EOD Rank & Replace swap block skipped (capacity check)."""
        pos = make_position("AAPL", buy_price=100.0, buy_date="2026-06-14T12:00:00+00:00", entry_rs_score=90, entry_final_score=50)
        portfolio = [pos, make_position("MSFT")]  # only 2
        trigger = make_trigger("GOOG", final_score=90)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        with patch("execution_agent._fetch_ohlcv", return_value=[]):
            mock_sell = _run_eod(ib, mock_sb, live_rs_return=70, live_price=100.0)

        mock_sell.assert_not_called()

    def test_tier2_recommendation_not_written(self):
        """TIER_2 score gap recommendation is obsolete and must never be written."""
        pos = make_position("AAPL", buy_price=100.0, buy_date="2026-06-14T12:00:00+00:00")
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=85)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=None)

        assert not any("TIER_2" in c for c in _update_call_strings(mock_sb))
