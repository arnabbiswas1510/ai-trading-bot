"""Exercise real HTTP client construction without a cloud or local database."""
import datetime as dt
import json
import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import postgrest
import pytest

import research_storage


@pytest.fixture
def local(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "local")
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "http://research-api:3000")
    monkeypatch.setenv("RESEARCH_DATABASE_KEY", "local-only-test-jwt")
    monkeypatch.setenv("SUPABASE_URL", "https://live.example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "live-only-test-key")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "retired-cloud-research-key")
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "research-api"
        assert request.headers["Authorization"] == "Bearer local-only-test-jwt"
        assert "/rest/v1" not in request.url.path
        if request.url.path.startswith("/rpc/"):
            data = {"ok": True}
        elif request.url.path == "/intraday_calibration_settings":
            data = [{"id": "default", "revision": 0, "value": {}}]
        else:
            data = []
        return httpx.Response(200, json=data)

    constructor = postgrest.SyncPostgrestClient
    clients = []

    def factory(url, **kwargs):
        http = httpx.Client(base_url=url, headers=kwargs["headers"],
                            transport=httpx.MockTransport(handle))
        clients.append(http)
        return constructor(url, http_client=http, **kwargs)

    monkeypatch.setattr(postgrest, "SyncPostgrestClient", factory)
    yield requests
    for client in clients:
        client.close()


def source_client():
    query = MagicMock()
    for method in ("select", "lte", "gte", "order", "range", "eq", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value.data = []
    client = MagicMock()
    client.table.return_value = query
    return client


def test_capture_splits_live_discovery_from_research_uploads(local, monkeypatch, tmp_path):
    import intraday_capture as capture
    import supabase
    live = source_client()
    monkeypatch.setattr(supabase, "create_client", lambda url, key, **kw: live)
    recorder = capture.Recorder({}, spool=tmp_path / "capture.sqlite3")
    recorder.emit("capture_gap", {"complete": False})
    monkeypatch.setattr(recorder, "sample", lambda *_: None)
    health = recorder.health

    def finish(db, client):
        health(db, client)
        recorder.stopping.set()

    monkeypatch.setattr(recorder, "health", finish)
    recorder.run()
    assert {call.args[0] for call in live.table.call_args_list} == {
        "daily_triggers", "trigger_history", "portfolio_positions"}
    assert {request.url.path for request in local} >= {
        "/intraday_capture_events", "/intraday_capture_health",
        "/intraday_capture_symbols"}
    assert "/rpc/purge_intraday_capture" not in {request.url.path for request in local}


def test_shadow_sources_never_use_live_client_for_observer_rows(local):
    from shadow_inputs import ReadOnlySources
    from research_source_client import ResearchSourceClient
    live = source_client()
    sources = ReadOnlySources(ResearchSourceClient(live, research_storage.research_client()))
    sources.read("intraday_capture_events")
    sources.read("daily_triggers")
    live.table.assert_called_once_with("daily_triggers")
    assert local[0].url.path == "/intraday_capture_events"
    with pytest.raises(ValueError, match="allowlist"):
        sources._client.table("unexpected_table")
    image = (Path(__file__).resolve().parents[1] / "Dockerfile.shadow").read_text()
    assert "research_source_client.py" in image


def test_web_calibration_risk_and_exit_shadow_use_selected_store(local, monkeypatch):
    import intraday_service
    import exit_shadow_store
    import supabase
    from backend import calibration_api
    from research.calibration_store import CalibrationStore
    from research.calibration_worker import ShadowDatasets, WaitingForData

    monkeypatch.setattr(supabase, "create_client", MagicMock(side_effect=AssertionError("cloud forbidden")))
    monkeypatch.setattr(intraday_service, "_client", None)
    monkeypatch.setattr(calibration_api.intraday_service, "_client", None)
    monkeypatch.setattr(exit_shadow_store, "_client", None)
    client = intraday_service.get_client()
    assert CalibrationStore(client).settings()["revision"] == 0
    assert calibration_api.get_store().settings()["revision"] == 0
    assert CalibrationStore(client).refresh_risk_analytics("proposal", 0, {}, {}) == {"ok": True}
    with pytest.raises(WaitingForData):
        ShadowDatasets(client).load()
    assert exit_shadow_store.write_observation({"ticker": "TEST"})
    paths = {r.url.path for r in local}
    assert paths >= {"/intraday_calibration_settings", "/rpc/intraday_calibration_refresh_risk",
                     "/intraday_shadow_runs", "/exit_shadow_log"}
    rpc = next(r for r in local if "/rpc/" in r.url.path)
    assert set(json.loads(rpc.content)) == {"p_payload"}
    supabase.create_client.assert_not_called()


@pytest.mark.parametrize("override", ["", "read-only-cloud-source-key"])
def test_shadow_startup_uses_only_live_key_for_live_sources(local, monkeypatch, tmp_path, override):
    import shadow_worker
    import supabase
    monkeypatch.setenv("FMP_API_KEY", "offline-market-key")
    monkeypatch.setenv("SHADOW_SOURCE_SUPABASE_KEY", override)
    factory = MagicMock(return_value=source_client())
    monkeypatch.setattr(supabase, "create_client", factory)
    stopped = threading.Event()
    stopped.set()
    args = shadow_worker.parser().parse_args([
        "--account", "U_TEST", "--spool", str(tmp_path / "shadow.sqlite3"),
    ])
    assert shadow_worker.run(args, stop=stopped) is None
    factory.assert_called_once_with("https://live.example.invalid", override or "live-only-test-key")


def test_direct_health_http_preserves_live_trigger_source(local, monkeypatch):
    from backend.calibration_daily_health import Reader
    reader = Reader.__new__(Reader)
    reader.url, reader.key = "https://live.example.invalid", "live-only-test-key"
    seen = []

    def get(url, params, headers):
        seen.append((url, headers))
        return [], {"Content-Range": "*/0"}

    monkeypatch.setattr(reader, "_get", get)
    reader.rows("intraday_shadow_events", "sequence")
    reader.rows("daily_triggers", "triggered_at")
    assert seen == [
        ("http://research-api:3000/intraday_shadow_events",
         {"apikey": "local-only-test-jwt", "Authorization": "Bearer local-only-test-jwt",
          "Prefer": "count=exact"}),
        ("https://live.example.invalid/rest/v1/daily_triggers",
         {"apikey": "live-only-test-key", "Authorization": "Bearer live-only-test-key",
          "Prefer": "count=exact"}),
    ]


def test_reporting_and_exit_review_direct_http_are_local(local, monkeypatch):
    from research.intraday_reporting_delivery import Store
    from research import exit_shadow_review
    http = MagicMock()
    http.request.return_value.status_code = 200
    http.request.return_value.json.return_value = []
    Store.configured(http).select("intraday_research_reports")
    args, kwargs = http.request.call_args
    assert args == ("GET", "http://research-api:3000/intraday_research_reports")
    assert kwargs["headers"]["Authorization"] == "Bearer local-only-test-jwt"
    response = MagicMock()
    response.json.return_value = []
    getter = MagicMock(return_value=response)
    monkeypatch.setattr(exit_shadow_review.requests, "get", getter)
    assert exit_shadow_review.fetch_rows(False) == []
    assert getter.call_args.args == ("http://research-api:3000/exit_shadow_log",)
    assert getter.call_args.kwargs["headers"]["Authorization"] == "Bearer local-only-test-jwt"


def test_local_configuration_failure_cannot_borrow_cloud(local, monkeypatch):
    import intraday_service
    from research.intraday_reporting_delivery import Store, StorageError
    monkeypatch.setattr(intraday_service, "_client", None)
    monkeypatch.delenv("RESEARCH_DATABASE_KEY")
    with pytest.raises(intraday_service.ResearchUnavailable):
        intraday_service.get_client()
    with pytest.raises(StorageError):
        Store.configured()
    assert local == []


def test_cloud_bootstrap_skips_all_credentials_and_http_in_local_mode(local):
    from scripts.run_intraday_reporting_bws import main
    runner = MagicMock(side_effect=AssertionError("must not fetch vault or research"))
    assert main([], {"RESEARCH_STORAGE_BACKEND": "local"}, runner) == 0
    runner.assert_not_called()


@pytest.mark.parametrize(("now", "expected"), [
    ("2026-10-26T13:01:00+00:00", "2026-10-26T13:05:00+00:00"),
    ("2026-10-26T12:59:00+00:00", "2026-10-26T13:00:00+00:00"),
    ("2026-10-26T20:59:00+00:00", "2026-10-26T21:00:00+00:00"),
    ("2026-10-25T14:01:00+00:00", "2026-10-25T14:15:00+00:00"),
])
def test_local_reporter_matches_legacy_cron(now, expected):
    from research.intraday_reporting import next_sweep
    assert next_sweep(dt.datetime.fromisoformat(now)).isoformat() == expected


def test_reporting_loop_retries_errors_and_restores_signal_handlers(monkeypatch):
    from research import intraday_reporting as reporting
    stop = threading.Event()
    attempts = []
    previous = signal.getsignal(signal.SIGTERM)

    def sweep():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("offline")
        stop.set()

    monkeypatch.setattr(stop, "wait", lambda *_: None)
    assert reporting.serve(stop=stop, sweep=sweep) == 0
    assert len(attempts) == 2
    assert signal.getsignal(signal.SIGTERM) == previous
    assert signal.alarm(0) == 0


def test_reporting_loop_shutdown_is_not_an_error(monkeypatch):
    from research import intraday_reporting as reporting
    emit = MagicMock()
    monkeypatch.setattr(reporting.diagnostics, "emit", emit)

    def sweep():
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    assert reporting.serve(sweep=sweep) == 0
    emit.assert_not_called()
