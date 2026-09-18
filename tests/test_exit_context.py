"""
Tests for the exit-context recorded against a reconciled broker exit.

When an IBKR GTC trailing-stop order fires, the agent is not involved — it only
discovers the fill at the next reconcile. Historically the only thing written to
`trade_history` was the bare label "Trailing stop (IBKR GTC TRAIL order)", which
says nothing about what trail was in force, what peak it was anchored to, or how
far the position had run before it turned. All of that lives on the
`portfolio_positions` row that reconcile deletes seconds later, so the numbers
were being thrown away irrecoverably.

`_exit_context_suffix()` captures them into the reason string. These tests pin
the output format, because the dashboard parses it back out
(`frontend/src/lib/exitDetails.js`) and a silent format drift would quietly
degrade the exit-detail panel back to "not recorded" without failing anything.
"""
import ast
import re

import pytest


def _load_exit_context_suffix():
    """
    Import the helper without importing execution_agent itself.

    execution_agent.py connects to IBKR and reads a large amount of environment
    at import time; this function is pure, so it is extracted and exec'd on its
    own rather than dragging in that machinery.
    """
    src = open("execution_agent.py").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_exit_context_suffix":
            namespace: dict = {}
            exec(compile(ast.Module([node], []), "<extracted>", "exec"), namespace)
            return namespace["_exit_context_suffix"]
    raise AssertionError("_exit_context_suffix not found in execution_agent.py")


exit_context_suffix = _load_exit_context_suffix()


FULL_POSITION = {
    "hwm_price": 52.025,
    "stop_loss_pct": 0.10,
    "hwm_date": "2026-08-21",
    "days_held": 2,
    "highest_unrealized_pct": 4.3003,
    "exit_armed": False,
    "power_hold": False,
}


