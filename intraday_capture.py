"""Optional observation-only recorder. Broker objects never leave their thread.

The bounded ingress queue never waits for disk or network. The worker journals
accepted events before upload; a crash can lose the not-yet-journaled queue tail,
so process boundaries are not evidence of continuous coverage.
"""
from __future__ import annotations

import atexit
import dataclasses
import datetime as dt
import functools
import json
import logging
import math
import os
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo
import research_diagnostics as diagnostics
from config import (
    INTRADAY_CAPTURE_ENABLED, INTRADAY_SAMPLE_SECONDS, INTRADAY_RETENTION_DAYS,
    INTRADAY_MAX_SYMBOLS, INTRADAY_MAX_QUOTE_AGE_SECONDS, INTRADAY_CAPTURE_SPOOL,
)

LOG = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
_recorder = None
_startup_error = None
_cycle = threading.local()
RULE_NAMES = (
    "STOP_LOSS_PCT", "MAX_LOSS_PCT", "TRAIL_PROFIT_TIERS",
    "PROVE_IT_ENABLED", "PROVE_IT_P1_DAY0_PCT", "PROVE_IT_P1_LATER_PCT",
    "PROVE_IT_P1_DAY0_LAST_DAY", "PROVE_IT_P2_ARM_GAIN_PCT",
    "PROVE_IT_P2_FLOOR_PCT", "PROVE_IT_BACKSTOP_SLACK_PCT",
    "POWER_HOLD_ENABLED", "POWER_HOLD_GAIN_PCT", "POWER_HOLD_TRIGGER_DAYS",
    "POWER_HOLD_DURATION_DAYS", "POWER_HOLD_TRAIL_PCT",
)
RUNTIME_NAMES = (
    "MARKET_DIRECTION_FILTER_ENABLED", "COOLING_OFF_DAYS",
    "ARMED_EXIT_TRAIL_PCT", "ATR_STOP_MAX_PCT", "TRIGGER_LOOKBACK_DAYS",
    "OCA_EXIT_ENABLED", "SMART_EXIT_FOR_RULES", "BREAKOUT_VERDICT_MIN_GAIN",
    "BREAKOUT_VERDICT_MIN_VOL_PCT", "RANK_REPLACE_THRESHOLD",
    "RANK_REPLACE_FAIL_THRESHOLD", "STALE_EXIT_DAYS", "STALE_EXIT_MIN_DAYS_HELD",
    "MARKET_DIRECTION_TICKERS", "MARKET_DIRECTION_BUFFER_PCT", "MARKET_DIRECTION_SLOPE_DAYS",
)


def now():
    return dt.datetime.now(UTC).isoformat()


def _plain(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(v) for v in value]
    raise TypeError(f"Unsupported capture value {type(value).__name__}")


def emit(kind, **payload):
    """Fail only at the optional instrumentation boundary, never in trading."""
    if _recorder is not None:
        try:
            if getattr(_cycle, "id", None):
                payload.setdefault("cycle_id", _cycle.id)
            _recorder.emit(kind, payload)
        except Exception as exc:
            _recorder.error(f"instrumentation {kind}: {type(exc).__name__}")


def _fields(obj, names):
    return {name: _plain(getattr(obj, name, None)) for name in names.split()}


def trade_snapshot(trade):
    return {
        "ticker": trade.contract.symbol,
        "contract": _fields(trade.contract, "conId symbol secType currency exchange"),
        "order": _fields(trade.order, "orderId permId clientId account action orderType "
                         "totalQuantity auxPrice lmtPrice trailingPercent trailStopPrice "
                         "tif ocaGroup ocaType parentId transmit orderRef outsideRth triggerMethod"),
        "status": _fields(trade.orderStatus, "status filled remaining avgFillPrice lastFillPrice"),
    }


def broker_snapshot(ib, account):
    """Read only IB's existing caches. Their market marks have no freshness proof."""
    observed = now()
    positions = []
    for p in ib.portfolio():
        if p.account == account:
            positions.append({
                **_fields(p, "account position marketPrice marketValue averageCost unrealizedPNL realizedPNL"),
                "ticker": p.contract.symbol,
                "contract": _fields(p.contract, "conId symbol secType currency exchange"),
                "price_source": "IBKR_CACHE", "provider_timestamp": None,
            })
    return {
        "snapshot_at": observed, "account": account,
        "connected": bool(ib.isConnected()), "positions": positions,
        "position_quantities": [
            {**_fields(p, "account position avgCost"), "ticker": p.contract.symbol,
             "contract": _fields(p.contract, "conId symbol secType currency exchange")}
            for p in ib.positions() if p.account == account
        ],
        "open_orders": [trade_snapshot(t) for t in ib.openTrades() if t.order.account == account],
        "account_values": [_fields(v, "tag value currency account")
                           for v in ib.accountValues() if v.account == account],
        "complete": bool(account) and bool(ib.isConnected()),
        "freshness": "cached_no_provider_timestamp",
    }


