"""Scheduled, research-only campaigns using frozen future evaluation windows."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import threading
import uuid
from zoneinfo import ZoneInfo

import research_diagnostics as diagnostics
import shadow_engine
from market_calendar import session_bounds
from research import auto_calibration, calibrate_intraday
from research.calibration_risk_report import build_risk_report, refresh_risk_reference
from research.calibration_store import CalibrationStore, Conflict
from research.intraday_reporting_delivery import (
    Store as DeliveryStore, Telegram, deliver, ReportingError, StorageError,
)

LOG = logging.getLogger(__name__)
UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
MAX_ROWS = 50_000
MAX_BYTES = 64 * 1024 * 1024


class WaitingForData(ValueError):
    pass


class RuntimeBudgetExceeded(BaseException):
    """A candidate-level rejection handler must not swallow the worker deadline."""


def timestamp(value):
    result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Research timestamps require an explicit timezone.")
    return result.astimezone(UTC)


def future_sessions(frozen_at, count):
    """Reserve unseen exchange sessions, including holidays and early closes."""
    day = frozen_at.astimezone(NY).date()
    result = []
    for _ in range(180):
        bounds = session_bounds(day)
        if bounds and bounds[0] > frozen_at:
            result.append(day.isoformat())
            if len(result) == count:
                return result
        day += dt.timedelta(days=1)
    raise ValueError("Could not reserve the requested future evaluation sessions.")


class ShadowDatasets:
    """Read a coherent published prefix; never fill missing observations."""

    def __init__(self, client):
        self.client = client

    def load(self, run_id=None):
        query = self.client.table("intraday_shadow_runs").select("*")
        if run_id:
            query = query.eq("id", run_id)
        runs = query.order("created_at", desc=True).limit(1).execute().data
        if not runs:
            raise WaitingForData("No hypothetical portfolio run has been recorded.")
        run = runs[0]
        if run["status"] != "running":
            if run_id:
                raise ValueError("The frozen campaign's source is blocked or superseded; "
                                 "new evidence requires a new campaign.")
            raise WaitingForData("The source shadow run is blocked; do not invent missing sessions.")
        if run["engine_revision"] != shadow_engine.engine_fingerprint():
            raise ValueError("Source run needs its original research image; engine fingerprint differs.")
        if run["initial_state"].get("config") != run["effective_config"]:
            raise ValueError("Source seed and effective configuration disagree.")
        sequence = run["latest_sequence"]
        if not sequence:
            raise WaitingForData("No hypothetical decision cycles have been published.")
        if type(sequence) is not int or not 1 <= sequence <= MAX_ROWS:
            raise ValueError("Source history exceeds the bounded full-prefix export.")
        records, size, offset = [], 0, 0
        while offset < sequence:
            rows = (self.client.table("intraday_shadow_events").select("*")
                    .eq("run_id", run["id"]).lte("sequence", sequence).order("sequence")
                    .range(offset, offset + 499).execute().data)
            if not isinstance(rows, list) or not rows:
                raise ValueError("The published shadow prefix is incomplete.")
            for row in rows:
                if row["sequence"] != len(records) + 1 or row["kind"] != "cycle":
                    raise ValueError("Shadow history contains a missing cycle or an explicit gap.")
                payload = row["payload"]
                if not isinstance(payload, dict) or not {"frame", "output"} <= payload.keys():
                    raise ValueError("Source cycle lacks reproducible inputs and output.")
                size += len(json.dumps(payload, allow_nan=False).encode())
                if size > MAX_BYTES:
                    raise ValueError("Complete source prefix exceeds 64 MiB; do not truncate it.")
                records.append({"frame": payload["frame"], "output": payload["output"]})
            offset += len(rows)
        saved = (self.client.table("intraday_shadow_checkpoints").select("*")
                 .eq("run_id", run["id"]).eq("sequence", sequence).limit(1).execute().data)
        if not saved:
            raise WaitingForData("The source checkpoint has not yet reached cloud storage.")
        if calibrate_intraday.digest(saved[0]["state"]) != row["state_sha256"]:
            raise ValueError("Published shadow checkpoint digest differs from its last cycle.")
        records[-1]["checkpoint"] = saved[0]["state"]
        return {"run": run, "records": records}

    @staticmethod
    def completed_sessions(source, now):
        seed_at = timestamp(source["run"]["initial_state"]["timestamp"])
        days = set()
        for row in source["records"]:
            for event in row["frame"]["events"]:
                if event["type"] != "end_mark":
                    continue
                day = dt.date.fromisoformat(event["session"])
                bounds = session_bounds(day)
                if bounds and bounds[0] >= seed_at and bounds[1] < now:
                    days.add(day.isoformat())
        return sorted(days)

    @staticmethod
    def export(source, start, end):
        return shadow_engine.export_shadow_dataset(
            source["run"]["initial_state"], source["records"], start, end)


class Worker:
    def __init__(self, store, datasets, clock=lambda: dt.datetime.now(UTC)):
        self.store, self.datasets, self.clock = store, datasets, clock

    @staticmethod
    def _risk_analytics(data, selection, evaluation=None, previous=None):
        try:
            return build_risk_report(data, selection, evaluation, previous=previous)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            LOG.error("Diagnostic risk analytics unavailable (%s): %s", type(exc).__name__, exc)
            return {"version": 1, "status": "unavailable", "error": str(exc),
                    "reference_snapshot": (previous or {}).get("reference_snapshot"),
                    "scope": "Risk analytics failed; no ratios inferred. Strategy selection and approval rules are unchanged."}

    @staticmethod
    def _risk_bundle(reports):
        reports = {key: value for key, value in reports.items() if key in ("training", "evaluation")}
        pending = any(report.get("calculation_inputs")
                      and report.get("reference_snapshot", {}).get("status") == "unavailable"
                      and not report.get("retry_error") for report in reports.values())
        return {**reports, "retry_pending": pending}

    def _retry_risk_analytics(self):
        for item in self.store.risk_retry_queue():
            row = self.store.proposal(item["id"])
            if row["status"] not in ("evaluating", "ready", "no_change"):
                continue
            reports = dict(row["artifact"].get("risk_analytics", {}))
            for phase in ("training", "evaluation"):
                old = reports.get(phase, {})
                if (not old.get("calculation_inputs") or old.get("retry_error")
                        or old.get("reference_snapshot", {}).get("status") != "unavailable"):
                    continue
                try:
                    reports[phase] = refresh_risk_reference(old)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    LOG.error("Diagnostic reference retry refused: %s", exc)
                    reports[phase] = {**old, "retry_error": str(exc)}
                    reports[phase]["sha256"] = calibrate_intraday.digest(
                        {key: value for key, value in reports[phase].items() if key != "sha256"})
            try:
                self.store.refresh_risk_analytics(row["id"], row["revision"], row["artifact"],
                                                 self._risk_bundle(reports))
            except Conflict:
                LOG.info("Research decision changed during diagnostic retry; no artifact was overwritten.")

    def _weekly_status(self, note, now):
        monday = now.astimezone(NY).date() - dt.timedelta(days=now.astimezone(NY).weekday())
        self.store.add_event(proposal_id=None, event="weekly_update", note=note,
                             data={"notification": True},
                             event_id="calibration-weekly:" + monday.isoformat() + ":" +
                                      calibrate_intraday.digest(note))

    def _hypotheses(self, proposal, hypotheses):
        for hypothesis in hypotheses[:2]:
            experiment = hypothesis.get("experiment")
            if not experiment:
                continue
            normalized = calibrate_intraday.experiments({"experiments": [experiment]})[0]
            identifier = calibrate_intraday.digest(
                {"parent": proposal["id"], "experiment": normalized})
            self.store.create_proposal({
                "id": identifier, "kind": "rule", "parent_id": proposal["id"],
                "title": hypothesis.get("title", "Permission to investigate a rule change"),
                "status": "investigation_requested", "artifact": {},
                "artifact_sha256": calibrate_intraday.digest({}),
                "request": {"experiment": normalized, "rationale": hypothesis.get("rationale", ""),
                            "investigation_approved": False},
            })

    def _announce_selection(self, proposal):
        artifact = proposal["artifact"]
        self.store.add_event(event="selection_frozen", proposal_id=proposal["id"],
                             note=f"Training {artifact['training_start']} through {artifact['training_end']}. "
                                  f"Future evaluation {artifact['evaluation_start']} through "
                                  f"{artifact['evaluation_end']}. No live changes.",
                             data={"notification": True,
                                   "selected_name": artifact["frozen"]["selection"]["selected_name"]},
                             event_id=proposal["id"] + ":frozen")
        self._hypotheses(proposal, artifact["frozen"].get("hypotheses", []))

    def _freeze(self, source, settings, now, request=None):
        days = self.datasets.completed_sessions(source, now)
        count = settings["value"]["training_sessions"]
        if len(days) < count:
            raise WaitingForData(f"Need {count} complete training sessions; only {len(days)} are available.")
        start, end = days[-count], days[-1]
        monday = now.astimezone(NY).date() - dt.timedelta(days=now.astimezone(NY).weekday())
        identifier = calibrate_intraday.digest({
            "run_id": source["run"]["id"], "week": monday.isoformat(),
            "settings_revision": settings["revision"], "request_id": request["id"] if request else None,
        })
        if self.store.has_proposal(identifier):
            self._announce_selection(self.store.proposal(identifier))
            self.store.set_health("waiting", metrics={"reason": "This week's campaign is already recorded."})
            return
        data = self.datasets.export(source, start, end)
        experiments = [request["request"]["experiment"]] if request else []
        frozen = auto_calibration.freeze_selection(data, settings["value"],
                                                   requested_experiments=experiments)
        frozen_at = self.clock()
        if timestamp(frozen["selection"]["training"]["observed_end"]) >= frozen_at:
            raise ValueError("Training contains observations at or after the selection time.")
        future = future_sessions(frozen_at, settings["value"]["evaluation_sessions"])
        artifact = {
            "frozen": frozen, "run_id": source["run"]["id"],
            "training_start": start, "training_end": end, "frozen_at": frozen_at.isoformat(),
            "evaluation_start": future[0], "evaluation_end": future[-1],
            "evaluation_sessions": future, "last_evaluated_session": None,
            "settings_revision": settings["revision"],
            "portfolio_scope": "One continuous challenger over the fixed evaluation window; "
                               "reconstructed from the same starting checkpoint, never reset daily.",
            "risk_analytics": self._risk_bundle({"training": self._risk_analytics(data, frozen["selection"])}),
        }
        selected = frozen["selection"]["selected_name"]
        status = "no_change" if selected == "baseline" else "evaluating"
        new_proposal = {
            "id": identifier, "kind": "parameter",
            "parent_id": request["id"] if request else None,
            "title": "No parameter change justified" if status == "no_change" else f"Evaluate {selected}",
            "status": status, "artifact": artifact,
            "artifact_sha256": calibrate_intraday.digest(artifact), "request": {},
        }
        if request:
            self.store.update_proposal(
                request["id"], request["revision"],
                {"status": "no_change", "request": {**request["request"], "campaign_id": identifier}},
                "experiment_enrolled", note="The approved request is linked to its immutable campaign.",
                data={"campaign_id": identifier}, child=new_proposal)
            proposal = self.store.proposal(identifier)
        else:
            proposal = self.store.create_proposal(new_proposal)
        self._announce_selection(proposal)
        self.store.set_health(status, metrics={"proposal_id": identifier, "evaluation_end": future[-1]})

    def _evaluate(self, proposal, settings, now):
        artifact = proposal["artifact"]
        self._announce_selection(proposal)
        source = self.datasets.load(artifact["run_id"])
        # A campaign must use the latest source run; a manual restart/config change
        # cannot silently switch its lineage or leave it evaluating an abandoned run.
        latest = self.datasets.load()
        if latest["run"]["id"] != artifact["run_id"]:
            raise ValueError("Source run changed after selection; this campaign cannot switch its seed.")
        days = [day for day in self.datasets.completed_sessions(source, now)
                if artifact["evaluation_start"] <= day <= artifact["evaluation_end"]]
        if not days or days[-1] == artifact.get("last_evaluated_session"):
            self.store.set_health("waiting_for_holdout", metrics={
                "proposal_id": proposal["id"], "evaluation_start": artifact["evaluation_start"],
                "evaluation_end": artifact["evaluation_end"],
                "last_evaluated_session": artifact.get("last_evaluated_session")})
            return
        through = days[-1]
        data = self.datasets.export(source, artifact["evaluation_start"], through)
        if timestamp(calibrate_intraday.validate_dataset(data)["observed_start"]) <= timestamp(artifact["frozen_at"]):
            raise ValueError("Evaluation observations are not strictly after the frozen selection.")
        evaluated = auto_calibration.evaluate_selection(artifact["frozen"], data)
        final = through == artifact["evaluation_end"]
        old_policy = artifact["frozen"]["settings"]["risk_policy"]
        if old_policy != settings["value"]["risk_policy"]:
            evaluated["eligibility"]["eligible"] = False
            evaluated["eligibility"]["reasons"].append(
                "Risk policy changed after freezing; a new campaign with future evidence is required.")
        elif artifact["settings_revision"] != settings["revision"]:
            evaluated["eligibility"]["eligible"] = False
            evaluated["eligibility"]["reasons"].append(
                "Research settings changed after freezing; a new campaign with future evidence is required.")
        updated = {**artifact, "evaluation": evaluated, "last_evaluated_session": through,
                   "evaluation_complete": final,
                   "risk_analytics": self._risk_bundle({**artifact.get("risk_analytics", {}),
                                      "evaluation": self._risk_analytics(
                                          data, artifact["frozen"]["selection"], evaluated["evaluation"],
                                          artifact.get("risk_analytics", {}).get("evaluation"))})}
        status = ("ready" if evaluated["eligibility"]["eligible"] else "no_change") if final else "evaluating"
        self.store.update_proposal(
            proposal["id"], proposal["revision"],
            {"status": status, "artifact": updated, "artifact_sha256": calibrate_intraday.digest(updated)},
            "evaluation_complete" if final else "evaluation_progress",
            note=("Full predeclared evaluation completed." if final else
                  "Provisional progress only; no early approval or retuning."),
            data={"notification": final, "through": through, "status": status})
        self.store.set_health(status, metrics={"proposal_id": proposal["id"], "through": through})

    def step(self):
        now = self.clock()
        settings = self.store.settings()
        settings["value"] = auto_calibration.validate_settings(settings["value"])
        if not settings["value"]["enabled"]:
            self.store.set_health("disabled")
            return
        self._retry_risk_analytics()
        proposals = self.store.research_queue()
        active = [row for row in proposals if row["status"] == "evaluating"]
        if len(active) > 1:
            raise ValueError("Multiple active evaluation campaigns; refusing overlapping evidence selection.")
        try:
            if active:
                active[0] = self.store.proposal(active[0]["id"])
                self._evaluate(active[0], settings, now)
                return
            # A deferred campaign reserves its unseen evaluation until explicitly
            # resumed/rejected. Do not start a replacement based on its progress.
            for row in proposals:
                if row["status"] != "deferred":
                    continue
                previous = self.store.deferred_status(row["id"])
                if previous not in {"evaluating", "ready", "blocked", "no_change",
                                    "investigation_requested", "investigation_approved", "needs_engine_support"}:
                    raise ValueError("Deferred proposal is missing its durable previous status.")
                if previous == "evaluating":
                    self.store.set_health("deferred", metrics={"reason": "An operator-deferred evaluation is pending."})
                    return
            approved = [row for row in proposals if row["status"] == "investigation_approved"
                        and row.get("request", {}).get("experiment")]
            request = sorted(approved, key=lambda row: row["created_at"])[0] if approved else None
            source = self.datasets.load()
            self._freeze(source, settings, now, request)
        except WaitingForData as exc:
            self.store.set_health("waiting_for_data", error=str(exc))
            self._weekly_status(str(exc) + " No calibrated recommendation is available.", now)
        except (ValueError, shadow_engine.ShadowError) as exc:
            # Validation failures invalidate the whole campaign, never selected
            # losing rows. Operator decisions racing this write remain protected by CAS.
            if active:
                row = active[0]
                self.store.update_proposal(row["id"], row["revision"], {"status": "blocked"},
                                           "evidence_rejected", note=str(exc),
                                           data={"notification": True})
            raise


def notify(store, delivery_store, telegram, now):
    failed = 0
    for event in store.events(pending_notifications=True):
        if not (event.get("data") or {}).get("notification"):
            store.mark_notified(event["id"])
            continue
        body = ("CALIBRATION RESEARCH — hypothetical, not real trading\n"
                f"{event['event']}\n{event.get('note', '')}\n"
                f"Proposal: {event.get('proposal_id') or 'weekly collection update'}\n"
                "Open Backtester > Research inbox to review. No live settings were changed.")
        try:
            deliver(delivery_store, telegram, "calibration:" + str(event["id"]), body, now)
        except StorageError:
            raise
        except ReportingError:
            failed += 1
            continue
        store.mark_notified(event["id"])
    if failed:
        raise ReportingError(f"{failed} calibration notifications remain pending delivery.")


def run_once(store, datasets, delivery_store, telegram, clock=lambda: dt.datetime.now(UTC)):
    owner = str(uuid.uuid4())
    if not store.claim(owner, seconds=1800):
        return "leased_elsewhere"
    try:
        store.set_health("researching")
        Worker(store, datasets, clock).step()
        notify(store, delivery_store, telegram, clock())
        return "complete"
    finally:
        store.release(owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=int, default=300)
    args = parser.parse_args()
    if args.poll < 30:
        parser.error("--poll must be at least 30 seconds.")
    logging.basicConfig(level=logging.INFO)
    from supabase import create_client
    url, key = os.getenv("SUPABASE_URL"), os.getenv("INTRADAY_SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Dedicated private research credentials are required.")
    client = create_client(url, key)
    store = CalibrationStore(client)
    delivery_store = DeliveryStore(url, key)
    telegram = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""),
                        os.getenv("TELEGRAM_CHAT_IDS", "").split(","))
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())

    def deadline(*_):
        raise RuntimeBudgetExceeded("Calibration exceeded its 15-minute processing budget.")

    signal.signal(signal.SIGALRM, deadline)
    while not stopped.is_set():
        try:
            signal.alarm(900)
            run_once(store, ShadowDatasets(client), delivery_store, telegram)
        except (Exception, RuntimeBudgetExceeded) as exc:
            diagnostics.emit("calibration-worker", "calibration_failed", error=exc, level="ERROR")
            LOG.error("Calibration failed (%s); no live changes or approvals.", type(exc).__name__)
            try:
                store.set_health("error", error=f"Calibration failed ({type(exc).__name__}); inspect diagnostics.")
            except Exception as health_error:
                diagnostics.emit("calibration-worker", "health_upload_failed", error=health_error)
                LOG.error("Calibration health could not be persisted (%s).", type(health_error).__name__)
            if args.once:
                return 1
        finally:
            signal.alarm(0)
        if args.once:
            return 0
        stopped.wait(args.poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
