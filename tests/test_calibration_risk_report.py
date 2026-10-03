import copy
import json

import pytest

from research import calibrate_intraday as calibration
from research import calibration_risk_report as reports
from test_intraday_replay import records  # noqa: F401
from test_shadow_calibration import training_and_holdout


def reference(start, end, previous=None):
    value = {
        "schema_version": 1, "status": "available", "source": "Fixture US Treasury 3-month",
        "retrieved_at": "2026-10-03T20:00:00+00:00", "observations": [
            {"date": "2026-09-24", "annual_yield_pct": 4.1},
            {"date": "2026-09-25", "annual_yield_pct": 4.2},
            {"date": "2026-09-28", "annual_yield_pct": 4.3},
        ], "urls": [], "error": None,
    }
    return {**value, "sha256": calibration.digest(value)}


def test_rate_window_never_freezes_unneeded_unpublished_session():
    sessions = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    assert reports._reference_window(sessions[:1]) == ("2026-10-04", "2026-10-04")
    assert reports._reference_window(sessions[:2]) == ("2026-10-04", "2026-10-04")
    assert reports._reference_window(sessions[:3]) == ("2026-10-04", "2026-10-05")
    assert reports._reference_window(sessions) == ("2026-10-04", "2026-10-06")
    assert reports._reference_window(["2026-10-02", "2026-10-05", "2026-10-06"]) == (
        "2026-10-01", "2026-10-04")


def test_risk_report_reproduces_exact_frozen_pair_without_editing_selection(records):
    training, holdout = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
    original = copy.deepcopy(plan)
    value = reports.build_risk_report(training, plan, reference_loader=reference)
    assert plan == original
    assert value["phase"] == "training"
    assert value["selected_name"] == plan["selected_name"]
    assert value["input_sha256"] == plan["training"]["input_sha256"]
    assert value["baseline"]["metrics"]["max_sampled_drawdown_pct"] == pytest.approx(
        plan["training_trials"][0]["summary"]["max_sampled_drawdown_pct"])
    assert value["baseline"]["metrics"]["sharpe"] is None  # One session is not a return sample.
    evaluation = calibration.evaluate(plan, holdout)
    measured = reports.build_risk_report(holdout, plan, evaluation, reference_loader=reference)
    assert measured["phase"] == "evaluation"
    assert measured["benchmark_sha256"] == evaluation["artifact_sha256"]
    assert measured["input_sha256"] != value["input_sha256"]
    assert measured["candidate"]["metrics"]["max_sampled_drawdown_pct"] == pytest.approx(
        evaluation["frozen_candidate"]["summary"]["max_sampled_drawdown_pct"])
    json.dumps(measured, allow_nan=False)


def test_wrong_window_and_tampered_summary_fail_loudly(records):
    training, holdout = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "unchanged"}]})
    with pytest.raises(calibration.CalibrationError, match="input differs"):
        reports.build_risk_report(holdout, plan, reference_loader=reference)
    plan["training_trials"][0]["summary"]["net_profit"] += 1
    with pytest.raises(calibration.CalibrationError, match="fingerprint"):
        reports.build_risk_report(training, plan, reference_loader=reference)


def test_rate_loader_failure_does_not_become_zero_risk_free(records):
    training, _ = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "unchanged"}]})

    def offline(*args, **kwargs):
        raise reports.treasury_reference.TreasuryUnavailable("Treasury unavailable in fixture")

    value = reports.build_risk_report(training, plan, reference_loader=offline)
    assert value["status"] == "partial"
    assert value["reference_snapshot"]["status"] == "unavailable"
    assert "Treasury unavailable in fixture" in value["reference_snapshot"]["error"]
    assert value["baseline"]["metrics"]["sharpe"] is None
    assert value["baseline"]["metrics"]["max_sampled_drawdown_pct"] is not None
    corrected = reports.refresh_risk_reference(value, reference_loader=reference)
    assert corrected["reference_snapshot"]["status"] == "available"
    assert corrected["calculation_inputs"] == value["calculation_inputs"]
    assert corrected["baseline"]["metrics"]["total_return_pct"] == value["baseline"]["metrics"]["total_return_pct"]
    assert corrected["input_sha256"] == value["input_sha256"]


def test_expanding_evaluation_passes_prior_reference_snapshot(records):
    training, holdout = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "unchanged"}]})
    evaluation = calibration.evaluate(plan, holdout)
    snapshot = reference(None, None)
    calls = []

    def loader(start, end, previous=None):
        calls.append((start, end, previous))
        return reference(start, end)

    reports.build_risk_report(holdout, plan, evaluation, previous={"reference_snapshot": snapshot},
                             reference_loader=loader)
    assert calls == [("2026-09-28", "2026-09-28", snapshot)]
