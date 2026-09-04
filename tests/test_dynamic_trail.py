"""
test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the
dynamic trailing stop tightening system.

Critical invariants:
  - Profit lever: arms at >=5% gain and tightens the trail to 1.5% from HWM
  - Time lever:   DISABLED by default (time held is not a sell signal)
  - When the time lever is enabled, the tighter of the two levers wins
  - One-way only: never loosens a stop (returns None if already tight enough)
  - Returns None when no change warranted (no IBKR order churn)

The current ladder is deliberately aggressive: once a trade is up +5%, the bot
stops treating it as a nascent leader and instead banks the first leg with a
1.5% give-back cap from the high-water mark. This was chosen after live-trade
review showed repeated round-trips from peak back into mediocre exits.
See decisions/2026-08-22_hwm-profit-lock-arm-5pct.md
"""

import sys
import os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from execution_agent import _compute_dynamic_trail_pct


class TestProfitLever:

    def test_no_change_below_5pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=4.9, calendar_days=0, current_pct=0.07)
        assert result is None

    def test_tightens_at_5pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=5.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_larger_winner_keeps_same_profit_lock(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_very_large_winner_still_uses_hwm_profit_lock(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=300.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_modest_gain_does_not_tighten_inside_base_stop(self):
        """
        The profit-lock must wait for the full +5% gain threshold. A merely green
        position is still allowed the base stop.
        """
        result = _compute_dynamic_trail_pct(unrealized_pct=4.2, calendar_days=12, current_pct=0.07)
        assert result is None


class TestOneWayOnly:

    def test_no_change_when_already_at_correct_tier(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=21.0, calendar_days=0, current_pct=0.015)
        assert result is None

    def test_no_loosening_on_dip(self):
        """Once locked to 1.5%, a dip in profit must not restore a wider trail."""
        result = _compute_dynamic_trail_pct(unrealized_pct=22.0, calendar_days=5, current_pct=0.015)
        assert result is None

    def test_crossing_the_arm_threshold_tightens_from_base_stop(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=5.0, calendar_days=5, current_pct=0.07)
        assert result == pytest.approx(0.015)

    def test_never_loosens_a_manually_tightened_stop(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=25.0, calendar_days=5, current_pct=0.01)
        assert result is None
