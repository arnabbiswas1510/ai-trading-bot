"""Bounded read-only access to hypothetical portfolios and private research reports."""
import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import intraday_service as research
import shadow_engine
from market_calendar import session_bounds


def _query(query):
    try:
        rows = query.execute().data
    except Exception as exc:
        research.log.error("Shadow research query failed (%s)", type(exc).__name__)
        detail = research.report_failure("shadow_query_failed", exc, query)
        raise research.ResearchUnavailable(
            "Shadow research is unavailable. Apply 20260930_add_intraday_shadow.sql "
            f"and 20260930_add_intraday_reporting.sql; check the private research key and connectivity. {detail}"
        ) from None
    if not isinstance(rows, list):
        raise research.ResearchUnavailable("Shadow research returned an invalid response.")
    if len(json.dumps(rows, allow_nan=False).encode()) > research.MAX_BYTES:
        raise ValueError("Research response exceeds the safe memory limit.")
    return rows


def _run(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{64}", run_id):
        raise ValueError("Invalid shadow run identifier.")
    rows = _query(research.get_client().table("intraday_shadow_runs")
                  .select("*").eq("id", run_id).limit(1))
    if not rows:
        raise LookupError("Shadow run not found.")
    return rows[0]


def reports():
    return _query(research.get_client().table("intraday_research_reports")
                  .select("*").order("created_at", desc=True).limit(20))


def _start_details(seed):
    if not isinstance(seed, dict):
        raise ValueError("Shadow run has malformed starting evidence.")
    seed_at = seed.get("timestamp")
    first_session = None
    if seed_at is not None:
        if not isinstance(seed_at, str):
            raise ValueError("Shadow start timestamp must be text.")
        start = dt.datetime.fromisoformat(seed_at.replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("Shadow start timestamp must include a timezone.")
        day = start.astimezone(ZoneInfo("America/New_York")).date()
        for offset in range(370):
            candidate = day + dt.timedelta(days=offset)
            bounds = session_bounds(candidate)
            if bounds and bounds[0] >= start:
                first_session = candidate.isoformat()
                break
        if first_session is None:
            raise ValueError("Calendar cannot resolve an eligible full research session.")
    evidence = seed.get("source_evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("Shadow run has malformed source evidence.")
    recovery = evidence.get("recovery")
    if recovery is not None:
        if (not isinstance(recovery, dict) or recovery.get("mode") != "automatic"
                or not re.fullmatch(r"[a-f0-9]{64}", str(recovery.get("previous_run_id", "")))):
            raise ValueError("Shadow replacement provenance is invalid.")
        recovery = {key: recovery.get(key) for key in
                    ("mode", "previous_run_id", "requested_at", "reason_code")}
    return {"seed_at": seed_at, "earliest_full_session": first_session, "recovery": recovery}


def status():
    client = research.get_client()
    health = _query(client.table("intraday_shadow_health").select("*")
                    .eq("id", "shadow-worker").limit(1))
    now = dt.datetime.now(dt.timezone.utc)
    for row in health:
        seen = row.get("last_seen_at")
        row["heartbeat_stale"] = not seen or (
            now - dt.datetime.fromisoformat(seen.replace("Z", "+00:00"))
        ).total_seconds() > 600
    fields = ("id", "created_at", "status", "engine_revision", "latest_sequence")
    rows = _query(client.table("intraday_shadow_runs")
                  .select(",".join(fields) + ",initial_state")
                  .order("created_at", desc=True).limit(20))
    runs = [{**{key: row.get(key) for key in fields},
             **_start_details(row["initial_state"])} for row in rows]
    portfolio = None
    if runs:
        row = runs[0]
        checkpoints = _query(client.table("intraday_shadow_checkpoints").select("*")
                             .eq("run_id", row["id"]).lte("sequence", row["latest_sequence"])
                             .order("sequence", desc=True).limit(1))
        if checkpoints:
            saved = checkpoints[0]
            state = saved["state"]
            portfolio = {
                "run_id": row["id"], "sequence": saved["sequence"], "hypothetical": True,
                **{key: state.get(key) for key in (
                    "cash", "positions", "equity", "max_drawdown_pct", "commission",
                    "slippage", "closed_positions", "last_timestamp",
                )},
            }
    return {"health": health, "runs": runs, "portfolio": portfolio, "reports": reports(),
            "approval_policy": "Hypothetical research only; no live changes or restart approval."}


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _state_digest(state):
    return hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _activity_portfolio(client, run, through_sequence):
    saved = _query(client.table("intraday_shadow_checkpoints").select("*")
                   .eq("run_id", run["id"]).lte("sequence", through_sequence)
                   .order("sequence", desc=True).limit(1))
    if not saved:
        return None
    checkpoint = saved[0]
    sequence = checkpoint.get("sequence")
    state = checkpoint.get("state")
    if (checkpoint.get("run_id") != run["id"] or type(sequence) is not int
            or not 0 <= sequence <= through_sequence or not isinstance(state, dict)):
        raise ValueError("Malformed shadow checkpoint.")
    unsigned = {key: value for key, value in state.items() if key != "state_sha256"}
    if state.get("state_sha256") != _state_digest(unsigned):
        raise ValueError("Shadow checkpoint integrity mismatch.")
    # Check recorded provenance, not compatibility with today's execution engine.
    if (state.get("seed") != run.get("initial_state")
            or state.get("effective_config") != run.get("effective_config")
            or state.get("engine_fingerprint") != run.get("engine_revision")
            or state.get("seed_fingerprint") != _state_digest(run.get("initial_state"))
            or state.get("config_fingerprint") != _state_digest(run.get("effective_config"))):
        raise ValueError("Shadow checkpoint provenance does not match the selected run.")
    if sequence:
        events = _query(client.table("intraday_shadow_events")
                        .select("run_id,sequence,kind,state_sha256")
                        .eq("run_id", run["id"]).eq("sequence", sequence).limit(1))
        if (not events or events[0].get("run_id") != run["id"]
                or events[0].get("sequence") != sequence or events[0].get("kind") != "cycle"
                or events[0].get("state_sha256") != _state_digest(state)):
            raise ValueError("Shadow checkpoint does not match its recorded cycle.")
    elif state.get("frame_count") != 0:
        raise ValueError("Shadow seed checkpoint contains later activity.")
    return {
        "run_id": run["id"], "sequence": sequence, "hypothetical": True,
        **{key: state.get(key) for key in (
            "cash", "positions", "equity", "max_drawdown_pct", "commission",
            "slippage", "closed_positions", "last_timestamp",
        )},
    }


def activity(run_id, limit=50, before_sequence=None, through_sequence=None):
    """Read recorded output at a fixed published watermark; never replay a strategy."""
    _positive_integer(limit, "limit")
    if limit > 100:
        raise ValueError("limit must not exceed 100.")
    for name, value in (("before_sequence", before_sequence), ("through_sequence", through_sequence)):
        if value is not None:
            _positive_integer(value, name)
    run = _run(run_id)
    latest = run.get("latest_sequence")
    if (run.get("id") != run_id or type(latest) is not int or latest < 0
            or not isinstance(run.get("status"), str) or not run["status"]):
        raise ValueError("Malformed shadow run publication metadata.")
    if through_sequence is None:
        through_sequence = latest
    elif through_sequence > latest:
        raise ValueError("through_sequence exceeds the published shadow sequence.")
    client = research.get_client()
    query = (client.table("intraday_shadow_events")
             .select("run_id,sequence,session,occurred_at,kind,output:payload->output,"
                     "reason:payload->>reason,new_run_id:payload->>new_run_id")
             .eq("run_id", run_id).lte("sequence", through_sequence))
    if before_sequence is not None:
        query = query.lt("sequence", before_sequence)
    rows = _query(query.order("sequence", desc=True).limit(limit + 1))
    events = []
    previous = min(through_sequence + 1, before_sequence or through_sequence + 1)
    if not rows and previous > 1:
        raise ValueError(f"Published shadow activity is missing expected sequence {previous - 1}.")
    for row in rows:
        sequence = row.get("sequence")
        if (row.get("run_id") != run_id or type(sequence) is not int
                or not 0 < sequence < previous
                or not isinstance(row.get("session"), str)
                or not isinstance(row.get("occurred_at"), str)):
            raise ValueError("Malformed or unordered shadow activity.")
        if sequence != previous - 1:
            raise ValueError(f"Published shadow activity is missing expected sequence {previous - 1}.")
        previous = sequence
        kind = row.get("kind")
        details = {}
        if kind in ("gap", "recovery_queued", "run_recovered", "recovery_blocked"):
            output = {"decisions": [], "fills": [], "equity_curve": []}
            reason, new_run = row.get("reason"), row.get("new_run_id")
            if reason is not None:
                if not isinstance(reason, str):
                    raise ValueError("Recorded recovery reason must be text.")
                details["reason"] = reason
            if new_run is not None:
                if not isinstance(new_run, str) or not re.fullmatch(r"[a-f0-9]{64}", new_run):
                    raise ValueError("Recorded replacement run identifier is invalid.")
                details["new_run_id"] = new_run
        elif kind == "cycle":
            output = row.get("output")
            if not isinstance(output, dict) or any(
                not isinstance(output.get(key), list)
                or any(not isinstance(item, dict) for item in output[key])
                for key in ("decisions", "fills", "equity_curve")
            ):
                raise ValueError(f"Shadow cycle {sequence} has malformed recorded output.")
        else:
            raise ValueError(f"Unsupported shadow activity kind at sequence {sequence}.")
        events.append({
            "sequence": sequence, "session": row["session"], "occurred_at": row["occurred_at"],
            "kind": kind, **{key: output[key] for key in ("decisions", "fills", "equity_curve")},
            **details,
        })
    page = events[:limit]
    coverage_warning = None
    if page and len(events) <= limit and page[-1]["sequence"] > 1:
        coverage_warning = (
            f"Recorded history before sequence {page[-1]['sequence']} is unavailable. "
            "The earlier prefix may be pruned or missing; this is not complete history."
        )
    result = {
        "run_id": run_id, "through_sequence": through_sequence, "events": page,
        "next_before_sequence": page[-1]["sequence"] if len(events) > limit else None,
        "portfolio": _activity_portfolio(client, run, through_sequence),
        "run_status": run["status"],
        "coverage_warning": coverage_warning,
        "scope": (
            "Recorded hypothetical decisions, simulated fills and equity marks only; no real orders. "
            "A gap means missing simulation coverage, not that no trades occurred. "
            "The portfolio is the latest verified checkpoint at or before the fixed published sequence. "
            "Run status is current; this read does not replay strategies or change research or live state."
        ),
    }
    if len(json.dumps(result, allow_nan=False).encode()) > research.MAX_BYTES:
        raise ValueError("Research response exceeds the safe memory limit.")
    return result


def export_dataset(run_id, start_date, end_date):
    start, end = research.validate_window(start_date, end_date)
    run = _run(run_id)
    if run["initial_state"].get("config") != run["effective_config"]:
        raise ValueError("Shadow seed and saved configuration disagree.")
    if run["engine_revision"] != shadow_engine.engine_fingerprint():
        raise ValueError("Shadow engine/environment changed. Export with the matching archived research image.")
    client = research.get_client()
    rows, offset, size = [], 0, len(json.dumps(run).encode())
    while True:
        batch = _query(client.table("intraday_shadow_events").select("*")
                       .eq("run_id", run_id).lte("session", end.isoformat())
                       .lte("sequence", run["latest_sequence"]).order("sequence")
                       .range(offset, offset + research.PAGE_SIZE - 1))
        size += len(json.dumps(batch, allow_nan=False).encode())
        if len(rows) + len(batch) > research.MAX_ROWS or size > research.MAX_BYTES:
            raise ValueError("Complete shadow history exceeds export limits; do not truncate its required prefix.")
        rows.extend(batch)
        if len(batch) < research.PAGE_SIZE:
            break
        offset += len(batch)
    if not rows:
        raise ValueError("No published shadow decisions exist for this window.")
    records = []
    for sequence, row in enumerate(rows, 1):
        if row["sequence"] != sequence:
            raise ValueError("Shadow history is missing a predecessor; cannot reproduce hypothetical holdings.")
        if row["kind"] != "cycle":
            raise ValueError("Shadow history contains a gap or unsupported event.")
        payload = row["payload"]
        if not isinstance(payload, dict) or not {"frame", "output"} <= payload.keys():
            raise ValueError("Shadow cycle is missing its inputs or recorded output.")
        records.append({"frame": payload["frame"], "output": payload["output"]})
    saved = _query(client.table("intraday_shadow_checkpoints").select("*")
                   .eq("run_id", run_id).eq("sequence", rows[-1]["sequence"]).limit(1))
    if not saved:
        raise ValueError("Final hypothetical checkpoint has not been published; retry after upload completes.")
    state = saved[0]["state"]
    digest = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if digest != rows[-1]["state_sha256"]:
        raise ValueError("Published shadow checkpoint does not match its recorded cycle.")
    records[-1]["checkpoint"] = state
    return shadow_engine.export_shadow_dataset(run["initial_state"], records, start, end)
