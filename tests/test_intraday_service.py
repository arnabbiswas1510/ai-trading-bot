"""Backend research jobs never depend on a live broker or mutate live settings."""
import datetime as dt
import importlib
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
service = importlib.import_module("intraday_service")


class Query:
    def __init__(self, client, table):
        self.client, self.name = client, table
        self.filters, self.slice = [], None
        self.operation, self.value = "select", None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def gt(self, key, value):
        self.filters.append(("gt", key, value))
        return self

    def lt(self, key, value):
        self.filters.append(("lt", key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte", key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.slice = (0, count - 1)
        return self

    def range(self, lo, hi):
        self.slice = (lo, hi)
        return self

    def insert(self, value):
        self.operation, self.value = "insert", value
        return self

    def update(self, value):
        self.operation, self.value = "update", value
        return self

    def execute(self):
        self.client.calls.append(self)
        if self.operation != "select":
            return Mock(data=[self.value])
        rows = list(self.client.rows.get(self.name, []))
        for op, key, value in self.filters:
            if op == "eq":
                rows = [r for r in rows if r[key] == value]
            elif op == "gte":
                rows = [r for r in rows if r[key] >= value]
            elif op == "gt":
                rows = [r for r in rows if r[key] > value]
            elif op == "lte":
                rows = [r for r in rows if r[key] <= value]
            else:
                rows = [r for r in rows if r[key] < value]
        if self.slice:
            rows = rows[self.slice[0]:self.slice[1] + 1]
        return Mock(data=rows)


class Client:
    def __init__(self, rows=None):
        self.rows, self.calls = rows or {}, []

    def table(self, name):
        return Query(self, name)


@pytest.fixture
def client(monkeypatch):
    client = Client()
    monkeypatch.setattr(service, "get_client", lambda: client)
    monkeypatch.setattr(service, "_pending_terminal", {})
    return client


def test_window_rejects_inverted_oversized_and_future():
    for first, last in [
        ("2026-09-30", "2026-09-01"),
        ("2026-01-01", "2026-09-30"),
        ("2999-01-01", "2999-01-02"),
    ]:
        with pytest.raises(ValueError):
            service.validate_window(first, last)


def test_invalid_recording_config_is_not_a_trading_import_failure(monkeypatch):
    import runpy
    monkeypatch.setenv("INTRADAY_SAMPLE_SECONDS", "invalid")
    values = runpy.run_path(str(Path(__file__).resolve().parents[1] / "config.py"))
    assert values["INTRADAY_CAPTURE_ENABLED"] is False
    assert values["INTRADAY_AUTO_COMPARE"] is False
    assert values["INTRADAY_CONFIG_ERRORS"]
    assert values["MAX_POSITIONS"] > 0


def test_pagination_does_not_silently_truncate(client, monkeypatch):
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    client.rows["intraday_capture_events"] = [
        {"id": str(i), "kind": "quote_sample", "occurred_at": f"2026-09-29T10:0{i}:00-04:00"}
        for i in range(5)
    ]
    assert len(service.load_records("2026-09-29", "2026-09-29")) == 5
    assert [c.slice for c in client.calls[1:]] == [(0, 1), (2, 3), (4, 5)]


def test_empty_and_oversized_capture_rejected(client, monkeypatch):
    with pytest.raises(ValueError, match="No intraday"):
        service.load_records("2026-09-29", "2026-09-29")
    client.rows["intraday_capture_events"] = [
        {"id": "1", "kind": "quote_sample", "occurred_at": "2026-09-29T10:00:00-04:00"},
    ]
    monkeypatch.setattr(service, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="safe memory"):
        service.load_records("2026-09-29", "2026-09-29")


def test_worker_persists_rejected_input_without_fake_result(client, monkeypatch):
    monkeypatch.setattr(service, "load_records", Mock(side_effect=ValueError("Missing initial protection")))
    service._job_lock.acquire()
    service._execute_job({"id": "job", "start_date": "2026-09-29", "end_date": "2026-09-29"})
    assert not service._job_lock.locked()
    update = client.calls[-1]
    assert update.name == "intraday_replay_runs"
    assert update.value["status"] == "rejected"
    assert "result" not in update.value
    assert update.value["error"] == "Missing initial protection"


def test_worker_stores_comparison_and_never_writes_settings(client, monkeypatch):
    result = {"baseline": {"final_equity_net": 123}, "variant": {"final_equity_net": 124},
              "net_final_equity_difference": 1}
    monkeypatch.setattr(service, "load_records", lambda *_: [{"id": "capture"}])
    monkeypatch.setattr(service, "run_comparison", lambda *_: result)
    service._job_lock.acquire()
    service._execute_job({"id": "job", "start_date": "2026-09-29", "end_date": "2026-09-29"})
    assert client.calls[-1].value["status"] == "completed"
    assert client.calls[-1].value["summary"]["difference"] == 1
    assert {c.name for c in client.calls} == {"intraday_replay_runs"}


def test_terminal_write_is_retried_after_database_recovery(client, monkeypatch):
    monkeypatch.setattr(service, "load_records", Mock(side_effect=ValueError("Missing history")))
    real_query = service._query
    monkeypatch.setattr(service, "_query", Mock(side_effect=service.ResearchUnavailable("Offline")))
    service._job_lock.acquire()
    service._execute_job({"id": "job", "start_date": "2026-09-29", "end_date": "2026-09-29"})
    assert service._pending_terminal["job"]["status"] == "rejected"
    assert not service._job_lock.locked()
    monkeypatch.setattr(service, "_query", real_query)
    service._flush_terminal_updates()
    assert not service._pending_terminal
    assert client.calls[-1].value["status"] == "rejected"


def test_orphan_reconciliation_never_interrupts_active_worker(client):
    service._job_lock.acquire()
    try:
        service._mark_interrupted()
        assert not client.calls
    finally:
        service._job_lock.release()


def test_status_never_claims_missing_heartbeat_is_healthy(client):
    status = service.status()
    assert status["heartbeat_stale"]
    assert status["latest_capture_at"] is None
    assert status["sessions"] == []


def test_status_separates_active_observer_from_stopped_trader(client):
    client.rows["intraday_capture_health"] = [
        {"id": "execution-agent", "last_seen_at": "2026-01-01T00:00:00+00:00",
         "last_error": "old error", "config": {}},
        {"id": "intraday-observer", "last_seen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
         "config": {"capture_mode": "observer", "enabled": True}},
    ]
    result = service.status()
    assert result["collector_id"] == "intraday-observer"
    assert result["capture_mode"] == "observer"
    assert result["heartbeat_stale"] is False
    assert result["collectors"][1]["heartbeat_stale"] is True
    assert result["raw_export_available"] and result["export_available"]


def test_raw_observer_export_preserves_unreplayable_evidence_and_rejection(client):
    event = dict(id="observation", run_id="observer-run", sequence=1, session="2026-09-29",
                 kind="broker_snapshot", occurred_at="2026-09-29T15:00:00+00:00",
                 payload={"capture_mode": "observer", "complete": False,
                          "position_quantities": [{"ticker": "SHIP", "position": -1004}]})
    client.rows["intraday_capture_events"] = [event]
    result = service.export_observations("2026-09-29", "2026-09-29")
    assert result["records"] == [event]
    assert result["format"] == "intraday-observation-bundle-v1"
    assert len(result["records_sha256"]) == 64
    assert result["coverage"]["incomplete_or_gap_event_ids"] == ["observation"]
    assert result["replay_input_validation"]["status"] == "rejected"
    assert "observer-only" in result["replay_input_validation"]["reason"]
    assert all(c.operation == "select" for c in client.calls)


def test_raw_export_route_returns_attachment_without_requiring_strategy_readiness(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    export = Mock(return_value={"format": "intraday-observation-bundle-v1", "records": []})
    monkeypatch.setattr(service, "export_observations", export)
    response = TestClient(app).get("/api/intraday/observations?start_date=2026-09-29&end_date=2026-09-29")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    export.assert_called_once()


def test_database_errors_do_not_echo_secret_urls():
    query = Mock()
    query.execute.side_effect = RuntimeError("https://example.invalid/?apikey=do-not-echo")
    with pytest.raises(service.ResearchUnavailable) as error:
        service._query(query)
    assert "do-not-echo" not in str(error.value)
    assert "20260930_add_intraday_research.sql" in str(error.value)


def test_client_initialization_does_not_echo_credentials(monkeypatch):
    monkeypatch.setattr(service, "_client", None)
    monkeypatch.setattr(service.db, "SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "do-not-echo")
    monkeypatch.setattr(service, "create_client", Mock(side_effect=ValueError("do-not-echo")))
    with pytest.raises(service.ResearchUnavailable) as error:
        service.get_client()
    assert "do-not-echo" not in str(error.value)


def test_http_route_starts_actual_account_job_and_forbids_capital_override(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app

    submit = Mock(return_value={"id": "job", "status": "running", "result": None, "error": None})
    monkeypatch.setattr(service, "submit", submit)
    client = TestClient(app)
    body = {"start_date": "2026-09-29", "end_date": "2026-09-29",
            "compare_without_ai_veto": True}
    response = client.post("/api/intraday/replay", json=body)
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert client.post("/api/intraday/replay", json={**body, "initial_cash": 100000}).status_code == 422
    assert client.post("/api/intraday/replay", json={**body, "compare_without_ai_veto": False}).status_code == 422
    assert submit.call_count == 1


def test_export_never_accepts_initial_cash_override(monkeypatch):
    monkeypatch.setattr(service, "load_records", lambda *_: [])
    builder = Mock(return_value={"schema_version": 2})
    monkeypatch.setattr(service, "build_dataset", builder)
    assert service.export_dataset("2026-09-29", "2026-09-29") == {"schema_version": 2}
    builder.assert_called_once_with([], "2026-09-29", "2026-09-29")


def test_auto_reviews_only_closed_prior_week_and_deduplicates(client, monkeypatch):
    monkeypatch.setenv("TRADING_RUNTIME_MODE", "live")
    today = dt.datetime.now(service.NY).date()
    monday = today - dt.timedelta(days=today.weekday())
    prior = monday - dt.timedelta(days=3)
    client.rows["intraday_capture_sessions"] = [
        {"session": (monday - dt.timedelta(days=1)).isoformat(), "frames": 0},
        {"session": prior.isoformat(), "frames": 1},
    ]
    submit = Mock()
    monkeypatch.setattr(service, "submit", submit)
    monkeypatch.setattr(service.config, "INTRADAY_AUTO_COMPARE", True)
    service.automatic_review()
    submit.assert_called_once_with(prior.isoformat(), prior.isoformat(), automatic=True)
    client.rows["intraday_replay_runs"] = [
        {"id": "previous", "automatic": True,
         "created_at": dt.datetime.combine(monday, dt.time(), service.NY).isoformat()}]
    service.automatic_review()
    assert submit.call_count == 1


@pytest.mark.parametrize("mode", [None, "observe"])
def test_actual_account_auto_review_does_not_run_in_observation_mode(client, monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("TRADING_RUNTIME_MODE", raising=False)
    else:
        monkeypatch.setenv("TRADING_RUNTIME_MODE", mode)
    monkeypatch.setattr(service.config, "INTRADAY_AUTO_COMPARE", True)
    get_client = Mock(side_effect=AssertionError("Observer data must not launch an actual-account comparison"))
    monkeypatch.setattr(service, "get_client", get_client)
    service.automatic_review()
    get_client.assert_not_called()
