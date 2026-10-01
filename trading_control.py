"""Persistent permission for NEW real long entries; never gates protective exits."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import time


log = logging.getLogger(__name__)
DEFAULT_PATH = "/app/control/trading-control.sqlite3"
LOCK_TIMEOUT_SECONDS = 2.0
HEARTBEAT_SECONDS = 20


class EntryDisabled(RuntimeError):
    """Entry permission is off or cannot be verified."""


class ControlUnavailable(RuntimeError):
    """A requested permission update could not be persisted and confirmed."""


def _connect(*, initialize=False, writable=False):
    path = Path(os.environ.get("TRADING_CONTROL_PATH", DEFAULT_PATH)).absolute()
    mode = "rwc" if initialize else ("rw" if writable else "ro")
    conn = sqlite3.connect(
        f"{path.as_uri()}?mode={mode}", uri=True,
        timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        if initialize:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entry_control (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    live_entries_enabled INTEGER NOT NULL
                        CHECK (live_entries_enabled IN (0, 1)),
                    revision INTEGER NOT NULL CHECK (revision >= 0)
                )
            """)
            conn.execute("INSERT OR IGNORE INTO entry_control VALUES (1, 0, 0)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_seen TEXT NOT NULL,
                    observed_revision INTEGER NOT NULL,
                    broker_connected INTEGER NOT NULL CHECK (broker_connected IN (0, 1))
                )
            """)
            conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def _permission(conn):
    rows = conn.execute(
        "SELECT live_entries_enabled, revision FROM entry_control"
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("missing or ambiguous entry permission")
    enabled, revision = rows[0]
    if type(enabled) is not int or enabled not in (0, 1):
        raise ValueError("invalid entry permission; expected boolean 0 or 1")
    if type(revision) is not int or revision < 0:
        raise ValueError("invalid entry permission revision")
    return {"live_entries_enabled": enabled == 1, "revision": revision}


def _status(conn):
    status = _permission(conn)
    row = conn.execute("SELECT * FROM agent_status WHERE id = 1").fetchone()
    status["agent"] = None
    if row:
        if (type(row["broker_connected"]) is not int
                or row["broker_connected"] not in (0, 1)
                or type(row["observed_revision"]) is not int
                or row["observed_revision"] < 0):
            raise ValueError("invalid agent heartbeat")
        datetime.fromisoformat(row["last_seen"])
        status["agent"] = {
            "last_seen": row["last_seen"],
            "observed_revision": row["observed_revision"],
            "broker_connected": row["broker_connected"] == 1,
        }
    return status


def _unavailable(exc):
    message = f"Real-entry control unavailable; NEW real buys blocked: {exc}"
    log.error(message)
    return {"live_entries_enabled": False, "revision": 0, "agent": None,
            "error": message}


def get_status():
    """Read a consistent snapshot; absent/unreadable state is explicitly unavailable."""
    conn = None
    try:
        conn = _connect()
        conn.execute("BEGIN")
        return _status(conn)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return _unavailable(exc)
    finally:
        if conn is not None:
            conn.close()


def set_entries_enabled(enabled: bool):
    """Persist permission; raise ControlUnavailable rather than imply a failed save worked."""
    if type(enabled) is not bool:
        raise ValueError("live_entries_enabled must be a boolean")
    conn = None
    try:
        conn = _connect(initialize=True)
        conn.execute("BEGIN IMMEDIATE")
        previous = _permission(conn)
        if previous["live_entries_enabled"] != enabled:
            conn.execute(
                "UPDATE entry_control SET live_entries_enabled = ?, revision = revision + 1 WHERE id = 1",
                (int(enabled),),
            )
        status = _status(conn)
        conn.commit()
        return status
    except (sqlite3.Error, OSError, ValueError) as exc:
        status = _unavailable(exc)
        raise ControlUnavailable(status["error"]) from exc
    finally:
        if conn is not None:
            conn.close()


def entries_allowed() -> bool:
    status = get_status()
    if not status["live_entries_enabled"]:
        log.warning("NEW real buys blocked: %s; protective exits remain enabled",
                    status.get("error", "dashboard entry permission is inactive"))
        return False
    return True


@contextmanager
def entry_submission():
    """Serialize the final placeOrder call with permission changes, not fill waits."""
    conn = None
    try:
        try:
            conn = _connect(writable=True)
            conn.execute("BEGIN IMMEDIATE")
            if not _permission(conn)["live_entries_enabled"]:
                raise EntryDisabled("dashboard entry permission is inactive")
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise EntryDisabled(f"real-entry permission unavailable: {exc}") from exc
        except EntryDisabled:
            raise
        yield
    finally:
        # No data is written by this context. Closing releases the reservation
        # without a post-submission commit that could misleadingly fail.
        if conn is not None:
            conn.close()


class _RotationBroker:
    """Scope a permission reservation to cancellation through ONE sell submission."""

    def __init__(self, ib, reservation):
        self._ib = ib
        self._reservation = reservation
        self._reserved = False
        self._submitted = False

    def __getattr__(self, name):
        return getattr(self._ib, name)

    @property
    def RequestTimeout(self):
        return self._ib.RequestTimeout

    @RequestTimeout.setter
    def RequestTimeout(self, value):
        # Safety helpers configure the IB instance that executes blocking requests.
        self._ib.RequestTimeout = value

    def _reserve(self):
        if not self._reserved:
            self._reservation.enter_context(entry_submission())
            self._reserved = True

    def cancelOrder(self, *args, **kwargs):
        # Do not remove an existing protective stop unless rotation is permitted.
        # Cleanup after submission must remain possible even after a later OFF.
        if not self._submitted:
            self._reserve()
        return self._ib.cancelOrder(*args, **kwargs)

    def placeOrder(self, contract, order):
        if self._submitted or order.action != "SELL" or order.orderType not in ("MKT", "LMT"):
            raise ValueError("Rotation authorization permits one discretionary sell only")
        self._reserve()
        try:
            return self._ib.placeOrder(contract, order)
        finally:
            self._submitted = True
            self._reservation.close()


@contextmanager
def rotation_submission(ib):
    """Guard rotation side effects, without locking quotes/scoring or fill waits.

    The first stop cancellation (or sell submission when no stop exists) reserves
    permission. OFF cannot commit until that sell has been submitted or aborted.
    Protective calls outside this narrowly scoped broker facade are unaffected.
    """
    with ExitStack() as reservation:
        yield _RotationBroker(ib, reservation)


def report_agent_status(broker_connected: bool):
    """Persist liveness only, NOT successful completion of risk protection."""
    if type(broker_connected) is not bool:
        raise ValueError("broker_connected must be a boolean")
    conn = None
    try:
        conn = _connect(initialize=True)
        conn.execute("BEGIN IMMEDIATE")
        revision = _permission(conn)["revision"]
        conn.execute("""
            INSERT INTO agent_status VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen = excluded.last_seen,
                observed_revision = excluded.observed_revision,
                broker_connected = excluded.broker_connected
        """, (datetime.now(timezone.utc).isoformat(), revision, int(broker_connected)))
        status = _status(conn)
        conn.commit()
        return status
    except (sqlite3.Error, OSError, ValueError) as exc:
        return _unavailable(exc)
    finally:
        if conn is not None:
            conn.close()


class AgentHeartbeat:
    """Cooperative liveness: all IB access stays on the execution thread."""

    def __init__(self, ib):
        self.ib = ib
        self._broker_sleep = ib.sleep
        self._last_report = float("-inf")
        self._stopped = False
        ib.sleep = self.sleep
        self.report(force=True)

    def report(self, *, force=False):
        now = time.monotonic()
        if self._stopped or (not force and now - self._last_report < HEARTBEAT_SECONDS):
            return
        self._last_report = now
        try:
            report_agent_status(bool(self.ib.isConnected()))
        except Exception:
            log.exception("Agent heartbeat failed; protective execution continues")

    def sleep(self, seconds=0.02, *, use_ib=True):
        """Preserve the scheduled wait while publishing status at least every 20s."""
        wait = self._broker_sleep if use_ib else time.sleep
        self.report()
        remaining = float(seconds)
        result = None
        if remaining <= 0:
            result = wait(remaining)
        while remaining > 0:
            chunk = min(remaining, HEARTBEAT_SECONDS)
            result = wait(chunk)
            remaining -= chunk
            self.report()
        return result

    def set(self):
        self._stopped = True
        self.ib.sleep = self._broker_sleep


def start_agent_heartbeat(ib):
    """Install cooperative IB sleep polling; call .sleep(use_ib=False) off-hours."""
    return AgentHeartbeat(ib)
