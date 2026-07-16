"""
test_plateau_rotation.py - Tests for the 3-tier plateau rotation strategy.

All tiers fire at 3:45 PM EOD only. Mock date is 2026-06-20 (Friday).
  Tier 1 (RS Decay)  -> writes recommendation, does NOT auto-sell.
  Tier 2 (Score Gap) -> writes recommendation, does NOT auto-sell.
  Tier 3 (Hard time-stop, >= 7 trading days) -> auto-executes execute_sell.

HWM date guidance relative to mock date 2026-06-20:
  _hwm(1)  = 2026-06-19 -> 1 trading day
  _hwm(3)  = 2026-06-17 -> 3 trading days (Mon)
  _hwm(4)  = 2026-06-16 -> 4 trading days
  _hwm(5)  = 2026-06-13 -> 5 trading days (Fri prior week)
  _hwm(6)  = 2026-06-12 -> 6 trading days
  _hwm(7)  = 2026-06-11 -> 7 trading days -> Tier 3 eligible
  _hwm(10) = 2026-06-06 -> ~8-9 trading days (Tier 3)
"""

import datetime
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_supabase_mock, make_ib_mock, make_position, make_trigger
import execution_agent

# Mock "today" for all tests (Friday)
_MOCK_DATE = datetime.date(2026, 6, 20)
_MOCK_DT   = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=ZoneInfo("America/New_York"))


def _hwm(calendar_days_ago: int) -> str:
    """Return hwm_date string that is exactly `calendar_days_ago` calendar days
    before the mocked date 2026-06-20."""
    return (_MOCK_DATE - datetime.timedelta(days=calendar_days_ago)).isoformat()


def _run_eod(ib, supabase_mock, live_rs_return=None):
    """Run monitor_portfolio_intraday at 3:50 PM ET (EOD window)."""
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
# Tier 1: RS Decay Gate
# ============================================================================

class TestTier1RsDecay:

    def test_writes_tier1_recommendation_when_rs_decayed(self):
        """RS decay >= 15 pts, stalled >= 3 trading days, fresh trigger -> TIER_1."""
        # 2026-06-17 = 3 trading days before 2026-06-20 (meets RS_DECAY_MIN_DAYS=3)
        pos = make_position("AAPL", hwm_date=_hwm(3), entry_rs_score=70)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=80)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        # live_rs=50 -> decay = 70-50 = 20 >= RS_DECAY_GATE=15
        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert any("TIER_1" in c for c in _update_call_strings(mock_sb)), \
            "Expected TIER_1 recommendation to be written"

    def test_no_recommendation_when_entry_rs_missing(self):
        """entry_rs_score=None -> Tier 1 skipped, no recommendation."""
        pos = make_position("AAPL", hwm_date=_hwm(3), entry_rs_score=None)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio,
                                     daily_triggers=[make_trigger("GOOG", final_score=80)])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        # With 3 trading days stalled, no Tier 3. entry_rs=None, so no Tier 1.
        # No Tier 2 because entry_final_score is also None (no score gap to compare).
        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert not any("TIER_1" in c for c in _update_call_strings(mock_sb))

    def test_no_recommendation_when_stalled_below_min_days(self):
        """1 trading day stalled < RS_DECAY_MIN_DAYS=3 -> no Tier 1 recommendation."""
        # 2026-06-19 = 1 trading day before 2026-06-20
        pos = make_position("AAPL", hwm_date=_hwm(1), entry_rs_score=70)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio,
                                     daily_triggers=[make_trigger("GOOG", final_score=80)])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert not any("TIER_1" in c for c in _update_call_strings(mock_sb))

    def test_no_recommendation_when_no_fresh_trigger(self):
        """RS decayed but no fresh trigger -> no recommendation."""
        pos = make_position("AAPL", hwm_date=_hwm(3), entry_rs_score=70)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert not any("TIER_1" in c for c in _update_call_strings(mock_sb))


# ============================================================================
# Tier 2: Score Differential
# ============================================================================

