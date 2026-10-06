"""Recorded shadow activity is bounded, immutable and independent of replay."""
import ast
import copy
import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_intraday_service import Client, Query
from test_shadow_engine import durable_records, seed_and_frames
from test_intraday_replay import DAY, records  # noqa: F401
import intraday_replay

service = importlib.import_module("shadow_service")
RUN = "a" * 64
OTHER = "b" * 64


class ActivityQuery(Query):
    def select(self, columns):
        self.columns = columns
        return self

    def order(self, key, desc=False):
        self.sort = (key, desc)
        return self

    def execute(self):
        self.client.calls.append(self)
        assert self.operation == "select", "Activity must never write"
        rows = copy.deepcopy(self.client.rows.get(self.name, []))
        for op, key, value in self.filters:
            rows = [row for row in rows if {
                "eq": lambda a: a == value, "lte": lambda a: a <= value,
                "lt": lambda a: a < value,
            }[op](row[key])]
        if hasattr(self, "sort"):
            key, desc = self.sort
            rows.sort(key=lambda row: row[key], reverse=desc)
        if self.slice:
            rows = rows[self.slice[0]:self.slice[1] + 1]
        if self.columns != "*":
            projected = []
            for row in rows:
                selected = {}
                for key in self.columns.split(","):
                    if key == "output:payload->output":
                        payload = row.get("payload")
                        selected["output"] = payload.get("output") if isinstance(payload, dict) else None
                    elif ":payload->>" in key:
                        alias, field = key.split(":payload->>")
                        selected[alias] = row.get("payload", {}).get(field)
                    else:
                        selected[key] = row.get(key)
                projected.append(selected)
            rows = projected
        return Mock(data=rows)


class ActivityClient(Client):
    def table(self, name):
        assert name in {
            "intraday_shadow_runs", "intraday_shadow_events", "intraday_shadow_checkpoints",
        }, "Only selected shadow research tables may be read"
        return ActivityQuery(self, name)


def seal(state):
    state.pop("state_sha256", None)
    state["state_sha256"] = service._state_digest(state)
    return state


def cycle(sequence, run_id=RUN):
    return {
        "run_id": run_id, "sequence": sequence, "session": "2026-09-28",
        "occurred_at": "2026-09-28T13:45:00Z", "kind": "cycle",
        "payload": {
            "frame": {"raw_price_frames": "Must not reach browser"},
            "output": {
                "decisions": [{"ticker": "HELD", "action": "HOLD", "hypothetical": True}],
                "fills": [{"ticker": "NEW", "shares": sequence, "price": 123.45,
                           "execution": "counterfactual_sampled_full_fill"}],
                "equity_curve": [{"timestamp": "2026-09-28T13:45:00Z", "equity": 101.25}],
            },
        },
    }


@pytest.fixture
def cloud(monkeypatch):
    config = {"archived": True}
    seed = {"config": config, "timestamp": "2026-09-28T13:30:00Z"}
    state = seal({
        "seed": seed, "effective_config": config, "engine_fingerprint": "archived-engine",
        "seed_fingerprint": service._state_digest(seed),
        "config_fingerprint": service._state_digest(config),
        "frame_count": 3, "cash": 25.5, "positions": {"HELD": {"shares": 1}},
        "equity": 101.25, "max_drawdown_pct": 0.2,
        "commission": 0.35, "slippage": 0.1, "closed_positions": 0,
        "last_timestamp": "2026-09-28T13:45:00Z",
    })
    rows = [cycle(i) for i in (1, 2, 3, 4)]
    rows[2]["state_sha256"] = service._state_digest(state)
    client = ActivityClient({
        "intraday_shadow_runs": [{
            "id": RUN, "latest_sequence": 3, "status": "running",
            "initial_state": seed, "effective_config": config, "engine_revision": "archived-engine",
        }],
        "intraday_shadow_events": rows + [cycle(2, OTHER)],
        "intraday_shadow_checkpoints": [
            {"run_id": RUN, "sequence": 3, "state": state},
            {"run_id": RUN, "sequence": 4, "state": {"unpublished": True}},
            {"run_id": OTHER, "sequence": 3, "state": {"wrong_run": True}},
        ],
    })
    monkeypatch.setattr(service.research, "get_client", lambda: client)
    for name in ("advance", "restore", "checkpoint", "engine_fingerprint", "export_shadow_dataset"):
        monkeypatch.setattr(service.shadow_engine, name,
                            Mock(side_effect=AssertionError("Read-only activity cannot run the engine")))
    return client


