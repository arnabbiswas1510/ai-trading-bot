"""Research outages remain visible without their private database or a broker."""
import importlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import intraday_capture
import research_diagnostics as diagnostics
import research_entrypoint
import shadow_worker
from research.intraday_reporting_delivery import Store, StorageError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
service = importlib.import_module("intraday_service")
shadow_service = importlib.import_module("shadow_service")


class PermissionFailure(Exception):
    code = "42501"


@pytest.fixture
def emissions(monkeypatch):
    emitted = Mock(return_value="diagnostic-123")
    monkeypatch.setattr(diagnostics, "emit", emitted)
    return emitted


@pytest.mark.parametrize("query_function,event", [
    (service._query, "research_query_failed"),
    (shadow_service._query, "shadow_query_failed"),
])
def test_api_failure_keeps_code_and_log_reference_but_not_transport_text(query_function, event, emissions):
    failure = PermissionFailure("apikey=private-secret U1234567")
    query = Mock(path="https://private.invalid/rest/v1/intraday_capture_health")
    query.execute.side_effect = failure
    with pytest.raises(service.ResearchUnavailable) as result:
        query_function(query)
    assert "42501" in str(result.value)
    assert "permission_denied" in str(result.value)
    assert "agent_logs" in str(result.value)
    assert "diagnostic-123" in str(result.value)
    assert "private-secret" not in str(result.value)
    assert "U1234567" not in str(result.value)
    assert emissions.call_args.args == ("web", event)
    assert emissions.call_args.kwargs["error"] is failure
    assert emissions.call_args.kwargs["context"]["table"] == "intraday_capture_health"


def test_missing_private_client_configuration_emits_without_private_client(monkeypatch, emissions):
    monkeypatch.setattr(service, "_client", None)
    monkeypatch.setattr(service.db, "SUPABASE_URL", "")
    with pytest.raises(service.ResearchUnavailable, match="agent_logs"):
        service.get_client()
    assert emissions.call_args.args == ("web", "research_credentials_missing")


def test_reporting_retains_http_and_database_code_without_body(emissions):
    response = Mock(status_code=401)
    response.json.return_value = {"code": "42501", "message": "private-account-data"}
    store = Store("https://private.invalid", "private-key",
                  http=Mock(request=Mock(return_value=response)))
    with pytest.raises(StorageError) as result:
        store.select("intraday_shadow_health")
    assert "private-account-data" not in str(result.value)
    error = emissions.call_args.kwargs["error"]
    assert diagnostics.exception_details(error)["code"] == "42501"
    assert error.status_code == 401
    assert emissions.call_args.kwargs["context"]["table"] == "intraday_shadow_health"


def test_shadow_upload_failure_uses_separate_sink_and_keeps_failure_status(emissions):
    store = Mock()
    failure = PermissionFailure("private-key")
    store.upload.side_effect = failure
    worker = shadow_worker.Worker(store, Mock(), Mock(), engine=Mock())
    assert worker.flush() is False
    assert emissions.call_args.args == ("shadow-worker", "shadow_upload_failed")
    assert emissions.call_args.kwargs["error"] is failure
    assert store.health.call_args.args[0] == "upload_error"


