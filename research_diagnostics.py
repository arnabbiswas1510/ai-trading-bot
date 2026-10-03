"""Credential-safe, independent research diagnostics with at-least-once delivery.

Producer calls only enqueue. A daemon persists to a bounded SQLite outbox before
using the ordinary SUPABASE_KEY sink (private key only if that key is absent).
An acknowledged insert followed by a crash can be retried: session_id + seq and
the JSON diagnostic_id identify duplicates; agent_logs has no uniqueness guard.
The spool must be a service-specific directory on a persistent mounted volume.
Repeated errors send immediately once, then aggregate for up to 60 seconds.
Summary rows carry repeat_count, last_occurred_at and first_diagnostic_id;
repeats are also flushed on transport recovery and graceful shutdown.
"""
import base64
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

SERVICES = frozenset({
    "web", "intraday-observer", "execution-agent", "shadow-worker",
    "research-reporting", "calibration-worker",
})
TABLES = frozenset({
    "agent_logs", "portfolio_positions", "trade_history", "ibkr_fills",
    "intraday_capture_events", "intraday_capture_health",
    "intraday_capture_symbols", "intraday_capture_sessions",
    "intraday_replay_runs", "intraday_shadow_runs", "intraday_shadow_events", "intraday_shadow_health",
    "intraday_shadow_checkpoints", "intraday_calibration_holdout",
    "intraday_research_reports", "intraday_research_incidents",
    "intraday_research_delivery_receipts", "intraday_research_reporting_state",
    "intraday_research_calibration_artifacts",
})
RPCS = frozenset({"purge_intraday_capture", "claim_intraday_reporting", "release_intraday_reporting"})
MESSAGE_PREFIX = "[RESEARCH-DIAGNOSTIC] "
QUEUE_LIMIT = 512
OUTBOX_LIMIT = 4096
BATCH_SIZE = 32
HEARTBEAT_SECONDS = 60.0
COALESCE_SECONDS = 60.0
COALESCE_LIMIT = 256
RETRY_MAX_SECONDS = 30.0
NETWORK_TIMEOUT = (3.0, 5.0)
CLOSE_SECONDS = 10.0
_IDENTIFIER = re.compile(r"[a-z][a-z_.-]{0,79}\Z")
_CODE = re.compile(r"[0-9A-Z]{5,8}\Z")
_NUMBERS = frozenset({
    "pending_events", "dropped_events", "sequence", "error_count",
    "poll_seconds", "client_id", "exit_code",
})
_TIMES = frozenset({"cycle_at", "last_persisted_at"})
_STRINGS = frozenset({"operation", "run_status", "reason_code"})
_lock = threading.Lock()
_handle = None
_unstarted_drops = 0


def _warn():
    try:
        # Bypass execution-agent's stderr tee: never feed the trading logger.
        stream = sys.__stderr__ or sys.stderr
        stream.write("research diagnostics unavailable or capacity exceeded; inspect diagnostics status\n")
        stream.flush()
    except Exception:
        pass


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _identifier(value):
    return (
        isinstance(value, str)
        and _IDENTIFIER.fullmatch(value) is not None
        and not value.startswith(("sb_secret_", "sb_publishable_"))
        and not any(
            token and token in value
            for token in (
                os.getenv("SUPABASE_KEY"), os.getenv("INTRADAY_SUPABASE_KEY"),
                os.getenv("FMP_API_KEY"),
            )
        )
    )


def _context(context):
    safe = {}
    if not isinstance(context, dict):
        return safe
    for key, value in context.items():
        if key == "table" and isinstance(value, str) and value in TABLES:
            safe[key] = value
        elif key == "rpc" and isinstance(value, str) and value in RPCS:
            safe[key] = value
        elif key in _STRINGS and _identifier(value):
            safe[key] = value
        elif key in _NUMBERS and type(value) in (int, float):
            if math.isfinite(value) and abs(value) <= 10**12:
                safe[key] = value
        elif key in _TIMES and isinstance(value, str) and len(value) <= 40:
            try:
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                safe[key] = value
            except ValueError:
                pass
    return safe