def test_recovery_history_is_visible_without_simulated_fills_or_engine_replay(cloud):
    run = cloud.rows["intraday_shadow_runs"][0]
    run.update(latest_sequence=6, status="blocked")
    cloud.rows["intraday_shadow_checkpoints"] = [
        row for row in cloud.rows["intraday_shadow_checkpoints"] if row["sequence"] <= 3]
    cloud.rows["intraday_shadow_events"] = [
        row for row in cloud.rows["intraday_shadow_events"] if row["sequence"] <= 3]
    for sequence, kind in enumerate(("recovery_queued", "recovery_blocked", "run_recovered"), 4):
        cloud.rows["intraday_shadow_events"].append({
            "run_id": RUN, "sequence": sequence, "session": "2026-09-28",
            "occurred_at": "2026-09-28T14:00:00Z", "kind": kind,
            "payload": {"reason": "Recorded acquisition gap",
                        **({"new_run_id": OTHER} if kind == "run_recovered" else {})},
        })
    result = service.activity(RUN)
    assert [event["kind"] for event in result["events"][:3]] == [
        "run_recovered", "recovery_blocked", "recovery_queued"]
    assert result["events"][0]["new_run_id"] == OTHER
    assert all(event["reason"] == "Recorded acquisition gap"
               and event["fills"] == event["decisions"] == event["equity_curve"] == []
               for event in result["events"][:3])
    assert result["portfolio"]["sequence"] == 3


def test_actual_recorded_output_scoped_and_projected_without_mutation(cloud):
    before = copy.deepcopy(cloud.rows)
    result = service.activity(RUN, limit=2)
    assert set(result) == {
        "run_id", "through_sequence", "events", "next_before_sequence",
        "portfolio", "run_status", "scope", "coverage_warning",
    }
    assert [e["sequence"] for e in result["events"]] == [3, 2]
    assert result["through_sequence"] == 3
    assert result["next_before_sequence"] == 2
    assert result["coverage_warning"] is None
    for event in result["events"]:
        recorded = before["intraday_shadow_events"][event["sequence"] - 1]["payload"]["output"]
        assert set(event) == {"sequence", "session", "occurred_at", "kind",
                              "decisions", "fills", "equity_curve"}
        assert event["fills"] == recorded["fills"]
        assert event["decisions"] == recorded["decisions"]
        assert event["equity_curve"] == recorded["equity_curve"]
    assert result["portfolio"]["cash"] == 25.5
    assert result["portfolio"]["sequence"] == 3
    assert result["portfolio"]["hypothetical"] is True
    assert "hypothetical" in result["scope"]
    assert result["run_status"] == "running"
    assert cloud.rows == before
    event_query = cloud.calls[1]
    assert "output:payload->output" in event_query.columns
    assert "frame" not in event_query.columns
    assert event_query.slice == (0, 2)  # limit + one look-ahead row
    assert all(c.operation == "select" for c in cloud.calls)
    assert all(("eq", "run_id", RUN) in c.filters for c in cloud.calls[1:])


def test_cursor_is_stable_when_a_new_cycle_is_published(cloud):
    first = service.activity(RUN, limit=2)
    cloud.rows["intraday_shadow_runs"][0]["latest_sequence"] = 4
    second = service.activity(RUN, limit=2, before_sequence=first["next_before_sequence"],
                              through_sequence=first["through_sequence"])
    assert [e["sequence"] for e in second["events"]] == [1]
    assert second["through_sequence"] == 3
    assert second["next_before_sequence"] is None
    assert second["portfolio"] == first["portfolio"]
    repeated = service.activity(RUN, limit=2, through_sequence=3)
    assert repeated == first


def test_gap_is_visible_and_keeps_last_good_checkpoint(cloud):
    cloud.rows["intraday_shadow_runs"][0].update(latest_sequence=4, status="blocked")
    cloud.rows["intraday_shadow_events"][3].update(
        kind="gap", payload={"reason": "Missing market coverage"})
    cloud.rows["intraday_shadow_checkpoints"] = cloud.rows["intraday_shadow_checkpoints"][:1]
    result = service.activity(RUN)
    gap = result["events"][0]
    assert gap["kind"] == "gap"
    assert gap["decisions"] == gap["fills"] == gap["equity_curve"] == []
    assert result["portfolio"]["sequence"] == 3
    assert result["run_status"] == "blocked"
    assert "not that no trades occurred" in result["scope"]


@pytest.mark.parametrize("limit", [1, 50])
def test_missing_sequence_is_not_invented_as_no_trade_cycle(cloud, limit):
    cloud.rows["intraday_shadow_events"].pop(1)
    with pytest.raises(ValueError, match="missing expected sequence 2"):
        service.activity(RUN, limit=limit)