def test_recorder_failure_reports_phase_even_when_private_health_also_fails(monkeypatch, tmp_path, emissions):
    recorder = intraday_capture.Recorder({}, spool=tmp_path / "capture.sqlite3",
                                        health_id="intraday-observer", mode="observer")
    failure = PermissionFailure("private-key")
    monkeypatch.setattr(recorder, "process_snapshot_jobs", Mock(side_effect=failure))
    monkeypatch.setattr(recorder, "health", Mock(side_effect=failure))
    monkeypatch.setenv("SUPABASE_URL", "https://private.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "public-test-key")
    import supabase
    monkeypatch.setattr(supabase, "create_client", Mock(return_value=Mock()))
    original_wait = recorder.stopping.wait

    def stop_after_failure(timeout):
        recorder.stopping.set()
        return original_wait(0)

    monkeypatch.setattr(recorder.stopping, "wait", stop_after_failure)
    recorder.run()
    worker_call = next(c for c in emissions.call_args_list
                       if c.args[1] == "capture_worker_failed")
    assert worker_call.kwargs["error"] is failure
    assert worker_call.kwargs["context"]["operation"] == "snapshot_jobs"
    assert any(c.args[1] == "capture_health_upload_failed" for c in emissions.call_args_list)


@pytest.mark.parametrize("target,module", list(research_entrypoint.MODULES.items()))
def test_entrypoint_starts_logging_before_service_import_and_closes_on_crash(
        monkeypatch, emissions, target, module):
    started = Mock()
    stopped = Mock()
    monkeypatch.setattr(diagnostics, "start", started)
    monkeypatch.setattr(diagnostics, "close", stopped)
    original_argv = sys.argv

    def import_failure(name, run_name):
        started.assert_called_once_with(target, research_entrypoint.SPOOLS[target])
        assert name == module and run_name == "__main__"
        assert sys.argv[1:] == ["--once"]
        raise ImportError("private exception text")

    monkeypatch.setattr(research_entrypoint.runpy, "run_module", import_failure)
    with pytest.raises(ImportError):
        research_entrypoint.main([target, "--once"])
    assert emissions.call_args.args == (target, "service_crash")
    stopped.assert_called_once()
    assert sys.argv is original_argv


def test_web_import_is_inside_startup_diagnostic_boundary(monkeypatch, emissions):
    start = Mock()
    monkeypatch.setattr(diagnostics, "start", start)
    monkeypatch.setattr(diagnostics, "close", Mock())

    def fail(**kwargs):
        start.assert_called_once()
        raise ImportError("web dependency missing")

    uvicorn = SimpleNamespace(run=lambda *args, **kwargs: fail(**kwargs))
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    with pytest.raises(ImportError):
        research_entrypoint.main(["web"])
    assert emissions.call_args.args == ("web", "service_crash")


def test_entrypoint_records_nonzero_exit_without_changing_it(monkeypatch, emissions):
    monkeypatch.setattr(diagnostics, "start", Mock())
    monkeypatch.setattr(diagnostics, "close", Mock())
    monkeypatch.setattr(research_entrypoint.runpy, "run_module", Mock(side_effect=SystemExit(2)))
    with pytest.raises(SystemExit) as result:
        research_entrypoint.main(["intraday-observer"])
    assert result.value.code == 2
    assert emissions.call_args.kwargs["context"]["exit_code"] == 2
    assert emissions.call_args.kwargs["level"] == "CRITICAL"


def test_packaged_services_start_through_diagnostic_boundary():
    root = Path(__file__).resolve().parents[1]
    for path, service_name in (("Dockerfile", "web"), ("Dockerfile.agent", "execution-agent"),
                               ("Dockerfile.shadow", "shadow-worker")):
        text = (root / path).read_text()
        assert "COPY research_diagnostics.py research_entrypoint.py" in text
        assert f'CMD ["python", "research_entrypoint.py", "{service_name}"]' in text


def test_direct_reporting_cli_retains_script_path_bootstrap(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "research/intraday_reporting.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert result.returncode == 0, result.stderr
    assert "--save-calibration" in result.stdout


def test_execution_sigterm_drains_diagnostics_before_resignalling(monkeypatch, emissions):
    import execution_agent as agent
    order = []
    monkeypatch.setattr(intraday_capture, "stop", lambda: order.append("capture"))
    monkeypatch.setattr(agent, "flush_logs_quietly", lambda: order.append("execution_logs"))
    monkeypatch.setattr(diagnostics, "close", lambda: order.append("diagnostics"))
    monkeypatch.setattr(agent.signal, "signal", lambda *_: order.append("default_signal"))
    monkeypatch.setattr(agent.os, "kill", lambda *_: order.append("kill"))
    agent._flush_logs_on_shutdown(signal.SIGTERM)
    assert order == ["capture", "execution_logs", "diagnostics", "default_signal", "kill"]
    assert emissions.call_args.args == ("execution-agent", "service_signal")
    assert emissions.call_args.kwargs["context"]["exit_code"] == 143
