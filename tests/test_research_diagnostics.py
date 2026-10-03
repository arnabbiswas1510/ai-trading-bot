import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import requests

import research_diagnostics as diag


def message(row):
    assert row["message"].startswith(diag.MESSAGE_PREFIX)
    return json.loads(row["message"][len(diag.MESSAGE_PREFIX):])


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(diag, "_handle", None)
    monkeypatch.setattr(diag, "_unstarted_drops", 0)
    monkeypatch.setattr(diag, "_revision", lambda: "a" * 40)
    monkeypatch.setattr(diag, "_post", lambda rows: None)
    yield
    diag.close()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_emit_before_start_is_visible_and_has_no_side_effects(capfd):
    threads = threading.active_count()
    assert diag.emit("web", "query_failed") is None
    assert diag.status()["dropped_events"] == 1
    assert threading.active_count() == threads
    assert "research diagnostics unavailable" in capfd.readouterr().err


@pytest.mark.parametrize("code,category", [
    ("42501", "permission_denied"), ("42P01", "missing_table"),
    ("PGRST205", "missing_table"), ("PGRST301", "invalid_credentials"),
])
def test_structured_codes_never_error_text(code, category):
    class APIError(Exception):
        pass
    error = APIError({"code": code, "message": "SECRET account U1234567", "details": "private position"})
    details = diag.exception_details(error)
    assert details["code"] == code
    assert details["category"] == category
    assert "SECRET" not in json.dumps(details)
    error.code = "secret.invalid-code"
    assert diag.exception_details(error)["code"] == code


@pytest.mark.parametrize("error,category", [
    (requests.Timeout("secret"), "timeout"),
    (requests.exceptions.SSLError("secret"), "tls_error"),
    (requests.ConnectionError("secret"), "connection_error"),
    (ValueError("secret"), "configuration_error"),
    (RuntimeError("permission denied 42501"), "unknown"),
])
def test_categories_use_type_not_message(error, category):
    assert diag.exception_details(error)["category"] == category


def test_traceback_contains_locations_only():
    try:
        secret = "CREDENTIAL_PRIVATE_VALUE"
        raise RuntimeError(secret)
    except RuntimeError as error:
        result = diag.exception_details(error)
    rendered = json.dumps(result)
    assert secret not in rendered
    assert str(Path(__file__).parent) not in rendered
    assert result["traceback"][-1]["function"] == "test_traceback_contains_locations_only"
    assert result["traceback"][-1]["file"] == "test_research_diagnostics.py"


def test_credentials_disclose_only_family_allowlisted_role(monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"role": "service_role", "account": "SECRET"}).encode()).decode().rstrip("=")
    token = f"header.{payload}.signature"
    monkeypatch.setenv("SUPABASE_KEY", token)
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "sb_secret_SECRET")
    monkeypatch.setenv("SUPABASE_URL", "https://private-project.supabase.co")
    state = diag.credential_state()
    assert state["public"] == {"present": True, "family": "jwt", "role": "service_role"}
    assert state["private"]["family"] == "secret"
    assert state["sink"] == "public"
    assert "SECRET" not in json.dumps(state)
    assert token not in json.dumps(state)
    monkeypatch.delenv("SUPABASE_KEY")
    assert diag.credential_state()["sink"] == "private"


def test_query_context_never_reads_url_or_values():
    assert diag.query_context(SimpleNamespace(path="/rest/v1/intraday_capture_events")) == {"table": "intraday_capture_events"}
    for path in [
        "https://secret/rest/v1/intraday_capture_events",
        "/intraday_capture_events?account=SECRET",
        SimpleNamespace(path="/rest/v1/intraday_capture_events"),
    ]:
        assert diag.query_context(SimpleNamespace(path=path)) == {"table": "intraday_capture_events"}
    assert diag.query_context(SimpleNamespace(path="/portfolio_positions")) == {"table": "portfolio_positions"}
    assert diag.query_context(SimpleNamespace(path="/rest/v1/rpc/purge_intraday_capture")) == {"rpc": "purge_intraday_capture", "operation": "rpc"}
    for path in ["/rpc/private_unknown", "/intraday_unknown"]:
        assert diag.query_context(SimpleNamespace(path=path, url="SECRET")) == {}
    assert diag.query_context(SimpleNamespace(url="/intraday_capture_events")) == {}


