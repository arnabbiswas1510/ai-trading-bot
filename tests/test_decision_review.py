"""Tests for the Provisional Decision Register's active half.

The register is the machinery that guarantees a deferred decision is never
silently forgotten, so its two gates are worth pinning:

  * is_due()             -- has the TRIGGER fired (trade count / date)?
  * check_preconditions() -- is it SAFE to act given LIVE bot state?

The failure mode these guard against is a due item that quietly disappears
because one of the two was conflated with the other.
"""
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import decision_review as dr  # noqa: E402

REGISTRY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "decisions", "provisional_decisions.json",
)
TODAY = dt.date(2026, 10, 20)


def _entry(**kw):
    base = {"id": "x", "status": "active", "revisit": {}}
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# is_due -- the trigger
# --------------------------------------------------------------------------

def test_inactive_entry_is_never_due():
    due, reasons = dr.is_due(_entry(status="resolved"), 999, TODAY)
    assert due is False
    assert "not 'active'" in reasons[0]


def test_both_thresholds_must_be_satisfied():
    e = _entry(revisit={"min_closed_trades": 50, "not_before": "2026-10-01"})
    assert dr.is_due(e, 50, TODAY)[0] is True
    # date met but trade count short
    assert dr.is_due(e, 49, TODAY)[0] is False
    # trade count met but date not reached
    assert dr.is_due(e, 50, dt.date(2026, 9, 1))[0] is False


def test_null_thresholds_do_not_gate():
    e = _entry(revisit={"min_closed_trades": None, "not_before": None})
    due, reasons = dr.is_due(e, 0, TODAY)
    assert due is True
    assert "always due" in reasons[0]


# --------------------------------------------------------------------------
# check_preconditions -- the safety gate
# --------------------------------------------------------------------------

def _state(n, youngest):
    return {"open_positions": n, "youngest_position_age_days": youngest, "tickers": []}


def test_absent_preconditions_are_always_actionable():
    """Every pre-existing parameter entry must be unaffected by this feature."""
    ok, reasons = dr.check_preconditions(_entry(), _state(5, 0))
    assert ok is True
    assert reasons == []


def test_open_position_cap_blocks():
    e = _entry(preconditions={"max_open_positions": 2})
    assert dr.check_preconditions(e, _state(5, 30))[0] is False
    assert dr.check_preconditions(e, _state(2, 30))[0] is True


def test_youngest_position_age_blocks():
    """A single day-0 position must block even when the others are old."""
    e = _entry(preconditions={"min_position_age_days": 7})
    assert dr.check_preconditions(e, _state(5, 0))[0] is False
    assert dr.check_preconditions(e, _state(5, 7))[0] is True


def test_flat_book_satisfies_the_age_precondition():
    """None means 'no positions', which must not read as age zero."""
    e = _entry(preconditions={"min_position_age_days": 7})
    ok, reasons = dr.check_preconditions(e, _state(0, None))
    assert ok is True
    assert "flat" in reasons[0]


def test_preconditions_are_conjunctive():
    e = _entry(preconditions={"max_open_positions": 2, "min_position_age_days": 7})
    assert dr.check_preconditions(e, _state(2, 3))[0] is False
    assert dr.check_preconditions(e, _state(3, 9))[0] is False
    assert dr.check_preconditions(e, _state(2, 9))[0] is True


def test_due_and_actionable_are_independent():
    """The whole point of the split: a due item can be blocked, and vice versa."""
    e = _entry(revisit={"not_before": "2026-10-01"},
               preconditions={"max_open_positions": 0})
    assert dr.is_due(e, 0, TODAY)[0] is True          # trigger fired
    assert dr.check_preconditions(e, _state(5, 30))[0] is False  # but not safe


# --------------------------------------------------------------------------
# the registry file itself
# --------------------------------------------------------------------------

def test_registry_is_valid_and_every_entry_parses():
    reg = json.load(open(REGISTRY))
    assert reg["decisions"], "registry must not be empty"
    for d in reg["decisions"]:
        assert d.get("id") and d.get("title")
        assert d.get("status") in {"active", "resolved", "superseded"}
        assert d.get("kind", "parameter") in {"parameter", "work-item", "investigation"}
        nb = (d.get("revisit") or {}).get("not_before")
        if nb:
            dt.date.fromisoformat(nb)  # raises on a malformed date
        # Must not crash on any real entry. Counts must be supplied for every
        # gate the registry actually uses -- is_due() raises rather than
        # silently skipping a gate it cannot evaluate, so omitting one here
        # would make this test fail loudly (which is the intended behaviour).
        dr.is_due(d, 45, TODAY, {"matured_triggers": 50})
        dr.check_preconditions(d, _state(5, 0))


def test_registry_ids_are_unique():
    reg = json.load(open(REGISTRY))
    ids = [d["id"] for d in reg["decisions"]]
    assert len(ids) == len(set(ids))


def test_orchestrator_split_is_registered_and_blocked_by_a_live_book():
    """The deferral recorded on 2026-09-18 must be machine-tracked, not prose."""
    reg = json.load(open(REGISTRY))
    entry = next(d for d in reg["decisions"] if d["id"] == "orchestrator-split")
    assert entry["kind"] == "work-item"
    assert entry["status"] == "active"
    # Five fresh day-0 positions -- exactly the state it was deferred in.
    assert dr.check_preconditions(entry, _state(5, 0))[0] is False


@pytest.mark.parametrize("age,n,expected", [(0, 5, False), (9, 1, True), (None, 0, True)])
def test_orchestrator_split_unblocks_when_the_book_quietens(age, n, expected):
    reg = json.load(open(REGISTRY))
    entry = next(d for d in reg["decisions"] if d["id"] == "orchestrator-split")
    assert dr.check_preconditions(entry, _state(n, age))[0] is expected


# --------------------------------------------------------------------------
# the matured-trigger gate (added 2026-09-18 for `entry-quality-right-tail`)
# --------------------------------------------------------------------------

def test_matured_trigger_gate_blocks_and_releases():
    e = _entry(revisit={"min_matured_triggers": 150})
    assert dr.is_due(e, 999, TODAY, {"matured_triggers": 149})[0] is False
    assert dr.is_due(e, 999, TODAY, {"matured_triggers": 150})[0] is True


def test_matured_trigger_gate_raises_when_it_cannot_be_evaluated():
    """A gate that cannot be checked must never be silently treated as passed."""
    e = _entry(revisit={"min_matured_triggers": 150})
    with pytest.raises(ValueError, match="min_matured_triggers"):
        dr.is_due(e, 999, TODAY)


def test_entry_quality_investigation_is_registered_and_not_yet_due():
    """The right-tail question must be machine-tracked, not left in prose."""
    reg = json.load(open(REGISTRY))
    entry = next(d for d in reg["decisions"] if d["id"] == "entry-quality-right-tail")
    assert entry["status"] == "active"
    assert entry["kind"] == "investigation"
    # 50 matured rows on 2026-09-18 -- nowhere near the 150-row gate.
    assert dr.is_due(entry, 999, TODAY, {"matured_triggers": 50})[0] is False
