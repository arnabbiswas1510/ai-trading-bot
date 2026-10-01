"""Bounded, persisted research jobs. There is deliberately no live-setting writer."""
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import threading
import uuid
from collections import Counter
from zoneinfo import ZoneInfo

from supabase import create_client

import config
import database as db
from intraday_replay import build_dataset, run_comparison

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
MAX_ROWS = 50_000
MAX_BYTES = 64 * 1024 * 1024
PAGE_SIZE = 500
_client = None
_job_lock = threading.Lock()
_pending_lock = threading.Lock()
_pending_terminal = {}


class ResearchUnavailable(RuntimeError):
    pass


def get_client():
    global _client
    if _client is None:
        key = os.getenv("INTRADAY_SUPABASE_KEY") or db.SUPABASE_KEY
        if not db.SUPABASE_URL or not key:
            raise ResearchUnavailable("Intraday research database credentials are not configured.")
        try:
            _client = create_client(db.SUPABASE_URL, key)
        except Exception as exc:
            log.error("Intraday client initialization failed (%s)", type(exc).__name__)
            raise ResearchUnavailable(
                "Intraday research credentials or database URL are invalid; "
                "check the server-side configuration."
            ) from None
    return _client


def _query(query):
    try:
        return query.execute().data
    except Exception as exc:
        # SDK errors may include URLs; keep credentials out of UI and capture health.
        log.error("Intraday research database request failed (%s)", type(exc).__name__)
        raise ResearchUnavailable(
            "Intraday research database is unavailable. Apply "
            "20260930_add_intraday_research.sql and configure a server-side "
            "service-role INTRADAY_SUPABASE_KEY; check server logs/connectivity."
        ) from exc


def validate_window(start_date, end_date):
    if config.INTRADAY_CONFIG_ERRORS:
        raise ValueError(" ".join(config.INTRADAY_CONFIG_ERRORS))
    start = dt.date.fromisoformat(str(start_date))
    end = dt.date.fromisoformat(str(end_date))
    if end < start:
        raise ValueError("End date must not precede start date.")
    if (end - start).days + 1 > config.INTRADAY_REPLAY_MAX_DAYS:
        raise ValueError(
            f"Select at most {config.INTRADAY_REPLAY_MAX_DAYS} calendar days per comparison.")
    if end > dt.datetime.now(NY).date():
        raise ValueError("Future sessions cannot be replayed.")
    return start, end


def load_records(start_date, end_date):
    start, end = validate_window(start_date, end_date)
    lower = dt.datetime.combine(start, dt.time(), NY).isoformat()
    upper = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), NY).isoformat()
    client = get_client()
    prior = _query(client.table("intraday_capture_events").select("*")
                   .eq("kind", "portfolio_snapshot").lt("occurred_at", lower)
                   .order("occurred_at", desc=True).limit(1))
    records = list(prior)
    size = len(json.dumps(records).encode())
    offset = 0
    while True:
        batch = _query(client.table("intraday_capture_events").select("*")
                       .gte("occurred_at", lower).lt("occurred_at", upper)
                       .order("occurred_at").order("id")
                       .range(offset, offset + PAGE_SIZE - 1))
        size += len(json.dumps(batch).encode())
        if len(records) + len(batch) > MAX_ROWS or size > MAX_BYTES:
            raise ValueError("Capture exceeds this worker's safe memory limit; select a shorter period.")
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += len(batch)
    if not records or (len(records) == len(prior)):
        raise ValueError("No intraday observations have been recorded for this period.")
    return records


def _collector_health(row):
    settings = row.get("config") or {}
    last_seen = row.get("last_seen_at")
    stale = (not last_seen or
             (dt.datetime.now(dt.timezone.utc) -
              dt.datetime.fromisoformat(last_seen.replace("Z", "+00:00"))).total_seconds()
             > max(900, config.INTRADAY_SAMPLE_SECONDS * 3))
    return {
        "id": row["id"], "capture_mode": settings.get("capture_mode", "live"),
        "last_seen_at": last_seen, "latest_capture_at": row.get("last_persisted_at"),
        "heartbeat_stale": stale, "latest_error": row.get("last_error"),
        "pending_events": row.get("pending_events", 0),
        "dropped_events": row.get("dropped_events", 0),
    }