class TestTier2ScoreDifferential:

    def test_writes_tier2_recommendation_when_gap_large(self):
        """Score gap >= 20, stalled >= 5 days -> TIER_2 set, no auto-sell.
        Uses hwm_date such that AAPL stalls exactly 5 trading days (< PLATEAU_DAYS=7).
        """
        # 2026-06-13 = 5 trading days before 2026-06-20 (Fri -> Mon Jun 9 to Fri Jun 13)
        # Actually let's count: Jun 20 (Fri), Jun 19, Jun 18, Jun 17, Jun 16, Jun 13 = 5 days
        # hwm_date = Jun 13 means 5 trading days stalled (>= SCORE_GAP_MIN_DAYS=5, < PLATEAU_DAYS=7)
        pos = make_position("AAPL",
                            hwm_date="2026-06-13",
                            entry_final_score=50,
                            entry_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=75)  # gap = 75-50 = 25 >= 20
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()
        assert any("TIER_2" in c for c in _update_call_strings(mock_sb)), \
            "Expected TIER_2 recommendation to be written"

    def test_no_recommendation_when_gap_too_small(self):
        """Score gap = 10 < SCORE_UPGRADE_GAP=20 -> no Tier 2 recommendation.
        Use 5 trading days so Tier 3 does not fire.
        """
        pos = make_position("AAPL",
                            hwm_date="2026-06-13",
                            entry_final_score=65,
                            entry_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=75)  # gap = 10 < 20
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()
        assert not any("TIER_2" in c for c in _update_call_strings(mock_sb))

    def test_no_recommendation_when_stalled_below_min_days(self):
        """days_since_hwm < SCORE_GAP_MIN_DAYS=5 -> no Tier 2. Use 1 day stalled."""
        pos = make_position("AAPL",
                            hwm_date=_hwm(1),    # 1 trading day
                            entry_final_score=50,
                            entry_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=80)  # gap = 30 >= 20
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()
        assert not any("TIER_2" in c for c in _update_call_strings(mock_sb))

    def test_tier1_not_overwritten_by_tier2(self):
        """TIER_1 already set on position -> Tier 2 must not overwrite with TIER_2.
        Use 3 trading days: meets RS_DECAY_MIN_DAYS + SCORE_GAP_MIN_DAYS < 5 so only T1 fires.
        """
        pos = make_position("AAPL",
                            hwm_date=_hwm(3),
                            entry_rs_score=70,
                            entry_final_score=50,
                            rotation_recommendation="TIER_1")
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=80)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=50)

        # TIER_2 should never be written (TIER_1 takes priority)
        assert not any("TIER_2" in c for c in _update_call_strings(mock_sb)), \
            "TIER_2 must not overwrite an existing TIER_1 recommendation"


# ============================================================================
# Tier 3: Hard Time-Stop (Auto-Execute)
# ============================================================================

class TestTier3HardStop:

    def test_tier3_auto_executes_at_7_days(self):
        """days_since_hwm >= PLATEAU_DAYS=7, fresh trigger -> execute_sell called."""
        # hwm = 2026-06-11 (Wed). Trading days to 2026-06-20 (Fri):
        # Jun 12(Thu), 13(Fri), 16(Mon), 17(Tue), 18(Wed), 19(Thu), 20(Fri) = 7 days
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",
                            entry_rs_score=None,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)  # gap=5 < 20, no Tier 2
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_called_once()
        sell_ticker = mock_sell.call_args.args[2]
        sell_reason = mock_sell.call_args.args[8]
        assert sell_ticker == "AAPL"
        assert "Tier 3" in sell_reason

    def test_tier3_clears_recommendation_after_sell(self):
        """After Tier 3 auto-sell, rotation_recommendation cleared to None."""
        pos = make_position("AAPL",
                            hwm_date="2026-06-11",
                            rotation_recommendation="TIER_2",
                            entry_rs_score=None)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        _run_eod(ib, mock_sb, live_rs_return=None)

        update_calls = _update_call_strings(mock_sb)
        assert any("None" in c or "rotation_recommendation': None" in c
                   for c in update_calls), \
            f"Expected None clear after Tier 3 sell; calls: {update_calls}"

    def test_tier3_no_auto_execute_when_not_stalled_enough(self):
        """6 trading days stalled < PLATEAU_DAYS=7 -> Tier 3 does NOT fire."""
        # 2026-06-12 (Thu) -> Jun 12,13,16,17,18,19,20 = 7? Let me use Jun 13:
        # Jun 13(Fri)->Jun16,17,18,19,20 = 5 days + Jun13 itself? No, trading_days_between
        # counts from start (exclusive) to end (exclusive). Jun13 -> Jun20:
        # Jun 16,17,18,19,20 = 5 trading days. So use Jun12 for 6 trading days:
        # Jun12->Jun20: Jun 13(Fri),16,17,18,19,20 = 6 trading days.
        pos = make_position("AAPL",
                            hwm_date="2026-06-12",
                            entry_rs_score=None,
                            entry_final_score=50)
        portfolio = _full_portfolio(pos)
        trigger = make_trigger("GOOG", final_score=55)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=None)

        mock_sell.assert_not_called()


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:

    def test_no_action_without_fresh_trigger(self):
        """No fresh triggers -> no sell, no recommendation for any tier."""
        pos = make_position("AAPL",
                            hwm_date="2026-05-20",  # >30 trading days - very stale
                            entry_rs_score=70, entry_final_score=50)
        portfolio = _full_portfolio(pos)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
        assert not any("TIER_" in c for c in _update_call_strings(mock_sb))

    def test_eod_block_skipped_when_portfolio_not_full(self):
        """Only 2 positions (< MAX_POSITIONS=4) -> EOD block skipped entirely."""
        pos = make_position("AAPL", hwm_date="2026-05-20", entry_rs_score=70)
        portfolio = [pos, make_position("MSFT")]  # only 2
        trigger = make_trigger("GOOG", final_score=90)
        mock_sb = make_supabase_mock(portfolio=portfolio, daily_triggers=[trigger])
        ib = make_ib_mock(symbols=[p["ticker"] for p in portfolio])

        mock_sell = _run_eod(ib, mock_sb, live_rs_return=50)

        mock_sell.assert_not_called()
