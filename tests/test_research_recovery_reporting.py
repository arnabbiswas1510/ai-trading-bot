"""Recovery notifications remain durable even when a run heals between sweeps."""
import copy

import pytest

from research import intraday_reporting as reporting
from test_intraday_reporting import (
    FakeIssues, FakeTelegram, MemoryStore as BaseMemoryStore, at, healthy, offline_calendar,
)


class MemoryStore(BaseMemoryStore):
    def select(self, table, params=None):
        params = dict(params or {})
        mode = params.pop("initial_state->source_evidence->recovery->>mode", None)
        rows = super().select(table, params)
        if mode is not None:
            assert table == "intraday_shadow_runs" and mode == "eq.automatic"
            rows = [row for row in rows if row.get("initial_state", {}).get(
                "source_evidence", {}).get("recovery", {}).get("mode") == "automatic"]
        return rows


def replacement():
    return {
        "id": "a" * 64, "created_at": "2026-10-06T13:35:20+00:00",
        "status": "running", "latest_sequence": 1,
        "initial_state": {"source_evidence": {"recovery": {
            "mode": "automatic", "previous_run_id": "b" * 64,
            "requested_at": "2026-10-06T13:32:01+00:00",
            "reason_code": "observation_gap",
        }}},
    }


def test_blocked_shadow_is_actionable_overnight():
    now = at("2026-10-05T21:00")
    data = healthy(now)
    data["shadow"]["status"] = "blocked"
    faults = reporting.health_failures(now, **data)
    assert set(faults) == {"shadow-progress"}
    assert "replacement status" in faults["shadow-progress"]
    store, telegram, issues = MemoryStore(), FakeTelegram(), FakeIssues()
    reporting.monitor(store, telegram, issues, now, data)
    assert "shadow-progress" in issues.rows
    assert len(telegram.sent) == 2
    assert all("blocked" in message for _, message in telegram.sent)


@pytest.mark.parametrize("clock,expected", [
    ("2026-10-06T08:59", False), ("2026-10-06T09:00", True),
    ("2026-10-06T09:29", True), ("2026-10-06T09:35", True),
    ("2026-10-10T09:15", False),
])
def test_preopen_heartbeats_do_not_require_premarket_quotes(clock, expected):
    now = at(clock)
    faults = reporting.health_failures(now, None, None, None, None)
    assert ("observer-heartbeat" in faults) is expected
    assert ("shadow-heartbeat" in faults) is expected
    assert "quote-coverage" not in faults
    assert "broker-snapshot" not in faults
    ready = healthy(now)
    ready.update(snapshot=None, quotes=None, shadow_output=None, shadow_decision=None)
    assert reporting.health_failures(now, **ready) == {}


def test_preopen_spool_failure_is_not_hidden_by_fresh_heartbeat():
    now = at("2026-10-06T09:15")
    data = healthy(now)
    data["observer"]["config"]["spool_available"] = False
    assert "observer-spool" in reporting.health_failures(now, **data)


def test_replacement_notification_retries_only_unacknowledged_recipient():
    row = replacement()
    store = MemoryStore({"intraday_shadow_runs": {row["id"]: row}})
    telegram = FakeTelegram()
    telegram.fail.add("two")
    errors = []
    now = at("2026-10-06T10:00")
    reporting.notify_shadow_replacements(store, telegram, now, errors)
    assert errors and len(telegram.sent) == 1
    telegram.fail.clear()
    for _ in range(2):
        errors = []
        reporting.notify_shadow_replacements(store, telegram, now, errors)
        assert errors == []
    assert [recipient for recipient, _ in telegram.sent] == ["one", "two"]
    assert all("separate experiment" in body and "Real trading is unchanged" in body
               for _, body in telegram.sent)
    assert len(store.rows[reporting.RECEIPTS]) == 2
    assert all(table == reporting.RECEIPTS for table, _ in store.writes)


def test_replacement_that_already_failed_is_not_announced_as_healthy():
    row = replacement()
    row["status"] = "superseded"
    store = MemoryStore({"intraday_shadow_runs": {row["id"]: row}})
    telegram = FakeTelegram()
    reporting.notify_shadow_replacements(store, telegram, at("2026-10-06T10:00"), [])
    assert len(telegram.sent) == 2
    assert all("status: superseded" in body for _, body in telegram.sent)
    assert all("proof of a complete training day" in body for _, body in telegram.sent)


def test_replacement_marker_is_required_and_malformed_marker_fails_loudly():
    row = replacement()
    bad = copy.deepcopy(row)
    bad["initial_state"]["source_evidence"]["recovery"]["previous_run_id"] = "not-a-run"
    telegram = FakeTelegram()
    store = MemoryStore({"intraday_shadow_runs": {row["id"]: bad}})
    with pytest.raises(reporting.ReportingError, match="invalid provenance"):
        reporting.notify_shadow_replacements(store, telegram, at("2026-10-06T10:00"), [])
    assert telegram.sent == []
    row["initial_state"]["source_evidence"].pop("recovery")
    store.rows["intraday_shadow_runs"] = {row["id"]: row}
    reporting.notify_shadow_replacements(store, telegram, at("2026-10-06T10:00"), [])
    assert telegram.sent == []


def test_regular_sweep_notifies_replacement_even_without_open_incident(monkeypatch):
    now = at("2026-10-06T10:00")
    row = replacement()
    store = MemoryStore({
        reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-10-06"}},
        "intraday_shadow_runs": {row["id"]: row},
    })
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    reporting.run(store, telegram, issues, now)
    reporting.run(store, telegram, issues, now)
    assert len(telegram.sent) == 2
    assert issues.created == 0
    assert all("AUTOMATIC SIMULATION REPLACEMENT" in body for _, body in telegram.sent)