def snapshot(ib, *, initial=False, periodic=False):
    if _recorder is None:
        return
    try:
        from execution_agent_ref import ea
        data = broker_snapshot(ib, ea.get_ibkr_account(ib))
        day = dt.datetime.fromisoformat(data["snapshot_at"]).astimezone(NY).date()
        initial = initial or _recorder.snapshot_day != day
        _recorder.snapshot_day = day
        signature = json.dumps({
            "positions": data["position_quantities"],
            "orders": [{
                "id": t["order"]["orderId"], "quantity": t["order"]["totalQuantity"],
                "status": t["status"],
            } for t in data["open_orders"]],
            "connected": data["connected"],
        }, sort_keys=True)
        if (periodic and not initial and signature == _recorder.broker_signature
                and time.monotonic() < _recorder.next_broker_sample):
            return
        _recorder.broker_signature = signature
        _recorder.next_broker_sample = time.monotonic() + _recorder.sample_seconds
        emit("broker_snapshot", **data, initial=initial,
             **({"effective_config": _recorder.config} if initial else {}))
    except Exception as exc:
        _recorder.error(f"broker_snapshot: {type(exc).__name__}")
        emit("capture_gap", area="broker_snapshot", complete=False,
             reason=type(exc).__name__)


def record_order(trade, stage):
    if _recorder is not None:
        try:
            origin = _recorder.order_origins.get((trade.order.clientId, trade.order.orderId), "unknown")
            if stage != "broker_status":
                origin = "protective_order" if stage in (
                    "trailing_stop_submitted", "protective_trail_submitted",
                    "protective_hard_stop_submitted") else "bot"
                _recorder.order_origins[(trade.order.clientId, trade.order.orderId)] = origin
            emit("order_event", stage=stage, origin=origin, **trade_snapshot(trade))
        except Exception as exc:
            _recorder.error(f"order_event: {type(exc).__name__}")


def observe_portfolio(ib, positions, phase):
    """Seed from a live DB read, never a later worker read of mutable holdings."""
    if _recorder is None:
        return
    try:
        observed = now()
        day = dt.datetime.fromisoformat(observed).astimezone(NY).date()
        if _recorder.portfolio_snapshot_day == day:
            return
        from execution_agent_ref import ea
        broker = broker_snapshot(ib, ea.get_ibkr_account(ib))
        emit("portfolio_snapshot_seed", snapshot_at=broker["snapshot_at"],
             broker_snapshot=broker, portfolio_positions=positions,
             portfolio_received_at=observed, phase=phase,
             effective_config=_recorder.config)
        _recorder.portfolio_snapshot_day = day
    except Exception as exc:
        _recorder.error(f"portfolio observation: {type(exc).__name__}")


