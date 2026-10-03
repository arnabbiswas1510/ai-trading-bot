"""Diagnostic risk evidence, separate from frozen strategy selection and approval."""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import importlib.metadata
import logging
from pathlib import Path
import sys

from research import calibrate_intraday as calibration
from research import calibration_risk, treasury_reference

LOG = logging.getLogger(__name__)
VERSION = 1


def analytics_fingerprint():
    root = Path(__file__).resolve().parent
    packages = {}
    for name in ("exchange-calendars", "pandas", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "version": VERSION, "python": sys.version, "packages": packages,
        "calendar_sha256": hashlib.sha256((root.parent / "market_calendar.py").read_bytes()).hexdigest(),
        "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
            "calibration_risk_report.py", "calibration_risk.py", "treasury_reference.py")},
    }


def _reference_window(sessions):
    # Only freeze dates a current return can use. Freezing today's unpublished
    # yield would prevent later windows from ever receiving that observation.
    start = dt.date.fromisoformat(sessions[0]) - dt.timedelta(days=1)
    last_start = sessions[-2] if len(sessions) > 1 else sessions[0]
    end = dt.date.fromisoformat(last_start) - dt.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _reference(start, end, previous=None, loader=None):
    try:
        return (loader or treasury_reference.fetch_reference)(start, end, previous=previous)
    except treasury_reference.TreasuryUnavailable as exc:
        LOG.warning("Treasury reference unavailable for risk analytics: %s", exc)
        return treasury_reference.unavailable_reference(str(exc), start, end, previous=previous)


def refresh_risk_reference(saved, *, reference_loader=None):
    """Retry only cash-reference diagnostics over the exact saved replay outputs."""
    calibration.require(calibration.digest({key: value for key, value in saved.items()
                                            if key != "sha256"}) == saved.get("sha256"),
                        "Stored diagnostic report fingerprint differs")
    calibration.require(saved["analytics_fingerprint"] == analytics_fingerprint(),
                        "Diagnostic calculation code changed; automatic reference refresh refused")
    inputs = saved["calculation_inputs"]
    result = copy.deepcopy(saved)
    start, end = saved["reference_window"]
    reference = _reference(start, end, saved["reference_snapshot"], reference_loader)
    for side in ("baseline", "candidate"):
        if inputs[side] is not None:
            result[side] = calibration_risk.calculate_risk(inputs["data"], inputs[side], reference)
    result["reference_snapshot"] = reference
    result["status"] = "available" if inputs["candidate"] is not None and reference["status"] == "available" else "partial"
    result["sha256"] = calibration.digest({key: value for key, value in result.items() if key != "sha256"})
    return result


def build_risk_report(data, selection, evaluation=None, *, previous=None, reference_loader=None):
    """Reproduce the exact selected pair; never search, alter a plan or infer a path."""
    calibration.verify_plan(selection)
    report = evaluation if evaluation is not None else selection
    calibration.require(calibration.digest({key: value for key, value in report.items()
                                            if key != "artifact_sha256"}) == report["artifact_sha256"],
                        "Risk evidence report fingerprint differs")
    if evaluation is not None:
        calibration.require(evaluation["selection_plan_sha256"] == selection["artifact_sha256"]
                            and evaluation["selected_name"] == selection["selected_name"],
                            "Risk evidence uses a different frozen selection")
    window = report["holdout" if evaluation is not None else "training"]
    calibration.require(calibration.digest(data) == window["input_sha256"],
                        "Risk evidence input differs from the benchmark window")
    baseline_trial = evaluation["baseline"] if evaluation is not None else selection["training_trials"][0]
    candidate_trial = evaluation["frozen_candidate"] if evaluation is not None else next(
        trial for trial in selection["training_trials"] if trial["name"] == selection["selected_name"])
    baseline, _, _ = calibration._run(data, calibration.BASELINE)
    calibration.require(calibration.summary(data, baseline, baseline) == baseline_trial["summary"],
                        "Risk replay does not reproduce the recorded baseline summary")
    candidate = None
    if candidate_trial["status"] == "modeled":
        candidate = baseline if selection["selected_name"] == "baseline" else calibration._run(
            data, selection["frozen_experiment"])[0]
        calibration.require(calibration.summary(data, candidate, baseline) == candidate_trial["summary"],
                            "Risk replay does not reproduce the frozen candidate summary")
    start, end = _reference_window(window["sessions"])
    old_reference = previous.get("reference_snapshot") if previous else None
    reference = _reference(start, end, old_reference, reference_loader)
    baseline_risk = calibration_risk.calculate_risk(data, baseline, reference)
    candidate_risk = calibration_risk.calculate_risk(data, candidate, reference) if candidate is not None else {
        "metrics": {}, "unavailable": {"all": candidate_trial.get("reason", "Candidate was not modeled")},
        "warnings": ["Rejected candidate: no risk ratios are inferred."],
    }
    result = {
        "version": VERSION, "phase": "evaluation" if evaluation is not None else "training",
        "status": "available" if candidate is not None and reference["status"] == "available" else "partial",
        "selection_sha256": selection["artifact_sha256"],
        "benchmark_sha256": report["artifact_sha256"],
        "input_sha256": window["input_sha256"], "selected_name": selection["selected_name"],
        "analytics_fingerprint": analytics_fingerprint(), "reference_snapshot": reference,
        "reference_window": [start, end],
        "calculation_inputs": {
            "data": {**({"initial_state": {"timestamp": data["initial_state"]["timestamp"]}}
                       if "initial_state" in data else {}),
                     **({"initial_positions": copy.deepcopy(data["initial_positions"])} if "initial_positions" in data else {}),
                     "events": [{key: event[key] for key in ("type", "session", "timestamp")} for event in data["events"]]},
            **{side: {key: copy.deepcopy(run[key]) for key in (
                "initial_equity", "equity_curve", "initial_positions", "position_sales")} if run is not None else None
               for side, run in (("baseline", baseline), ("candidate", candidate))},
        },
        "baseline": baseline_risk, "candidate": candidate_risk,
        "scope": "Diagnostic only; no change to candidate ranking, approval policy or live trading. "
                 "Session-final returns exclude the opening partial interval; historical rates are "
                 "a lagged cash proxy, not a Treasury total-return investment.",
    }
    return {**result, "sha256": calibration.digest(result)}
