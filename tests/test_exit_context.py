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
