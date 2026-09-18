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
    def test_unproven_uses_phase1_static_backstop(self):
        """Unproven -> the Phase 1 band, one backstop-slack wider, held STATIC.

        This leg used to be carried by the IBKR TRAIL order, whose anchor
        ratchets up with price; a stop written to cap a loss climbed into profit
        and sold SMTC at +0.76% 23 minutes after entry. It is now an entry-
        anchored STP that cannot chase the HWM.
        See decisions/2026-09-18_phase1-static-backstop.md.
        """
        pos = {"closed_above_entry": False}
        expected = round(ENTRY * (1 - ea.PROVE_IT_P1_LATER_PCT)
                               * (1 - ea.PROVE_IT_BACKSTOP_SLACK_PCT), 2)
        assert ea.hard_stop_price(pos, ENTRY, 0.0, False, 3) == expected
        # Day 0 uses the tighter band.
        expected_d0 = round(ENTRY * (1 - ea.PROVE_IT_P1_DAY0_PCT)
                                  * (1 - ea.PROVE_IT_BACKSTOP_SLACK_PCT), 2)
        assert ea.hard_stop_price(pos, ENTRY, 0.0, False, 0) == expected_d0
        # Never looser than the disaster floor.
        assert ea.hard_stop_price(pos, ENTRY, 0.0, False, 0) >= _disaster()

    def test_phase1_backstop_never_rises_above_entry(self):
        """THE REGRESSION THIS FIXES. Whatever the position does, the Phase 1
        floor stays below entry — it is a loss cap and must never become a
        profit-taker. The old trailing implementation reached entry +0.89% on
        SMTC. Swept across the whole plausible day range."""
        pos = {"closed_above_entry": False}
        for day in range(0, 30):
            level = ea.hard_stop_price(pos, ENTRY, 0.0, False, day)
            assert level < ENTRY, f"day {day}: floor {level} is at/above entry {ENTRY}"

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


class TestSafeHardStop:
    """
    A SELL stop resting at or above the market triggers immediately and sells at
    market. safe_hard_stop() is what stops a legitimate raise from becoming an
    accidental liquidation. See decisions/2026-09-18_phase1-static-backstop.md.
    """

    def test_normal_raise_is_allowed(self):
        assert ea.safe_hard_stop(96.0, 100.0, 93.0) == 96.0

    def test_level_at_or_above_market_keeps_the_resting_stop(self):
        # Desired is above the market: placing it would sell instantly.
        assert ea.safe_hard_stop(101.0, 100.0, 93.0) == 93.0
        # Exactly at the market is equally unsafe — a stop triggers on touch.
        assert ea.safe_hard_stop(100.0, 100.0, 93.0) == 93.0

    def test_missing_price_never_moves_the_stop(self):
        assert ea.safe_hard_stop(96.0, 0.0, 93.0) == 93.0

    def test_the_deployment_scenario_that_motivated_it(self):
        """THE REGRESSION. Moving the Phase 1 floor from the disaster level to
        the entry band raises it by ~3-5%. A position already trading in that
        gap must NOT have a stop placed above it."""
        entry, stored = 100.0, 93.0
        pos = {"closed_above_entry": False}
        desired = ea.hard_stop_price(pos, entry, 0.0, False, 3)   # ~96.03
        # Position has drifted to 95 — below the new floor, above the old one.
        assert desired > 95.0, "precondition: the new floor is above this price"
        assert ea.safe_hard_stop(desired, 95.0, stored) == stored