@pytest.mark.parametrize("before_sequence,missing", [(None, 3), (3, 2), (99, 3)])
def test_missing_page_top_is_not_silently_skipped(cloud, before_sequence, missing):
    cloud.rows["intraday_shadow_events"] = [
        row for row in cloud.rows["intraday_shadow_events"]
        if row["sequence"] != missing
    ]
    with pytest.raises(ValueError, match=f"missing expected sequence {missing}"):
        service.activity(RUN, before_sequence=before_sequence, through_sequence=3)


def test_missing_prefix_has_coverage_warning_at_end(cloud):
    cloud.rows["intraday_shadow_events"].pop(0)
    first = service.activity(RUN, limit=1)
    assert first["coverage_warning"] is None
    assert first["next_before_sequence"] == 3
    last = service.activity(RUN, limit=1, before_sequence=3, through_sequence=3)
    assert [e["sequence"] for e in last["events"]] == [2]
    assert last["next_before_sequence"] is None
    assert "before sequence 2" in last["coverage_warning"]
    assert "not complete history" in last["coverage_warning"]
    single_page = service.activity(RUN)
    assert single_page["coverage_warning"] == last["coverage_warning"]


@pytest.mark.parametrize("before_sequence", [None, 2, 4, 99])
def test_empty_page_with_expected_published_rows_is_an_error(cloud, before_sequence):
    cloud.rows["intraday_shadow_events"] = []
    with pytest.raises(ValueError, match="missing expected sequence"):
        service.activity(RUN, before_sequence=before_sequence, through_sequence=3)


def test_exhausted_cursor_before_first_sequence_is_genuinely_empty(cloud):
    result = service.activity(RUN, before_sequence=1, through_sequence=3)
    assert result["events"] == []
    assert result["next_before_sequence"] is None
    assert result["coverage_warning"] is None


@pytest.mark.parametrize("name", ["limit", "before_sequence", "through_sequence"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "2", [], {}])
def test_cursors_are_strict_positive_non_boolean_integers(cloud, name, value):
    with pytest.raises(ValueError, match="positive integer"):
        service.activity(RUN, **{name: value})
    assert not cloud.calls


def test_limit_defaults_and_cap(cloud):
    service.activity(RUN)
    assert cloud.calls[1].slice == (0, 50)
    service.activity(RUN, limit=100)
    assert cloud.calls[-3].slice == (0, 100)
    with pytest.raises(ValueError, match="100"):
        service.activity(RUN, limit=101)
    with pytest.raises(ValueError, match="published"):
        service.activity(RUN, through_sequence=4)


@pytest.mark.parametrize("identifier", ["bad", "../run", "A" * 64, None, 1])
def test_invalid_run(cloud, identifier):
    with pytest.raises(ValueError, match="identifier"):
        service.activity(identifier)


def test_unknown_run(cloud):
    with pytest.raises(LookupError, match="not found"):
        service.activity(OTHER)


@pytest.mark.parametrize("field,value", [("latest_sequence", True), ("latest_sequence", -1),
                                       ("latest_sequence", None), ("status", None)])
def test_malformed_run(cloud, field, value):
    cloud.rows["intraday_shadow_runs"][0][field] = value
    with pytest.raises(ValueError, match="publication metadata"):
        service.activity(RUN)


@pytest.mark.parametrize("key", ["decisions", "fills", "equity_curve"])
@pytest.mark.parametrize("malformed", [None, {}, "not a list", [123]])
def test_malformed_recorded_lists_are_not_fabricated(cloud, key, malformed):
    cloud.rows["intraday_shadow_events"][2]["payload"]["output"][key] = malformed
    with pytest.raises(ValueError, match="malformed recorded output"):
        service.activity(RUN)


def test_missing_output_is_explicit_error(cloud):
    del cloud.rows["intraday_shadow_events"][2]["payload"]["output"]
    with pytest.raises(ValueError, match="malformed recorded output"):
        service.activity(RUN)


def test_unsupported_event_is_not_hidden(cloud):
    cloud.rows["intraday_shadow_events"][2]["kind"] = "unknown"
    with pytest.raises(ValueError, match="Unsupported"):
        service.activity(RUN)


@pytest.mark.parametrize("fault", ["seal", "event_hash", "missing_event", "provenance"])
def test_checkpoint_seal_and_exact_provenance(cloud, fault):
    state = cloud.rows["intraday_shadow_checkpoints"][0]["state"]
    if fault == "seal":
        state["cash"] = 0
    elif fault == "event_hash":
        cloud.rows["intraday_shadow_events"][2]["state_sha256"] = "wrong"
    elif fault == "missing_event":
        cloud.rows["intraday_shadow_events"].pop(2)
    else:
        state["seed"]["timestamp"] = "2026-09-28T14:00:00Z"
        seal(state)
    with pytest.raises(ValueError, match="checkpoint|missing expected sequence"):
        service.activity(RUN)


