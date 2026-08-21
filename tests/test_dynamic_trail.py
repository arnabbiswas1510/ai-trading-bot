"""
test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the
dynamic trailing stop tightening system.

Critical invariants:
  - Profit lever: arms at >=6% gain and tightens the trail to 1.5% from HWM
  - Time lever:   DISABLED by default (time held is not a sell signal)
  - When the time lever is enabled, the tighter of the two levers wins
  - One-way only: never loosens a stop (returns None if already tight enough)
  - Returns None when no change warranted (no IBKR order churn)

The current ladder is deliberately aggressive: once a trade is up +6%, the bot
stops treating it as a nascent leader and instead banks the first leg with a
1.5% give-back cap from the high-water mark. This was chosen after live-trade
review showed repeated round-trips from peak back into mediocre exits.
See decisions/2026-08-20_hwm-profit-lock-first-leg.md
"""

import sys
import os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from execution_agent import _compute_dynamic_trail_pct


class TestProfitLever:

    def test_no_change_below_6pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=5.9, calendar_days=0, current_pct=0.07)
        assert result is None

    def test_tightens_at_6pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=6.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_larger_winner_keeps_same_profit_lock(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_very_large_winner_still_uses_hwm_profit_lock(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=300.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_modest_gain_does_not_tighten_inside_base_stop(self):
        """
        The profit-lock must wait for the full +6% gain threshold. A merely green
        position is still allowed the base stop.
        """
        result = _compute_dynamic_trail_pct(unrealized_pct=5.2, calendar_days=12, current_pct=0.07)
        assert result is None


class TestTimeLeverDisabledByDefault:
    """Time held is not a sell signal - the time lever must not fire."""

    @pytest.mark.parametrize("days", [8, 15, 22, 30, 45, 90])
    def test_time_alone_never_tightens(self, days):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=days, current_pct=0.07)
        assert result is None, f"time lever fired at {days} days"

    def test_long_held_flat_position_keeps_base_stop(self):
        """
        Regression: the time lever must not penalize a flat position just because
        time passed.
        """
        result = _compute_dynamic_trail_pct(unrealized_pct=2.0, calendar_days=30, current_pct=0.07)
        assert result is None

    def test_big_winner_held_long_uses_profit_tier_only(self):
        """A big winner still uses the profit-lock tier, not the disabled time lever."""
        result = _compute_dynamic_trail_pct(unrealized_pct=35.0, calendar_days=60, current_pct=0.07)
        assert result == pytest.approx(0.015)


class TestTimeLeverOptIn:
    """TRAIL_TIME_TIERS_ENABLED=true restores the legacy behaviour."""

    LEGACY = [(30, 0.035), (22, 0.040), (15, 0.050), (8, 0.060), (0, None)]

    def test_time_lever_fires_when_explicitly_enabled(self):
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=15, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_tighter_of_two_levers_wins_when_enabled(self):
        """+6% (profit->1.5%) vs 30 days (time->3.5%) - profit is tighter."""
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=6.0, calendar_days=30, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_profit_lever_wins_when_tighter_than_time(self):
        """Any winner beyond +6% keeps the 1.5% cap even when time tightening exists."""
        with patch.object(execution_agent, "TRAIL_TIME_TIERS", self.LEGACY):
            result = _compute_dynamic_trail_pct(unrealized_pct=50.0, calendar_days=8, current_pct=0.07)
        assert result == pytest.approx(0.015)


class TestOneWayOnly:

    def test_no_change_when_already_at_correct_tier(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=21.0, calendar_days=0, current_pct=0.015)
        assert result is None

    def test_no_loosening_on_dip(self):
        """Once locked to 1.5%, a dip in profit must not restore a wider trail."""
        result = _compute_dynamic_trail_pct(unrealized_pct=22.0, calendar_days=5, current_pct=0.015)
        assert result is None

    def test_crossing_the_arm_threshold_tightens_from_base_stop(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=6.0, calendar_days=5, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_never_loosens_a_manually_tightened_stop(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=25.0, calendar_days=5, current_pct=0.01)
        assert result is None
