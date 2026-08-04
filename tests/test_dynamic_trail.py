"""
test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the
dynamic trailing stop tightening system.

Critical invariants:
  - Profit lever: fires only at >=20%, >=30%, >=50% gain, and never tighter than 5%
  - Time lever:   DISABLED by default (time held is not a sell signal)
  - When the time lever is enabled, the tighter of the two levers wins
  - One-way only: never loosens a stop (returns None if already tight enough)
  - Returns None when no change warranted (no IBKR order churn)

The profit tiers were widened (from 2-5% starting at +3% gain, to 5-6.5%
starting at +20%) and the time lever disabled after analysis of live trading
showed the old settings capped average wins at +1.27% against average losses of
-2.58% - a 0.49:1 payoff ratio requiring a 67% win rate just to break even.
See decisions/2026-08-04_widen-exits-and-tighten-entries.md
"""

import sys
import os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from execution_agent import _compute_dynamic_trail_pct


class TestProfitLever:

    def test_no_change_below_20pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=19.9, calendar_days=0, current_pct=0.07)
        assert result is None

    def test_tightens_at_20pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=20.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.065)

    def test_tightens_at_30pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=30.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.06)

    def test_tightens_at_50pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=50.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_never_tighter_than_5pct(self):
        """Even a +300% moonshot must keep enough room to survive a shakeout."""
        result = _compute_dynamic_trail_pct(unrealized_pct=300.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_modest_gain_does_not_tighten_inside_base_stop(self):
        """
        Regression: a +5.2% gain used to clamp the trail to 5%, which stopped the
        position out on an ordinary pullback. It must now leave the 7% base stop
        alone.
        """
        result = _compute_dynamic_trail_pct(unrealized_pct=5.2, calendar_days=12, current_pct=0.07)
        assert result is None

    def test_early_teens_gain_does_not_tighten(self):
        """Regression: +14% used to force a 3% trail. Now no change."""
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=5, current_pct=0.07)
        assert result is None


class TestTimeLeverDisabledByDefault:
    """Time held is not a sell signal - the time lever must not fire."""

    @pytest.mark.parametrize("days", [8, 15, 22, 30, 45, 90])
    def test_time_alone_never_tightens(self, days):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=days, current_pct=0.07)
        assert result is None, f"time lever fired at {days} days"

    def test_long_held_flat_position_keeps_base_stop(self):
        """
        Regression: at day 30 the old time lever forced a 3.5% trail regardless
        of performance, making a large winner structurally impossible.
        """
        result = _compute_dynamic_trail_pct(unrealized_pct=2.0, calendar_days=30, current_pct=0.07)
        assert result is None

    def test_big_winner_held_long_uses_profit_tier_only(self):
        """A +35% position at day 60 sits at the 30% profit tier (6%), not 3.5%."""
        result = _compute_dynamic_trail_pct(unrealized_pct=35.0, calendar_days=60, current_pct=0.07)
        assert result == pytest.approx(0.06)


class TestTimeLeverOptIn:
    """TRAIL_TIME_TIERS_ENABLED=true restores the legacy behaviour."""

    LEGACY = [(30, 0.035), (22, 0.040), (15, 0.050), (8, 0.060), (0, None)]

    def test_time_lever_fires_when_explicitly_enabled(self):
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=15, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_tighter_of_two_levers_wins_when_enabled(self):
        """+20% (profit->6.5%) vs 30 days (time->3.5%) - time is tighter."""
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=20.0, calendar_days=30, current_pct=0.07)
        assert result == pytest.approx(0.035)

    def test_profit_lever_wins_when_tighter_than_time(self):
        """+50% (profit->5%) vs 8 days (time->6%) - profit is tighter."""
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=50.0, calendar_days=8, current_pct=0.07)
        assert result == pytest.approx(0.05)


class TestOneWayOnly:

    def test_no_change_when_already_at_correct_tier(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=21.0, calendar_days=0, current_pct=0.065)
        assert result is None

    def test_no_loosening_on_dip(self):
        """Was at 6% trail, dipped to +22% (would suggest 6.5%). Must not loosen."""
        result = _compute_dynamic_trail_pct(unrealized_pct=22.0, calendar_days=5, current_pct=0.06)
        assert result is None

    def test_tightens_further_when_crossing_next_tier(self):
        """Already at 6.5%, crosses +30% -> should tighten to 6%."""
        result = _compute_dynamic_trail_pct(unrealized_pct=30.0, calendar_days=5, current_pct=0.065)
        assert result == pytest.approx(0.06)

    def test_never_loosens_a_manually_tightened_stop(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=25.0, calendar_days=5, current_pct=0.03)
        assert result is None