def query_context(query):
    """Extract only an exact known research table from query.path, never filters."""
    try:
        path = getattr(query, "path", None)
        if not isinstance(path, str):
            # postgrest-py stores an httpx.URL here; its .path excludes filters.
            path = getattr(path, "path", None)
        if isinstance(path, str):
            path = urlsplit(path).path
            match = re.fullmatch(r"(?:/rest/v1/|/)?([a-z_]+)", path)
            if match and match[1] in TABLES:
                return {"table": match[1]}
            match = re.fullmatch(r"(?:/rest/v1/|/)?rpc/([a-z_]+)", path)
            if match and match[1] in RPCS:
                return {"rpc": match[1], "operation": "rpc"}
    except Exception:
        pass
    return {}


def _single_exception_details(error):
    """Never stringify errors, inspect source lines, or retain response bodies."""
    result = {"category": "unknown", "code": ""}
    try:
        name = type(error).__name__
        type_names = {base.__name__ for base in type(error).__mro__}
        result["error_type"] = name if re.fullmatch(r"[A-Za-z_]{1,80}", name) else "Exception"
        code = getattr(error, "code", None)
        if not (isinstance(code, str) and _CODE.fullmatch(code)):
            code = None
            for arg in error.args[:3]:
                if isinstance(arg, dict):
                    candidate = arg.get("code")
                    if isinstance(candidate, str) and _CODE.fullmatch(candidate):
                        code = candidate
                        break
        if code:
            result["code"] = code
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        if status is None:
            status = getattr(error, "status", None)
        if type(status) is int and 100 <= status <= 599:
            result["http_status"] = status
        else:
            status = None
        if code == "42501" or status == 403:
            result["category"] = "permission_denied"
        elif code in {"42P01", "PGRST205"}:
            result["category"] = "missing_table"
        elif code in {"28000", "28P01", "PGRST301", "PGRST302", "PGRST303"} or status == 401:
            result["category"] = "invalid_credentials"
        elif isinstance(error, TimeoutError) or type_names & {"Timeout", "TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"}:
            result["category"] = "timeout"
        elif isinstance(error, ssl.SSLError) or "SSLError" in type_names:
            result["category"] = "tls_error"
        elif isinstance(error, ConnectionError) or type_names & {"ConnectionError", "ConnectError", "NetworkError", "ReadError", "WriteError", "RemoteProtocolError"}:
            result["category"] = "connection_error"
        elif isinstance(error, (ValueError, TypeError)) or name in {"ResearchUnavailable", "InvalidURL", "MissingSchema"}:
            result["category"] = "configuration_error"
        frames = []
        tb = error.__traceback__
        while tb is not None and len(frames) < 12:
            code_object = tb.tb_frame.f_code
            filename = code_object.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
            function = code_object.co_name
            frames.append({
                "file": filename if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.-]{0,100}\.py", filename) else "unknown",
                "line": tb.tb_lineno,
                "function": function if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,100}", function) else "unknown",
            })
            tb = tb.tb_next
        result["traceback"] = frames
    except Exception:
        pass
    return result


def exception_details(error):
    """Inspect a bounded cause/context chain, including ``raise ... from None``."""
    chain = []
    seen = set()
    current = error
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        chain.append(_single_exception_details(current))
        try:
            cause = current.__cause__
            current = cause if cause is not None else current.__context__
        except Exception:
            break
    result = dict(chain[0]) if chain else {"category": "unknown", "code": ""}
    if len(chain) > 1:
        result["exception_chain"] = chain
        for details in chain[1:]:
            if details.get("code"):
                result["code"] = details["code"]
            if "http_status" in details:
                result["http_status"] = details["http_status"]
            if details["category"] != "unknown":
                result["category"] = details["category"]
                result["cause_error_type"] = details.get("error_type", "Exception")
    return result


def _key_state(value):
    result = {"present": bool(value), "family": "absent", "role": "unknown"}
    if not value:
        return result
    result["family"] = "unknown"
    if value.startswith("sb_secret_"):
        result["family"] = "secret"
    elif value.startswith("sb_publishable_"):
        result["family"] = "publishable"
    elif len(value) <= 16384 and value.count(".") == 2:
        result["family"] = "jwt"
        try:
            part = value.split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            role = payload.get("role")
            if role in ("anon", "authenticated", "service_role"):
                result["role"] = role
        except Exception:
            pass
    return result


