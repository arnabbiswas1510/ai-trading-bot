"""Independent, observation-only cloud watchdog and HYPOTHETICAL research reports.

Private read API: ``list_reports(store, limit=20)`` returns persisted report rows
(id, report_kind, period_start/end, status, body, payload, created_at/delivered_at).
Payload schema 1 contains coverage, shadow, calibration and evidence_warnings.
Only reporting-owned tables are writable. No brokerage or trading imports.
``save_calibration(store, artifact)`` archives a previously frozen evaluation;
``--save-calibration report.json`` exposes this explicit operator action. The
watchdog never selects, reruns or changes calibration settings.

Delivery is at-least-once: a crash after Telegram accepts a message but before its
receipt is stored can duplicate that message. Successful recipient receipts stop
ordinary retry duplicates. GitHub issues provide outage deduplication when the DB
is unavailable. GitHub cron is best-effort, not an exact-minute delivery promise.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
from collections import Counter
from zoneinfo import ZoneInfo

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research_diagnostics as diagnostics
from market_calendar import session_bounds
from research.intraday_reporting_delivery import (
    CALIBRATIONS, INCIDENTS, RECEIPTS, REPORTS, STATE, WRITE_TABLES,
    Issues, ReportingError, StorageError, Store, Telegram,
    deliver, fallback_alert, identity, safe_detail,
)

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
FRESH_SECONDS = 600
DECISION_FRESH_SECONDS = 1200
STARTUP_GRACE = dt.timedelta(minutes=10)
REPORT_DELAY = dt.timedelta(minutes=30)
LABEL = "HYPOTHETICAL — research only; NOT real trades."


class CollectionAttention(ReportingError):
    """Collection faults already have durable incidents and recipient receipts."""


class RuntimeBudgetExceeded(BaseException):
    """Bypass ordinary retry/rejection handlers so an expired worker must stop."""


def stamp(value):
    if not value:
        return None
    try:
        result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def fresh(value, now, seconds=FRESH_SECONDS):
    parsed = stamp(value)
    return parsed is not None and -60 <= (now - parsed).total_seconds() <= seconds


def list_reports(store, limit=20):
    return store.select(REPORTS, {"select": "*", "order": "created_at.desc,id.desc",
                                  "limit": min(max(int(limit), 1), 100)})


def saved_calibration(artifact):
    """Validate a saved frozen artifact, not its scientific independence."""
    if not isinstance(artifact, dict):
        raise ReportingError("Saved calibration must be an object")
    unsigned = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    try:
        digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
        training_start = stamp(artifact["training"]["observed_start"])
        training_end = stamp(artifact["training"]["observed_end"])
        holdout_start = stamp(artifact["holdout"]["observed_start"])
        holdout_end = stamp(artifact["holdout"]["observed_end"])
        if not training_start or not training_end or not holdout_start or not holdout_end:
            raise ValueError("Missing timezone-aware calibration dates")
        end = holdout_end.astimezone(NY).date().isoformat()
    except (KeyError, TypeError, ValueError):
        raise ReportingError("Saved calibration lacks valid dated windows or finite JSON") from None
    if (artifact.get("artifact_type") != "intraday_calibration_holdout"
            or artifact.get("version") != 1 or artifact.get("artifact_sha256") != digest
            or artifact.get("recommendation") != "research_only_manual_approval"
            or not artifact.get("selection_plan_sha256")
            or training_start > training_end
            or training_end >= holdout_start
            or holdout_end < holdout_start
            or artifact.get("holdout_candidates_tested") not in (0, 1)):
        raise ReportingError("Saved calibration is not an intact, frozen chronological evaluation")
    for key in ("baseline", "frozen_candidate"):
        trial = artifact.get(key) or {}
        if trial.get("status") not in ("modeled", "rejected"):
            raise ReportingError("Saved calibration has an invalid trial status")
        if trial["status"] == "modeled":
            summary = trial.get("summary") or {}
            if not all(_finite(summary.get(k)) for k in (
                    "final_equity_net", "equity_delta_vs_recorded_config_baseline",
                    "n_completed_positions", "n_distinct_sessions")):
                raise ReportingError("Saved calibration lacks finite sample/equity evidence")
            if any(type(summary[k]) is not int or summary[k] < 0
                   for k in ("n_completed_positions", "n_distinct_sessions")):
                raise ReportingError("Saved calibration sample sizes must be nonnegative integer counts")
    baseline = artifact["baseline"]
    candidate = artifact["frozen_candidate"]
    if baseline["status"] != "modeled" or baseline["summary"]["equity_delta_vs_recorded_config_baseline"] != 0:
        raise ReportingError("Saved calibration lacks a valid recorded-configuration baseline")
    if candidate["status"] == "modeled" and not math.isclose(
            candidate["summary"]["equity_delta_vs_recorded_config_baseline"],
            candidate["summary"]["final_equity_net"] - baseline["summary"]["final_equity_net"],
            abs_tol=1e-8):
        raise ReportingError("Saved calibration equity difference does not match its baseline")
    return {"id": digest, "holdout_end": end, "artifact": artifact}


def save_calibration(store, artifact):
    row = saved_calibration(artifact)
    store.put(CALIBRATIONS, row, ignore=True)
    return row["id"]


def due_periods(now, started_on):
    """All completed periods since the durable install date, oldest first."""
    local = now.astimezone(NY)
    day = started_on
    result = []
    while day <= local.date():
        bounds = session_bounds(day)
        if bounds and now >= bounds[1] + REPORT_DELAY:
            result.append({"id": "daily:" + day.isoformat(), "report_kind": "daily",
                           "period_start": day.isoformat(), "period_end": day.isoformat()})
        day += dt.timedelta(days=1)
    monday = started_on - dt.timedelta(days=started_on.weekday())
    while True:
        due = dt.datetime.combine(monday + dt.timedelta(days=7), dt.time(8), NY)
        if now < due:
            break
        result.append({"id": "weekly:" + monday.isoformat(), "report_kind": "weekly",
                       "period_start": monday.isoformat(),
                       "period_end": (monday + dt.timedelta(days=6)).isoformat()})
        monday += dt.timedelta(days=7)
    return sorted(result, key=lambda p: (p["period_end"], p["report_kind"]))


def expected_market(now):
    bounds = session_bounds(now.astimezone(NY).date())
    return bool(bounds and bounds[0] + STARTUP_GRACE <= now <= bounds[1])


def health_failures(now, observer, shadow, snapshot, quotes, shadow_output=None, shadow_decision=None,
                    runtime_mode="observe", calibration=None, calibration_required=False):
    """Heartbeat alone cannot prove broker, quote or shadow output progress."""
    if runtime_mode not in ("observe", "live"):
        raise ReportingError("Unsupported TRADING_RUNTIME_MODE; expected observe or live")
    failures = {}
    if calibration_required:
        if not calibration or not fresh(calibration.get("last_seen_at"), now, 1800):
            failures["calibration-heartbeat"] = "Calibration worker has no heartbeat within 30 minutes."
        elif calibration.get("status") in ("error", "blocked"):
            failures["calibration-progress"] = "Calibration worker reports a blocked or failed research cycle."
    if not expected_market(now):
        return failures
    if not observer or not fresh(observer.get("last_seen_at"), now):
        failures["observer-heartbeat"] = "Observer health row is missing or heartbeat is older than 10 minutes."
    if not observer or not fresh(observer.get("last_persisted_at"), now):
        failures["observer-output"] = "Observer has not persisted collection output in the last 10 minutes."
    if observer and (observer.get("config") or {}).get("spool_available") is False:
        failures["observer-spool"] = "Observer durable event spool is unavailable."
    broker = (snapshot or {}).get("payload") or {}
    if (not snapshot or not fresh(broker.get("snapshot_at"), now)
            or broker.get("complete") is not True or broker.get("connected") is False):
        failures["broker-snapshot"] = "Observer broker snapshot is missing, older than 10 minutes, disconnected or incomplete."
    quote = (quotes or {}).get("payload") or {}
    valid_quotes = quote.get("quotes") or []
    if (not quotes or not fresh(quotes.get("occurred_at"), now)
            or quote.get("complete") is not True or not valid_quotes
            or not all(fresh(q.get("provider_timestamp"), now) for q in valid_quotes)):
        failures["quote-coverage"] = "Observed quotes are missing, older than 10 minutes or incomplete."
    if not shadow or not fresh(shadow.get("last_seen_at"), now):
        failures["shadow-heartbeat"] = "Shadow-worker health row is missing or heartbeat is older than 10 minutes."
    if (not shadow or not fresh(shadow.get("last_cycle_at"), now)
            or not fresh(shadow.get("last_persisted_at"), now)
            or shadow.get("status") not in ("healthy", "ok", "running", "waiting")
            or not shadow_output or not fresh(shadow_output.get("occurred_at"), now)
            or shadow_output.get("kind") != "cycle"):
        failures["shadow-progress"] = ("Shadow progress lacks a valid persisted observation frame in the last "
                                       "10 minutes, or the worker reports a blocked/error state.")
    decision_frame = ((shadow_decision or {}).get("payload") or {}).get("frame") or {}
    event_types = {event.get("type") for event in (decision_frame.get("events") or [])
                   if isinstance(event, dict)}
    if (not shadow_decision or not fresh(shadow_decision.get("occurred_at"), now, DECISION_FRESH_SECONDS)
            or not {"buy_cycle", "monitor"} <= event_types):
        failures["shadow-progress"] = (
            "Shadow has no complete paired buy/monitor decision cycle in the last 20 minutes. "
            "Fresh five-minute quote frames and heartbeats do not prove decision progress.")
    return failures


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def source_coverage(events, periods, health):
    quotes = [e for e in events if e["kind"] == "quote_sample"]
    broker = [e for e in events if e["kind"] == "observer_snapshot"]
    frame_times = sorted(t for e in quotes if (t := stamp(e.get("occurred_at"))))
    gaps, broker_gaps, missing_sessions = [], [], []
    broker_times = sorted(t for e in broker if (t := stamp(e["payload"].get("snapshot_at"))))
    for day in periods:
        opened, closed = session_bounds(day)
        times = [t for t in frame_times if opened <= t <= closed]
        if not times:
            missing_sessions.append(day)
        points = [opened, *times, closed]
        gaps.extend({"session": day, "start": a.isoformat(), "end": b.isoformat(),
                     "seconds": int((b - a).total_seconds())}
                    for a, b in zip(points, points[1:])
                    if (b - a).total_seconds() > FRESH_SECONDS)
        points = [opened, *(t for t in broker_times if opened <= t <= closed), closed]
        broker_gaps.extend({"session": day, "start": a.isoformat(), "end": b.isoformat(),
                            "seconds": int((b - a).total_seconds())}
                           for a, b in zip(points, points[1:])
                           if (b - a).total_seconds() > FRESH_SECONDS)
    run_ids = {e.get("run_id") for e in events if e.get("run_id")}
    return {
        "sessions_expected": len(periods), "sessions_with_quotes": len({
            e["session"] for e in quotes if e.get("session") in periods}),
        "quote_frames": len(quotes),
        "complete_quote_frames": sum(e["payload"].get("complete") is True for e in quotes),
        "broker_snapshots": len(broker),
        "complete_broker_snapshots": sum(e["payload"].get("complete") is True for e in broker),
        "missing_quote_observations": sum(len(e["payload"].get("missing_quotes") or []) for e in quotes),
        "quote_sources": dict(Counter(q.get("source", "unavailable") for e in quotes
                                     for q in e["payload"].get("quotes", []))),
        "gaps_over_10min": gaps, "sessions_without_quotes": missing_sessions,
        "broker_gaps_over_10min": broker_gaps,
        "observed_runs": len(run_ids), "restart_events": sum(
            e["kind"] in ("observer_reconnect", "shadow_restart") for e in events),
        "process_restarts_at_least": max(0, len(run_ids) - 1),
        "capture_gap_events": sum(e["kind"] == "capture_gap" for e in events),
        "current_pending_events": (health or {}).get("pending_events"),
        "current_dropped_events": (health or {}).get("dropped_events"),
        "current_collector_last_seen_at": (health or {}).get("last_seen_at"),
        "current_collector_last_persisted_at": (health or {}).get("last_persisted_at"),
        "counter_scope": "Current collector lifetime counters; not attributed to this report period.",
    }


def _shadow_output(event):
    payload = event.get("payload") or {}
    output = payload.get("output", payload.get("decisions"))
    return output if isinstance(output, dict) else {}


def checkpoint_metrics(start, end, fills, marks, last):
    """Reconcile period metrics against preserved, same-run checkpoint counters."""
    if not start or not end:
        return {}, "Opening/closing checkpoints unavailable; period equity change, drawdown and concentration unavailable."
    try:
        for state in (start, end):
            unsigned = {k: v for k, v in state.items() if k != "state_sha256"}
            digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode()).hexdigest()
            if (state.get("origin") != "decision_only_shadow" or state.get("version") != 1
                    or digest != state.get("state_sha256")):
                raise ValueError("Invalid checkpoint")
            if not all(_finite(state.get(k)) for k in ("equity", "commission", "slippage", "closed_positions")):
                raise ValueError("Missing counters")
        for field in ("seed_fingerprint", "config_fingerprint", "engine_fingerprint"):
            if not start.get(field) or start[field] != end.get(field):
                raise ValueError("Different checkpoint lineage")
        if not math.isclose(end["equity"], last["equity"], abs_tol=1e-7):
            raise ValueError("End checkpoint differs from output")
        completed = end["closed_positions"] - start["closed_positions"]
        commission = end["commission"] - start["commission"]
        slippage = end["slippage"] - start["slippage"]
        if type(completed) is not int or completed < 0 or min(commission, slippage) < -1e-7:
            raise ValueError("Counters moved backwards")
        if not math.isclose(commission, sum(f["commission"] for f in fills), abs_tol=1e-7):
            raise ValueError("Commission ledger mismatch")
        if not math.isclose(slippage, sum(abs(f["price"] - f["quote"]) * f["shares"] for f in fills),
                            abs_tol=1e-7):
            raise ValueError("Slippage ledger mismatch")
        contributions = Counter()
        for sign, state in ((-1, start), (1, end)):
            for ticker, position in state["positions"].items():
                price = state["last_quotes"][ticker]["price"]
                if not _finite(price) or not _finite(position["shares"]):
                    raise ValueError("Missing position marks")
                contributions[ticker] += sign * position["shares"] * price
        for fill in fills:
            if fill.get("side") not in ("BUY", "SELL") or not fill.get("ticker"):
                raise ValueError("Incomplete fill attribution")
            sign = 1 if fill["side"] == "SELL" else -1
            contributions[fill["ticker"]] += sign * fill["price"] * fill["shares"] - fill["commission"]
        net = end["equity"] - start["equity"]
        if not math.isclose(sum(contributions.values()), net, abs_tol=1e-6):
            raise ValueError("Period equity attribution does not reconcile")
        peak, drawdown = start["equity"], 0.0
        if peak <= 0:
            raise ValueError("Nonpositive opening equity")
        for value in [*marks, end["equity"]]:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak * 100)
        positive = sorted((value for value in contributions.values() if value > 0), reverse=True)
        concentration = None
        if positive:
            concentration = {
                "scope": "Period hypothetical equity change by ticker, including open-position marks and modeled fees. "
                         "Winner exclusions are arithmetic subtraction, not replacement-portfolio simulations.",
                "largest_positive_share": positive[0] / sum(positive),
                "top_three_positive_share": sum(positive[:3]) / sum(positive),
                "net_excluding_top_three_positive_tickers": net - sum(positive[:3]),
                "by_ticker": dict(contributions),
            }
        return {"equity_pnl": net, "completed_positions": completed,
                "costs": {"commission": commission, "slippage": slippage},
                "drawdown": drawdown, "contribution_concentration": concentration}, None
    except (KeyError, TypeError, ValueError):
        return {}, "Checkpoint/period-fill evidence does not reconcile; period change, drawdown and concentration unavailable."


def shadow_performance(events, days, start_checkpoint=None, end_checkpoint=None):
    """Describe recorded validated outputs; never re-run or choose a strategy."""
    if not events:
        return {}, "Shadow performance unavailable: no recorded decision outputs."
    if len({e.get("run_id") for e in events}) != 1 or any(e["kind"] != "cycle" for e in events):
        return {}, "Shadow performance unavailable: multiple runs or rejected/gap events cannot be combined."
    ordered = sorted(events, key=lambda e: e["sequence"])
    if any(b["sequence"] != a["sequence"] + 1 for a, b in zip(ordered, ordered[1:])):
        return {}, "Shadow performance unavailable: recorded sequence is incomplete."
    outputs = []
    for event in ordered:
        output = _shadow_output(event)
        if (not isinstance(output, dict) or output.get("hypothetical") is not True
                or output.get("origin") != "decision_only_shadow"
                or not all(_finite(output.get(k)) for k in ("equity", "cash"))
                or not all(isinstance(output.get(k), list)
                           for k in ("positions", "fills", "position_sales", "equity_curve"))):
            return {}, "Shadow performance unavailable: complete validated portfolio outputs were not saved."
        outputs.append(output)
    for day in days:
        opened, closed = session_bounds(day)
        times = [t for output in outputs if (t := stamp(output.get("timestamp"))) and opened <= t <= closed]
        points = [opened, *times, closed]
        if not times or any((b - a).total_seconds() > FRESH_SECONDS for a, b in zip(points, points[1:])):
            return {}, "Shadow performance unavailable: decision coverage does not span the completed sessions."
    last = outputs[-1]
    sales = [s for output in outputs for s in output["position_sales"]]
    fills = [f for output in outputs for f in output["fills"]]
    if (any(not _finite(s.get("net_profit_loss")) or not isinstance(s.get("partial"), bool)
            or not s.get("ticker") or not s.get("buy_date") for s in sales)
            or any(not all(_finite(f.get(k)) for k in ("commission", "price", "quote", "shares"))
                   for f in fills)
            or any(not all(_finite(p.get(k)) for k in ("buy_price", "shares", "entry_fee_remaining"))
                   for p in last["positions"])):
        return {}, "Shadow performance unavailable: missing finite position/fill/cost evidence."
    marks = [m.get("equity") for output in outputs for m in output["equity_curve"]]
    if not marks or any(not _finite(value) or value <= 0 for value in marks):
        return {}, "Shadow performance unavailable: complete positive-equity marks were not saved."
    net = sum(s["net_profit_loss"] for s in sales)
    metrics = {
        "equity": last["equity"], "cash": last["cash"], "last_mark_at": last["timestamp"],
        "realized_pnl": net,
        "unrealized_pnl": last["equity"] - last["cash"] - sum(
            p["buy_price"] * p["shares"] + p["entry_fee_remaining"] for p in last["positions"]),
        "costs": {"commission": sum(f["commission"] for f in fills),
                  "slippage": sum(abs(f["price"] - f["quote"]) * f["shares"] for f in fills)},
        "equity_pnl": None, "drawdown": None,
        "completed_positions": sum(not s["partial"] for s in sales),
        "contribution_concentration": None,
        "performance_scope": "Equity/cash/unrealized at last observed mark; realized/costs/drawdown within "
                             "the report period. Costs are modeled, not actual broker charges. "
                             "Realized sales exclude pre-period partial sales; entry fees are charged at final close. "
                             "Drawdown is percent peak-to-trough of observed equity including the opening checkpoint, "
                             "not intrabar.",
    }
    anchored, warning = checkpoint_metrics(start_checkpoint, end_checkpoint, fills, marks, last)
    metrics.update(anchored)
    return metrics, warning


def build_report(period, events, shadow_events, observer, now, calibration=None, shadow_health=None,
                 runtime_mode="observe", start_checkpoint=None, end_checkpoint=None):
    days = []
    day = dt.date.fromisoformat(period["period_start"])
    end = dt.date.fromisoformat(period["period_end"])
    while day <= end:
        if session_bounds(day):
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    observer_runs = {e.get("run_id") for e in events
                     if e["kind"] in ("observer_config", "observer_snapshot")}
    events = [e for e in events if e.get("run_id") in observer_runs]
    coverage = source_coverage(events, days, observer)
    actions = Counter()
    reasons = Counter()
    fill_counts = Counter()
    for event in shadow_events:
        payload = event.get("payload") or {}
        output = _shadow_output(event)
        decisions = output.get("decisions", payload.get("decisions", [payload]))
        if isinstance(decisions, dict):
            decisions = decisions.get("decisions", [])
        for decision in decisions:
            action = decision.get("portfolio_action", decision.get("action"))
            if action in ("BUY", "HOLD", "SELL", "SKIP"):
                actions[action] += 1
                reasons[f"{action}: {decision.get('reason', 'unavailable')}"] += 1
        for fill in output.get("fills", []):
            if fill.get("side") in ("BUY", "SELL"):
                fill_counts[fill["side"]] += 1
    shadow = {"decisions": {a: actions[a] for a in ("BUY", "HOLD", "SELL", "SKIP")},
              "observed_fill_records": {side: fill_counts[side] for side in ("BUY", "SELL")},
              "reasons": dict(reasons), "equity": None, "cash": None,
              "realized_pnl": None, "unrealized_pnl": None, "costs": None,
              "equity_pnl": None, "drawdown": None, "completed_positions": None,
              "distinct_sessions": len({e["session"] for e in shadow_events if e.get("session")}),
              "observed_runs": len({e["run_id"] for e in shadow_events if e.get("run_id")}),
              "gap_events": sum(e["kind"] == "gap" for e in shadow_events),
              "current_pending_uploads": ((shadow_health or {}).get("metrics") or {}).get("pending_uploads"),
              "current_dropped_events": ((shadow_health or {}).get("metrics") or {}).get("dropped_events"),
              "contribution_concentration": None,
              "performance_scope": "Unavailable until a complete validated shadow portfolio export exists."}
    warnings = []
    if (coverage["sessions_without_quotes"] or coverage["gaps_over_10min"]
            or coverage["broker_gaps_over_10min"]):
        warnings.append("Collection is incomplete: missing sessions or quote/broker gaps over 10 minutes.")
    if not shadow_events:
        warnings.append("No shadow decisions were recorded; this is missing evidence, not zero profit.")
    performance, unavailable = shadow_performance(shadow_events, days, start_checkpoint, end_checkpoint)
    if unavailable:
        warnings.append(unavailable)
    if (coverage["sessions_without_quotes"] or coverage["gaps_over_10min"]
          or coverage["broker_gaps_over_10min"]
          or coverage["complete_quote_frames"] != coverage["quote_frames"]
          or coverage["complete_broker_snapshots"] != coverage["broker_snapshots"]
          or not coverage["broker_snapshots"] or coverage["capture_gap_events"]):
        warnings.append("Shadow performance withheld: source collection evidence is incomplete.")
    elif performance:
        shadow.update(performance)
    calibration_info = {
        "available": False, "recommendation_status": "none",
        "reason": "No calibrated recommendation: no saved frozen-calibration result is available.",
    }
    if calibration:
        try:
            saved_calibration(calibration)
            calibration_info = {"available": True, "saved_result": calibration,
                                "recommendation_status": "saved_result_requires_human_review",
                                "warning": "Descriptive saved result only. A digest is not a signature or "
                                           "independence proof. Reused holdout data is not independent."}
            if calibration["baseline"]["summary"].get("conditional_experiment"):
                calibration_info["warning"] += (
                    " Conditional comparison: both settings start from the same reproduced hypothetical "
                    "portfolio in each window—not independent actual-account snapshots or a continuous "
                    "candidate portfolio.")
        except ReportingError as exc:
            warnings.append(safe_detail(exc))
            calibration_info = {
                "available": False, "recommendation_status": "none",
                "reason": "No calibrated recommendation: saved calibration rejected for incomplete or invalid evidence.",
            }
    payload = {"schema": 1, "hypothetical": True, "period": period,
               "runtime_mode": runtime_mode,
               "expected_services": ["intraday-observer", "shadow-worker"],
               "mode_scope": "Compatibility label only; live entry permission is dashboard-controlled and not read by research.",
               "generated_at": now.isoformat(), "coverage": coverage, "shadow": shadow,
               "calibration": calibration_info, "evidence_warnings": warnings,
               "human_review_required": True,
               "schedule": "Best-effort 15-minute cloud cron; delayed runs catch up durable period keys."}
    body = render_report(period, payload)
    return {**period, "status": "pending", "body": body, "payload": payload,
            "created_at": now.isoformat()}


def render_report(period, payload):
    coverage, shadow = payload["coverage"], payload["shadow"]
    def shown(value):
        return "unavailable" if value is None else str(value)
    lines = [LABEL, f"{period['report_kind'].upper()} {period['period_start']} — {period['period_end']}",
             "Compatibility runtime label: " + payload.get("runtime_mode", "observe").upper() +
             "; observer + shadow always expected. Live entry permission is controlled separately in the dashboard.",
             f"Source sessions with quotes: {coverage['sessions_with_quotes']}/{coverage['sessions_expected']}; "
             f"complete quote frames {coverage['complete_quote_frames']}/{coverage['quote_frames']}; "
             f"broker snapshots {coverage['complete_broker_snapshots']}/{coverage['broker_snapshots']}.",
             f"Quote gaps >10min: {len(coverage['gaps_over_10min'])}; "
             f"broker gaps >10min: {len(coverage['broker_gaps_over_10min'])}; "
             f"explicit gaps: {coverage['capture_gap_events']}; reconnects: {coverage['restart_events']}; "
             f"process restarts at least: {coverage['process_restarts_at_least']}.",
             "Quote sources: " + (json.dumps(coverage["quote_sources"]) or "unavailable"),
             f"Current pending/dropped events: {shown(coverage['current_pending_events'])} / "
             f"{shown(coverage['current_dropped_events'])} (lifetime counters, not period totals).",
             "Shadow decision records: " + ", ".join(f"{k}={v}" for k, v in shadow["decisions"].items()) +
             ". These can include both a proposed action and its later execution notice.",
             "Observed simulated fill records: " +
             ", ".join(f"{k}={v}" for k, v in shadow["observed_fill_records"].items()) +
             ". Partial position sales are not additional completed positions.",
             f"Shadow runs: {shadow['observed_runs']}; gaps: {shadow['gap_events']}; "
             f"current pending uploads/dropped events: {shown(shadow['current_pending_uploads'])} / "
             f"{shown(shadow['current_dropped_events'])} (unreported counters are unavailable).",
             "Reasons: " + (", ".join(f"{k} ({v})" for k, v in shadow["reasons"].items()) or "unavailable"),
             f"Completed POSITIONS: {shadow['completed_positions'] if shadow['completed_positions'] is not None else 'unavailable'}; "
             f"distinct observed shadow sessions: {shadow['distinct_sessions']}.",
             "Hypothetical equity/cash/realized/unrealized/costs/drawdown: " +
             " / ".join(str(shadow[k]) if shadow[k] is not None else "unavailable"
                        for k in ("equity", "cash", "realized_pnl", "unrealized_pnl", "costs", "drawdown")),
             shadow["performance_scope"],
             "Hypothetical period equity change: " + shown(shadow["equity_pnl"]),
             "Contribution concentration (equity gain dominated by a few tickers): " +
             (json.dumps(shadow["contribution_concentration"]) if shadow["contribution_concentration"] else "unavailable"),
             "Calibration: " + ("saved frozen result available; human review required"
                                if payload["calibration"]["available"] else payload["calibration"]["reason"]),
             *payload["evidence_warnings"],
             "No automatic parameter selection or live promotion. Human review is mandatory.",
             "GitHub schedule is best-effort; delayed reports catch up. Full evidence is in the private report store."]
    if payload["calibration"]["available"]:
        artifact = payload["calibration"]["saved_result"]
        lines += [f"Saved frozen evaluation {artifact['artifact_sha256'][:12]}: "
                  f"training {artifact['training']['observed_start']}–{artifact['training']['observed_end']}, "
                  f"later evaluation {artifact['holdout']['observed_start']}–{artifact['holdout']['observed_end']}."]
        for key in ("baseline", "frozen_candidate"):
            trial = artifact[key]
            summary = trial.get("summary") or {}
            lines.append(f"{key}: {trial['status']}; completed positions "
                         f"{summary.get('n_completed_positions', 'unavailable')}, sessions "
                         f"{summary.get('n_distinct_sessions', 'unavailable')}, "
                         f"final hypothetical equity {summary.get('final_equity_net', 'unavailable')}, "
                         f"difference vs recorded-configuration simulation "
                         f"{summary.get('equity_delta_vs_recorded_config_baseline', 'unavailable')}.")
        lines.append(payload["calibration"]["warning"])
    return "\n".join(lines)


def rejected_report(period, now, error, runtime_mode="observe"):
    """Freeze a visible rejection instead of blocking every later catchup job."""
    reason = "Report evidence unavailable: " + safe_detail(error)
    return {**period, "status": "pending", "created_at": now.isoformat(),
            "body": f"{LABEL}\n{period['report_kind'].upper()} {period['period_start']} — "
                    f"{period['period_end']}\nRuntime at report generation: {runtime_mode.upper()}.\n"
                    f"{reason}\nSource counts and performance are unavailable, "
                    "not zero. Human review is required; no live changes are authorized.",
            "payload": {"schema": 1, "hypothetical": True, "period": period,
                        "runtime_mode": runtime_mode,
                        "expected_services": ["intraday-observer", "shadow-worker"],
                        "generated_at": now.isoformat(), "coverage": None, "shadow": None,
                        "calibration": {"available": False, "recommendation_status": "none", "reason": reason},
                        "evidence_warnings": [reason], "human_review_required": True,
                        "evidence_status": "rejected"}}


def _one(store, table, params):
    rows = store.select(table, {"select": "*", "limit": 1, **params})
    return rows[0] if rows else None


def load_health(store):
    observer = _one(store, "intraday_capture_health", {"id": "eq.intraday-observer"})
    shadow = _one(store, "intraday_shadow_health", {"id": "eq.shadow-worker"})
    calibration = _one(store, "intraday_calibration_health", {"id": "eq.calibration-worker"})
    source_run = ((observer or {}).get("config") or {}).get("run_id")
    shadow_run = (shadow or {}).get("run_id")
    latest = {}
    for name, kind in (("snapshot", "observer_snapshot"), ("quotes", "quote_sample")):
        latest[name] = (_one(store, "intraday_capture_events", {
            "kind": "eq." + kind, "run_id": "eq." + source_run,
            "order": "occurred_at.desc,id.desc"}) if source_run else None)
    latest["shadow_output"] = (_one(store, "intraday_shadow_events", {
        "run_id": "eq." + shadow_run, "order": "sequence.desc"})
        if shadow_run else None)
    latest["shadow_decision"] = (_one(store, "intraday_shadow_events", {
        "run_id": "eq." + shadow_run, "kind": "eq.cycle", "order": "sequence.desc",
        "payload->frame->events": 'cs.[{"type":"buy_cycle"},{"type":"monitor"}]'})
        if shadow_run else None)
    return {"observer": observer, "shadow": shadow, "calibration": calibration,
            "calibration_required": True, **latest}


def _attempt_notification(errors, operation, *args, **kwargs):
    """Notification transport errors must not stop independent durable work."""
    try:
        operation(*args, **kwargs)
        return True
    except StorageError:
        raise
    except ReportingError as exc:
        errors.append(safe_detail(exc))
        return False


def monitor(store, telegram, issues, now, health, runtime_mode="observe"):
    failures = health_failures(now, **health, runtime_mode=runtime_mode)
    existing = {r["issue_key"]: r for r in store.select(
        INCIDENTS, {"select": "*", "status": "eq.open"})}
    errors = []
    for key, message in failures.items():
        incident = existing.get(key)
        if not incident:
            incident = {"id": f"{key}:{now.isoformat()}", "issue_key": key,
                        "status": "open", "body": LABEL + "\nCOLLECTION FAILURE: " + message,
                        "payload": {"reason": message}, "opened_at": now.isoformat()}
            store.put(INCIDENTS, incident, ignore=True)
        _attempt_notification(errors, issues.ensure, key, incident["body"])
        try:
            deliver(store, telegram, "incident:" + incident["id"], incident["body"], now)
        except StorageError:
            raise
        except ReportingError as exc:
            errors.append(safe_detail(exc))
    # Overnight/weekends are unknown, not proof of recovery.
    if expected_market(now):
        for key, incident in existing.items():
            if key in failures:
                continue
            body = LABEL + f"\nRECOVERED: {key}. Fresh collection/output evidence is available."
            try:
                deliver(store, telegram, "recovery:" + incident["id"], body, now)
                issues.close(key)
                store.put(INCIDENTS, {**incident, "status": "resolved",
                                      "resolved_at": now.isoformat()})
            except StorageError:
                raise
            except ReportingError as exc:
                errors.append(safe_detail(exc))
    return failures, errors


def _period_events(store, table, period):
    params = {
        "select": "*", "order": "session,run_id,sequence",
        "and": f"(session.gte.{period['period_start']},session.lte.{period['period_end']})"}
    if table == "intraday_capture_events":
        params["kind"] = "in.(observer_snapshot,observer_config,quote_sample,observer_reconnect,capture_gap)"
    return store.all(table, params)


def period_checkpoints(store, events):
    if not events or len({event["run_id"] for event in events}) != 1:
        return None, None
    ordered = sorted(events, key=lambda event: event["sequence"])
    run_id = ordered[0]["run_id"]
    result = []
    for sequence in (ordered[0]["sequence"] - 1, ordered[-1]["sequence"]):
        row = _one(store, "intraday_shadow_checkpoints",
                   {"run_id": "eq." + run_id, "sequence": "eq." + str(sequence)})
        result.append(row.get("state") if row else None)
    return tuple(result)


def run(store, telegram, issues, now, runtime_mode="observe"):
    """One serialized sweep; bounded catchup continues on the next scheduled run."""
    worker = str(uuid.uuid4())
    if runtime_mode not in ("observe", "live"):
        raise ReportingError("Unsupported TRADING_RUNTIME_MODE; expected observe or live")
    if store.rpc("claim_intraday_reporting", worker) is not True:
        return {"leased_elsewhere": True, "runtime_mode": runtime_mode}
    try:
        health = load_health(store)
        failures, errors = monitor(store, telegram, issues, now, health, runtime_mode)
        state = _one(store, STATE, {"id": "eq.scheduler"})
        if not state:
            raise StorageError("Reporting scheduler state is missing; apply reporting migration")
        periods = due_periods(now, dt.date.fromisoformat(state["started_on"]))
        existing = {r["id"]: r for r in store.all(REPORTS, {"select": "id,status"})}
        completed = []
        outstanding = [p for p in periods if (existing.get(p["id"]) or {}).get("status") != "delivered"]
        new_periods = [p for p in outstanding if p["id"] not in existing][:6]
        generated = []
        # Persist new periods before attempting any report notification. A bad
        # recipient cannot keep the oldest pending rows at the head forever.
        for period in new_periods:
            try:
                events = _period_events(store, "intraday_capture_events", period)
                shadow_events = _period_events(store, "intraday_shadow_events", period)
                start_checkpoint, end_checkpoint = period_checkpoints(store, shadow_events)
                calibration = None
                if period["report_kind"] == "weekly":
                    saved = _one(store, CALIBRATIONS, {
                        "holdout_end": "lte." + period["period_end"],
                        "order": "holdout_end.desc,created_at.desc,id.desc"})
                    calibration = saved["artifact"] if saved else None
                report = build_report(period, events, shadow_events, health["observer"], now,
                                      calibration, health["shadow"], runtime_mode,
                                      start_checkpoint, end_checkpoint)
            except StorageError:
                raise
            except (ReportingError, ValueError, TypeError, KeyError) as exc:
                report = rejected_report(period, now, exc, runtime_mode)
            store.put(REPORTS, report, ignore=True)
            generated.append(report)
        retries = [p for p in outstanding if p["id"] in existing][:6 - len(generated)]
        delivery_batch = generated + [_one(store, REPORTS, {"id": "eq." + p["id"]}) for p in retries]
        for report in delivery_batch:
            if report is None:
                raise StorageError("A pending report disappeared before delivery")
            if report["payload"].get("evidence_status") == "rejected":
                _attempt_notification(errors, issues.ensure, "report-evidence:" + report["id"],
                                      "Research report evidence rejected; inspect the private report store.")
                errors.append("Research report evidence was rejected; see its persistent review issue")
            try:
                deliver(store, telegram, "report:" + report["id"], report["body"], now)
                store.put(REPORTS, {**report, "status": "delivered", "delivered_at": now.isoformat()})
                completed.append(report["id"])
            except StorageError:
                raise
            except ReportingError as exc:
                errors.append(safe_detail(exc))
        _attempt_notification(
            errors, fallback_alert, issues, telegram, "database",
            LABEL + "\nRECOVERED: private research database access restored.", recovery=True)
        if errors:
            _attempt_notification(errors, issues.ensure, "notification-delivery",
                                  LABEL + "\nResearch notification delivery requires attention.")
        else:
            _attempt_notification(errors, issues.close, "notification-delivery")
        if errors:
            raise ReportingError(f"Research monitoring needs attention: {len(failures)} collection faults, "
                                 f"{len(errors)} delivery faults")
        if failures:
            raise CollectionAttention(f"Research monitoring detected {len(failures)} collection faults; "
                                      "incident alerts are persisted and delivered")
        return {"reports_delivered": completed, "remaining_reports": len(outstanding) - len(completed),
                "runtime_mode": runtime_mode,
                "collection_monitoring": "expected"}
    finally:
        store.rpc("release_intraday_reporting", worker)


def main(env=None, http=requests, now=None):
    env = os.environ if env is None else env
    now = now or dt.datetime.now(UTC)
    telegram = Telegram(env.get("TELEGRAM_BOT_TOKEN", ""),
                        env.get("TELEGRAM_CHAT_IDS", "").split(","), http)
    issues = Issues(env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""), http)
    try:
        store = Store(env.get("SUPABASE_URL", ""), env.get("INTRADAY_SUPABASE_KEY", ""), http)
        result = run(store, telegram, issues, now, env.get("TRADING_RUNTIME_MODE") or "observe")
        if not result.get("leased_elsewhere"):
            fallback_alert(issues, telegram, "watchdog",
                           LABEL + "\nRECOVERED: independent reporting sweep succeeded. "
                           "Collection recovery is assessed during expected market hours, independently of live entry permission.",
                           recovery=True)
        print(json.dumps(result))
        diagnostics.emit("research-reporting", "reporting_sweep_completed", level="INFO")
        return 0
    except CollectionAttention as exc:
        diagnostics.emit("research-reporting", "collection_needs_attention", error=exc)
        print("::error::" + safe_detail(exc), file=sys.stderr)
        return 1
    except (Exception, RuntimeBudgetExceeded) as exc:
        diagnostics.emit("research-reporting", "reporting_sweep_failed", error=exc)
        detail = ("Reporting sweep exceeded its nine-minute runtime budget"
                  if isinstance(exc, RuntimeBudgetExceeded) else safe_detail(exc))
        print("::error::Intraday research reporting failed: " + detail, file=sys.stderr)
        key = "database" if isinstance(exc, StorageError) else "watchdog"
        try:
            fallback_alert(issues, telegram, key, LABEL + "\nIndependent watchdog failure: " + detail)
        except Exception as fallback_exc:
            diagnostics.emit("research-reporting", "reporting_fallback_failed", error=fallback_exc)
            print("::error::" + safe_detail(fallback_exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    # The DB lease outlives the workflow timeout. Manual runs are bounded too,
    # so a stalled previous owner cannot resume after its lease is reassigned.
    import signal
    def deadline(*_):
        raise RuntimeBudgetExceeded()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(540)
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-calibration", type=Path,
                        help="Archive an existing frozen evaluation; does not run or select experiments.")
    args = parser.parse_args()
    if args.save_calibration:
        try:
            with args.save_calibration.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ReportingError("Saved calibration exceeds 4 MiB")
            store = Store(os.getenv("SUPABASE_URL", ""), os.getenv("INTRADAY_SUPABASE_KEY", ""))
            print(save_calibration(store, json.loads(raw)))
        except Exception as exc:
            diagnostics.emit("research-reporting", "calibration_archive_failed", error=exc)
            print("::error::" + safe_detail(exc), file=sys.stderr)
            raise SystemExit(1)
    else:
        raise SystemExit(main())