def status():
    _flush_terminal_updates()
    client = get_client()
    health = _query(client.table("intraday_capture_health").select("*")
                    .order("last_seen_at", desc=True).limit(20))
    sessions = _query(client.table("intraday_capture_sessions").select("*")
                     .order("session", desc=True).limit(366))
    runs = _query(client.table("intraday_replay_runs").select(
        "id,created_at,status,start_date,end_date,summary,error,automatic")
                  .order("created_at", desc=True).limit(20))
    health = [row for row in health if row["id"] in ("execution-agent", "intraday-observer")]
    health.sort(key=lambda row: dt.datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00"))
                if row.get("last_seen_at") else dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                reverse=True)
    collectors = [_collector_health(row) for row in health]
    h = health[0] if health else {}
    settings = h.get("config") or {}
    stale = collectors[0]["heartbeat_stale"] if collectors else True
    return {
        "enabled": settings.get("enabled", config.INTRADAY_CAPTURE_ENABLED),
        "retention_days": settings.get("retention_days", config.INTRADAY_RETENTION_DAYS),
        "sample_seconds": settings.get("sample_seconds", config.INTRADAY_SAMPLE_SECONDS),
        "latest_capture_at": h.get("last_persisted_at"),
        "latest_error": " ".join(config.INTRADAY_CONFIG_ERRORS) or h.get("last_error"),
        "latest_error_at": h.get("error_at"),
        "heartbeat_stale": stale,
        "pending_events": h.get("pending_events", 0),
        "dropped_events": h.get("dropped_events", 0),
        "sessions": sessions, "runs": runs,
        "collector_id": h.get("id"), "collectors": collectors,
        "capture_mode": settings.get("capture_mode", "live") if health else "unknown",
        "export_available": True, "raw_export_available": True,
        "max_replay_days": config.INTRADAY_REPLAY_MAX_DAYS,
        "approval_policy": "Research only. Human approval required for every live change.",
    }