def test_allowlisted_context():
    result = diag._context({
        "table": "intraday_capture_events", "operation": "select",
        "pending_events": 2, "poll_seconds": 0.5, "cycle_at": "2026-10-03T12:30:00Z",
        "positions": ["SECRET"], "client_id": "U1234567", "run_status": "SECRET",
        "reason_code": "https://SECRET", "error_count": float("inf"),
    })
    assert result == {
        "table": "intraday_capture_events", "operation": "select",
        "pending_events": 2, "poll_seconds": 0.5, "cycle_at": "2026-10-03T12:30:00Z",
    }


def test_identifier_fields_reject_credentials(monkeypatch):
    monkeypatch.setenv("SUPABASE_KEY", "secretvalue")
    assert diag._context({"reason_code": "secretvalue", "operation": "sb_secret_abcdef"}) == {}


def test_at_least_once_retry_preserves_diagnostic_id(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "RETRY_MAX_SECONDS", 0.01)
    seen = []
    def post(rows):
        seen.extend(rows)
        if len(seen) == len(rows):
            raise requests.Timeout("acknowledgement lost")
    monkeypatch.setattr(diag, "_post", post)
    diag.start("web", str(tmp_path))
    wait_for(lambda: len(seen) >= 2)
    identifiers = [message(row)["diagnostic_id"] for row in seen]
    assert len(set(identifiers)) < len(identifiers)


def test_async_start_idempotence_and_schema(monkeypatch, tmp_path):
    seen = []
    callers = []
    def post(rows):
        seen.extend(rows)
        callers.append(threading.get_ident())
    monkeypatch.setattr(diag, "_post", post)
    handle = diag.start("web", str(tmp_path))
    assert diag.start("web", str(tmp_path / "other")) is handle
    identifier = diag.emit("web", "query_failed", error=ValueError("SECRET"), context={"table": "intraday_replay_runs"})
    wait_for(lambda: any(message(row)["diagnostic_id"] == identifier for row in seen))
    assert all(caller != threading.get_ident() for caller in callers)
    assert "SECRET" not in json.dumps(seen)
    assert all(set(row) == {"logged_at", "session_id", "seq", "level", "message", "repeat_count"} for row in seen)
    startup = next(message(row) for row in seen if message(row)["event"] == "startup")
    assert startup["git_revision"] == "a" * 40
    assert "credentials" in startup
    assert not (tmp_path / "other").exists()


def test_outage_persists_and_replays_across_instances(monkeypatch, tmp_path):
    def unavailable(rows):
        raise requests.ConnectionError("SECRET")
    monkeypatch.setattr(diag, "_post", unavailable)
    first = diag.start("web", str(tmp_path))
    identifier = diag.emit("web", "query_failed")
    wait_for(lambda: first.status()["error_count"] > 0)
    first.close()
    assert first.status()["pending_events"] >= 1
    seen = []
    monkeypatch.setattr(diag, "_post", seen.extend)
    monkeypatch.setattr(diag, "_handle", None)
    second = diag.start("web", str(tmp_path))
    wait_for(lambda: any(message(row)["diagnostic_id"] == identifier for row in seen))
    assert second.session_id != first.session_id
    wait_for(lambda: second.status()["pending_events"] == 0)


def test_outbox_and_queue_bounded_drops_survive_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "OUTBOX_LIMIT", 3)
    handle = diag.Diagnostics("web", str(tmp_path))
    for _ in range(diag.QUEUE_LIMIT + 2):
        handle.emit("web", "query_failed", level="INFO")
    assert handle.status()["dropped_events"] == 2
    connection = handle._open()
    try:
        for _ in range(3):
            pending = [handle.queue.get_nowait() for _ in range(3)]
            handle._persist(connection, pending)
        assert connection.execute("SELECT count(*) FROM outbox").fetchone()[0] == 3
        assert handle.status()["dropped_events"] == 8
    finally:
        connection.close()
    other = diag.Diagnostics("web", str(tmp_path))
    connection = other._open()
    connection.close()
    assert other.status()["dropped_events"] == 8


def test_heartbeat_runs_independently(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "HEARTBEAT_SECONDS", 0.05)
    seen = []
    monkeypatch.setattr(diag, "_post", seen.extend)
    handle = diag.start("intraday-observer", str(tmp_path))
    wait_for(lambda: any(message(row)["event"] == "heartbeat" for row in seen))
    heartbeat = next(message(row) for row in seen if message(row)["event"] == "heartbeat")
    assert "pending_events" in heartbeat["context"]
    assert "dropped_events" in heartbeat["context"]
    handle.close()


