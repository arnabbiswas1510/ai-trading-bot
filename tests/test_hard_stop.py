"""
tests/test_hard_stop.py

Tests for the static broker-side hard stop — the disconnect-proof max-loss floor
that rests in an OCA group with the base trailing stop (see hard_stop_price() and
place_protective_stops() in execution_agent.py).

The design intent these tests protect:

  * Pre-proven / unarmed  -> a flat disaster floor at entry*(1-MAX_LOSS_PCT).
    The 2026-09-07 --basetrail replay showed 7% is free in normal operation.
  * Proven AND armed      -> ratchets UP to the give-back floor, one backstop
    slack wider than the bot's own stop, so a proven green trade's floor is
    broker-GUARANTEED even while the bot is disconnected.
  * Static & entry-anchored, so it can never chase the HWM up and clip a winner
    — the property that let us tighten it where a 5% *trailing* base could not.
  * Power-hold is the single exception: the tight floor is suppressed back to the
    disaster level so the widened trail can let a leader run.
  * place_protective_stops places BOTH legs in one OCA group (ocaType=1) so a
    fill on either cancels the sibling — the same shares are never sold twice.

See decisions/2026-09-07_static-hard-stop.md.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import execution_agent as ea


ENTRY = 100.0


def _disaster(entry=ENTRY):
    return round(entry * (1.0 - ea.MAX_LOSS_PCT), 2)


def _armed_floor(entry=ENTRY):
    floor = entry * (1.0 + ea.PROVE_IT_P2_FLOOR_PCT)
    return round(floor * (1.0 - ea.PROVE_IT_BACKSTOP_SLACK_PCT), 2)


class TestHardStopPrice:
    def test_unproven_uses_disaster_floor(self):
        pos = {"closed_above_entry": False}
        assert ea.hard_stop_price(pos, ENTRY, 0.0) == _disaster()

    def test_proven_but_unarmed_uses_disaster_floor(self):
        # Closed above entry, but peak gain below the +2% arm gain: no tight
        # floor yet (it would sit inside noise), so the disaster floor governs.
        pos = {"closed_above_entry": True}
        peak = ea.PROVE_IT_P2_ARM_GAIN_PCT * 100.0 - 0.5   # e.g. +1.5%
        assert ea.hard_stop_price(pos, ENTRY, peak) == _disaster()

    def test_proven_and_armed_ratchets_to_giveback_floor(self):
        pos = {"closed_above_entry": True}
        peak = ea.PROVE_IT_P2_ARM_GAIN_PCT * 100.0 + 1.0   # e.g. +3.0%
        got = ea.hard_stop_price(pos, ENTRY, peak)
        assert got == _armed_floor()
        # And it must be a genuine ratchet UP from the disaster floor.
        assert got > _disaster()

    def test_power_hold_suppresses_tight_floor(self):
        # Even fully proven and armed, power-hold widens back to the disaster
        # floor so the leader is not clipped by the give-back floor.
        pos = {"closed_above_entry": True}
        peak = 30.0
        assert ea.hard_stop_price(pos, ENTRY, peak, power_held=True) == _disaster()

    def test_result_rounded_to_cents(self):
        pos = {"closed_above_entry": False}
        got = ea.hard_stop_price(pos, 37.37, 0.0)
        assert got == round(got, 2)

    def test_zero_buy_price_is_safe(self):
        assert ea.hard_stop_price({}, 0.0, 5.0) == 0.0


class TestPlaceProtectiveStops:
    def _mock_ib(self):
        ib = MagicMock()
        # placeOrder(contract, order) -> a Trade whose .order is the order passed,
        # so the function can read back trailingPercent from the trail leg.
        ib.placeOrder.side_effect = lambda contract, order: SimpleNamespace(order=order)
        ib.sleep.return_value = None
        return ib

    def test_places_two_oca_legs(self):
        ib = self._mock_ib()
        contract = SimpleNamespace(symbol="DHT")
        group, confirmed = ea.place_protective_stops(
            ib, contract, shares=100, trail_pct=0.0494,
            hard_price=98.01, account="U123")

        assert ib.placeOrder.call_count == 2
        orders = [c.args[1] for c in ib.placeOrder.call_args_list]
        trail = next(o for o in orders if getattr(o, "orderType", "") == "TRAIL")
        hard = next(o for o in orders if getattr(o, "orderType", "") == "STP")

        # Same OCA group, cancel-with-block, both GTC SELLs.
        assert trail.ocaGroup == hard.ocaGroup == group
        assert trail.ocaType == hard.ocaType == 1
        assert trail.action == hard.action == "SELL"
        assert trail.tif == hard.tif == "GTC"

        # Correct sizing / prices.
        assert hard.auxPrice == 98.01
        assert hard.totalQuantity == 100
        assert round(trail.trailingPercent, 2) == 4.94
        assert round(confirmed, 4) == 0.0494

    def test_hard_price_rounded_to_cents(self):
        ib = self._mock_ib()
        contract = SimpleNamespace(symbol="ABC")
        ea.place_protective_stops(ib, contract, shares=10, trail_pct=0.07,
                                  hard_price=12.345, account="U1")
        hard = next(c.args[1] for c in ib.placeOrder.call_args_list
                    if getattr(c.args[1], "orderType", "") == "STP")
        assert hard.auxPrice == 12.35