def test_missing_checkpoint_and_fields_are_not_fake_zeros(cloud):
    state = cloud.rows["intraday_shadow_checkpoints"][0]["state"]
    del state["commission"]
    seal(state)
    cloud.rows["intraday_shadow_events"][2]["state_sha256"] = service._state_digest(state)
    assert service.activity(RUN)["portfolio"]["commission"] is None
    cloud.rows["intraday_shadow_checkpoints"] = []
    assert service.activity(RUN)["portfolio"] is None


def test_empty_seeded_run(cloud):
    cloud.rows["intraday_shadow_runs"][0]["latest_sequence"] = 0
    checkpoint = cloud.rows["intraday_shadow_checkpoints"][0]
    checkpoint["sequence"] = 0
    checkpoint["state"]["frame_count"] = 0
    seal(checkpoint["state"])
    result = service.activity(RUN)
    assert result["through_sequence"] == 0
    assert result["events"] == []
    assert result["next_before_sequence"] is None
    assert result["portfolio"]["sequence"] == 0
    assert result["coverage_warning"] is None


def test_byte_limit_is_enforced(cloud, monkeypatch):
    monkeypatch.setattr(service.research, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="safe memory"):
        service.activity(RUN)


def test_actual_engine_output_remains_readable_after_engine_changes(records, monkeypatch):
    seed, frames = seed_and_frames(intraday_replay.build_dataset(records, DAY, DAY))
    state, stored = durable_records(seed, frames[:2])
    rows = []
    for sequence, record in enumerate(stored, 1):
        event = cycle(sequence)
        event["payload"] = {"frame": record["frame"], "output": record["output"]}
        event["state_sha256"] = service._state_digest(record["checkpoint"])
        rows.append(event)
    client = ActivityClient({
        "intraday_shadow_runs": [{
            "id": RUN, "latest_sequence": 2, "status": "superseded",
            "initial_state": seed, "effective_config": seed["config"],
            "engine_revision": state["engine_fingerprint"],
        }],
        "intraday_shadow_events": rows,
        "intraday_shadow_checkpoints": [{"run_id": RUN, "sequence": 2, "state": state}],
    })
    monkeypatch.setattr(service.research, "get_client", lambda: client)
    for name in ("engine_fingerprint", "advance", "restore", "export_shadow_dataset"):
        monkeypatch.setattr(service.shadow_engine, name, Mock(side_effect=AssertionError("No replay")))
    result = service.activity(RUN)
    assert result["events"][0]["decisions"] == stored[-1]["output"]["decisions"]
    assert result["events"][0]["fills"] == stored[-1]["output"]["fills"]
    assert result["events"][0]["equity_curve"] == stored[-1]["output"]["equity_curve"]
    assert result["portfolio"]["equity"] == state["equity"]


@pytest.fixture
def api(cloud):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    # Load just this production route without starting dashboard database/schedulers.
    source = Path(__file__).resolve().parents[1] / "backend" / "main.py"
    tree = ast.parse(source.read_text())
    route = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "shadow_research_activity")
    app = FastAPI()
    namespace = {"app": app, "HTTPException": HTTPException, "intraday_service": service.research}
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)
    return TestClient(app)


def test_route_get_and_no_store(api):
    response = api.get(f"/api/intraday/shadow/runs/{RUN}/activity?limit=2&through_sequence=3")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["events"]) == 2
    assert api.post(f"/api/intraday/shadow/runs/{RUN}/activity").status_code == 405


@pytest.mark.parametrize("query", ["limit=true", "limit=1.0", "limit=0", "limit=101",
                                  "before_sequence=false", "before_sequence=-1",
                                  "through_sequence=1.0", "through_sequence=4"])
def test_route_rejects_invalid_cursors(api, query):
    response = api.get(f"/api/intraday/shadow/runs/{RUN}/activity?{query}")
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_route_error_mapping(api, monkeypatch):
    assert api.get("/api/intraday/shadow/runs/bad/activity").status_code == 422
    assert api.get(f"/api/intraday/shadow/runs/{OTHER}/activity").status_code == 404
    monkeypatch.setattr(service, "activity",
                        Mock(side_effect=service.research.ResearchUnavailable("Unavailable")))
    response = api.get(f"/api/intraday/shadow/runs/{RUN}/activity")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
