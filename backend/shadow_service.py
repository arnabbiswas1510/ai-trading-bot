"""Bounded read-only access to hypothetical portfolios and private research reports."""
import datetime as dt
import hashlib
import json
import re

import intraday_service as research
import shadow_engine


def _query(query):
    try:
        rows = query.execute().data
    except Exception as exc:
        research.log.error("Shadow research query failed (%s)", type(exc).__name__)
        raise research.ResearchUnavailable(
            "Shadow research is unavailable. Apply 20260930_add_intraday_shadow.sql "
            "and 20260930_add_intraday_reporting.sql; check the private research key and connectivity."
        ) from None
    if not isinstance(rows, list):
        raise research.ResearchUnavailable("Shadow research returned an invalid response.")
    if len(json.dumps(rows, allow_nan=False).encode()) > research.MAX_BYTES:
        raise ValueError("Research response exceeds the safe memory limit.")
    return rows


def _run(run_id):
    if not re.fullmatch(r"[a-f0-9]{64}", run_id):
        raise ValueError("Invalid shadow run identifier.")
    rows = _query(research.get_client().table("intraday_shadow_runs")
                  .select("*").eq("id", run_id).limit(1))
    if not rows:
        raise LookupError("Shadow run not found.")
    return rows[0]


def reports():
    return _query(research.get_client().table("intraday_research_reports")
                  .select("*").order("created_at", desc=True).limit(20))


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
    runs = _query(client.table("intraday_shadow_runs")
                  .select("id,created_at,status,engine_revision,latest_sequence")
                  .order("created_at", desc=True).limit(20))
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