def export_observations(start_date, end_date):
    """Archive evidence even when it cannot support a strategy replay."""
    records = load_records(start_date, end_date)
    counts = Counter(row["kind"] for row in records)
    quotes = [q for row in records if row["kind"] == "quote_sample"
              for q in row["payload"].get("quotes", [])]
    gaps = [row["id"] for row in records
            if row["kind"] == "capture_gap" or row["payload"].get("complete") is False]
    try:
        build_dataset(records, start_date, end_date)
        validation = {"status": "passed", "reason": "Inputs validated; a simulation may still reject unsupported activity."}
    except ValueError as exc:
        validation = {"status": "rejected", "reason": str(exc)}
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    oldest = min(dt.datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")) for row in records)
    return {
        "format": "intraday-observation-bundle-v1",
        "start_date": str(start_date), "end_date": str(end_date),
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "records_sha256": hashlib.sha256(canonical).hexdigest(),
        "records": records,
        "coverage": {
            "record_count": len(records), "event_kinds": dict(sorted(counts.items())),
            "distinct_sessions": len({row["session"] for row in records}),
            "distinct_runs": len({row["run_id"] for row in records}),
            "quote_observations": len(quotes),
            "quoted_symbols": sorted({q["ticker"] for q in quotes}),
            "incomplete_or_gap_event_ids": gaps,
        },
        "replay_input_validation": validation,
        "estimated_first_raw_expiry_at": (oldest + dt.timedelta(days=config.INTRADAY_RETENTION_DAYS)).isoformat(),
        "limitations": [
            "Raw observations are evidence, not a simulated strategy return or a guarantee of continuous coverage.",
            "Observer-only captures lack live buy/monitor decision inputs; do not invent those inputs.",
            "Export separately validated replay inputs for calibration; this raw bundle is not a schema-2 replay dataset.",
            "Expiry is an estimate using this server's retention setting; preserve this private export securely.",
            "No live changes are authorized by this report.",
        ],
    }


def get_run(run_id):
    try:
        identifier = str(uuid.UUID(run_id))
    except ValueError:
        raise ValueError("Invalid replay identifier.") from None
    _flush_terminal_updates()
    rows = _query(get_client().table("intraday_replay_runs").select("*")
                  .eq("id", identifier).limit(1))
    if not rows:
        raise LookupError("Replay not found.")
    return rows[0]


def _summary(result):
    baseline, variant = result.get("baseline", {}), result.get("variant", {})
    return {
        "baseline_final_equity": baseline.get("final_equity_net"),
        "variant_final_equity": variant.get("final_equity_net"),
        "difference": result.get("net_final_equity_difference"),
        "evidence": result.get("evidence"),
        "approval_policy": "research_only_manual_approval",
    }


def _execute_job(row):
    try:
        try:
            records = load_records(row["start_date"], row["end_date"])
            result = run_comparison(records, row["start_date"], row["end_date"])
            update = {"status": "completed", "result": result, "summary": _summary(result)}
        except ValueError as exc:
            update = {"status": "rejected", "error": str(exc)}
        except ResearchUnavailable as exc:
            update = {"status": "failed", "error": str(exc)}
        except Exception as exc:
            log.error("Intraday replay %s failed (%s)", row["id"], type(exc).__name__)
            update = {"status": "failed", "error": "Replay failed; inspect server logs before retrying."}
        update["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with _pending_lock:
            _pending_terminal[row["id"]] = update
        _flush_terminal_updates()
    except ResearchUnavailable:
        log.error("Could not persist replay completion for %s; its running state is not success.", row["id"])
    finally:
        _job_lock.release()


def submit(start_date, end_date, *, automatic=False):
    start, end = validate_window(start_date, end_date)
    if not _job_lock.acquire(blocking=False):
        raise BlockingIOError("A comparison is already running. Wait for it to finish.")
    row = {
        "id": str(uuid.uuid4()),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "status": "running", "automatic": automatic,
        "request": {"compare_without_ai_veto": True, "initial_state": "recorded_actual"},
        "engine_revision": os.getenv("GIT_COMMIT", "unknown"),
    }
    try:
        # Do not accumulate new jobs while a previous terminal state is unsaved.
        _flush_terminal_updates()
        _query(get_client().table("intraday_replay_runs").insert(row))
        threading.Thread(target=_execute_job, args=(row,), name="intraday-replay", daemon=True).start()
    except Exception:
        _job_lock.release()
        raise
    return {**row, "result": None, "error": None}


def export_dataset(start_date, end_date):
    return build_dataset(load_records(start_date, end_date), str(start_date), str(end_date))


def automatic_review():
    """Weekly expanding (up to 30-day) comparisons; rejection is a durable outcome."""
    if not config.INTRADAY_AUTO_COMPARE or os.getenv("TRADING_RUNTIME_MODE", "observe") != "live":
        return
    client = get_client()
    today = dt.datetime.now(NY).date()
    monday = today - dt.timedelta(days=today.weekday())
    week_start = dt.datetime.combine(monday, dt.time(), NY).isoformat()
    # Review only completed weeks; idempotence includes rejected attempts.
    recent = _query(client.table("intraday_replay_runs").select("id")
                    .eq("automatic", True).gte("created_at", week_start).limit(1))
    if recent:
        return
    sessions = _query(client.table("intraday_capture_sessions").select("session")
                     .gt("frames", 0).lt("session", monday.isoformat())
                     .order("session", desc=True).limit(1))
    if not sessions:
        return
    end = dt.date.fromisoformat(sessions[0]["session"])
    first = _query(client.table("intraday_capture_sessions").select("session")
                   .gt("frames", 0).order("session").limit(1))
    start = max(dt.date.fromisoformat(first[0]["session"]), end - dt.timedelta(days=29))
    submit(start.isoformat(), end.isoformat(), automatic=True)


async def scheduler():
    # This deployment runs one web worker; a held lock identifies its active job.
    while True:
        try:
            await asyncio.to_thread(_mark_interrupted)
            await asyncio.to_thread(automatic_review)
        except (ResearchUnavailable, ValueError, BlockingIOError) as exc:
            log.warning("Automatic intraday comparison unavailable: %s", exc)
        await asyncio.sleep(3600)


def _mark_interrupted():
    if not _job_lock.acquire(blocking=False):
        return
    try:
        _flush_terminal_updates()
        _query(get_client().table("intraday_replay_runs").update({
            "status": "failed", "error": "No active worker owns this interrupted comparison.",
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }).eq("status", "running"))
    finally:
        _job_lock.release()


def _flush_terminal_updates():
    with _pending_lock:
        updates = dict(_pending_terminal)
    for identifier, update in updates.items():
        _query(get_client().table("intraday_replay_runs").update(update).eq("id", identifier))
        with _pending_lock:
            _pending_terminal.pop(identifier, None)
