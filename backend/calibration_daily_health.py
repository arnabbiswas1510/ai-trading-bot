"""Manual, bounded, read-only evidence checks; never a trading authorization."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests

from market_calendar import session_bounds
from research_storage import research_rest_url, research_headers

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
MAX_ROWS = 2000
MAX_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT = (3, 8)
REQUEST_BUDGET_SECONDS = 45
DECISION_INTERVAL = dt.timedelta(minutes=15)
ACQUISITION_GRACE = dt.timedelta(seconds=120)
WORKFLOW_GRACE = dt.timedelta(hours=3)
REPORT_DELAY = dt.timedelta(minutes=30)
GITHUB = "https://api.github.com/repos/arnabbiswas1510/ai-trading-bot"
STAGES = ("Run Unit Tests", "Execute TradingView Fundamental Screener",
          "Execute Technical Filter", "Execute AI Evaluator")


class EvidenceUnavailable(RuntimeError):
    """Only static, credential-free messages cross the I/O boundary."""


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def fresh(value, now, seconds=600):
    parsed = stamp(value)
    return parsed is not None and -60 <= (now - parsed).total_seconds() <= seconds


@dataclass(frozen=True)
class Window:
    day: dt.date
    opened: dt.datetime
    closed: dt.datetime
    now: dt.datetime

    @property
    def complete(self):
        return self.now >= self.closed


def adjacent_session(day, direction):
    for offset in range(1, 15):
        candidate = day + dt.timedelta(days=offset * direction)
        if session_bounds(candidate):
            return candidate
    raise ValueError("NYSE calendar could not resolve an adjacent session.")


def window(session_date=None, now=None):
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Check time must include a timezone.")
    today = now.astimezone(NY).date()
    try:
        if session_date is None:
            day = today
            for _ in range(15):
                bounds = session_bounds(day)
                if bounds and now >= bounds[1]:
                    break
                day -= dt.timedelta(days=1)
            else:
                raise ValueError("No completed session.")
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session_date):
                raise ValueError("Invalid date.")
            day = dt.date.fromisoformat(session_date)
            bounds = session_bounds(day)
            if day > today or not bounds:
                raise ValueError("Not a past or current session.")
    except (ValueError, OverflowError):
        raise ValueError("Choose a past or current NYSE session within the installed calendar.") from None
    return Window(day, *bounds, now)


def check(identifier, title, status, summary, next_step="", scope="session", evidence_at=None):
    parsed = stamp(evidence_at)
    return dict(id=identifier, title=title, status=status, summary=summary,
                next_step=next_step, scope=scope,
                evidence_at=parsed.isoformat() if parsed else None)


def require(condition):
    if not condition:
        raise EvidenceUnavailable("Required evidence is missing or malformed; inspect the saved source.")


def count(value):
    require(type(value) is int and value >= 0)
    return value


class Reader:
    """Fixed GitHub routes and projected Supabase GETs, with byte/row limits."""

    def __init__(self):
        # Use the same server-side credentials as intraday_service, without its
        # diagnostic/write paths or status() terminal-update flush.
        if __package__:
            from . import database
        else:
            import database
        self.url = database.SUPABASE_URL
        self.key = database.SUPABASE_KEY
        self.deadline = time.monotonic() + REQUEST_BUDGET_SECONDS

    def _get(self, url, params, headers):
        if not hasattr(self, "deadline"):
            self.deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceUnavailable("Manual check reached its 45-second request budget; retry unavailable sources.")
        timeout = tuple(min(value, remaining) for value in HTTP_TIMEOUT)
        try:
            with requests.get(url, params=params, headers=headers, timeout=timeout,
                              stream=True, allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise EvidenceUnavailable(
                        "Source request refused or unavailable; check connectivity, access and API rate limits.")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    if time.monotonic() >= self.deadline:
                        raise EvidenceUnavailable(
                            "Manual check reached its 45-second request budget; no partial response assessed.")
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise EvidenceUnavailable("Source exceeds the 4 MiB safety bound; no partial result assessed.")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                return result, response.headers
        except (requests.RequestException, ValueError, UnicodeError):
            raise EvidenceUnavailable("Source request failed or returned invalid JSON; inspect service diagnostics.") from None

    def rows(self, table, fields, filters=None, *, order=None, limit=MAX_ROWS, latest=False):
        if table.startswith("intraday_"):
            try:
                url, headers = research_rest_url(), research_headers()
            except ValueError:
                raise EvidenceUnavailable("Research database access is not configured on the web server.") from None
        else:
            if table != "daily_triggers" or not self.url or not self.key:
                raise EvidenceUnavailable("Live source database access is not configured on the web server.")
            url = self.url.rstrip("/") + "/rest/v1"
            headers = {"apikey": self.key, "Authorization": "Bearer " + self.key}
        params = {"select": fields, "limit": limit, **(filters or {})}
        if order:
            params["order"] = order
        rows, headers = self._get(url.rstrip("/") + "/" + table, params, {
            **headers, "Prefer": "count=exact",
        })
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows))
        total = (headers.get("Content-Range") or "").rsplit("/", 1)[-1]
        require(total.isdigit())
        if not latest and int(total) != len(rows):
            raise EvidenceUnavailable("Source exceeds the row bound or was truncated; no partial result assessed.")
        require(len(rows) <= limit)
        return rows

    def workflow(self, start, end):
        params = {"created": f"{start.astimezone(UTC).isoformat()}..{end.astimezone(UTC).isoformat()}",
                  "per_page": 100, "branch": "main"}
        headers = {"Accept": "application/vnd.github+json"}
        result, _ = self._get(GITHUB + "/actions/workflows/daily_screener.yml/runs", params, headers)
        require(isinstance(result, dict) and isinstance(result.get("workflow_runs"), list))
        runs = result["workflow_runs"]
        require(count(result.get("total_count")) == len(runs) and len(runs) <= 100)
        for run in runs:
            require(isinstance(run, dict) and stamp(run.get("run_started_at"))
                    and type(run.get("id")) is int and type(run.get("run_attempt")) is int)
        runs = [r for r in runs if start <= stamp(r["run_started_at"]) < end]
        if not runs:
            return None
        run = max(runs, key=lambda r: (stamp(r["run_started_at"]), r["run_attempt"], r["id"]))
        jobs, _ = self._get(
            GITHUB + f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",
            {"per_page": 100}, headers)
        require(isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list))
        require(count(jobs.get("total_count")) == len(jobs["jobs"]) and len(jobs["jobs"]) <= 100)
        return {"run": run, "jobs": jobs["jobs"]}


def assess_workflow(data, w, *, upcoming=False):
    identifier = "screening-next-session" if upcoming else "screening-session-inputs"
    scope = "next_session" if upcoming else "session"
    title = "After-close screening for next session" if upcoming else "Screening before selected session"
    end = session_bounds(adjacent_session(w.day, 1))[0] if upcoming else w.opened
    start = w.closed if upcoming else session_bounds(adjacent_session(w.day, -1))[1]
    scheduled = dt.datetime.combine(start.astimezone(UTC).date(), dt.time(21), UTC)
    prefix = ("This after-close run prepares the NEXT session, not the selected session's intraday inputs. "
              if upcoming else "Only the screening window BEFORE the selected session is assessed. ")
    action = "Open Actions → Daily CANSLIM Trading Bot Screener; inspect the latest attempt's tests and skipped stages."
    if data is None:
        status = "wait" if w.now < min(end, scheduled + WORKFLOW_GRACE) else "fail"
        return check(identifier, title, status, prefix + "No run recorded in the expected window.",
                     action, scope)
    run, jobs = data["run"], data["jobs"]
    require(run.get("status") in ("queued", "in_progress", "completed", "waiting",
                                  "pending", "requested"))
    started = stamp(run.get("run_started_at"))
    require(started is not None and start <= started < end)
    if run["status"] != "completed":
        status = "wait" if w.now < min(end, scheduled + WORKFLOW_GRACE) else "fail"
        return check(identifier, title, status, prefix + "Latest attempt has not completed.",
                     action, scope, started.isoformat())
    steps = []
    states = ["success", "failure", "skipped", "cancelled", "timed_out", "neutral", "action_required",
              "startup_failure", "stale"]
    require(run.get("conclusion") in states)
    for job in jobs:
        require(isinstance(job, dict) and isinstance(job.get("steps"), list))
        steps.extend(job["steps"])
    results = {}
    for name in STAGES:
        matches = [s for s in steps if isinstance(s, dict) and s.get("name") == name]
        if not matches and run["conclusion"] != "success":
            results[name] = "not recorded"
            continue
        require(len(matches) == 1 and isinstance(matches[0].get("conclusion"), str))
        results[name] = matches[0]["conclusion"]
    good = run.get("conclusion") == "success" and all(v == "success" for v in results.values())
    if good:
        require(all(stamp(job.get("completed_at")) is not None for job in jobs))
        require(all(stamp(job["completed_at"]) <= w.now for job in jobs))
        good = all(stamp(job["completed_at"]) < end for job in jobs)
    # Never echo untrusted job text or arbitrary GitHub URLs.
    summary = "; ".join(f"{name}: {value if value in states + ['not recorded'] else 'unrecognized'}"
                        for name, value in results.items())
    if run.get("conclusion") == "success" and not good:
        summary += "; required stages were unsuccessful or completed after the preparation window"
    return check(identifier, title, "pass" if good else "fail", prefix + summary,
                 "" if good else action, scope, run.get("updated_at"))


def assess_report(rows, w):
    if not rows:
        status = "wait" if w.now < w.closed + REPORT_DELAY else "unknown"
        return [check("source-coverage", "Saved session source coverage", status,
                      "No saved daily report for this exact session.",
                      "Wait until 30 minutes after close, then inspect the research reporting workflow and Reports tab."),
                check("report-delivery", "Saved report delivery", status, "No exact-session report to deliver.",
                      "Inspect the research reporting workflow; this check never generates or sends a report.")]
    row = rows[0]
    require(row.get("period_start") == w.day.isoformat() == row.get("period_end")
            and row.get("report_kind") == "daily")
    created = stamp(row.get("created_at"))
    require(created is not None and created <= w.now and row.get("schema") == 1)
    c = row.get("coverage")
    require(isinstance(c, dict) and isinstance(row.get("evidence_warnings"), list)
            and all(isinstance(item, str) for item in row["evidence_warnings"]))
    for name in ("sessions_expected", "sessions_with_quotes", "quote_frames", "complete_quote_frames",
                 "broker_snapshots", "complete_broker_snapshots", "missing_quote_observations",
                 "capture_gap_events", "restart_events", "process_restarts_at_least", "observed_runs"):
        count(c.get(name))
    for name in ("gaps_over_10min", "broker_gaps_over_10min", "sessions_without_quotes"):
        require(isinstance(c.get(name), list))
    require(c["sessions_expected"] == 1 and c["sessions_with_quotes"] <= 1
            and c["complete_quote_frames"] <= c["quote_frames"]
            and c["complete_broker_snapshots"] <= c["broker_snapshots"])
    problems = (c["sessions_with_quotes"] != 1 or not c["quote_frames"] or not c["broker_snapshots"]
                or c["complete_quote_frames"] != c["quote_frames"]
                or c["complete_broker_snapshots"] != c["broker_snapshots"]
                or c["missing_quote_observations"] or c["capture_gap_events"]
                or c["gaps_over_10min"] or c["broker_gaps_over_10min"]
                or c["sessions_without_quotes"] or row["evidence_warnings"])
    summary = (f"Complete quotes {c['complete_quote_frames']}/{c['quote_frames']}; "
               f"broker snapshots {c['complete_broker_snapshots']}/{c['broker_snapshots']}; "
               f"quote/broker gaps >10 min {len(c['gaps_over_10min'])}/{len(c['broker_gaps_over_10min'])}; "
               f"explicit gaps {c['capture_gap_events']}; process restarts at least "
               f"{c['process_restarts_at_least']}; evidence warnings {len(row['evidence_warnings'])}.")
    status = "fail" if problems else ("pass" if w.complete and created >= w.closed else "wait")
    if w.complete and created < w.closed:
        summary += " Saved report predates close and cannot certify the completed session."
    require(row.get("status") in ("pending", "delivered"))
    receipt = stamp(row.get("delivered_at"))
    require(row.get("delivered_at") is None or (receipt is not None and created <= receipt <= w.now))
    delivered = row["status"] == "delivered" and receipt is not None
    return [check("source-coverage", "Saved session source coverage", status, summary,
                  ("Open this session's Reports evidence; missing/gapped evidence cannot support performance."
                   if problems else "Wait for the post-close daily report, then rerun this manual check.")
                  if status != "pass" else "", evidence_at=row["created_at"]),
            check("report-delivery", "Saved report delivery", "pass" if delivered else "wait",
                  "Delivery receipt saved." if delivered else "Report exists but delivery is not confirmed.",
                  "" if delivered else "Inspect reporting delivery receipts and Telegram/GitHub delivery diagnostics.",
                  evidence_at=row.get("delivered_at") or row["created_at"])]


def assess_shadow(rows, w, screening):
    title = "Actual shadow inputs and continuity"
    if not rows:
        return check("shadow-inputs", title, "unknown" if w.complete else "wait",
                     "No recorded shadow frames for the selected session.",
                     "Inspect Shadow activity and worker diagnostics; fresh heartbeats do not replace missing inputs.")
    runs, times, sequences, covered_slots = set(), [], [], set()
    closing_slot = w.closed - dt.timedelta(minutes=1)
    closing_mark = False
    cycles = candidates = end_marks = gaps = monitors = 0
    for row in rows:
        require(row.get("session") == w.day.isoformat() and isinstance(row.get("run_id"), str))
        at = stamp(row.get("occurred_at"))
        require(at is not None and at <= w.now and at.astimezone(NY).date() == w.day)
        runs.add(row["run_id"])
        times.append(at)
        sequences.append(count(row.get("sequence")))
        if row.get("kind") != "cycle":
            gaps += 1
            continue
        events = row.get("events")
        require(isinstance(events, list))
        event_types = set()
        for event in events:
            require(isinstance(event, dict) and isinstance(event.get("type"), str))
            if event["type"] in ("buy_cycle", "monitor", "end_mark"):
                require(stamp(event.get("timestamp")) == at)
                event_types.add(event["type"])
            if event["type"] == "buy_cycle":
                triggers = event.get("triggers")
                require(isinstance(triggers, list) and all(isinstance(t, dict) for t in triggers))
                cycles += 1
                candidates += len(triggers)
            elif event["type"] == "end_mark":
                end_marks += 1
                if closing_slot <= at <= closing_slot + ACQUISITION_GRACE:
                    closing_mark = True
            elif event["type"] == "monitor":
                monitors += 1
        if {"buy_cycle", "monitor"} <= event_types and at >= w.opened:
            slot = w.opened + ((at - w.opened) // DECISION_INTERVAL) * DECISION_INTERVAL
            if slot < w.closed and at <= slot + ACQUISITION_GRACE:
                covered_slots.add(slot)
    expected_slots = set()
    slot = w.opened
    while slot < w.closed:
        if w.now >= slot + ACQUISITION_GRACE:
            expected_slots.add(slot)
        slot += DECISION_INTERVAL
    missing_slots = expected_slots - covered_slots
    closing_matured = w.now >= closing_slot + ACQUISITION_GRACE
    times.sort()
    sequence_gap = len(runs) == 1 and sorted(sequences) != list(range(min(sequences), max(sequences) + 1))
    points = [w.opened, *[t for t in times if w.opened <= t <= min(w.closed, w.now)],
              min(w.closed, w.now)]
    time_gap = w.now >= w.opened and any((b - a).total_seconds() > 600 for a, b in zip(points, points[1:]))
    summary = (f"{cycles} buy cycles, {candidates} candidate inputs (repeated appearances counted), "
               f"{monitors} monitor cycles; {len(expected_slots & covered_slots)}/{len(expected_slots)} "
               f"matured 15-minute slots have paired buy/monitor inputs within 120 seconds; "
               f"{end_marks} end marks, {len(runs)} runs, {gaps} gap/recovery events. ")
    if missing_slots:
        status, reason = "fail", f"Missing timely paired decisions in {len(missing_slots)} matured slots; quote-only ticks are not decision coverage."
    elif closing_matured and not closing_mark:
        status, reason = "fail", "No end mark within the closing tick's 120-second acquisition window (scheduled one minute before NYSE close)."
    elif len(runs) != 1 or gaps or sequence_gap or time_gap:
        status, reason = "fail", "Selected-session continuity is incomplete; do not combine replaced runs."
    elif not cycles or not candidates:
        status = "fail" if screening["status"] == "fail" else ("wait" if screening["status"] == "pass" else "unknown")
        reason = ("All-empty inputs are not productive calibration evidence. "
                  "Successful screening may legitimately find no qualifiers; heartbeat/training-day counts do not prove it.")
    else:
        status, reason = ("pass" if w.complete and closing_mark else "wait"), "Candidate inputs exist; this is not proof of profitable decisions."
    return check("shadow-inputs", title, status, summary + reason,
                 "" if status == "pass" else "Compare this day's Shadow activity with the preceding screener attempt; inspect missing inputs and gaps.",
                 evidence_at=max(times).isoformat())


def assess_worker(rows, w, name):
    identifier = name + "-worker"
    title = "Current " + name + " worker"
    if not rows:
        return check(identifier, title, "unknown", "Current worker health row is missing.",
                     "Inspect the worker's persisted health and container logs; no restart is performed.", "current")
    row = rows[0]
    seen = stamp(row.get("last_seen_at"))
    require(seen is not None and "last_error" in row)
    status = "pass"
    details = []
    state = row.get("status")
    if not fresh(row["last_seen_at"], w.now, 1800 if name == "calibration" else 600):
        status = "fail"
        details.append("heartbeat is stale")
    if name != "observer":
        require(state in ("healthy", "ok", "running", "waiting", "error", "upload_error", "blocked", "disabled",
                          "waiting_for_data", "waiting_for_holdout", "deferred", "researching",
                          "no_change", "evaluating", "ready"))
        if state in ("error", "upload_error", "blocked"):
            status = "fail"
            details.append("worker reports an error/blocked state")
    if row.get("last_error") and not (name == "calibration" and state == "waiting_for_data"):
        status = "fail"
        details.append("a recorded error needs inspection (details withheld)")
    if name in ("observer", "shadow"):
        metrics = row if name == "observer" else row.get("metrics")
        require(isinstance(metrics, dict))
        pending = count(metrics.get("pending_events" if name == "observer" else "pending_uploads"))
        details.append(f"pending {pending}")
        if name == "observer":
            dropped = count(metrics.get("dropped_events"))
            details.append(f"lifetime dropped {dropped}")
            require(type(row.get("spool_available")) is bool and type(row.get("enabled")) is bool)
            if dropped or not row["spool_available"]:
                status = "fail"
                details.append("dropped records or unavailable durable spool")
            if not row["enabled"] and status == "pass":
                status = "wait"
                details.append("collection disabled")
        else:
            details.append("shadow does not publish a dropped-event counter")
        if pending and status == "pass":
            status = "wait"
        bounds = session_bounds(w.now.astimezone(NY).date())
        if bounds and bounds[0] + dt.timedelta(minutes=10) <= w.now <= bounds[1]:
            if not fresh(row.get("last_persisted_at"), w.now):
                status = "fail"
                details.append("no persisted output within 10 minutes during market hours")
    elif state in ("disabled", "waiting", "waiting_for_data", "waiting_for_holdout", "deferred", "researching"):
        if status == "pass":
            status = "wait"
        details.append({
            "disabled": "calibration is disabled",
            "waiting": "worker is waiting between scheduled research checks",
            "waiting_for_data": "worker is waiting for usable training observations",
            "waiting_for_holdout": "worker is waiting for reserved future-session observations",
            "deferred": "campaign is deferred for operator review",
            "researching": "research computation is in progress",
        }[state])
    return check(identifier, title, status, "Current state only: " + ("; ".join(details) or "recent heartbeat, no recorded errors."),
                 "" if status == "pass" else "Inspect current worker diagnostics and queue; do not infer selected-session coverage from liveness.",
                 "current", row["last_seen_at"])


def assess_settings(rows):
    require(len(rows) == 1 and isinstance(rows[0].get("value"), dict))
    value = rows[0]["value"]
    require(type(value.get("enabled")) is bool and "risk_policy" in value)
    for name in ("training_sessions", "evaluation_sessions"):
        require(count(value.get(name)) > 0)
    from research.auto_calibration import validate_settings
    try:
        validate_settings(value)
    except (ValueError, TypeError, KeyError):
        raise EvidenceUnavailable("Saved calibration settings or numeric risk policy are invalid.") from None
    enabled, policy = value["enabled"], value["risk_policy"] is not None
    return check("calibration-policy", "Current calibration settings and risk policy",
                 "pass" if enabled and policy else "wait",
                 f"Calibration {'enabled' if enabled else 'disabled'}; numeric risk policy {'present' if policy else 'unset'}. "
                 f"Requires {value['training_sessions']} training and {value['evaluation_sessions']} future evaluation sessions.",
                 "" if enabled and policy else "Review Calibration settings and define the numeric research policy before treating a campaign as eligible.",
                 "current")


def evaluation_evidence(selected, w):
    start, end = selected.get("evaluation_start"), selected.get("evaluation_end")
    require(isinstance(start, str) and isinstance(end, str))
    reserved = selected.get("evaluation_sessions")
    require(isinstance(reserved, list) and reserved
            and all(isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in reserved)
            and reserved == sorted(set(reserved)) and reserved[0] == start and reserved[-1] == end)
    try:
        bounds = {day: session_bounds(day) for day in reserved}
    except ValueError:
        raise EvidenceUnavailable("Campaign reserved dates are invalid.") from None
    require(all(bounds.values()) and start <= end)
    through = selected.get("last_evaluated_session")
    require(through is None or (isinstance(through, str) and through in reserved
                               and bounds[through][1] <= w.now))
    final = selected.get("evaluation_complete")
    require(final is None or type(final) is bool)
    require(through is None or type(final) is bool)
    completed = [day for day in reserved if bounds[day][1] + REPORT_DELAY <= w.now]
    return start, end, through, final, completed


def assess_campaigns(rows, runs, w):
    active = [r for r in rows if r.get("status") in ("evaluating", "deferred")]
    title = "Current campaign and reserved evaluation"
    require(runs and runs[0].get("status") in ("running", "blocked", "superseded")
            and isinstance(runs[0].get("id"), str))
    if runs[0]["status"] != "running":
        return check("calibration-campaign", title, "fail",
                     "The latest current shadow source is blocked or superseded; retained completed campaigns do not establish current readiness.",
                     "Inspect the latest Shadow run and recorded gap/recovery evidence before treating new sessions as usable.",
                     "current")
    if not rows:
        return check("calibration-campaign", title, "wait", "No campaign recorded; completed end marks alone do not prove useful training inputs.",
                     "Wait for complete training evidence; inspect the input-coverage check before counting training days.", "current")
    require(all(isinstance(r.get("status"), str) for r in rows))
    evaluating = [r for r in active if r["status"] == "evaluating"]
    if len(evaluating) > 1:
        return check("calibration-campaign", title, "fail", "Multiple evaluation campaigns are pending.",
                     "Review the calibration inbox; overlapping reserved evidence must not be silently reused.", "current")
    selected = evaluating[0] if evaluating else (active[0] if active else rows[0])
    state = selected["status"]
    status, summary = "wait", "A human review or additional research evidence is pending."
    if state == "blocked":
        status, summary = "fail", "A campaign is blocked; fresh worker heartbeats do not repair frozen evidence."
    elif state == "evaluating":
        require(runs and isinstance(selected.get("run_id"), str))
        if selected["run_id"] != runs[0].get("id") or runs[0].get("status") != "running":
            status, summary = "fail", "The frozen campaign source is blocked or replaced; a campaign cannot switch its source run."
        else:
            start, end, through, final, completed = evaluation_evidence(selected, w)
            summary = f"Reserved future evaluation {start} through {end}; last evaluated session {through or 'none'}."
            if final is True or through == end:
                status = "fail"
                summary += " Final evaluation evidence and the still-evaluating campaign status disagree."
            elif completed and (through is None or through < completed[-1]):
                status = "fail"
                summary += f" Daily evaluation progress is overdue through reserved session {completed[-1]} (close +30 minutes)."
            else:
                summary += " Waiting for reserved evidence is not failure or approval."
    elif state == "no_change":
        status, summary = "pass", "Latest recorded result is no_change: a legitimate research conclusion, not proof of profitability."
    elif state not in ("ready", "approved", "rejected", "deferred", "investigation_requested",
                       "investigation_approved", "needs_engine_support"):
        raise EvidenceUnavailable("Campaign status is unrecognized.")
    if state in ("no_change", "ready", "approved") and selected.get("evaluation_start") is not None:
        _, end, through, final, _ = evaluation_evidence(selected, w)
        baseline_only = state == "no_change" and through is None and final is None
        if not baseline_only and not (through == end and final is True):
            status, summary = "fail", "Recorded completed campaign lacks consistent final-session evidence and completion flag."
    return check("calibration-campaign", title, status, summary,
                 "" if status == "pass" else "Open the campaign's frozen dates and source evidence; blocked/replaced sources require a separately reviewed new campaign.",
                 "current", selected.get("updated_at"))


def daily_health(session_date=None, *, now=None, reader=None):
    w = window(session_date, now)
    reader = reader or Reader()
    checks = []

    def assess(identifier, title, scope, operation):
        try:
            result = operation()
            checks.extend(result if isinstance(result, list) else [result])
            return result
        except EvidenceUnavailable as exc:
            result = check(identifier, title, "unknown", str(exc),
                           "Inspect the named source's diagnostics and retry this manual check; other checks remain independent.", scope)
            checks.append(result)
            return result

    previous_close = session_bounds(adjacent_session(w.day, -1))[1]
    next_open = session_bounds(adjacent_session(w.day, 1))[0]
    before = assess("screening-session-inputs", "Screening before selected session", "session",
                    lambda: assess_workflow(reader.workflow(previous_close, w.opened), w))
    assess("screening-next-session", "After-close screening for next session", "next_session",
           lambda: assess_workflow(reader.workflow(w.closed, next_open) if w.complete else None, w, upcoming=True))

    def triggers():
        rows = reader.rows("daily_triggers", "triggered_at", order="triggered_at.desc", limit=1, latest=True)
        from research_configuration import TRIGGER_LOOKBACK_DAYS
        latest = rows[0].get("triggered_at") if rows else None
        require(not rows or isinstance(latest, str))
        if latest is not None:
            require(isinstance(latest, str))
            try:
                day = dt.date.fromisoformat(latest[:10])
            except (TypeError, ValueError):
                raise EvidenceUnavailable("Latest trigger date is malformed.") from None
            require(day <= w.now.astimezone(NY).date())
            recent = day >= w.now.astimezone(NY).date() - dt.timedelta(days=TRIGGER_LOOKBACK_DAYS)
        else:
            recent = False
        # These rows are mutable current data: no historical feed certification.
        status = "pass" if recent else "wait"
        return check("trigger-freshness", "Current trigger freshness", status,
                     f"Latest stored trigger date: {str(day) if latest else 'none'}; "
                     f"{TRIGGER_LOOKBACK_DAYS}-calendar-day lookback {'contains rows' if recent else 'has no fresh rows'}. "
                     "Rows are not proof of a completed scan; no rows may mean no qualifiers or a broken feed.",
                     "Compare current screener attempts with trigger dates; never count all-empty days as productive calibration."
                     if not recent else "", "current", latest if stamp(latest) else None)

    assess("trigger-freshness", "Current trigger freshness", "current", triggers)
    reports = assess("source-coverage", "Saved session source coverage", "session", lambda: assess_report(
        reader.rows("intraday_research_reports",
                    "report_kind,period_start,period_end,status,created_at,delivered_at,"
                    "schema:payload->schema,coverage:payload->coverage,evidence_warnings:payload->evidence_warnings",
                    {"report_kind": "eq.daily", "period_start": "eq." + w.day.isoformat(),
                     "period_end": "eq." + w.day.isoformat()},
                    order="created_at.desc", limit=1, latest=True), w))
    if isinstance(reports, dict):
        checks.append(check("report-delivery", "Saved report delivery", "unknown",
                            "Report source is unavailable or malformed; delivery cannot be assessed.",
                            "Inspect the exact-session saved report and delivery receipt."))
    assess("shadow-inputs", "Actual shadow inputs and continuity", "session", lambda: assess_shadow(
        reader.rows("intraday_shadow_events",
                    "run_id,sequence,session,occurred_at,kind,events:payload->frame->events",
                    {"session": "eq." + w.day.isoformat()}, order="run_id,sequence"), w, before))
    for name, table, fields, identifier in (
        ("observer", "intraday_capture_health",
         "last_seen_at,last_persisted_at,last_error,pending_events,dropped_events,"
         "spool_available:config->spool_available,enabled:config->enabled", "intraday-observer"),
        ("shadow", "intraday_shadow_health",
         "last_seen_at,last_persisted_at,status,last_error,metrics", "shadow-worker"),
        ("calibration", "intraday_calibration_health", "last_seen_at,status,last_error", "calibration-worker"),
    ):
        assess(name + "-worker", "Current " + name + " worker", "current",
               lambda name=name, table=table, fields=fields, identifier=identifier: assess_worker(
                   reader.rows(table, fields, {"id": "eq." + identifier}, limit=1), w, name))
    assess("calibration-policy", "Current calibration settings and risk policy", "current",
           lambda: assess_settings(reader.rows("intraday_calibration_settings", "value", {"id": "eq.default"}, limit=1)))
    assess("calibration-campaign", "Current campaign and reserved evaluation", "current", lambda: assess_campaigns(
        reader.rows("intraday_calibration_proposals",
                    "status,updated_at,run_id:artifact->>run_id,evaluation_start:artifact->>evaluation_start,"
                    "evaluation_end:artifact->>evaluation_end,evaluation_sessions:artifact->evaluation_sessions,"
                    "evaluation_complete:artifact->evaluation_complete,"
                    "last_evaluated_session:artifact->>last_evaluated_session",
                    order="created_at.desc", limit=200),
        reader.rows("intraday_shadow_runs", "id,status", order="created_at.desc", limit=1, latest=True), w))
    rank = {"pass": 0, "wait": 1, "unknown": 2, "fail": 3}
    statuses = [c["status"] for c in checks]
    if not w.complete:
        statuses.append("wait")
    return {"schema_version": 1, "checked_at": w.now.isoformat(), "timezone": str(NY),
            "session_date": w.day.isoformat(), "session_complete": w.complete,
            "overall_status": max(statuses, key=rank.get), "checks": checks,
            "limitations": [
                "Manual read-only diagnostic: no settings, orders, restarts, report generation or approvals.",
                "A passing pipeline does not prove profitability, select a candidate, or authorize trading.",
                "Current workers, triggers and campaign state are not historical session evidence.",
                "After-close screening prepares the next session; it cannot validate earlier intraday inputs.",
                "Saved report warnings are counted, not echoed. Open Reports for the original detailed evidence.",
                "Projected session inputs are not a full replay or verification of a campaign's complete training history.",
                "GitHub run creation windows cannot certify reruns of older runs created outside those windows.",
                "Bounds: 2,000 projected event rows, 200 proposals, 100 GitHub runs/jobs and 4 MiB per response; incomplete reads are UNKNOWN.",
                "Network budget: 45 seconds shared across sources; remaining sources become UNKNOWN when exhausted.",
            ]}