def capture_phase(name):
    """Markers include early returns; omitted downstream inputs stay unknown."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(ib, *args, **kwargs):
            if _recorder is None:
                return fn(ib, *args, **kwargs)
            cycle_id = str(uuid.uuid4())
            previous_cycle = getattr(_cycle, "id", None)
            _cycle.id = cycle_id
            kind = {"buy": "buy_cycle", "monitor": "monitor"}[name]
            emit(kind, phase=name, stage="start", cycle_id=cycle_id,
                 complete=False, marker_only=True)
            snapshot(ib)
            status = "returned"
            try:
                return fn(ib, *args, **kwargs)
            except BaseException:
                status = "exception"
                raise
            finally:
                snapshot(ib)
                emit(kind, phase=name, stage="end", cycle_id=cycle_id,
                     status=status, complete=status == "returned", marker_only=True,
                     unevaluated_inputs="not_evaluated")
                _cycle.id = previous_cycle
        return wrapped
    return decorate


def effective_config(ea):
    import decision_core
    import exit_core
    import exit_rules
    from trade_costs import CostModel
    return _plain({
        "decision_config": dataclasses.asdict(decision_core.config_from_module(ea)),
        "exit_config": dataclasses.asdict(exit_core.config_from_module(ea)),
        "replay_config": {
            "cooling_off_days": ea.COOLING_OFF_DAYS,
            "armed_exit_trail_pct": ea.ARMED_EXIT_TRAIL_PCT,
            "atr_stop_max_pct": ea.ATR_STOP_MAX_PCT,
            "trigger_lookback_days": ea.TRIGGER_LOOKBACK_DAYS,
        },
        "shared_exit_rules": {k: getattr(exit_rules, k) for k in RULE_NAMES},
        "costs": dataclasses.asdict(CostModel()),
        "costs_semantics": "simulation_assumptions_not_measured_live_costs",
        "runtime": {k: getattr(ea, k, None) for k in RUNTIME_NAMES},
        "git_commit": os.getenv("GIT_COMMIT", "unknown"),
    })


def collector_config():
    """Observation settings only; no execution-agent import or live rule claims."""
    return {
        "sample_seconds": INTRADAY_SAMPLE_SECONDS,
        "retention_days": INTRADAY_RETENTION_DAYS,
        "max_symbols": INTRADAY_MAX_SYMBOLS,
        "max_quote_age_seconds": INTRADAY_MAX_QUOTE_AGE_SECONDS,
        "git_commit": os.getenv("GIT_COMMIT", "unknown"),
    }


def start(ib, ea):
    global _recorder, _startup_error
    if not INTRADAY_CAPTURE_ENABLED:
        diagnostics.emit("execution-agent", "capture_disabled", level="INFO")
        return
    try:
        if _recorder is None:
            _recorder = Recorder(effective_config(ea))
            _recorder.start()
            emit("effective_config", **_recorder.config)
            atexit.register(stop)
        if ib is None or _recorder.broker_attached:
            return
        snapshot(ib, initial=True)
        from ib_insync.util import getLoop
        loop = getLoop()
        account = ea.get_ibkr_account(ib)

        def order_changed(trade):
            if trade.order.account == account:
                record_order(trade, "broker_status")

        def filled(trade, fill):
            try:
                if fill.execution.acctNumber == account:
                    emit("fill_event", ticker=fill.contract.symbol,
                         origin=_recorder.order_origins.get(
                             (fill.execution.clientId, fill.execution.orderId), "unknown"),
                         execution=_fields(fill.execution, "execId time acctNumber side shares price "
                                           "permId clientId orderId cumQty avgPrice"),
                         commission=_fields(fill.commissionReport, "execId commission currency realizedPNL"),
                         received_at=now())
            except Exception as exc:
                _recorder.error(f"fill_event: {type(exc).__name__}")
        ib.orderStatusEvent += order_changed
        ib.execDetailsEvent += filled
        _recorder.broker_attached = True

        def tick():
            if _recorder is not None and not _recorder.stopping.is_set():
                snapshot(ib, periodic=True)
                loop.call_later(5, tick)
        loop.call_later(5, tick)
    except Exception as exc:
        diagnostics.emit("execution-agent", "capture_startup_failed", error=exc)
        _startup_error = {"message": f"startup: {type(exc).__name__}", "occurred_at": now()}
        if _recorder:
            _recorder.error(f"startup: {type(exc).__name__}")


def stop():
    if _recorder is not None:
        with _recorder.lock:
            _recorder.stopping.set()
        if _recorder.thread:
            # Shutdown only: do not report completion while accepted events remain.
            _recorder.thread.join()
        return _recorder.queue.empty()
    return True


class Recorder:
    def __init__(self, config, *, spool=None, queue_size=2048,
                 health_id="execution-agent", mode="execution"):
        self.config = (config if mode == "execution" else
                       {**config, "capture_mode": mode, "mode": mode, "replay_ready": False})
        self.health_id = health_id
        self.mode = mode
        self.run_id = str(uuid.uuid4())
        self.sequence = 0
        self.lock = threading.Lock()
        self.queue = queue.Queue(maxsize=queue_size)
        self.stopping = threading.Event()
        self.thread = None
        self.spool = Path(spool or INTRADAY_CAPTURE_SPOOL)
        self.sample_seconds = max(5, INTRADAY_SAMPLE_SECONDS)
        self.retention_days = max(1, INTRADAY_RETENTION_DAYS)
        self.max_symbols = max(1, INTRADAY_MAX_SYMBOLS)
        self.max_quote_age = max(1, INTRADAY_MAX_QUOTE_AGE_SECONDS)
        self.max_spool_events = 100000
        self.last_error = self.error_at = self.last_persisted_at = None
        self.dropped = 0
        self.symbols = {}
        self.snapshot_day = None
        self.portfolio_snapshot_day = None
        self.order_origins = {}
        self.broker_signature = None
        self.next_broker_sample = 0
        self.broker_attached = False
        self.error_count = 0
        self.logged_error_count = 0
        self.next_error_log = 0
        self.stats_loaded = False
        self.snapshot_job_cursor = 0
        self.last_uploaded_sequence = 0

    def error(self, message):
        """Producer-safe state only: never acquire a logging handler or do I/O."""
        self.last_error, self.error_at = message, now()
        self.error_count += 1

    def log_errors(self):
        """Worker-only, coalesced reporting; no producer/queue lock is held."""
        count = self.error_count
        if count == self.logged_error_count or time.monotonic() < self.next_error_log:
            return
        pending = count - self.logged_error_count
        self.logged_error_count = count
        self.next_error_log = time.monotonic() + 15
        self.emit("capture_gap", {"area": "recorder", "complete": False,
                                 "reason": self.last_error, "error_count": pending})
        LOG.error("Intraday capture incomplete (%s coalesced errors): %s",
                  pending, self.last_error)
        diagnostics.emit(self.health_id, "capture_incomplete", context={
            "error_count": pending, "dropped_events": self.dropped,
            "pending_events": self.queue.qsize(),
        })

    def _event(self, kind, payload, event_id=None):
        """Called under the short sequence lock, never during disk/network I/O."""
        if self.mode != "execution":
            payload = {**payload, "capture_mode": self.mode,
                       "mode": self.mode, "replay_ready": False}
        self.sequence += 1
        occurred = now()
        return {"id": event_id or str(uuid.uuid4()), "run_id": self.run_id,
                "sequence": self.sequence,
                "session": dt.datetime.fromisoformat(occurred).astimezone(NY).date().isoformat(),
                "occurred_at": occurred, "kind": kind, "payload": payload}

    def emit(self, kind, payload):
        payload = _plain(payload)
        with self.lock:
            if self.stopping.is_set():
                return None
            event = self._event(kind, payload)
            try:
                self.queue.put_nowait(event)
            except queue.Full:
                self.dropped += 1
                self.error("ingress queue full; event lost (sequence gap)")
        return event

    def start(self):
        self.thread = threading.Thread(target=self.run, name=self.health_id, daemon=True)
        self.thread.start()

    def open_spool(self):
        self.spool.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.spool))
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, event TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS symbols (ticker TEXT PRIMARY KEY, first_seen_at TEXT, last_seen_at TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS snapshot_jobs "
                       "(seed_id TEXT PRIMARY KEY, event TEXT NOT NULL, snapshot_event_id TEXT, completed_at TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS recorder_state (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            if not self.stats_loaded:
                stored = db.execute("SELECT value FROM recorder_state WHERE id='health'").fetchone()
                if stored:
                    state = json.loads(stored[0])
                    self.dropped += state.get("dropped_events", 0)
                    if self.last_error is None and state.get("last_error"):
                        self.error(state["last_error"])
                        self.error_at = state.get("error_at")
                self.stats_loaded = True
            # Recover seeds journaled by an older recorder before jobs were durable.
            db.execute("INSERT OR IGNORE INTO snapshot_jobs (seed_id,event) "
                       "SELECT id,event FROM pending WHERE "
                       "json_extract(event,'$.kind')='portfolio_snapshot_seed' OR "
                       "(json_extract(event,'$.kind')='broker_snapshot' AND "
                       "json_extract(event,'$.payload.initial')=1)")
            db.commit()
            self.symbols = {r[0]: {"ticker": r[0], "first_seen_at": r[1], "last_seen_at": r[2]}
                            for r in db.execute("SELECT * FROM symbols")}
        except Exception:
            db.close()
            raise
        return db

    def journal(self, db):
        pending = db.execute("SELECT count(*) FROM pending").fetchone()[0]
        for _ in range(512):
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                if pending >= self.max_spool_events:
                    self.dropped += 1
                    self.error("durable spool full; event lost (sequence gap)")
                    continue
                db.execute("INSERT OR IGNORE INTO pending VALUES (?,?)",
                           (event["id"], json.dumps(event, allow_nan=False)))
                if (event["kind"] == "portfolio_snapshot_seed"
                        or (event["kind"] == "broker_snapshot" and event["payload"].get("initial"))):
                    db.execute("INSERT OR IGNORE INTO snapshot_jobs (seed_id,event) VALUES (?,?)",
                               (event["id"], json.dumps(event, allow_nan=False)))
                pending += 1
                payload = event["payload"]
                held = [p for p in (payload.get("positions") or [])
                        if p.get("position", p.get("shares", 0))]
                rows = (payload.get("triggers") or []) + held
                for row in rows:
                    ticker = row.get("ticker")
                    if ticker:
                        old = self.symbols.get(ticker, {})
                        observed = event["occurred_at"]
                        if payload.get("phase") == "discovery":
                            # Re-reading an expired archive must not renew its
                            # sampling horizon forever.
                            observed = str(row.get("triggered_at") or observed)
                            if len(observed) == 10:
                                observed += "T00:00:00+00:00"
                        symbol = {"ticker": ticker,
                                  "first_seen_at": min(old.get("first_seen_at", observed), observed),
                                  "last_seen_at": max(old.get("last_seen_at", observed), observed)}
                        self.symbols[ticker] = symbol
                        db.execute("INSERT OR REPLACE INTO symbols VALUES (?,?,?)", tuple(symbol.values()))
                db.commit()
            except Exception:
                db.rollback()
                # Never silently acknowledge a queue item that did not reach disk.
                self.dropped += 1
                raise
            finally:
                self.queue.task_done()
        self.persist_state(db)

    def persist_state(self, db):
        db.execute("INSERT OR REPLACE INTO recorder_state VALUES ('health',?)", (json.dumps({
            "dropped_events": self.dropped, "last_error": self.last_error,
            "error_at": self.error_at,
        }),))
        db.commit()

    def drain(self, db):
        """Account for every accepted event, including when the disk is unusable."""
        while not self.queue.empty():
            before = self.queue.qsize()
            try:
                self.journal(db)
            except Exception as exc:
                self.error(f"shutdown spool failed: {type(exc).__name__}")
                if self.queue.qsize() >= before:
                    self.drop_queued("shutdown spool unavailable")
                    break
        try:
            self.persist_state(db)
        except Exception as exc:
            self.error(f"shutdown loss accounting unavailable: {type(exc).__name__}")

    def drop_queued(self, reason):
        lost = 0
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
            self.queue.task_done()
            lost += 1
        self.dropped += lost
        if lost:
            self.error(f"{reason}; {lost} accepted events lost")

    def flush(self, db, client):
        rows = db.execute("SELECT id,event FROM pending ORDER BY rowid LIMIT 100").fetchall()
        if rows:
            # Duplicate retries are ignored, never used to overwrite raw history.
            client.table("intraday_capture_events").upsert(
                [json.loads(r[1]) for r in rows], on_conflict="id", ignore_duplicates=True).execute()
            db.executemany("DELETE FROM pending WHERE id=?", [(r[0],) for r in rows])
            db.commit()
            self.last_persisted_at = now()
            current = [json.loads(r[1]) for r in rows]
            self.last_uploaded_sequence = max(
                [self.last_uploaded_sequence] +
                [e["sequence"] for e in current if e["run_id"] == self.run_id])

    def health(self, db, client):
        client.table("intraday_capture_health").upsert({
            "id": self.health_id, "last_seen_at": now(),
            "last_persisted_at": self.last_persisted_at,
            "last_error": self.last_error, "error_at": self.error_at,
            "pending_events": (db.execute("SELECT count(*) FROM pending").fetchone()[0] if db else 0) + self.queue.qsize(),
            "dropped_events": self.dropped,
            "config": {**self.config, "mode": self.mode,
                       "enabled": True, "sample_seconds": self.sample_seconds,
                       "max_symbols": self.max_symbols, "retention_days": self.retention_days,
                       "max_quote_age_seconds": self.max_quote_age,
                       "max_spool_events": self.max_spool_events,
                       "ingress_queue_capacity": self.queue.maxsize,
                       "spool_available": db is not None,
                       "pending_snapshot_jobs": (db.execute(
                           "SELECT count(*) FROM snapshot_jobs WHERE snapshot_event_id IS NULL"
                       ).fetchone()[0] if db else 0),
                       "run_id": self.run_id, "worker_alive": True,
                       "ingress_durability": "queued_until_worker_journal",
                       "approval_required_for_live_changes": True},
        }, on_conflict="id").execute()

    def initial_state(self, client, broker, portfolio_seed=None, *, config=None, publish=True):
        config = self.config if config is None else config
        cutoff = broker["snapshot_at"]
        recent = (dt.datetime.fromisoformat(cutoff) - dt.timedelta(
            days=max(30, int(config["replay_config"]["cooling_off_days"]) + 7))).isoformat()
        data = {"snapshot_at": cutoff, "broker_snapshot": broker,
                "complete": True, "errors": [], "source_times": {},
                "atomic": False, "portfolio_state_semantics": "current_at_worker_read_not_historical"}
        if portfolio_seed is not None:
            data.pop("atomic")
            data["coherence"] = "main_thread_adjacent_observations"
            data["portfolio_state_semantics"] = "copied_live_db_read_before_adjacent_broker_cache"
        for table, time_field, lower in (
            ("portfolio_positions", "buy_date", None),
            ("trade_history", "sell_date", recent),
            ("ibkr_fills", "fill_time", recent),
        ):
            if table == "portfolio_positions" and portfolio_seed is not None:
                data[table] = portfolio_seed["portfolio_positions"]
                data["source_times"][table] = {
                    "received_at": portfolio_seed["portfolio_received_at"],
                    "source": "live_main_thread_read",
                }
                continue
            try:
                rows, offset = [], 0
                started = now()
                while True:
                    if self.stopping.is_set():
                        data["complete"] = False
                        data["errors"].append(f"{table}: shutdown interrupted source read")
                        break
                    query = client.table(table).select("*").lte(time_field, cutoff)
                    if lower:
                        query = query.gte(time_field, lower)
                    batch = query.order(time_field).range(offset, offset + 499).execute().data or []
                    rows.extend(batch)
                    if len(batch) < 500:
                        break
                    offset += 500
                data[table] = rows
                data["source_times"][table] = {"started_at": started, "received_at": now(),
                                              "filter_lte": cutoff, "filter_gte": lower}
            except Exception as exc:
                diagnostics.emit(self.health_id, "snapshot_source_failed", error=exc,
                                 context={"table": table, "operation": "select"})
                data["complete"] = False
                data["errors"].append(f"{table}: {type(exc).__name__}")
                data[table] = None
        data["effective_config"] = config
        if publish:
            self.emit("portfolio_snapshot", data)
        return data

    def process_snapshot_jobs(self, db, client):
        """Enrich durable seeds from every run, atomically marking each result."""
        jobs = db.execute("SELECT rowid,seed_id,event FROM snapshot_jobs "
                          "WHERE snapshot_event_id IS NULL AND rowid>? ORDER BY rowid LIMIT 25",
                          (self.snapshot_job_cursor,)).fetchall()
        if not jobs:
            self.snapshot_job_cursor = 0
            jobs = db.execute("SELECT rowid,seed_id,event FROM snapshot_jobs "
                              "WHERE snapshot_event_id IS NULL ORDER BY rowid LIMIT 25").fetchall()
        for row_id, seed_id, raw in jobs:
            if self.stopping.is_set():
                return
            if db.execute("SELECT count(*) FROM pending").fetchone()[0] >= self.max_spool_events:
                self.error("durable spool full; snapshot enrichment deferred, seed retained")
                return
            seed_event = json.loads(raw)
            payload = seed_event["payload"]
            config = payload.get("effective_config")
            if not isinstance(config, dict):
                self.error("snapshot seed missing original effective configuration; retained")
                self.snapshot_job_cursor = row_id
                continue
            is_portfolio = seed_event["kind"] == "portfolio_snapshot_seed"
            broker = payload["broker_snapshot"] if is_portfolio else payload
            data = self.initial_state(client, broker, payload if is_portfolio else None,
                                      config=config, publish=False)
            if not data["complete"]:
                if self.stopping.is_set():
                    return
                self.error("snapshot historical reads incomplete; seed retained for retry")
                self.snapshot_job_cursor = row_id
                continue
            data.update(seed_event_id=seed_id, source_run_id=seed_event["run_id"],
                        source_occurred_at=seed_event["occurred_at"],
                        recovered_from_prior_run=seed_event["run_id"] != self.run_id)
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"intraday-snapshot:{seed_id}"))
            with self.lock:
                event = self._event("portfolio_snapshot", _plain(data), event_id)
            # Either both the output and marker survive, or the original job retries.
            with db:
                db.execute("INSERT OR IGNORE INTO pending VALUES (?,?)",
                           (event_id, json.dumps(event, allow_nan=False)))
                db.execute("UPDATE snapshot_jobs SET snapshot_event_id=?,completed_at=? WHERE seed_id=?",
                           (event_id, now(), seed_id))
            self.snapshot_job_cursor = row_id

    def refresh_universe(self, client):
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=self.retention_days)).isoformat()
        for table, field in (("daily_triggers", "triggered_at"), ("trigger_history", "triggered_at")):
            offset = 0
            while True:
                if self.stopping.is_set():
                    return
                rows = client.table(table).select("*").gte(field, cutoff).order(field).range(
                    offset, offset + 499).execute().data or []
                self.emit("candidate_universe", {"phase": "discovery", "source_table": table,
                          "triggers": rows, "complete": True, "historical_gate_inputs": False})
                if len(rows) < 500:
                    break
                offset += 500
        rows = client.table("portfolio_positions").select("*").execute().data or []
        self.emit("source_snapshot", {"table": "portfolio_positions", "positions": rows,
                                     "received_at": now()})

    def sample(self, http):
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=self.retention_days)).isoformat()
        tickers = sorted(t for t, row in self.symbols.items() if row["last_seen_at"] >= cutoff)
        if not tickers:
            self.error("no symbols available; price coverage incomplete")
            self.emit("quote_sample", {"quotes": [], "requested_symbols": [],
                      "complete": False, "missing_quotes": [],
                      "errors": [{"reason": "no_symbols"}], "received_at": now()})
            return
        if len(tickers) > self.max_symbols:
            self.error(f"symbol limit exceeded: {len(tickers)} > {self.max_symbols}; sampling incomplete")
            self.emit("quote_sample", {"quotes": [], "requested_symbols": tickers,
                      "complete": False, "missing_quotes": tickers,
                      "errors": [{"reason": "symbol_limit_exceeded"}],
                      "reason": "symbol_limit_exceeded", "symbol_count": len(tickers),
                      "received_at": now()})
            return
        quotes, errors, seen = [], [], set()
        for offset in range(0, len(tickers), 100):
            if self.stopping.is_set():
                return
            chunk = tickers[offset:offset + 100]
            try:
                response = http.get("https://financialmodelingprep.com/stable/batch-quote",
                                    params={"symbols": ",".join(chunk), "apikey": os.getenv("FMP_API_KEY", "")},
                                    timeout=(3, 10))
                response.raise_for_status()
                received = now()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError("FMP response is not a quote list")
            except Exception as exc:
                diagnostics.emit(self.health_id, "quote_request_failed", error=exc,
                                 context={"operation": "fmp_batch_quote"})
                errors.extend({"ticker": t, "reason": "quote_request_failed",
                               "error_type": type(exc).__name__} for t in chunk)
                continue
            for row in rows:
                if not isinstance(row, dict):
                    errors.append({"reason": "invalid_quote_row"})
                    continue
                ticker = row.get("symbol")
                if ticker not in chunk:
                    continue
                try:
                    price = float(row["price"])
                    stamp = float(row["timestamp"])
                    age = dt.datetime.fromisoformat(received).timestamp() - stamp
                    if not math.isfinite(price) or price <= 0 or not math.isfinite(stamp) or not -60 <= age <= self.max_quote_age:
                        raise ValueError("invalid price or stale provider timestamp")
                    quotes.append({"ticker": ticker, "price": price, "source": "FMP",
                                   "provider_timestamp": dt.datetime.fromtimestamp(stamp, UTC).isoformat(),
                                   "received_at": received})
                    seen.add(ticker)
                except (KeyError, TypeError, ValueError, OverflowError):
                    errors.append({"ticker": ticker, "reason": "invalid_or_stale_quote"})
        errors.extend({"ticker": t, "reason": "quote_missing"} for t in tickers if t not in seen)
        self.emit("quote_sample", {"quotes": quotes, "requested_symbols": tickers,
                                  "complete": not errors, "errors": errors,
                                  "missing_quotes": [t for t in tickers if t not in seen],
                                  "received_at": now()})
        if errors:
            self.error(f"quote coverage incomplete: {len(errors)} errors")

    def run(self):
        db = client = http = None
        next_sample = next_health = next_discovery = next_purge = 0
        next_diagnostic = 0
        operation = "recorder_imports"
        try:
            import requests
            from supabase import create_client
            from supabase.lib.client_options import SyncClientOptions
            http = requests.Session()
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    operation = "open_spool"
                    if db is None:
                        db = self.open_spool()
                    operation = "journal"
                    self.journal(db)
                    if self.stopping.is_set():
                        self.drain(db)
                        break
                    if client is None:
                        operation = "research_client"
                        client = create_client(os.environ["SUPABASE_URL"],
                            os.getenv("INTRADAY_SUPABASE_KEY") or os.environ["SUPABASE_KEY"],
                            options=SyncClientOptions(postgrest_client_timeout=10))
                    operation = "snapshot_jobs"
                    self.process_snapshot_jobs(db, client)
                    if time.monotonic() >= next_discovery:
                        operation = "universe_discovery"
                        next_discovery = time.monotonic() + 900
                        self.refresh_universe(client)
                        self.journal(db)
                        if self.stopping.is_set():
                            continue
                        # The local spool preserves union if cloud discovery is unavailable.
                        offset = 0
                        while True:
                            if self.stopping.is_set():
                                break
                            rows = client.table("intraday_capture_symbols").select("*").order(
                                "ticker").range(offset, offset + 499).execute().data or []
                            for row in rows:
                                old = self.symbols.get(row["ticker"], row)
                                merged = {"ticker": row["ticker"],
                                          "first_seen_at": min(old["first_seen_at"], row["first_seen_at"]),
                                          "last_seen_at": max(old["last_seen_at"], row["last_seen_at"])}
                                self.symbols[row["ticker"]] = merged
                                db.execute("INSERT OR REPLACE INTO symbols VALUES (?,?,?)",
                                    (merged["ticker"], merged["first_seen_at"], merged["last_seen_at"]))
                            if len(rows) < 500:
                                break
                            offset += 500
                        db.commit()
                        for offset in range(0, len(self.symbols), 100):
                            if self.stopping.is_set():
                                break
                            client.table("intraday_capture_symbols").upsert(
                                list(self.symbols.values())[offset:offset + 100],
                                on_conflict="ticker").execute()
                    if time.monotonic() >= next_sample:
                        operation = "quote_sample"
                        next_sample = time.monotonic() + self.sample_seconds
                        local = dt.datetime.now(NY)
                        if local.weekday() < 5 and (9, 30) <= (local.hour, local.minute) < (16, 0):
                            self.sample(http)
                    operation = "journal"
                    self.journal(db)
                    if self.stopping.is_set():
                        continue
                    operation = "capture_upload"
                    self.flush(db, client)
                    self.log_errors()
                    if time.monotonic() >= next_purge:
                        operation = "retention"
                        client.rpc("purge_intraday_capture",
                                   {"keep_days": self.retention_days}).execute()
                        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=self.retention_days)).isoformat()
                        db.execute("DELETE FROM symbols WHERE last_seen_at < ?", (cutoff,))
                        db.execute("DELETE FROM snapshot_jobs WHERE completed_at < ?", (cutoff,))
                        db.commit()
                        self.symbols = {t: r for t, r in self.symbols.items() if r["last_seen_at"] >= cutoff}
                        next_purge = time.monotonic() + 86400
                    if time.monotonic() >= next_health:
                        operation = "health_upload"
                        next_health = time.monotonic() + 15
                        self.health(db, client)
                    if time.monotonic() >= next_diagnostic:
                        next_diagnostic = time.monotonic() + 60
                        diagnostics.emit(self.health_id, "capture_progress", level="INFO", context={
                            "last_persisted_at": self.last_persisted_at,
                            "sequence": self.last_uploaded_sequence,
                            "pending_events": db.execute("SELECT count(*) FROM pending").fetchone()[0],
                            "dropped_events": self.dropped,
                        })
                except Exception as exc:
                    diagnostics.emit(self.health_id, "capture_worker_failed", error=exc,
                                     context={"operation": operation, "dropped_events": self.dropped})
                    # Never include transport exception text: it may contain the API key URL.
                    self.error(f"worker: {type(exc).__name__}")
                    self.emit("capture_gap", {"area": "worker", "complete": False,
                                             "reason": type(exc).__name__})
                    if self.stopping.is_set():
                        break
                    self.log_errors()
                    if client is None:
                        try:
                            client = create_client(os.environ["SUPABASE_URL"],
                                os.getenv("INTRADAY_SUPABASE_KEY") or os.environ["SUPABASE_KEY"],
                                options=SyncClientOptions(postgrest_client_timeout=10))
                        except Exception as health_exc:
                            diagnostics.emit(self.health_id, "capture_health_client_failed", error=health_exc)
                            self.error("Intraday capture health client unavailable")
                    if client:
                        try:
                            self.health(db, client)
                        except Exception as health_exc:
                            diagnostics.emit(self.health_id, "capture_health_upload_failed", error=health_exc,
                                             context={"table": "intraday_capture_health"})
                            self.error("Intraday capture health upload unavailable")
                    self.stopping.wait(5)
                self.stopping.wait(0.25)
        except Exception as exc:
            diagnostics.emit(self.health_id, "capture_worker_stopped", error=exc, level="CRITICAL",
                             context={"operation": operation})
            self.error(f"worker stopped: {type(exc).__name__}")
        finally:
            if db:
                try:
                    self.drain(db)
                finally:
                    db.close()
            else:
                self.drop_queued("shutdown without durable spool")
            if http:
                http.close()
