"""
test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the
two-lever dynamic trailing stop tightening system.

Critical invariants:
  - Profit lever: fires at >=3%, >=8%, >=14%, >=20% gain thresholds
  - Time lever:   fires at >=8, >=15, >=22, >=30 calendar days
  - Tighter of the two levers always wins
  - One-way only: never loosens a stop (returns None if already tight enough)
  - Returns None when no change warranted (no IBKR order churn)
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution_agent import _compute_dynamic_trail_pct


class TestProfitLever:

    def test_no_change_below_3pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=2.9, calendar_days=0, current_pct=0.07)
        assert result is None

    def test_tightens_at_3pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=3.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_tightens_at_8pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=8.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.04)

    def test_tightens_at_14pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.03)

    def test_tightens_at_20pct_gain(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=20.0, calendar_days=0, current_pct=0.07)
        assert result == pytest.approx(0.02)

    def test_fr_scenario_5pt2pct_gain(self):
        """FR: +5.2% gain, 12 days. Profit lever (5%) beats time lever (6%)."""
        result = _compute_dynamic_trail_pct(unrealized_pct=5.2, calendar_days=12, current_pct=0.07)
        assert result == pytest.approx(0.05)


class TestTimeLever:

    def test_no_change_at_7_days(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=0.5, calendar_days=7, current_pct=0.07)
        assert result is None

    def test_tightens_at_8_days(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=8, current_pct=0.07)
        assert result == pytest.approx(0.06)

    def test_tightens_at_15_days(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=15, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_tightens_at_22_days(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=22, current_pct=0.07)
        assert result == pytest.approx(0.04)

    def test_tightens_at_30_days(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=30, current_pct=0.07)
        assert result == pytest.approx(0.035)


class TestTighterOfTwoRule:

    def test_profit_lever_wins_when_tighter(self):
        """+8% gain (profit->4%) vs 12 days (time->6%). Profit wins."""
        result = _compute_dynamic_trail_pct(unrealized_pct=8.0, calendar_days=12, current_pct=0.07)
        assert result == pytest.approx(0.04)

    def test_time_lever_wins_when_tighter(self):
        """+1% gain (profit->None) vs 15 days (time->5%). Time wins."""
        result = _compute_dynamic_trail_pct(unrealized_pct=1.0, calendar_days=15, current_pct=0.07)
        assert result == pytest.approx(0.05)

    def test_both_levers_fire_tighter_wins(self):
        """+14% gain (profit->3%) vs 22 days (time->4%). Profit wins."""
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=22, current_pct=0.07)
        assert result == pytest.approx(0.03)


class TestOneWayOnly:

    def test_no_change_when_already_at_correct_tier(self):
        result = _compute_dynamic_trail_pct(unrealized_pct=3.5, calendar_days=0, current_pct=0.05)
        assert result is None

    def test_no_loosening_on_dip(self):
        """Was at 4% trail, dipped to +6% (would suggest 5%). Must not loosen."""
        result = _compute_dynamic_trail_pct(unrealized_pct=6.0, calendar_days=5, current_pct=0.04)
        assert result is None

    def test_tightens_further_when_crossing_next_tier(self):
        """Already at 4%, crosses +14% -> should tighten to 3%."""
        result = _compute_dynamic_trail_pct(unrealized_pct=14.0, calendar_days=5, current_pct=0.04)
        assert result == pytest.approx(0.03)

    def test_no_change_when_result_equals_current(self):
        """Both levers agree on 5%, current already 5% -> None."""
        result = _compute_dynamic_trail_pct(unrealized_pct=3.0, calendar_days=15, current_pct=0.05)
        assert result is None