class TestExitContextSuffix:
    def test_records_the_trail_actually_in_force(self):
        out = exit_context_suffix(FULL_POSITION, 49.0)
        assert "trail 10.00%" in out

    def test_records_the_peak_the_trail_was_anchored_to(self):
        out = exit_context_suffix(FULL_POSITION, 49.0)
        assert "HWM $52.02" in out
        assert "set 2026-08-21" in out

    def test_implied_trigger_is_derived_from_the_peak_and_the_trail(self):
        # 52.025 * (1 - 0.10) = 46.8225 -> 46.82
        out = exit_context_suffix(FULL_POSITION, 49.0)
        assert "implied trigger $46.82" in out

    def test_no_implied_trigger_without_a_trail_to_derive_it_from(self):
        pos = dict(FULL_POSITION, stop_loss_pct=None)
        out = exit_context_suffix(pos, 49.0)
        assert "implied trigger" not in out
        assert "HWM $52.02" in out

    def test_prove_it_floor_pin_does_not_fabricate_a_trigger_above_the_exit(self):
        # Regression for the FIVE (2026-09-09) display bug. The Prove-It floor
        # lever stores a trail measured from the re-anchor price, not the HWM, so
        # hwm*(1-trail) lands ABOVE where the stop actually sat. A trailing stop
        # can never trigger above its own fill, so the fabricated figure must be
        # suppressed and the real re-anchored floor reported instead.
        pos = dict(FULL_POSITION, hwm_price=256.09, stop_loss_pct=0.0018)
        out = exit_context_suffix(pos, 248.85)
        assert "implied trigger" not in out           # would have said $255.63
        assert "floor re-anchored near $248.85" in out
        assert "trail not HWM-relative" in out

    def test_hwm_anchored_trigger_at_or_below_exit_is_still_shown(self):
        # The guard must only suppress physically impossible triggers. A normal
        # HWM-anchored trail whose implied trigger sits at/below the fill is real
        # and must survive.
        pos = dict(FULL_POSITION, hwm_price=52.025, stop_loss_pct=0.10)
        out = exit_context_suffix(pos, 49.0)   # implied 46.82 < 49.0 → valid
        assert "implied trigger $46.82" in out

    def test_records_hold_day_and_peak_excursion(self):
        out = exit_context_suffix(FULL_POSITION, 49.0)
        assert "day 2 of hold" in out
        assert "peak +4.30%" in out

    def test_peak_excursion_keeps_its_sign_when_negative(self):
        pos = dict(FULL_POSITION, highest_unrealized_pct=-1.5)
        assert "peak -1.50%" in exit_context_suffix(pos, 49.0)

    def test_armed_state_is_recorded_with_price_and_reason(self):
        pos = dict(
            FULL_POSITION,
            exit_armed=True,
            exit_armed_price=51.7,
            exit_armed_reason="profit lock +5%",
        )
        out = exit_context_suffix(pos, 49.0)
        assert "armed at $51.70 (profit lock +5%)" in out

    def test_power_hold_is_recorded(self):
        pos = dict(FULL_POSITION, power_hold=True)
        assert "power hold active" in exit_context_suffix(pos, 49.0)

    def test_unarmed_position_says_nothing_about_arming(self):
        assert "armed" not in exit_context_suffix(FULL_POSITION, 49.0)

    # ── Degradation: a sparse or malformed row must not corrupt the reason ────

    def test_empty_position_yields_no_suffix_at_all(self):
        # Must be empty, not " — ", so the label is never left with a dangling
        # separator when there is nothing to say.
        assert exit_context_suffix({}, 49.0) == ""

    def test_partial_position_records_only_what_it_has(self):
        assert exit_context_suffix({"days_held": 3}, 49.0) == " — day 3 of hold"

    def test_unparseable_numbers_are_skipped_rather_than_raising(self):
        pos = {"hwm_price": "nope", "stop_loss_pct": None, "days_held": 1}
        out = exit_context_suffix(pos, 49.0)
        assert out == " — day 1 of hold"

    def test_zero_hwm_is_treated_as_absent(self):
        # A zero high-water mark means it was never recorded, not that the stock
        # peaked at $0; reporting "HWM $0.00" would be worse than silence.
        pos = dict(FULL_POSITION, hwm_price=0)
        assert "HWM" not in exit_context_suffix(pos, 49.0)

    # ── Format contract with the dashboard parser ────────────────────────────

    def test_suffix_is_appended_with_an_em_dash_separator(self):
        out = exit_context_suffix(FULL_POSITION, 49.0)
        assert out.startswith(" — ")

    def test_every_field_the_dashboard_parses_is_present_and_matches(self):
        """
        Mirrors the regexes in frontend/src/lib/exitDetails.js. If the agent's
        format changes without the parser following, this fails here first.
        """
        pos = dict(
            FULL_POSITION,
            exit_armed=True,
            exit_armed_price=51.7,
            exit_armed_reason="profit lock +5%",
            power_hold=True,
        )
        reason = "Trailing stop (IBKR GTC TRAIL order)" + exit_context_suffix(pos, 49.0)

        expectations = {
            r"trail\s+([\d.]+)%": "10.00",
            r"HWM\s+\$([\d,]+(?:\.\d+)?)": "52.02",
            r"HWM\s+\$[\d,.]+\s+set\s+([\d-]+)": "2026-08-21",
            r"implied trigger\s+\$([\d,]+(?:\.\d+)?)": "46.82",
            r"day\s+(\d+)\s+of hold": "2",
            r"peak\s+([+-][\d.]+)%": "+4.30",
            r"armed at\s+\$([\d,]+(?:\.\d+)?)": "51.70",
        }
        for pattern, expected in expectations.items():
            match = re.search(pattern, reason, re.IGNORECASE)
            assert match, f"dashboard pattern {pattern!r} no longer matches: {reason!r}"
            assert match.group(1) == expected, (
                f"{pattern!r} captured {match.group(1)!r}, expected {expected!r}"
            )
        assert re.search(r"power hold active", reason, re.IGNORECASE)

    def test_context_does_not_disturb_the_leading_label(self):
        # classifyExit() in the dashboard matches on the label text; appending
        # context must not break that attribution.
        reason = "Trailing stop (IBKR GTC TRAIL order)" + exit_context_suffix(FULL_POSITION, 49.0)
        assert reason.startswith("Trailing stop (IBKR GTC TRAIL order)")

    def test_price_uncertain_marker_survives_the_appended_context(self):
        reason = (
            "Manual close in IBKR (reconciled) — PRICE_UNCERTAIN"
            + exit_context_suffix(FULL_POSITION, 49.0)
        )
        assert "PRICE_UNCERTAIN" in reason


class TestReconcileUsesTheHelper:
    """The helper is worthless if the reconcile path stops calling it."""

    def test_both_reconcile_branches_record_context(self):
        src = open("execution_agent.py").read()
        trail_branch = re.search(
            r'sell_reason\s*=\s*"Trailing stop \(IBKR GTC TRAIL order\)"([^\n]*)', src
        )
        manual_branch = re.search(
            r'sell_reason\s*=\s*"Manual close in IBKR \(reconciled\) — PRICE_UNCERTAIN"([^\n]*)',
            src,
        )
        assert trail_branch, "the trailing-stop reconcile branch has moved"
        assert manual_branch, "the manual-close reconcile branch has moved"
        assert "_exit_context_suffix" in trail_branch.group(1), (
            "trailing-stop exits are being written without their risk context again"
        )
        assert "_exit_context_suffix" in manual_branch.group(1), (
            "manual-close exits are being written without their risk context again"
        )