def test_unwritable_spool_never_kills_caller(monkeypatch, tmp_path, capfd):
    path = tmp_path / "not-directory"
    path.write_text("existing")
    handle = diag.start("web", str(path))
    assert diag.emit("web", "query_failed")
    wait_for(lambda: handle.status()["error_count"] > 0)
    assert "research diagnostics unavailable" in capfd.readouterr().err
    handle.close()


def test_bounded_close_persists_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "_post", lambda rows: (_ for _ in ()).throw(requests.Timeout("SECRET")))
    handle = diag.start("web", str(tmp_path))
    identifier = diag.emit("web", "query_failed")
    handle.close()
    with sqlite3.connect(tmp_path / "research-diagnostics.sqlite3") as connection:
        rows = connection.execute("SELECT payload FROM outbox").fetchall()
    assert any(identifier in row[0] for row in rows)


def test_transport_key_precedence_modern_keys_timeout_and_no_body(monkeypatch):
    # Restore the production function without a network call.
    import importlib.util
    spec = importlib.util.spec_from_file_location("diagnostics_transport_test", diag.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []
    class Response:
        status_code = 403
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        @property
        def text(self):
            pytest.fail("response body read")
    def post(url, **kwargs):
        captured.append((url, kwargs))
        return Response()
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_PUBLIC")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "sb_secret_PRIVATE")
    with pytest.raises(requests.HTTPError) as caught:
        module._post([])
    assert module.exception_details(caught.value)["category"] == "permission_denied"
    assert captured[0][1]["headers"]["apikey"] == "sb_publishable_PUBLIC"
    assert "Authorization" not in captured[0][1]["headers"]
    assert captured[0][1]["timeout"] == module.NETWORK_TIMEOUT
    assert captured[0][1]["allow_redirects"] is False
    assert captured[0][1]["stream"] is True


def test_repeated_errors_coalesce_with_counts_and_last_occurrence(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "COALESCE_SECONDS", 0.15)
    seen = []
    monkeypatch.setattr(diag, "_post", seen.extend)
    handle = diag.start("web", str(tmp_path))
    identifier = diag.emit("web", "query_failed", error=ValueError("PRIVATE"))
    for count in range(20):
        assert diag.emit("web", "query_failed", error=ValueError("OTHER_PRIVATE"),
                         context={"cycle_at": f"2026-10-03T14:00:{count:02d}Z"}) == identifier
    wait_for(lambda: sum(row["repeat_count"] for row in seen if message(row)["event"] == "query_failed") == 21)
    rows = [row for row in seen if message(row)["event"] == "query_failed"]
    assert len(rows) == 2
    assert rows[1]["repeat_count"] == 20
    assert message(rows[1])["first_diagnostic_id"] == identifier
    assert message(rows[1])["last_occurred_at"] >= message(rows[0])["last_occurred_at"]
    assert "PRIVATE" not in json.dumps(seen)
    handle.close()


def test_close_persists_coalesced_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "_post", lambda rows: (_ for _ in ()).throw(requests.Timeout()))
    handle = diag.start("web", str(tmp_path))
    for _ in range(8):
        handle.emit("web", "query_failed")
    handle.close()
    with sqlite3.connect(tmp_path / "research-diagnostics.sqlite3") as connection:
        rows = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM outbox")]
    assert sum(row["repeat_count"] for row in rows if message(row)["event"] == "query_failed") == 8


