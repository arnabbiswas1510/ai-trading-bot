"""Diagnostic retries have a separate, narrow transaction; frozen writes stay blocked."""
import copy
from pathlib import Path
import re
from unittest.mock import MagicMock

import pytest

from research.calibration_store import (
    CalibrationStore, Conflict, StoreUnavailable, ValidationError, artifact_digest,
)


@pytest.fixture
def store():
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = {"id": "p", "revision": 8}
    return CalibrationStore(client)


def test_refresh_binds_old_and_replacement_hashes_without_touching_original(store):
    artifact = {
        "frozen": {"selection": "unchanged"}, "evaluation": {"eligible": True},
        "risk_analytics": {"training": {"status": "partial"}, "retry_pending": True},
    }
    original = copy.deepcopy(artifact)
    risk = {"training": {"status": "available"}, "retry_pending": False}
    result = store.refresh_risk_analytics("p", 7, artifact, risk)
    assert result == {"id": "p", "revision": 8}
    name, arguments = store.client.rpc.call_args.args
    assert name == "intraday_calibration_refresh_risk"
    assert set(arguments) == {"p_payload"}
    payload = arguments["p_payload"]
    assert payload == {
        "id": "p", "expected_revision": 7,
        "expected_artifact_sha256": artifact_digest(original),
        "risk_analytics": risk,
        "artifact_sha256": artifact_digest({**original, "risk_analytics": risk}),
        "event_id": payload["event_id"],
    }
    assert re.fullmatch("[0-9a-f]{32}", payload["event_id"])
    assert artifact == original
    assert payload["artifact_sha256"] != payload["expected_artifact_sha256"]
    store.client.rpc.assert_called_once()
    store.client.table.assert_not_called()


@pytest.mark.parametrize("revision", [True, False, -1, 1.0, "1", None])
def test_refresh_rejects_invalid_revisions_before_rpc(store, revision):
    with pytest.raises(ValidationError):
        store.refresh_risk_analytics("p", revision, {}, {})
    store.client.rpc.assert_not_called()


@pytest.mark.parametrize("identifier", ["", None, 1, "p" * 201])
def test_refresh_rejects_invalid_identifiers_before_rpc(store, identifier):
    with pytest.raises(ValidationError):
        store.refresh_risk_analytics(identifier, 0, {}, {})
    store.client.rpc.assert_not_called()


@pytest.mark.parametrize("artifact,risk", [
    (None, {}), ([], {}), ({}, []), ({}, None),
    ({"frozen": float("nan")}, {}),
    ({}, {"training": float("inf")}),
    ({"frozen": object()}, {}),
    ({}, {"training": "x" * (32 * 1024 * 1024)}),
])
def test_refresh_validates_finite_bounded_objects_before_rpc(store, artifact, risk):
    with pytest.raises(ValidationError):
        store.refresh_risk_analytics("p", 0, artifact, risk)
    store.client.rpc.assert_not_called()


@pytest.mark.parametrize("code,error", [
    ("P4090", Conflict), ("P4000", ValidationError), ("XX000", StoreUnavailable),
])
def test_refresh_maps_transaction_failures_without_exposing_database_details(store, code, error):
    failure = RuntimeError("private database details")
    failure.code = code
    store.client.rpc.return_value.execute.side_effect = failure
    with pytest.raises(error) as raised:
        store.refresh_risk_analytics("p", 0, {}, {})
    assert "private" not in str(raised.value)


def test_refresh_requires_acknowledgement(store):
    store.client.rpc.return_value.execute.return_value.data = None
    with pytest.raises(StoreUnavailable, match="not confirmed"):
        store.refresh_risk_analytics("p", 0, {}, {})


@pytest.mark.parametrize("code", ["PGRST202", "42883"])
def test_missing_refresh_rpc_names_the_required_migration(store, code):
    failure = RuntimeError("private database details")
    failure.code = code
    store.client.rpc.return_value.execute.side_effect = failure
    with pytest.raises(StoreUnavailable) as raised:
        store.refresh_risk_analytics("p", 0, {}, {})
    assert "migrations/20261003_refresh_calibration_risk_diagnostics.sql" in str(raised.value)
    assert "private" not in str(raised.value)


@pytest.fixture
def migration():
    return (Path(__file__).parents[1] /
            "migrations/20261003_refresh_calibration_risk_diagnostics.sql").read_text()


