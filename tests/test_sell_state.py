"""
tests/test_sell_state.py

Tests for the sell-state transition notifier — a concise Telegram whenever a
position's GOVERNING exit regime changes (Unproven -> Proven, give-back floor
arming, profit-lock engaging).

Two units:
  sell_state_code()        — the pure regime-precedence function.
  maybe_notify_sell_state() — the latch + notify state machine.

See decisions/2026-09-08_sell-state-transitions.md.
"""

from unittest.mock import MagicMock, patch

import execution_agent as ea


# ── sell_state_code() — governing regime precedence ───────────────────────────
class TestSellStateCode:
    def test_exit_armed_governs_everything(self):
        pos = {"exit_armed": True}
        # even with power hold and a huge peak, armed wins
        assert ea.sell_state_code(pos, "phase2", True, 40.0) == "EXITING"

    def test_power_hold_beats_profit_lock(self):
        pos = {"exit_armed": False}
        assert ea.sell_state_code(pos, "power-hold", True, 12.0) == "POWER_HOLD"

    def test_profit_lock_at_five_percent_peak(self):
        pos = {"exit_armed": False}
        # peak >= first TRAIL_PROFIT_TIERS threshold (5%) -> profit-locked
        assert ea.sell_state_code(pos, "phase2", False, 5.0) == "PROFIT_LOCKED"
        assert ea.sell_state_code(pos, "phase2", False, 4.9) == "PROVEN_FLOOR"

    def test_phase_mapping(self):
        pos = {"exit_armed": False}
        assert ea.sell_state_code(pos, "phase2", False, 3.0) == "PROVEN_FLOOR"
        assert ea.sell_state_code(pos, "phase2-unarmed", False, 1.0) == "PROVEN"
        assert ea.sell_state_code(pos, "phase1", False, 0.0) == "UNPROVEN"

    def test_disabled_returns_none(self):
        pos = {"exit_armed": False}
        assert ea.sell_state_code(pos, "disabled", False, 0.0) is None


# ── maybe_notify_sell_state() — latch + notify machine ────────────────────────
def _client_ok():
    c = MagicMock()
    c.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return c


def _call(pos, code, client=None):
    client = client or _client_ok()
    with patch.object(ea, "notifier") as notif:
        ea.maybe_notify_sell_state(
            client, pos, pos.get("ticker", "AAPL"), code,
            prove_it_level=99.0, unrealized_pct=1.5, peak_pct=3.0, days_held=4,
        )
    return notif


class TestMaybeNotifySellState:
    def test_first_observation_is_silent(self):
        pos = {"ticker": "AAPL"}                 # no prior sell_state
        notif = _call(pos, "UNPROVEN")
        notif.notify_sell_state_change.assert_not_called()
        assert pos["sell_state"] == "UNPROVEN"   # but latched

    def test_transition_fires_once(self):
        pos = {"ticker": "AAPL", "sell_state": "UNPROVEN"}
        notif = _call(pos, "PROVEN")
        notif.notify_sell_state_change.assert_called_once()
        args = notif.notify_sell_state_change.call_args
        assert args.args[0] == "AAPL"
        assert args.args[1] == "Unproven"        # from label
        assert args.args[2] == "Proven"          # to label
        assert args.args[3] == "PROVEN"          # code
        assert pos["sell_state"] == "PROVEN"

    def test_no_change_no_fire(self):
        pos = {"ticker": "AAPL", "sell_state": "PROVEN"}
        notif = _call(pos, "PROVEN")
        notif.notify_sell_state_change.assert_not_called()

    def test_none_code_noop(self):
        pos = {"ticker": "AAPL", "sell_state": "PROVEN"}
        notif = _call(pos, None)
        notif.notify_sell_state_change.assert_not_called()
        assert pos["sell_state"] == "PROVEN"     # untouched

    def test_transition_into_suppressed_is_latched_not_announced(self):
        # PROFIT_LOCKED -> EXITING: the Prove-It arm message already fired, so the
        # generic notice is suppressed, but the latch still advances.
        pos = {"ticker": "AAPL", "sell_state": "PROFIT_LOCKED"}
        notif = _call(pos, "EXITING")
        notif.notify_sell_state_change.assert_not_called()
        assert pos["sell_state"] == "EXITING"

    def test_transition_out_of_suppressed_fires(self):
        # POWER_HOLD -> PROFIT_LOCKED (power hold expired): a real, announced change.
        pos = {"ticker": "AAPL", "sell_state": "POWER_HOLD"}
        notif = _call(pos, "PROFIT_LOCKED")
        notif.notify_sell_state_change.assert_called_once()

    def test_missing_column_disables_without_spam(self):
        pos = {"ticker": "AAPL", "sell_state": "UNPROVEN"}
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.execute.side_effect = \
            Exception("PGRST204: column sell_state does not exist")
        notif = _call(pos, "PROVEN", client=client)
        notif.notify_sell_state_change.assert_not_called()
        # latch did NOT advance in memory either, so no false 'change already made'
        assert pos["sell_state"] == "UNPROVEN"