def test_startup_account_and_fmp_presence_only(monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT", "U1234567")
    monkeypatch.setenv("FMP_API_KEY", "PRIVATE_API_KEY")
    state = diag.credential_state()
    assert state["ibkr_account_present"] is True
    assert state["fmp_api_key_present"] is True
    assert "U1234567" not in json.dumps(state)
    assert "PRIVATE_API_KEY" not in json.dumps(state)
    assert diag.exception_details(RuntimeError())["code"] == ""


def test_suppressed_http_error_context_preserves_status_not_private_content():
    class InputGap(Exception):
        pass
    try:
        try:
            response = requests.Response()
            response.status_code = 401
            response._content = b"PRIVATE_RESPONSE"
            raise requests.HTTPError("https://private?apikey=SECRET", response=response)
        except requests.HTTPError:
            raise InputGap("private account U1234567 and positions") from None
    except InputGap as error:
        assert error.__suppress_context__ is True
        details = diag.exception_details(error)
    assert details["error_type"] == "InputGap"
    assert details["cause_error_type"] == "HTTPError"
    assert details["http_status"] == 401
    assert details["category"] == "invalid_credentials"
    assert details["exception_chain"][1]["traceback"]
    text = json.dumps(details)
    for private in ("PRIVATE_RESPONSE", "SECRET", "U1234567", "https://", str(Path(__file__).parent)):
        assert private not in text


def test_exception_chain_prefers_explicit_cause_and_is_bounded():
    wrapper = RuntimeError("PRIVATE")
    wrapper.__context__ = requests.Timeout("unrelated context")
    cause = RuntimeError({"code": "42501", "message": "SECRET"})
    wrapper.__cause__ = cause
    cause.__context__ = wrapper
    details = diag.exception_details(wrapper)
    assert details["category"] == "permission_denied"
    assert details["code"] == "42501"
    assert len(details["exception_chain"]) == 2
    current = wrapper
    for _ in range(20):
        outer = RuntimeError("SECRET")
        outer.__cause__ = current
        current = outer
    assert len(diag.exception_details(current)["exception_chain"]) == 6


def test_import_and_helpers_require_only_standard_library():
    script = """
import builtins
original = builtins.__import__
forbidden = {'requests', 'httpx', 'supabase', 'postgrest', 'ib_insync',
             'execution_agent', 'agent_logging'}
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in forbidden:
        raise AssertionError('forbidden import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import research_diagnostics
assert research_diagnostics.exception_details(TimeoutError())['category'] == 'timeout'
assert research_diagnostics.credential_state()['sink'] in {'public', 'private', 'absent'}
assert not research_diagnostics.status()['running']
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(diag.__file__).parent,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_fast_crash_close_attempts_cloud_delivery(monkeypatch, tmp_path):
    delivered = []
    monkeypatch.setattr(diag, "_post", delivered.extend)
    handle = diag.start("web", str(tmp_path))
    identifier = handle.emit("web", "service_crash", error=ImportError("secret"), level="CRITICAL")
    handle.close()
    event = next(message(row) for row in delivered if message(row)["diagnostic_id"] == identifier)
    assert event["event"] == "service_crash"
    assert "credentials" in event


def test_spool_failure_is_reported_to_cloud_without_disk(monkeypatch, tmp_path):
    path = tmp_path / "blocked"
    path.write_text("not a directory")
    delivered = []
    monkeypatch.setattr(diag, "_post", delivered.extend)
    handle = diag.start("web", str(path))
    wait_for(lambda: any(message(row)["event"] == "diagnostic_spool_failed" for row in delivered))
    row = next(message(row) for row in delivered if message(row)["event"] == "diagnostic_spool_failed")
    assert row["error_type"] == "FileExistsError"
    assert str(path) not in json.dumps(row)
    assert "pending_events" in row["context"]
    handle.close()


def test_warning_uses_existing_agent_logs_level(tmp_path):
    handle = diag.Diagnostics("web", str(tmp_path))
    assert handle.emit("web", "query_failed", level="WARN")
    row = handle.queue.get_nowait()
    assert row["level"] == "WARN"
    assert handle.status()["dropped_events"] == 0


def test_recovery_preserves_transport_failure_details(monkeypatch, tmp_path):
    delivered = []
    calls = 0
    def post(rows):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.Timeout("private-url")
        delivered.extend(rows)
    monkeypatch.setattr(diag, "_post", post)
    handle = diag.start("web", str(tmp_path))
    wait_for(lambda: any(message(row)["event"] == "diagnostic_transport_recovered" for row in delivered))
    recovery = next(message(row) for row in delivered if message(row)["event"] == "diagnostic_transport_recovered")
    assert recovery["previous_error"]["category"] == "timeout"
    assert "private-url" not in json.dumps(recovery)
    handle.close()


def test_revision_uses_deployed_git_commit(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("diagnostic_revision_test", diag.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("GIT_COMMIT", "b" * 40)
    assert module._revision() == "b" * 40


def test_signal_time_emission_can_reenter_the_producer_lock(tmp_path):
    handle = diag.Diagnostics("execution-agent", str(tmp_path))
    completed = threading.Event()
    def interrupted_producer():
        with handle._mutex:
            handle.emit("execution-agent", "service_signal", level="INFO")
        completed.set()
    thread = threading.Thread(target=interrupted_producer, daemon=True)
    thread.start()
    assert completed.wait(1)
    thread.join()
