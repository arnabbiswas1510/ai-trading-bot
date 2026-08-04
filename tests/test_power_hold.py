"""
test_power_hold.py - Tests for the O'Neil 8-week hold rule.

From "How to Make Money in Stocks": a stock that gains 20%+ within 3 weeks of a
proper breakout is behaving like a genuine market leader and should be held at
least 8 weeks rather than trimmed on the first wobble.

Critical invariants:
  - Qualifies only if the gain threshold is met INSIDE the trigger window
  - Once armed the flag is sticky: a later pullback cannot cancel it
  - Protection expires after POWER_HOLD_DURATION_DAYS
  - Persisting the flag degrades gracefully when the column is missing (PGRST204)
  - The rule can be disabled entirely via POWER_HOLD_ENABLED
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from execution_agent import is_power_hold_active, maybe_arm_power_hold


def _pos(peak_pct=0.0, power_hold=False, ticker="TEST"):
    return {
        "ticker": ticker,
        "highest_unrealized_pct": peak_pct,
        "power_hold": power_hold,
    }


def _client():
    """Supabase client stub whose update() call chain records the payload."""
    client = MagicMock()
    return client


class TestQualification:

    def test_qualifies_at_20pct_within_window(self):
        assert is_power_hold_active(_pos(peak_pct=20.0), calendar_days=14) is True

    def test_does_not_qualify_below_gain_threshold(self):
        assert is_power_hold_active(_pos(peak_pct=19.9), calendar_days=14) is False

    def test_does_not_qualify_when_gain_came_too_late(self):
        """+25% but only after 30 days - that is a normal advance, not a leader."""
        assert is_power_hold_active(_pos(peak_pct=25.0), calendar_days=30) is False

    def test_qualifies_on_the_boundary_day(self):
        assert is_power_hold_active(_pos(peak_pct=20.0), calendar_days=21) is True

    def test_handles_missing_peak_field(self):
        assert is_power_hold_active({"ticker": "X"}, calendar_days=5) is False

    def test_handles_null_peak_field(self):
        assert is_power_hold_active(_pos(peak_pct=None), calendar_days=5) is False


class TestStickiness:

    def test_armed_flag_survives_a_pullback(self):
        """
        Qualified on day 12, now day 40 and the peak field is irrelevant.
        Without stickiness the position would silently lose protection once the
        21-day trigger window closed.
        """
        assert is_power_hold_active(_pos(peak_pct=25.0, power_hold=True), calendar_days=40) is True

    def test_armed_flag_expires_after_duration(self):
        assert is_power_hold_active(_pos(peak_pct=25.0, power_hold=True), calendar_days=57) is False

    def test_armed_flag_active_on_final_day(self):
        assert is_power_hold_active(_pos(peak_pct=25.0, power_hold=True), calendar_days=56) is True


class TestDisableSwitch:

    def test_disabled_never_activates(self):
        with patch.object(execution_agent, "POWER_HOLD_ENABLED", False):
            assert is_power_hold_active(_pos(peak_pct=50.0), calendar_days=5) is False

    def test_disabled_never_arms(self):
        client = _client()
        with patch.object(execution_agent, "POWER_HOLD_ENABLED", False):
            assert maybe_arm_power_hold(client, _pos(peak_pct=50.0), 5) is False
        client.table.assert_not_called()


class TestArming:

    def test_arms_and_persists_flag(self):
        client = _client()
        pos = _pos(peak_pct=22.0, ticker="NVDA")
        with patch.object(execution_agent.notifier, "_send", MagicMock()):
            assert maybe_arm_power_hold(client, pos, 10) is True
        assert pos["power_hold"] is True
        client.table.assert_called_once_with("portfolio_positions")
        update_payload = client.table.return_value.update.call_args.args[0]
        assert update_payload == {"power_hold": True}

    def test_does_not_arm_when_not_qualified(self):
        client = _client()
        assert maybe_arm_power_hold(client, _pos(peak_pct=5.0), 10) is False
        client.table.assert_not_called()

    def test_does_not_rewrite_already_armed_flag(self):
        client = _client()
        assert maybe_arm_power_hold(client, _pos(peak_pct=30.0, power_hold=True), 10) is True
        client.table.assert_not_called()

    def test_already_armed_but_expired_returns_false(self):
        client = _client()
        assert maybe_arm_power_hold(client, _pos(peak_pct=30.0, power_hold=True), 90) is False

    def test_degrades_gracefully_when_column_missing(self):
        """
        PGRST204 = migration not run yet. The rule must still apply in-memory for
        this cycle rather than crashing the monitor loop.
        """
        client = _client()
        client.table.return_value.update.return_value.eq.return_value.execute.side_effect = \
            Exception("PGRST204: column power_hold does not exist")
        pos = _pos(peak_pct=25.0)
        with patch.object(execution_agent.notifier, "_send", MagicMock()):
            assert maybe_arm_power_hold(client, pos, 10) is True
        assert pos["power_hold"] is True

    def test_notification_failure_does_not_break_arming(self):
        client = _client()
        with patch.object(execution_agent.notifier, "_send",
                          MagicMock(side_effect=Exception("telegram down"))):
            assert maybe_arm_power_hold(client, _pos(peak_pct=25.0), 10) is True