def test_refresh_rpc_locks_and_checks_revision_hash_status_and_approval(migration):
    assert "WHERE id=p_payload->>'id' FOR UPDATE" in migration
    assert "proposal.revision <> (p_payload->>'expected_revision')::bigint" in migration
    assert "proposal.artifact_sha256 IS DISTINCT FROM p_payload->>'expected_artifact_sha256'" in migration
    assert "proposal.status NOT IN ('evaluating','ready','no_change')" in migration
    assert "proposal.approval IS NOT NULL" in migration
    assert "proposal.approval <> 'null'::jsonb AND proposal.approval <> '{}'::jsonb" in migration
    assert migration.index("FOR UPDATE") < migration.index("UPDATE public.intraday_calibration_proposals SET")


def test_refresh_rpc_only_sets_diagnostic_artifact_and_audits_atomically(migration):
    updates = re.findall(r"UPDATE public\.intraday_calibration_proposals SET(.*?)WHERE", migration, re.S)
    assert len(updates) == 1
    update = updates[0]
    assert "artifact=jsonb_set(artifact, '{risk_analytics}', new_bundle, false)" in update
    assert "artifact_sha256=p_payload->>'artifact_sha256'" in update
    assert "revision=revision+1, updated_at=clock_timestamp()" in update
    assert not re.search(r"\b(status|approval|request|initial_content|frozen|evaluation)\s*=", update)
    assert "INSERT INTO public.intraday_calibration_events" in migration
    assert "'risk_reference_retry'" in migration
    assert "ON CONFLICT" not in migration  # Duplicate event IDs roll back the proposal update.
    assert migration.index("INSERT INTO public.intraday_calibration_events") < migration.index("RETURN to_jsonb")
    assert migration.startswith("-- Retry") and "\nBEGIN;\n" in migration and "\nCOMMIT;\n" in migration


def test_refresh_rpc_freezes_all_strategy_inputs_and_non_treasury_metrics(migration):
    assert "old_bundle->'retry_pending' IS DISTINCT FROM 'true'::jsonb" in migration
    assert "WHERE k NOT IN ('training','evaluation','retry_pending')" in migration
    assert "(old_bundle ? phase) IS DISTINCT FROM (new_bundle ? phase)" in migration
    assert "Nonpending diagnostic report is immutable" in migration
    assert "old_report - mutable_report IS DISTINCT FROM new_report - mutable_report" in migration
    assert "'status','sha256','reference_snapshot','baseline','candidate','retry_error'" in migration
    assert "'selection_sha256','benchmark_sha256','input_sha256','sha256'" in migration
    assert "old_report->>'phase' IS DISTINCT FROM phase" in migration
    assert "old_side->'daily_equity' IS DISTINCT FROM new_side->'daily_equity'" in migration
    assert "old_side - mutable_side IS DISTINCT FROM new_side - mutable_side" in migration
    assert "(old_side->field) - ARRAY['sharpe','sortino'] IS DISTINCT FROM" in migration
    assert "old_row.value - mutable_return IS DISTINCT FROM new_row.value - mutable_return" in migration
    assert "Unmodelled risk results are immutable" in migration
    assert "new_bundle->'retry_pending' IS DISTINCT FROM to_jsonb(still_pending)" in migration


def test_refresh_rpc_validates_payload_and_has_service_only_permissions(migration):
    assert "jsonb_typeof(p_payload) IS DISTINCT FROM 'object'" in migration
    assert "octet_length(p_payload::text) > 32 * 1024 * 1024" in migration
    assert "jsonb_object_keys(p_payload)" in migration
    assert "p_payload ?& ARRAY" in migration
    assert "^[0-9a-f]{64}$" in migration
    assert "^[0-9]{1,19}$" in migration
    assert "CREATE OR REPLACE FUNCTION public.intraday_calibration_refresh_risk(p_payload jsonb)" in migration
    assert "SECURITY DEFINER SET search_path = public AS" in migration
    assert "REVOKE ALL ON FUNCTION public.intraday_calibration_refresh_risk(jsonb) FROM PUBLIC, anon, authenticated;" in migration
    assert "GRANT EXECUTE ON FUNCTION public.intraday_calibration_refresh_risk(jsonb) TO service_role;" in migration
    assert "has_function_privilege('service_role'" in migration
    assert "has_function_privilege('anon'" in migration
    assert "has_function_privilege('authenticated'" in migration
    assert "a.grantee=0 AND a.privilege_type='EXECUTE'" in migration
    assert "THEN 'OK' ELSE 'FAIL'" in migration


def test_original_frozen_artifact_write_policy_remains_unchanged():
    sql = (Path(__file__).parents[1] / "migrations/20261003_add_calibration_loop.sql").read_text()
    assert "IF (patch ? 'artifact' OR patch ? 'artifact_sha256') AND" in sql
    assert "proposal.status NOT IN ('evaluating','investigation_approved')" in sql
    assert "RAISE EXCEPTION 'Frozen evidence is immutable'" in sql
