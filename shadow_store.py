"""Single-writer durable shadow journal. A cycle and its outbox commit together."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

import research_diagnostics as diagnostics

SHADOW_TABLES = frozenset({
    "intraday_shadow_runs", "intraday_shadow_events",
    "intraday_shadow_checkpoints", "intraday_shadow_health",
})
SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_SPOOL_BYTES = 1024 * 1024 * 1024
RECOVERABLE_REASONS = frozenset({"quote_unavailable", "stale_quote", "observation_deadline",
                               "source_unavailable", "history_unavailable"})


def legacy_gap_reason(message):
    """Only known acquisition messages from older workers authorize recovery."""
    if message in {
        "Missed scheduled observation; cannot backfill using current quotes.",
        "Cycle acquisition missed its 120-second scheduled observation window.",
        "Cycle acquisition crossed the regular-session boundary; cannot use after-hours quotes.",
    }:
        return "observation_deadline"
    if re.fullmatch(r"[A-Z0-9.^_-]+: stale/future provider quote(?: \([^()\n]+\))?\.?", message):
        return "stale_quote"
    if message == "FMP quote response omitted requested symbols." or message.startswith(
            "FMP quote acquisition incomplete: "):
        return "quote_unavailable"
    if message == "Daily-history batch exceeded 90-second acquisition budget." or re.fullmatch(
            r"FMP historical-price-eod/full request failed "
            r"\((?:ConnectionError|ConnectTimeout|ReadTimeout|Timeout|ConnectError|ReadError|"
            r"WriteError|PoolTimeout|RemoteProtocolError|TimeoutError)\)\.", message):
        return "history_unavailable"
    return None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class StoreError(RuntimeError):
    pass


class ShadowStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise StoreError("Another shadow writer owns this spool.") from None
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, seed TEXT NOT NULL, config TEXT NOT NULL,
                state TEXT NOT NULL, state_hash TEXT NOT NULL, sequence INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, revision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inputs(
                run_id TEXT NOT NULL, cycle_key TEXT NOT NULL, frame TEXT NOT NULL,
                input_hash TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(run_id,cycle_key));
            CREATE TABLE IF NOT EXISTS events(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                row_json TEXT NOT NULL, UNIQUE(run_id,sequence));
            CREATE TABLE IF NOT EXISTS outbox(
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        version = self.db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if version and int(version[0]) != SCHEMA_VERSION:
            self.close()
            raise StoreError("Unsupported shadow spool schema; refusing reset.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('schema_version',?)",
                            (str(SCHEMA_VERSION),))

    def close(self):
        self.db.close()
        fcntl.flock(self._lock, fcntl.LOCK_UN)
        self._lock.close()

    def _enqueue(self, table, body):
        if table not in SHADOW_TABLES:
            raise StoreError("Shadow writer attempted a non-shadow table.")
        self.db.execute("INSERT INTO outbox(target,body) VALUES(?,?)", (table, canonical(body)))

    def active(self):
        row = self.db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = dict(row)
        for field in ("seed", "config", "state"):
            result[field] = json.loads(result[field])
        if fingerprint(result["state"]) != result["state_hash"]:
            raise StoreError("Checkpoint checksum mismatch; refusing to resume.")
        return result

    def new_run_request(self, run):
        row = self.db.execute(
            "SELECT value FROM metadata WHERE key='new_run_request'").fetchone()
        if not row:
            return None
        try:
            request = json.loads(row["value"])
            fields = {"run_id", "requested_at"}
            automatic = isinstance(request, dict) and request.get("mode") == "automatic"
            if automatic:
                fields |= {"mode", "reason_code", "reason"}
            valid = (isinstance(request, dict) and set(request) == fields
                     and run is not None and run["status"] == "blocked"
                     and request["run_id"] == run["id"]
                     and isinstance(request["requested_at"], str)
                     and dt.datetime.fromisoformat(request["requested_at"]).tzinfo is not None
                     and (not automatic or (
                         request["reason_code"] in RECOVERABLE_REASONS
                         and isinstance(request["reason"], str)
                         and self.recovery_failure(run) == {
                             "reason_code": request["reason_code"], "reason": request["reason"]})))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise StoreError("Invalid or stale new-run request; refusing to replace a different run.")
        return request

    def recovery_failure(self, run):
        if not run or run["status"] != "blocked":
            return None
        if not run["state"].get("last_frame_id") and not self.empty_run(run):
            return None
        if self.pending(run["id"]) is not None:
            return None
        rows = self.db.execute("SELECT row_json FROM events WHERE run_id=? ORDER BY sequence DESC",
                               (run["id"],))
        for row in rows:
            event = json.loads(row[0])
            if event["kind"] == "recovery_blocked":
                return None
            if event["kind"] != "gap":
                continue
            payload = event["payload"]
            reason = payload.get("reason", "")
            evidence = payload.get("evidence", {})
            if evidence:
                code = evidence.get("reason_code") if evidence.get("failure_class") == "acquisition" else None
            else:
                code = legacy_gap_reason(reason)
            return {"reason_code": code, "reason": reason} if code in RECOVERABLE_REASONS else None
        return None

    def empty_run(self, run):
        """Prove an old experiment never advanced, without loading its old engine."""
        state = run["state"]
        if (type(state.get("frame_count")) is not int or state["frame_count"] != 0
                or state.get("last_frame_id") is not None
                or state.get("last_frame_digest") is not None
                or state.get("last_captured_at") is not None
                or state.get("last_output") is not None
                or state.get("previous") is not None):
            return False
        if self.db.execute("SELECT 1 FROM inputs WHERE run_id=? LIMIT 1", (run["id"],)).fetchone():
            return False
        for row in self.db.execute("SELECT row_json FROM events WHERE run_id=?", (run["id"],)):
            if json.loads(row[0])["kind"] not in {"gap", "recovery_queued", "recovery_blocked"}:
                return False
        unsigned = {key: value for key, value in state.items() if key != "state_sha256"}
        if (fingerprint(state) != run["state_hash"]
                or fingerprint(unsigned) != state.get("state_sha256")
                or state.get("seed") != run["seed"]
                or state.get("effective_config") != run["config"]
                or fingerprint(run["seed"]) != state.get("seed_fingerprint")
                or fingerprint(run["config"]) != state.get("config_fingerprint")
                or state.get("engine_fingerprint") != run["revision"]
                or state.get("last_timestamp") != run["seed"].get("timestamp")):
            raise StoreError("Empty-run checkpoint integrity mismatch; manual action required.")
        return True

    def _recovery_event(self, run_id, kind, payload):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        run = dict(row)
        for field in ("seed", "config", "state"):
            run[field] = json.loads(run[field])
        sequence, stamp = run["sequence"] + 1, now()
        event = {"id": f"{run_id}:{sequence}", "run_id": run_id, "sequence": sequence,
                 "session": dt.datetime.fromisoformat(stamp).astimezone(
                     ZoneInfo("America/New_York")).date().isoformat(),
                 "occurred_at": stamp, "kind": kind, "payload": payload,
                 "input_sha256": None, "state_sha256": run["state_hash"]}
        self.db.execute("UPDATE runs SET sequence=? WHERE id=?", (sequence, run_id))
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
                        (event["id"], run_id, sequence, canonical(event)))
        self._enqueue("intraday_shadow_events", event)
        self._enqueue("intraday_shadow_runs", self._run_row(run, latest_sequence=sequence))

    def queue_recovery(self):
        run = self.active()
        failure = self.recovery_failure(run)
        if not failure:
            raise StoreError("Automatic recovery requires a classified acquisition gap without pending input.")
        existing = self.new_run_request(run)
        if existing:
            return existing
        request = {"run_id": run["id"], "requested_at": now(), "mode": "automatic", **failure}
        episode = {"old_run_id": run["id"], "new_run_id": None, "status": "queued",
                   "requested_at": request["requested_at"], **failure}
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES('new_run_request',?)", (canonical(request),))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('recovery',?)", (canonical(episode),))
            self._recovery_event(run["id"], "recovery_queued", episode)
            self._health("waiting", f"Automatic research recovery queued for {run['id']}: {failure['reason']}")
        return request

    def hold_recovery(self, message):
        row = self.db.execute("SELECT value FROM metadata WHERE key='new_run_request'").fetchone()
        request = json.loads(row[0]) if row else None
        if not request or request.get("mode") != "automatic":
            return
        episode = {"old_run_id": request["run_id"], "new_run_id": None, "status": "blocked",
                   "requested_at": request["requested_at"], "reason_code": "manual_action_required",
                   "reason": message}
        with self.db:
            self.db.execute("DELETE FROM metadata WHERE key='new_run_request'")
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('recovery',?)", (canonical(episode),))
            self._recovery_event(request["run_id"], "recovery_blocked", episode)

    def queue_new_run(self):
        run = self.active()
        if not run or run["status"] != "blocked":
            raise StoreError("A queued replacement requires an existing blocked shadow run.")
        request = self.new_run_request(run)
        if request:
            return request
        request = {"run_id": run["id"], "requested_at": now()}
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES('new_run_request',?)", (canonical(request),))
            self._health("waiting", "Operator requested a fresh run; waiting for valid regular-session inputs.")
        return request

    def create_run(self, seed, config, state, revision, *, explicit=False):
        previous = self.active()
        if previous and not explicit:
            raise StoreError("An existing shadow run requires explicit --new-run.")
        request = self.new_run_request(previous)
        run_id = fingerprint({"seed": seed, "config": config, "revision": revision})
        if self.db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
            raise StoreError("Identical seed/config run already exists; use fresh observations.")
        created = now()
        with self.db:
            automatic = request and request.get("mode") == "automatic"
            if previous and not automatic:
                self.db.execute("UPDATE runs SET status='superseded' WHERE id=?", (previous["id"],))
                self._enqueue("intraday_shadow_runs", self._run_row(previous, status="superseded"))
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (
                run_id, canonical(seed), canonical(config), canonical(state),
                fingerprint(state), 0, "running", created, revision))
            current = self.active()
            self._enqueue("intraday_shadow_runs", self._run_row(current))
            self._enqueue("intraday_shadow_checkpoints", {
                "run_id": run_id, "sequence": 0, "state": state, "created_at": created})
            if request:
                self.db.execute("DELETE FROM metadata WHERE key='new_run_request'")
                if automatic:
                    episode = {"old_run_id": previous["id"], "new_run_id": run_id,
                               "reason_code": request["reason_code"], "reason": request["reason"],
                               "requested_at": request["requested_at"], "status": "starting"}
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES('recovery',?)",
                                    (canonical(episode),))
        return run_id

    def _run_row(self, run, **overrides):
        return {
            "id": run["id"], "created_at": run["created_at"], "status": run["status"],
            "initial_state": run["seed"], "effective_config": run["config"],
            "engine_revision": run["revision"], "latest_sequence": run["sequence"], **overrides,
        }

    def stage(self, run_id, frame):
        text, digest = canonical(frame), fingerprint(frame)
        if len(text.encode()) > MAX_FRAME_BYTES:
            raise StoreError("Shadow input exceeds 16 MiB; refusing truncated universe.")
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        if pages * page_size + len(text.encode()) * 3 > MAX_SPOOL_BYTES:
            raise StoreError("Shadow spool reached 1 GiB; archive complete runs before explicit new run.")
        key = frame["cycle_key"]
        row = self.db.execute("SELECT * FROM inputs WHERE run_id=? AND cycle_key=?",
                              (run_id, key)).fetchone()
        if row:
            if row["input_hash"] != digest:
                raise StoreError("Conflicting duplicate cycle input; immutable inputs cannot change.")
            return json.loads(row["frame"])
        with self.db:
            self.db.execute("INSERT INTO inputs(run_id,cycle_key,frame,input_hash) VALUES(?,?,?,?)",
                            (run_id, key, text, digest))
        return json.loads(text)

    def pending(self, run_id):
        row = self.db.execute(
            "SELECT frame,input_hash FROM inputs WHERE run_id=? AND committed=0 ORDER BY rowid LIMIT 1",
            (run_id,)).fetchone()
        if not row:
            return None
        frame = json.loads(row["frame"])
        if fingerprint(frame) != row["input_hash"]:
            raise StoreError("Pending input checksum mismatch; refusing to replay.")
        return frame

    def commit_cycle(self, run_id, frame, state, decisions):
        row = self.db.execute("SELECT * FROM inputs WHERE run_id=? AND cycle_key=?",
                              (run_id, frame["cycle_key"])).fetchone()
        if not row or row["input_hash"] != fingerprint(frame):
            raise StoreError("Only the exact durably staged input may be committed.")
        if row["committed"]:
            return False
        run = self.active()
        if run["id"] != run_id or run["status"] != "running":
            raise StoreError("Cannot advance an inactive or blocked run.")
        sequence = run["sequence"] + 1
        state_hash = fingerprint(state)
        event = {
            "id": f"{run_id}:{sequence}", "run_id": run_id, "sequence": sequence,
            "session": frame["session"], "occurred_at": frame["occurred_at"], "kind": "cycle",
            "payload": {"frame": frame.get("engine_frame", frame), "output": decisions,
                        "source_evidence": frame.get("source_evidence", {})},
            "input_sha256": row["input_hash"], "state_sha256": state_hash,
        }
        with self.db:
            self.db.execute("UPDATE inputs SET committed=1 WHERE run_id=? AND cycle_key=?",
                            (run_id, frame["cycle_key"]))
            self.db.execute("UPDATE runs SET state=?,state_hash=?,sequence=? WHERE id=?",
                            (canonical(state), state_hash, sequence, run_id))
            self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
                            (event["id"], run_id, sequence, canonical(event)))
            self._enqueue("intraday_shadow_events", event)
            self._enqueue("intraday_shadow_checkpoints", {
                "run_id": run_id, "sequence": sequence, "state": state, "created_at": now()})
            self._enqueue("intraday_shadow_runs", self._run_row(run, latest_sequence=sequence))
            recovery = self.db.execute("SELECT value FROM metadata WHERE key='recovery'").fetchone()
            if recovery:
                episode = json.loads(recovery[0])
                if episode["new_run_id"] == run_id and episode["status"] == "starting":
                    episode.update(status="recovered", recovered_at=now())
                    self._recovery_event(episode["old_run_id"], "run_recovered", episode)
                    self.db.execute("UPDATE metadata SET value=? WHERE key='recovery'",
                                    (canonical(episode),))
        return True

    def block(self, message, occurred_at, evidence=None):
        run = self.active()
        with self.db:
            if run and run["status"] == "running":
                sequence = run["sequence"] + 1
                event = {
                    "id": f"{run['id']}:{sequence}", "run_id": run["id"],
                    "sequence": sequence, "session": occurred_at[:10],
                    "occurred_at": occurred_at, "kind": "gap",
                    "payload": {"reason": message, "evidence": evidence or {},
                                "requires_explicit_new_run": not (
                                    (evidence or {}).get("failure_class") == "acquisition"
                                    and (evidence or {}).get("reason_code") in RECOVERABLE_REASONS)},
                    "input_sha256": None, "state_sha256": run["state_hash"],
                }
                self.db.execute("UPDATE runs SET status='blocked',sequence=? WHERE id=?",
                                (sequence, run["id"]))
                self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
                                (event["id"], run["id"], sequence, canonical(event)))
                self._enqueue("intraday_shadow_events", event)
                self._enqueue("intraday_shadow_runs",
                              self._run_row(run, status="blocked", latest_sequence=sequence))
            self._health("blocked", message, occurred_at)

    def _health(self, status, error=None, cycle_at=None):
        # Health must remain writable even when a checkpoint checksum is broken.
        run = self.db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        old = self.db.execute("SELECT value FROM metadata WHERE key='health'").fetchone()
        prior = json.loads(old[0]) if old else {}
        body = {
            "id": "shadow-worker", "last_seen_at": now(),
            "last_cycle_at": cycle_at or prior.get("last_cycle_at"),
            "last_persisted_at": prior.get("last_persisted_at"),
            "status": status, "last_error": error, "run_id": run["id"] if run else None,
            "metrics": {"pending_uploads": self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]},
        }
        recovery = self.db.execute("SELECT value FROM metadata WHERE key='recovery'").fetchone()
        if recovery:
            body["metrics"]["recovery"] = json.loads(recovery[0])
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('health',?)", (canonical(body),))
        # Heartbeats are replaceable; decision records are never coalesced.
        self.db.execute("DELETE FROM outbox WHERE target='intraday_shadow_health'")
        self._enqueue("intraday_shadow_health", body)

    def health(self, status, error=None, cycle_at=None):
        with self.db:
            self._health(status, error, cycle_at)

    def upload(self, client, limit=100):
        """Ordered idempotent cloud upserts; an acknowledgement loss is safe to retry."""
        rows = self.db.execute("SELECT * FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        for row in rows:
            if row["target"] not in SHADOW_TABLES:
                raise StoreError("Outbox contains forbidden write target.")
            body = json.loads(row["body"])
            if row["target"] == "intraday_shadow_health":
                body["last_persisted_at"] = now()
            try:
                client.table(row["target"]).upsert(body).execute()
            except Exception as exc:
                diagnostics.emit("shadow-worker", "shadow_table_upload_failed", error=exc,
                                 context={"table": row["target"], "operation": "upsert"})
                raise
            with self.db:
                self.db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
                if row["target"] == "intraday_shadow_health":
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES('health',?)",
                                    (canonical(body),))
        return len(rows)
