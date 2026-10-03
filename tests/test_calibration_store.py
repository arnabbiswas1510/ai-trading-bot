"""Durable inbox never substitutes volatile memory or partial writes for database commits."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from research.calibration_store import (
    CalibrationStore, Conflict, StoreUnavailable, ValidationError, artifact_digest,
)


@pytest.fixture
def store():
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = {"id": "p", "revision": 1}
    return CalibrationStore(client)


def row():
    return {"id": "p", "title": "Numeric research", "kind": "parameter",
            "status": "evaluating", "artifact": {"frozen": {"value": 1}}, "request": {}}


def test_create_recomputes_artifact_hash_and_uses_atomic_rpc(store):
    store.create_proposal(row())
    name, payload = store.client.rpc.call_args.args
    assert name == "intraday_calibration_mutate"
    assert payload["p_operation"] == "create"
    assert payload["p_payload"]["row"]["artifact_sha256"] == artifact_digest(row()["artifact"])
    store.client.table.assert_not_called()


def test_forged_artifact_hash_is_rejected_before_write(store):
    with pytest.raises(ValidationError):
        store.create_proposal({**row(), "artifact_sha256": "a" * 64})
    store.client.rpc.assert_not_called()


@pytest.mark.parametrize("revision", [True, False, -1, 1.0, "1"])
def test_revision_is_strict_integer(store, revision):
    with pytest.raises(ValidationError):
        store.update_proposal("p", revision, {}, "comment")


def test_update_and_audit_are_a_single_rpc(store):
    store.update_proposal("p", 1, {"status": "ready"}, "evaluated", data={"run": 2})
    assert store.client.rpc.call_count == 1
    payload = store.client.rpc.call_args.args[1]["p_payload"]
    assert payload["expected_revision"] == 1
    assert payload["event_id"]
    assert payload["patch"] == {"status": "ready"}
    assert payload["data"] == {"run": 2}


def test_linked_child_is_in_same_transaction(store):
    store.update_proposal("p", 1, {}, "request_experiment", child={**row(), "id": "child",
                                                                "parent_id": "p"})
    assert store.client.rpc.call_count == 1
    assert store.client.rpc.call_args.args[1]["p_payload"]["child"]["parent_id"] == "p"


@pytest.mark.parametrize("patch", [{"eligible": True}, {"actor": "alice"},
                                 {"artifact_sha256": "a" * 64}])
def test_unknown_or_unbound_patch_is_rejected(store, patch):
    with pytest.raises(ValidationError):
        store.update_proposal("p", 1, patch, "updated")


def test_database_failure_never_reports_success(store):
    store.client.rpc.return_value.execute.side_effect = RuntimeError("private database URL")
    with pytest.raises(StoreUnavailable, match="storage is unavailable") as error:
        store.create_proposal(row())
    assert "private" not in str(error.value)


def test_missing_write_acknowledgement_is_not_success(store):
    store.client.rpc.return_value.execute.return_value.data = None
    with pytest.raises(StoreUnavailable):
        store.add_event(None, "comment")


def test_event_contract_and_deterministic_id(store):
    store.add_event(None, "worker_health", note="Weekly research health",
                    data={"status": "waiting"}, event_id="health:2026-W40")
    payload = store.client.rpc.call_args.args[1]["p_payload"]
    assert payload["id"] == "health:2026-W40"
    assert payload["event"] == "worker_health"
    assert payload["proposal_id"] is None


def test_worker_can_request_bounded_two_hundred_proposals(store):
    store.list_proposals(limit=200)
    store.client.table.return_value.select.return_value.order.return_value.limit.assert_called_once_with(200)


def test_research_queue_reads_all_active_states_in_oldest_first_order(store):
    query = store.client.table.return_value.select.return_value
    final = query.in_.return_value.order.return_value.order.return_value.limit.return_value
    final.execute.return_value.data = [{"id": "old-campaign", "status": "deferred"}]
    assert store.research_queue() == [{"id": "old-campaign", "status": "deferred"}]
    query.in_.assert_called_once_with("status", ["evaluating", "deferred", "investigation_approved"])
    query.in_.return_value.order.assert_called_once_with("created_at")
    query.in_.return_value.order.return_value.order.assert_called_once_with("id")
    query.in_.return_value.order.return_value.order.return_value.limit.assert_called_once_with(201)


def test_research_queue_overflow_fails_instead_of_hiding_campaigns(store):
    query = store.client.table.return_value.select.return_value
    final = query.in_.return_value.order.return_value.order.return_value.limit.return_value
    final.execute.return_value.data = [{"id": str(index)} for index in range(201)]
    with pytest.raises(StoreUnavailable, match="exceeds 200"):
        store.research_queue()


@pytest.mark.parametrize("rows,exists", [([], False), ([{"id": "exact-week"}], True)])
def test_has_proposal_checks_exact_id_without_paginated_inbox(store, rows, exists):
    query = store.client.table.return_value.select.return_value
    query.eq.return_value.limit.return_value.execute.return_value.data = rows
    assert store.has_proposal("exact-week") is exists
    store.client.table.return_value.select.assert_called_once_with("id")
    query.eq.assert_called_once_with("id", "exact-week")
    query.eq.return_value.limit.assert_called_once_with(1)


@pytest.mark.parametrize("code,expected", [("P4090", Conflict), ("P4000", ValidationError)])
def test_database_transaction_errors_are_mapped(store, code, expected):
    error = RuntimeError("private")
    error.code = code
    store.client.rpc.return_value.execute.side_effect = error
    with pytest.raises(expected):
        store.create_proposal(row())


def test_missing_settings_fail_closed(store):
    store.client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with pytest.raises(StoreUnavailable):
        store.settings()


@pytest.mark.parametrize("seconds", [True, 0, 3601, "600"])
def test_lease_is_bounded(store, seconds):
    with pytest.raises(ValidationError):
        store.claim("worker", seconds)


def test_claim_conflict_returns_false(store):
    store.client.rpc.return_value.execute.return_value.data = False
    assert store.claim("worker") is False


def test_migration_private_transactional_and_append_only():
    sql = (Path(__file__).parents[1] / "migrations/20261003_add_calibration_loop.sql").read_text()
    assert "SECURITY INVOKER" in sql
    assert "FOR UPDATE" in sql and "FOR SHARE" in sql
    assert "Idempotency conflict" in sql
    assert "initial_content IS DISTINCT FROM row_data" in sql
    assert "Frozen evidence is immutable" in sql
    assert "Approval revoked or policy changed" in sql
    assert "proposal.artifact->'frozen'->'settings'->'risk_policy' IS DISTINCT FROM settings.value->'risk_policy'" in sql
    assert "Investigation approval required" in sql
    assert "Invalid atomic investigation enrollment" in sql
    assert "source_status <> 'investigation_approved' OR target <> 'no_change'" in sql
    assert "p_payload->'child'->'request' IS DISTINCT FROM '{}'::jsonb" in sql
    assert "GRANT UPDATE (notified_at)" in sql
    assert "FROM PUBLIC, anon, authenticated" in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT UPDATE ON public.intraday_calibration_events" not in sql