# The four positions closed on 2026-09-18 by a Phase 1 stop that had ratcheted
# above entry. Every one of them logged an "implied trigger" BELOW the entry
# price — a string that reads exactly like a loss cap doing its job — while the
# order had in fact fired at or above breakeven. Diagnosing it required
# re-fetching 5-minute bars, which defeats the purpose of shipping logs at all.
#
# (ticker, entry, stored HWM, trail, stored peak %, actual fill)
RATCHET_EXITS = [
    ("SMTC", 180.50, 180.99, 0.0172, 0.27, 181.877),
    ("TEN",   52.25,  53.18, 0.0188, 1.78,  52.53),
    ("DHT",   23.04,  23.28, 0.0195, 1.04,  23.055),
    ("TWLO", 241.38, 243.80, 0.0125, 1.00, 243.23),
]


def _ratchet_pos(entry, hwm, trail, peak):
    return {
        "buy_price": entry, "hwm_price": hwm, "stop_loss_pct": trail,
        "hwm_date": "2026-09-18", "days_held": 0,
        "highest_unrealized_pct": peak, "exit_armed": False, "power_hold": False,
    }


class TestStaleHighWaterMarkIsNotPublishedAsFact:
    """The stored HWM is refreshed on a 15-minute cycle; a resting order is not.

    A position that runs up and turns over between two cycles is closed against
    a peak the agent never saw, so every figure derived from the stored HWM
    understates what happened. The fill is the one number known exactly, and for
    a trailing order it IS the trigger — so `fill / (1 - trail)` recovers the
    anchor the broker was really using.
    """

    @pytest.mark.parametrize("ticker,entry,hwm,trail,peak,fill", RATCHET_EXITS)
    def test_stale_hwm_is_labelled_not_silently_used(
            self, ticker, entry, hwm, trail, peak, fill):
        out = exit_context_suffix(
            _ratchet_pos(entry, hwm, trail, peak), fill, broker_trail_fill=True)
        assert "stored HWM STALE" in out, ticker
        # The figure that concealed the defect must not be published unqualified.
        assert "implied trigger" not in out, ticker
        assert f"actual trigger ${fill:.2f}" in out, ticker

    @pytest.mark.parametrize("ticker,entry,hwm,trail,peak,fill", RATCHET_EXITS)
    def test_stop_position_relative_to_entry_is_always_recorded(
            self, ticker, entry, hwm, trail, peak, fill):
        # The single most diagnostic number on a stopped-out trade: a level at or
        # above entry means whatever fired was taking profit, whichever rule
        # believed it was capping a loss. All four of these were above entry.
        out = exit_context_suffix(
            _ratchet_pos(entry, hwm, trail, peak), fill, broker_trail_fill=True)
        expected = (fill / entry - 1) * 100
        assert f"stop sat at entry {expected:+.2f}%" in out, ticker
        assert expected > 0, f"{ticker} fixture should be an above-entry exit"

    @pytest.mark.parametrize("ticker,entry,hwm,trail,peak,fill", RATCHET_EXITS)
    def test_understated_peak_is_corrected_from_the_fill(
            self, ticker, entry, hwm, trail, peak, fill):
        out = exit_context_suffix(
            _ratchet_pos(entry, hwm, trail, peak), fill, broker_trail_fill=True)
        assert f"peak {peak:+.2f}% recorded but >=" in out, ticker

    def test_reconstructed_peak_matches_the_real_intraday_high(self):
        # SMTC's true high was $185.30; the agent recorded $180.99. The
        # reconstruction must land near the truth, not near the stored value.
        out = exit_context_suffix(
            _ratchet_pos(180.50, 180.99, 0.0172, 0.27), 181.877,
            broker_trail_fill=True)
        assert "fill implies peak $185.06" in out

    def test_manual_close_never_infers_an_anchor_from_an_unrelated_fill(self):
        # A manual close fills at a price with no relationship to the trail.
        # Reconstructing an anchor from it would manufacture a stale-HWM claim
        # out of an unrelated number, so the inference is gated on the caller
        # proving the fill came from the trailing order itself.
        pos = _ratchet_pos(180.50, 180.99, 0.0172, 0.27)
        out = exit_context_suffix(pos, 181.877)          # no broker_trail_fill
        assert "stored HWM STALE" not in out
        assert "implied trigger $177.88" in out

    def test_a_fresh_hwm_still_reports_a_plain_implied_trigger(self):
        # The stale branch must fire only when the fill genuinely disagrees with
        # the stored peak. A position whose HWM was captured accurately must be
        # unaffected, or every normal exit gains a false staleness warning.
        pos = _ratchet_pos(100.0, 110.0, 0.05, 10.0)
        out = exit_context_suffix(pos, 104.50, broker_trail_fill=True)
        assert "stored HWM STALE" not in out
        assert "implied trigger $104.50" in out

    def test_floor_pin_still_wins_over_the_stale_branch(self):
        # FIVE (2026-09-09): a floor-pinned stop is not HWM-anchored at all, so
        # neither the old implied trigger nor the new reconstruction applies.
        pos = _ratchet_pos(250.0, 256.09, 0.0018, 4.3)
        out = exit_context_suffix(pos, 248.85, broker_trail_fill=True)
        assert "floor re-anchored near $248.85" in out
        assert "stored HWM STALE" not in out
