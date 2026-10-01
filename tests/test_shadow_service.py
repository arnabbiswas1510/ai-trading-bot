"""Private shadow endpoints retain predecessor evidence and never mutate live state."""
import hashlib
import importlib
import json
from unittest.mock import Mock

import pytest

from test_intraday_service import Client

service = importlib.import_module("shadow_service")
RUN = "f" * 64


@pytest.fixture
def cloud(monkeypatch):
    state = {"cash": 100.0, "positions": {}, "equity": 100.0}
    digest = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    client = Client({
        "intraday_shadow_runs": [{
            "id": RUN, "status": "running", "created_at": "2026-09-28T13:30:00Z",
            "initial_state": {"config": {}}, "effective_config": {},
            "engine_revision": "revision", "latest_sequence": 2,
        }],
        "intraday_shadow_events": [
            {"sequence": sequence, "session": day, "run_id": RUN, "kind": "cycle",
             "payload": {"frame": {"frame_id": str(sequence)}, "output": {"sequence": sequence}},
             "state_sha256": digest}
            for sequence, day in [(1, "2026-09-28"), (2, "2026-09-29")]
        ],
        "intraday_shadow_checkpoints": [{"run_id": RUN, "sequence": 2, "state": state}],
    })
    monkeypatch.setattr(service.research, "get_client", lambda: client)
    monkeypatch.setattr(service.shadow_engine, "engine_fingerprint", lambda: "revision")
    return client


def test_export_includes_pre_window_history_and_final_checkpoint(cloud, monkeypatch):
    exporter = Mock(return_value={"schema_version": 3})
    monkeypatch.setattr(service.shadow_engine, "export_shadow_dataset", exporter)
    assert service.export_dataset(RUN, "2026-09-29", "2026-09-29") == {"schema_version": 3}
    seed, records, start, end = exporter.call_args.args
    assert len(records) == 2
    assert records[0]["frame"]["frame_id"] == "1"
    assert records[-1]["checkpoint"]["cash"] == 100
    assert str(start) == str(end) == "2026-09-29"
    assert all(call.operation == "select" for call in cloud.calls)


@pytest.mark.parametrize("fault,reason", [
    ("prefix", "predecessor"), ("gap", "gap"), ("checkpoint", "not been published"),
    ("corruption", "does not match"), ("output", "missing its inputs"),
    ("engine", "environment changed"), ("configuration", "configuration disagree"),
])
def test_export_refuses_incomplete_or_incompatible_evidence(cloud, fault, reason):
    if fault == "prefix":
        cloud.rows["intraday_shadow_events"].pop(0)
    elif fault == "gap":
        cloud.rows["intraday_shadow_events"][0]["kind"] = "gap"
    elif fault == "checkpoint":
        cloud.rows["intraday_shadow_checkpoints"] = []
    elif fault == "corruption":
        cloud.rows["intraday_shadow_checkpoints"][0]["state"]["cash"] = 1
    elif fault == "output":
        del cloud.rows["intraday_shadow_events"][0]["payload"]["output"]
    elif fault == "engine":
        cloud.rows["intraday_shadow_runs"][0]["engine_revision"] = "other"
    else:
        cloud.rows["intraday_shadow_runs"][0]["effective_config"] = {"changed": True}
    with pytest.raises(ValueError, match=reason):
        service.export_dataset(RUN, "2026-09-29", "2026-09-29")


def test_status_labels_checkpoint_hypothetical_and_keeps_unknowns(cloud):
    result = service.status()
    assert result["portfolio"]["hypothetical"] is True
    assert result["portfolio"]["cash"] == 100
    assert result["portfolio"]["commission"] is None
    assert result["health"] == []
    assert result["reports"] == []
    assert all(call.operation == "select" for call in cloud.calls)


def test_empty_status_is_not_a_zero_balance(cloud):
    cloud.rows.clear()
    assert service.status()["portfolio"] is None


def test_private_database_error_is_sanitized():
    query = Mock()
    query.execute.side_effect = RuntimeError("https://private.example?apikey=SECRET")
    with pytest.raises(service.research.ResearchUnavailable, match="add_intraday_shadow") as error:
        service._query(query)
    assert "SECRET" not in str(error.value)


def test_export_does_not_truncate_oversize_history(cloud, monkeypatch):
    monkeypatch.setattr(service.research, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="Complete shadow history"):
        service.export_dataset(RUN, "2026-09-29", "2026-09-29")


def test_export_requires_known_run(cloud):
    with pytest.raises(ValueError, match="identifier"):
        service.export_dataset("../not-a-run", "2026-09-29", "2026-09-29")
    with pytest.raises(LookupError, match="not found"):
        service.export_dataset("a" * 64, "2026-09-29", "2026-09-29")
