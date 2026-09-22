"""
test_exit_path_golden.py — locks the money-path call sequence of
monitor_portfolio_intraday against a committed snapshot.

This is the safety net for the Stage-2 orchestrator split (see
tests/golden_log.py for the rationale). It scripts a fixed, deterministic book
that exercises the distinct exit regimes side by side:

    LOSER_P1   : an unproven position sitting below its Phase-1 backstop
    WINNER     : a green position in the profit-ladder zone
    ARMED      : a position whose exit is already armed
    PROVEN     : a proven position resting on its Phase-2 floor

and records the exact ordered sequence of execute_sell / arm_exit /
execute_scale_out / place_protective_stops / cancel_sells / notify calls.

The recorded sequence is compared to `golden/exit_path_monitor.json`. Any change
to that sequence — a dropped sell, a reordered stop placement, a lost
notification — fails the test. Regenerate intentionally with:

    GOLDEN_UPDATE=1 python3 -m pytest tests/test_exit_path_golden.py

and review the diff in the committed JSON as part of the change. A refactor that
preserves behaviour produces NO diff; a diff is either a real behaviour change
(document it) or a bug (fix it).
"""
import json
import os
from pathlib import Path

import pytest

from tests.conftest import make_position
from tests.golden_log import record_monitor

_GOLDEN = Path(__file__).parent / "golden" / "exit_path_monitor.json"


def _scripted_book():
    """A fixed multi-regime book. Prices are chosen relative to a $100 entry so
    the regimes are unambiguous and independent of live data."""
    positions = [
        # Unproven, day 2, never went green, now well below entry -> Phase 1 fires.
        make_position("LOSERAA", buy_price=100.0, days_ago=2, shares=100,
                      highest_unrealized_pct=0.0),
        # Green winner, peaked well into the profit ladder, still above floor.
        make_position("WINNERB", buy_price=100.0, days_ago=10, shares=100,
                      highest_unrealized_pct=9.0),
        # Proven position resting near its Phase-2 floor.
        make_position("PROVENC", buy_price=100.0, days_ago=15, shares=100,
                      highest_unrealized_pct=6.0),
    ]
    live = {
        "LOSERAA": 96.0,    # ~-4% : below the day-2 Phase-1 band + slack
        "WINNERB": 108.0,   # +8%  : ladder zone, no exit
        "PROVENC": 104.0,   # +4%  : proven, above floor
    }
    return positions, live


def _record_rows():
    positions, live = _scripted_book()
    with record_monitor(positions, live) as rec:
        return rec.rows()


def test_monitor_exit_path_matches_golden():
    rows = _record_rows()

    if os.environ.get("GOLDEN_UPDATE") == "1":
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(json.dumps(rows, indent=2) + "\n")
        pytest.skip(f"golden regenerated at {_GOLDEN}")

    assert _GOLDEN.exists(), (
        f"golden snapshot missing at {_GOLDEN}. Generate it once with "
        f"GOLDEN_UPDATE=1 python3 -m pytest {__file__}"
    )
    expected = json.loads(_GOLDEN.read_text())

    assert rows == expected, (
        "monitor_portfolio_intraday money-path call sequence changed.\n"
        "If this change is intentional (a real behaviour change), regenerate the "
        "golden with GOLDEN_UPDATE=1 and review the JSON diff in the commit.\n"
        f"expected={json.dumps(expected, indent=2)}\n"
        f"actual  ={json.dumps(rows, indent=2)}"
    )


def test_golden_scenario_is_non_vacuous():
    """Guard against a golden that locks an EMPTY sequence — which would pass
    forever while asserting nothing. The scripted book must emit at least one
    money-path action (the loser's Phase-1 exit)."""
    rows = _record_rows()
    assert rows, "scripted book produced no money-path events — the recorder or "\
        "scenario is broken, and an empty golden would be a false safety net."
    kinds = {r["kind"] for r in rows}
    assert kinds & {"execute_sell", "arm_exit"}, (
        f"expected the loser to trigger a sell/arm; got kinds={kinds}"
    )


def test_recorder_is_deterministic():
    """Two runs of the same scripted book must produce an identical sequence, or
    the golden would be flaky and useless as a refactor gate."""
    assert _record_rows() == _record_rows()