def credential_state():
    public = os.getenv("SUPABASE_KEY", "")
    private = os.getenv("INTRADAY_SUPABASE_KEY", "")
    return {
        "url_present": bool(os.getenv("SUPABASE_URL")),
        "ibkr_account_present": bool(os.getenv("IBKR_ACCOUNT")),
        "fmp_api_key_present": bool(os.getenv("FMP_API_KEY")),
        "public": _key_state(public),
        "private": _key_state(private),
        "sink": "public" if public else "private" if private else "absent",
    }


def _revision():
    for variable in ("GIT_COMMIT", "GIT_SHA", "GITHUB_SHA", "BUILD_REVISION", "SOURCE_COMMIT"):
        value = os.getenv(variable, "")
        if re.fullmatch(r"[a-fA-F0-9]{7,40}", value):
            return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=1, check=False,
        )
        value = result.stdout.strip()
        if re.fullmatch(r"[a-fA-F0-9]{40}", value):
            return value
    except Exception:
        pass
    return "unknown"


def _post(rows):
    import requests

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_KEY") or os.getenv("INTRADAY_SUPABASE_KEY")
    parsed = urlsplit(url)
    if not key or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError()
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "return=minimal"}
    # Modern API keys are not JWTs and must not be used as bearer tokens.
    if not key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = "Bearer " + key
    with requests.post(
        url + "/rest/v1/agent_logs", json=rows, headers=headers,
        timeout=NETWORK_TIMEOUT, allow_redirects=False, stream=True,
    ) as response:
        if not 200 <= response.status_code < 300:
            error = requests.HTTPError()
            error.status_code = response.status_code
            raise error


class Diagnostics:
    def __init__(self, service, spool):
        self.service = service
        self.spool = spool
        self.session_id = "research-" + uuid.uuid4().hex
        self.queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._seq = 0
        self._disk_pending = 0
        self._inflight = 0
        self._dropped = 0
        self._failures = 0
        self._last_error = None
        self._last_persisted = None
        self._revision = "unknown"
        self._repeats = {}
        self._thread = threading.Thread(target=self._run, name="research-diagnostics", daemon=True)

    def status(self):
        with self._mutex:
            return {
                "service": self.service, "running": self._thread.is_alive(),
                "pending_events": self._disk_pending + self.queue.qsize() + self._inflight
                + sum(item["count"] for item in self._repeats.values()),
                "dropped_events": self._dropped, "error_count": self._failures,
                "last_error": self._last_error, "last_persisted_at": self._last_persisted,
            }

    def _failure(self, error=None, dropped=0):
        with self._mutex:
            self._failures += 1
            self._dropped += dropped
            self._last_error = exception_details(error) if error is not None else {"category": "unknown"}
        _warn()

    def emit(self, service, event, error=None, level="ERROR", context=None):
        try:
            level = {"WARNING": "WARN", "DEBUG": "INFO"}.get(level, level)
            if service not in SERVICES or not _identifier(event) or level not in {"INFO", "WARN", "ERROR", "CRITICAL"} or self._stop.is_set():
                self._failure(dropped=1)
                return None
            payload = {
                "service": service, "event": event,
                "severity": level, "context": _context(context),
                "git_revision": self._revision,
                "credentials": credential_state(),
            }
            if error is not None:
                payload.update(exception_details(error))
            if event in {"startup", "heartbeat"}:
                payload["diagnostics_status"] = self.status()
            signature = None
            if level in {"WARN", "ERROR", "CRITICAL"}:
                signature = json.dumps({
                    key: value for key, value in payload.items()
                    if key not in {"context", "git_revision", "traceback", "exception_chain"}
                }, sort_keys=True) + json.dumps({
                    key: value for key, value in payload["context"].items()
                    if key not in (_NUMBERS - {"client_id", "exit_code"}) | _TIMES
                }, sort_keys=True)
            with self._mutex:
                item = self._repeats.get(signature)
                if item is not None:
                    item["count"] += 1
                    item["last_occurred_at"] = _now()
                    item["payload"]["context"] = payload["context"]
                    return item["payload"]["diagnostic_id"]
                diagnostic_id = self._enqueue(payload, 1)
                if signature is not None and len(self._repeats) < COALESCE_LIMIT:
                    self._repeats[signature] = {
                        "payload": payload, "count": 0, "since": time.monotonic(),
                        "last_occurred_at": _now(),
                    }
            return diagnostic_id
        except Exception as exc:
            self._failure(exc, dropped=1)
            return None

    def _enqueue(self, payload, count):
        """Called with the mutex held; no transport or disk work."""
        row = self._row(payload, count)
        self.queue.put_nowait(row)
        return payload["diagnostic_id"]

    def _row(self, payload, count):
        """Called with the mutex held, including for a spool-failure notification."""
        self._seq += 1
        payload["diagnostic_id"] = f"{self.session_id}:{self._seq}"
        payload["last_occurred_at"] = payload.get("last_occurred_at", _now())
        return {
            "logged_at": _now(), "session_id": self.session_id, "seq": self._seq,
            "level": payload["severity"],
            "message": MESSAGE_PREFIX + json.dumps(payload, separators=(",", ":")),
            "repeat_count": count,
        }

    def _flush_repeats(self, force=False):
        with self._mutex:
            for signature, item in list(self._repeats.items()):
                if not force and time.monotonic() - item["since"] < COALESCE_SECONDS:
                    continue
                if not item["count"]:
                    del self._repeats[signature]
                    continue
                payload = dict(item["payload"])
                payload["first_diagnostic_id"] = payload.get("first_diagnostic_id", payload["diagnostic_id"])
                payload["last_occurred_at"] = item["last_occurred_at"]
                try:
                    self._enqueue(payload, item["count"])
                except queue.Full:
                    break  # Keep the count until there is room, rather than drop it.
                item.update(payload=payload, count=0, since=time.monotonic())

    def _open(self):
        directory = Path(self.spool)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / "research-diagnostics.sqlite3"
        # restrictive from creation, not only after SQLite has written rows
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(path, timeout=0.1)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA max_page_count=16384")
            connection.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS counters (id INTEGER PRIMARY KEY CHECK(id=1), dropped INTEGER NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO counters VALUES (1, 0)")
            connection.commit()
            with self._mutex:
                self._dropped += connection.execute("SELECT dropped FROM counters WHERE id=1").fetchone()[0]
                self._disk_pending = connection.execute("SELECT count(*) FROM outbox").fetchone()[0]
            return connection
        except Exception:
            connection.close()
            raise

    def _persist(self, connection, pending):
        with connection:
            count = connection.execute("SELECT count(*) FROM outbox").fetchone()[0]
            overflow = max(0, count + len(pending) - OUTBOX_LIMIT)
            dropped = 0
            if overflow:
                evicted = connection.execute("SELECT id,payload FROM outbox ORDER BY id LIMIT ?", (overflow,)).fetchall()
                dropped = sum(json.loads(row[1])["repeat_count"] for row in evicted)
                connection.executemany("DELETE FROM outbox WHERE id=?", [(row[0],) for row in evicted])
            connection.executemany("INSERT INTO outbox(payload) VALUES (?)", [
                (json.dumps(row, separators=(",", ":")),) for row in pending
            ])
            with self._mutex:
                total_dropped = self._dropped + dropped
            connection.execute("UPDATE counters SET dropped=? WHERE id=1", (total_dropped,))
        with self._mutex:
            self._dropped += dropped
            self._disk_pending = min(OUTBOX_LIMIT, count + len(pending))
            self._inflight = 0
            if pending:
                self._last_persisted = _now()
        if dropped:
            _warn()

    def _run(self):
        connection = None
        pending = []
        retry_at = 0.0
        retry_delay = 1.0
        heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
        spool_alert_at = 0.0
        self._revision = _revision()
        self.emit(self.service, "startup", level="INFO")
        try:
            while True:
                try:
                    if connection is None:
                        connection = self._open()
                    self._flush_repeats(force=self._stop.is_set())
                    if not pending:
                        for _ in range(min(BATCH_SIZE, OUTBOX_LIMIT)):
                            try:
                                pending.append(self.queue.get_nowait())
                            except queue.Empty:
                                break
                        with self._mutex:
                            self._inflight = len(pending)
                    self._persist(connection, pending)
                    pending.clear()
                    if self._stop.is_set():
                        self._flush_repeats(force=True)
                        if self.queue.empty():
                            # Fast startup failures must attempt delivery before exiting,
                            # not merely enqueue another crash for the next restart.
                            batch = connection.execute(
                                "SELECT id,payload FROM outbox ORDER BY id LIMIT ?", (BATCH_SIZE,)
                            ).fetchall()
                            if batch:
                                try:
                                    _post([json.loads(row[1]) for row in batch])
                                except Exception as exc:
                                    self._failure(exc)
                                else:
                                    with connection:
                                        connection.executemany(
                                            "DELETE FROM outbox WHERE id=?", [(row[0],) for row in batch])
                                    with self._mutex:
                                        self._disk_pending -= len(batch)
                            break
                        continue
                    now = time.monotonic()
                    if now >= heartbeat_at:
                        self.emit(self.service, "heartbeat", level="INFO", context=self.status())
                        heartbeat_at = now + HEARTBEAT_SECONDS
                    if now >= retry_at:
                        batch = connection.execute("SELECT id,payload FROM outbox ORDER BY id LIMIT ?", (BATCH_SIZE,)).fetchall()
                        if batch:
                            try:
                                _post([json.loads(row[1]) for row in batch])
                            except Exception as exc:
                                self._failure(exc)
                                retry_at = time.monotonic() + retry_delay
                                retry_delay = min(RETRY_MAX_SECONDS, retry_delay * 2)
                            else:
                                with connection:
                                    connection.executemany("DELETE FROM outbox WHERE id=?", [(row[0],) for row in batch])
                                with self._mutex:
                                    self._disk_pending -= len(batch)
                                    previous_error = self._last_error
                                    self._last_error = None
                                    if previous_error is not None:
                                        try:
                                            self._enqueue({
                                                "service": self.service, "event": "diagnostic_transport_recovered",
                                                "severity": "INFO", "context": {},
                                                "previous_error": previous_error,
                                                "git_revision": self._revision,
                                            }, 1)
                                        except queue.Full:
                                            self._dropped += 1
                                            _warn()
                                retry_delay = 1.0
                                if retry_at:
                                    self._flush_repeats(force=True)
                                retry_at = 0.0
                except Exception as exc:
                    self._failure(exc)
                    if time.monotonic() >= spool_alert_at:
                        spool_alert_at = time.monotonic() + RETRY_MAX_SECONDS
                        try:
                            # Disk failures must not force an SSH-only diagnosis when
                            # the independent cloud sink is still reachable.
                            context = _context(self.status())
                            with self._mutex:
                                row = self._row({
                                    "service": self.service, "event": "diagnostic_spool_failed",
                                    "severity": "ERROR", "context": context,
                                    "git_revision": self._revision, **exception_details(exc),
                                }, 1)
                            _post([row])
                            if connection is not None:
                                batch = connection.execute(
                                    "SELECT id,payload FROM outbox ORDER BY id LIMIT ?", (BATCH_SIZE,)
                                ).fetchall()
                                if batch:
                                    _post([json.loads(row[1]) for row in batch])
                                    with connection:
                                        connection.executemany(
                                            "DELETE FROM outbox WHERE id=?", [(row[0],) for row in batch])
                                    with self._mutex:
                                        self._disk_pending -= len(batch)
                        except Exception as delivery_error:
                            self._failure(delivery_error)
                    if self._stop.is_set():
                        break
                    self._stop.wait(1.0)
                self._stop.wait(0.1)
        finally:
            if connection is not None:
                connection.close()

    def close(self):
        self._stop.set()
        try:
            if self._thread.ident is not None:
                self._thread.join(CLOSE_SECONDS)
            with self._mutex:
                incomplete = (
                    self._thread.is_alive() or self.queue.qsize() or self._inflight
                    or any(item["count"] for item in self._repeats.values())
                )
        except Exception:
            incomplete = True
        if incomplete:
            self._failure()


def start(service: str, spool: str):
    """Start one logger per process; repeated calls return the original handle."""
    global _handle
    with _lock:
        if _handle is not None:
            return _handle
        try:
            if service not in SERVICES:
                raise ValueError()
            _handle = Diagnostics(service, spool)
            _handle._dropped = _unstarted_drops
            _handle._thread.start()
            return _handle
        except Exception:
            _handle = None
            _warn()
            return None


def emit(service: str, event: str, *, error=None, level="ERROR", context=None):
    global _unstarted_drops
    handle = _handle
    if handle is None:
        _unstarted_drops += 1
        _warn()
        return None
    return handle.emit(service, event, error=error, level=level, context=context)


def status():
    if _handle is not None:
        return _handle.status()
    return {"running": False, "pending_events": 0, "dropped_events": _unstarted_drops}


def close():
    if _handle is not None:
        _handle.close()
