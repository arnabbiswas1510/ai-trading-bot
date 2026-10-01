"""Offline deterministic calibration on the existing recorded-account fixture."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import socket
import stat
import uuid

import pytest

from intraday_replay import build_dataset
from research import calibrate_intraday as calibration
from research import live_rule_replay as core
from test_intraday_replay import DAY, records  # noqa: F401


@pytest.fixture
def data(records):
    return build_dataset(records, DAY, DAY)


@pytest.fixture
def candidates():
    return {"experiments": [
        {"name": "no_veto", "disable_ai_veto": True},
        {"name": "score100", "decision_config": {"min_trigger_score": 100}},
        {"name": "no_scale", "exit_config": {"scale_out_enabled": False}},
    ]}


def shift_window(data, days=1):
    """Move the full synthetic capture, including every observed timestamp."""
    def shift(value):
        if isinstance(value, dict):
            return {k: shift(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shift(v) for v in value]
        if isinstance(value, str):
            try:
                if "T" in value:
                    return (dt.datetime.fromisoformat(value) + dt.timedelta(days=days)).isoformat()
                return (dt.date.fromisoformat(value) + dt.timedelta(days=days)).isoformat()
            except ValueError:
                return value
        return value
    result = shift(data)
    result["dataset_label"] = "Later independent actual account"
    return result


def rising(data):
    result = copy.deepcopy(data)
    for event in result["events"]:
        if event["timestamp"][11:16] >= "09:35":
            event["market_observations"]["CANDIDATE"]["price"] = 104
    return result


def test_baseline_wins_ties_and_preserves_actual_input(data, candidates):
    before = copy.deepcopy(data)
    shared = copy.deepcopy(core.shared_rule_snapshot())
    plan = calibration.select(data, candidates)
    assert data == before
    assert core.shared_rule_snapshot() == shared
    assert plan["selected_name"] == "baseline"
    assert plan["original_settings"]["decision_config"] == data["decision_config"]
    assert plan["original_capture_evidence"] == data["capture_evidence"]
    for trial in plan["training_trials"]:
        assert trial["summary"]["n_distinct_sessions"] == 1
        assert trial["summary"]["starting_actual_account"]["net_liquidation"] == 10000
        assert trial["summary"]["starting_cash"] == 8990
        assert trial["summary"]["final_equity_net"] == pytest.approx(
            trial["summary"]["final_cash"] + trial["summary"]["final_holdings_value"])
    assert plan["candidate_trial_count"] == 3
    assert plan["total_trials_including_baseline"] == 4


def test_frozen_selection_never_consults_holdout_or_reselects(data, candidates, monkeypatch):
    plan = calibration.select(rising(data), candidates)
    assert plan["selected_name"] == "no_veto"
    holdout = shift_window(data)
    for event in holdout["events"]:
        if event["timestamp"][11:16] >= "09:35":
            event["market_observations"]["CANDIDATE"]["price"] = 95
    calls, original = [], calibration._run

    def record(dataset, experiment):
        calls.append(experiment["name"])
        return original(dataset, experiment)

    monkeypatch.setattr(calibration, "_run", record)
    result = calibration.evaluate(plan, holdout)
    assert calls == ["baseline", "no_veto"]
    assert result["selected_name"] == "no_veto"
    assert result["frozen_candidate"]["summary"]["equity_delta_vs_recorded_config_baseline"] < 0
    assert result["recommendation"] == "research_only_manual_approval"
    assert plan["selected_name"] == "no_veto"


def test_deterministic_plan_and_alphabetic_candidate_tie(data):
    candidates = {"experiments": [{"name": "z", "disable_ai_veto": True},
                                  {"name": "a", "disable_ai_veto": True}]}
    assert calibration.select(rising(data), candidates) == calibration.select(rising(data), candidates)
    assert calibration.select(rising(data), candidates)["selected_name"] == "a"


@pytest.mark.parametrize("row", [
    {"name": "baseline"}, {"name": ""}, {"name": " padded"},
    {"name": "x", "disable_ai_veto": 1},
    {"name": "x", "max_positions": 3},
    {"name": "x", "shared_exit_rules": {"STOP_LOSS_PCT": 0.04}},
    {"name": "x", "exit_config": {"prove_it_p2_floor_pct": 0}},
    {"name": "x", "exit_config": {"stop_loss_pct": 0.02}},
    {"name": "x", "exit_config": {"power_hold_trail_pct": 0.1}},
    {"name": "x", "exit_config": {"scale_out_enabled": "false"}},
    {"name": "x", "exit_config": {"scale_out_fraction": 1}},
    {"name": "x", "exit_config": {"armed_exit_deadline_hours": 0}},
    {"name": "x", "decision_config": {"min_trigger_score": True}},
    {"name": "x", "decision_config": {"min_trigger_score": float("nan")}},
    {"name": "x", "decision_config": {"min_trigger_score": float("inf")}},
    {"name": "x", "decision_config": {"min_trigger_score": 101}},
    {"name": "x", "decision_config": {"min_trigger_score": 10**400}},
    {"name": "x", "decision_config": {"max_pre_breakout_pivot_dist": 20}},
    {"name": "x", "replay_config": {"cooling_off_days": 0}},
])
def test_unknown_or_invalid_overrides_fail_closed(row):
    with pytest.raises(calibration.CalibrationError):
        calibration.experiments({"experiments": [row]})


@pytest.mark.parametrize("config", [
    {"experiments": []}, {"experiments": [{"name": "x"}] * 2},
    {"experiments": [{"name": str(n)} for n in range(33)]},
    {"experiments": [{"name": "x"}], "automatic_apply": True},
])
def test_experiment_count_and_duplicate_names(config):
    with pytest.raises(calibration.CalibrationError):
        calibration.experiments(config)


def test_original_shared_rule_validation_precedes_experiment(data):
    data["exit_config"]["prove_it_p2_floor_pct"] = 0.9
    with pytest.raises(core.ReplayInputError, match="must match"):
        calibration.select(data, {"experiments": [{"name": "no_scale", "exit_config": {"scale_out_enabled": False}}]})


def test_only_supported_exit_config_changes_apply(data):
    for event in data["events"]:
        event["market_observations"]["HELD"]["price"] = 104.5
    config = {"experiments": [{"name": "no_scale", "exit_config": {"scale_out_enabled": False}},
                              {"name": "half", "exit_config": {"scale_out_fraction": 0.5}}]}
    plan = calibration.select(data, config)
    baseline, no_scale, half = plan["training_trials"]
    assert baseline["summary"]["position_attribution"]["positions"][0]["sale_rows"] == 1
    assert no_scale["summary"]["position_attribution"]["positions"] == []
    assert half["effective_settings"]["exit_config"]["scale_out_fraction"] == 0.5
    assert half["settings_diff"] == [{"field": "exit_config.scale_out_fraction",
                                     "original": data["exit_config"]["scale_out_fraction"], "effective": 0.5}]
    assert plan["original_settings"]["exit_config"]["scale_out_enabled"] is True
    assert all(t["summary"]["n_completed_positions"] == 0 for t in plan["training_trials"])


def test_supported_entry_threshold_reaches_shared_gate(data):
    data = rising(data)
    for event in data["events"]:
        for trigger in event.get("triggers", []):
            trigger.update(ai_grade="A", final_score=80, adjusted_score=80)
    plan = calibration.select(data, {"experiments": [
        {"name": "strict", "decision_config": {"min_trigger_score": 100}}]})
    baseline, candidate = plan["training_trials"]
    assert baseline["summary"]["open_position_count"] == 2
    assert candidate["summary"]["open_position_count"] == 1
    assert candidate["summary"]["equity_delta_vs_recorded_config_baseline"] < 0


def test_armed_deadline_override_changes_only_future_simulated_decisions(data):
    state = data["initial_state"]
    state["positions"][0].update(exit_armed=True, exit_armed_at=DAY + "T09:00:00-04:00",
                                 exit_armed_reason="Recorded prior arm")
    state["orders"] = [o for o in state["orders"] if o["order_type"] == "TRAIL"]
    data["initial_positions"] = calibration.capture._initial_positions(state)
    before = copy.deepcopy(data)
    baseline, _, _ = calibration._run(data, calibration.BASELINE)
    candidate = calibration.experiments({"experiments": [
        {"name": "deadline", "exit_config": {"armed_exit_deadline_hours": 0.25}}]})[0]
    variant, _, _ = calibration._run(data, candidate)
    assert variant["fills"][0]["timestamp"][11:16] == "09:30"
    assert baseline["fills"][0]["timestamp"][11:16] == "12:15"
    assert data == before


def test_partials_aggregate_with_final_sale_not_counted_as_positions(data):
    for event in data["events"]:
        event["market_observations"]["HELD"]["price"] = 104.5 if event["timestamp"][11:16] < "09:35" else 95
    plan = calibration.select(data, {"experiments": [{"name": "same"}]})
    summary = plan["training_trials"][0]["summary"]
    assert summary["n_completed_positions"] == 1
    attribution = summary["position_attribution"]
    assert attribution["completed_positions"] == 1
    assert len(attribution["positions"]) == 1
    pos = attribution["positions"][0]
    assert pos["sale_rows"] == 2
    assert pos["buy_date"] == "2026-09-25"
    assert pos["realised_net"] == pytest.approx(3 * 4.5 + 7 * -5 - 0.35)
    assert attribution["realised_loss_completed_positions"] == pytest.approx(pos["realised_net"])
    assert summary["attribution_only_excluding_top1_net"] == summary["net_profit"]  # No positive contributor.


def test_missing_position_keys_never_inferred():
    result = calibration.position_attribution({"position_sales": [
        dict(ticker="X", sell_date="2026-09-29", partial=False, net_profit_loss=100)]})
    assert result["missing_position_key_rows"] == 1
    assert result["positions"] == []


def test_ticker_equity_attribution_includes_open_holdings_and_costs(data):
    data = rising(data)
    data["costs"] = dict(commission_min=1, commission_per_share=0.02, slippage_bps=5)
    plan = calibration.select(data, {"experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
    trial = plan["training_trials"][1]["summary"]
    assert trial["commission"] > 0 and trial["slippage_cost"] > 0
    assert sum(trial["all_ticker_window_net"].values()) == pytest.approx(trial["net_profit"])
    assert sum(trial["all_ticker_equity_deltas"].values()) == pytest.approx(
        trial["equity_delta_vs_recorded_config_baseline"])
    assert trial["n_completed_positions"] == 0
    assert trial["attribution_only_excluding_top1_delta"] == pytest.approx(0)


def test_overlapping_labels_do_not_override_actual_timestamps(data, candidates):
    plan = calibration.select(data, candidates)
    data["dataset_label"] = "Tomorrow"
    with pytest.raises(calibration.CalibrationError, match="nonoverlapping"):
        calibration.evaluate(plan, data)


def test_snapshot_and_observation_bounds_are_included(data):
    expected = dt.datetime.fromisoformat(data["initial_state"]["timestamp"]) - dt.timedelta(minutes=2)
    data["initial_state"]["broker_positions"][0]["observed_at"] = expected.isoformat()
    assert calibration.validate_dataset(data)["observed_start"] == expected.isoformat()


def test_inclusive_observation_boundary_rejected(data, candidates):
    plan = calibration.select(data, candidates)
    holdout = shift_window(data)
    # A sealed boundary exactly equal to the observed holdout start is not disjoint.
    plan["training"]["observed_end"] = calibration.validate_dataset(holdout)["observed_start"]
    plan.pop("artifact_sha256")
    plan = calibration.seal(plan)
    with pytest.raises(calibration.CalibrationError, match="strictly later"):
        calibration.evaluate(plan, holdout)


@pytest.mark.parametrize("change", ["account", "config", "engine", "plan", "config_digest"])
def test_incompatible_or_corrupt_plan_rejected(data, candidates, monkeypatch, change):
    plan = calibration.select(data, candidates)
    holdout = shift_window(data)
    if change == "account":
        holdout["initial_state"]["account"]["account_id"] = "U_OTHER"
        for row in holdout["initial_state"]["broker_positions"] + holdout["initial_state"]["orders"]:
            row["account"] = "U_OTHER"
    elif change == "config":
        holdout["decision_config"]["min_trigger_score"] += 1
    elif change == "engine":
        fingerprint = calibration.engine_fingerprint()
        fingerprint["sha256"] = "new-engine"
        monkeypatch.setattr(calibration, "engine_fingerprint", lambda: fingerprint)
    elif change == "plan":
        plan["frozen_experiment"]["disable_ai_veto"] = True
    else:
        plan["candidate_config_sha256"] = "corrupt"
        plan.pop("artifact_sha256")
        plan = calibration.seal(plan)
    with pytest.raises(calibration.CalibrationError):
        calibration.evaluate(plan, holdout)


def test_changed_imported_rule_constants_change_engine(data, candidates, monkeypatch):
    plan = calibration.select(data, candidates)
    monkeypatch.setattr(core.er, "PROVE_IT_P1_DAY0_PCT", core.er.PROVE_IT_P1_DAY0_PCT + 0.001)
    with pytest.raises(calibration.CalibrationError, match="engine/config"):
        calibration.evaluate(plan, shift_window(data))


def test_changed_loaded_replay_function_rejects_plan(data, candidates, monkeypatch):
    plan = calibration.select(data, candidates)
    original = core._Replay.run

    def replaced(self):
        return original(self)

    monkeypatch.setattr(core._Replay, "run", replaced)
    with pytest.raises(calibration.CalibrationError, match="engine/config"):
        calibration.evaluate(plan, shift_window(data))


def test_forged_frozen_winner_disagrees_with_ranking(data, candidates):
    plan = calibration.select(data, candidates)
    plan["selected_name"] = "no_veto"
    plan["frozen_experiment"] = calibration.experiments(candidates)[0]
    plan.pop("artifact_sha256")
    with pytest.raises(calibration.CalibrationError, match="selection"):
        calibration.verify_plan(calibration.seal(plan))


def test_rejected_candidate_remains_in_training_ranking(data, monkeypatch):
    original = calibration._run

    def unsupported(dataset, experiment):
        if experiment["name"] == "unsupported":
            raise core.ReplayInputError("Rank & Replace unsupported for this candidate")
        return original(dataset, experiment)

    monkeypatch.setattr(calibration, "_run", unsupported)
    plan = calibration.select(data, {"experiments": [{"name": "unsupported"}]})
    assert plan["selected_name"] == "baseline"
    assert plan["training_trials"][1]["status"] == "rejected"
    assert "Rank & Replace" in plan["training_trials"][1]["reason"]
    assert plan["candidate_trial_count"] == 1


def test_underlying_margin_path_rejects_whole_candidate_not_rows(data):
    data["events"][0]["market_observations"]["CANDIDATE"]["price"] = 1000
    plan = calibration.select(data, {"experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
    candidate = plan["training_trials"][1]
    assert candidate["status"] == "rejected"
    assert "would need margin" in candidate["reason"]
    assert "summary" not in candidate
    assert plan["selected_name"] == "baseline"


def test_observer_only_and_forged_coverage_fail(data, candidates):
    with pytest.raises(calibration.CalibrationError, match="observer-only"):
        calibration.select({"schema_version": 1}, candidates)
    data["events"] = [e for e in data["events"] if e["type"] in ("quote", "end_mark")]
    with pytest.raises((calibration.CalibrationError, ValueError), match="monitor|missing|starts after"):
        calibration.select(data, candidates)


def test_gap_missing_universe_and_bad_session_metadata_rejected(data):
    missing = copy.deepcopy(data)
    missing["events"] = [e for e in missing["events"] if not ("10:00" < e["timestamp"][11:16] < "10:30")]
    with pytest.raises(ValueError, match="missing|outage"):
        calibration.validate_dataset(missing)
    missing = copy.deepcopy(data)
    next(e for e in missing["events"] if e["type"] == "quote")["market_observations"].pop("CANDIDATE")
    with pytest.raises(calibration.CalibrationError, match="universe"):
        calibration.validate_dataset(missing)
    data["capture_evidence"]["sessions"] = ["2026-09-29"]
    with pytest.raises(calibration.CalibrationError, match="session labels"):
        calibration.validate_dataset(data)


@pytest.fixture
def workspace():
    # All artifacts stay inside the repository; never use pytest's tmp_path.
    folder = Path("tests") / f".calibration-{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        yield folder
    finally:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()


def test_cli_offline_atomic_artifacts_and_no_input_overwrite(data, candidates, workspace, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    training, config, plan, holdout, report = [workspace / f"{name}.json"
                                             for name in ("training", "config", "plan", "holdout", "report")]
    training.write_text(json.dumps(data))
    config.write_text(json.dumps(candidates))
    holdout.write_text(json.dumps(shift_window(data)))
    assert calibration.main(["select", str(training), str(config), "--output", str(plan)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("n=0 completed baseline positions")
    assert "1 distinct sessions" in output
    before = plan.read_bytes()
    with pytest.raises(SystemExit) as exc:
        calibration.main(["select", str(training), str(config), "--output", str(plan)])
    assert exc.value.code == 2
    assert plan.read_bytes() == before
    assert calibration.main(["evaluate", str(plan), str(holdout), "--output", str(report)]) == 0
    assert calibration.load_json(report)["recommendation"] == "research_only_manual_approval"
    with pytest.raises(SystemExit):
        calibration.main(["select", str(training), str(config), "--output", str(training), "--overwrite"])
    assert calibration.load_json(training) == data
    assert {p.name for p in workspace.iterdir()} == {"training.json", "config.json", "plan.json",
                                                   "holdout.json", "report.json"}


def test_strict_json_duplicate_nonfinite_and_atomic_failure(workspace, monkeypatch):
    path = workspace / "bad.json"
    for raw in ('{"name":"a","name":"b"}', '{"x": NaN}', '{"x": Infinity}'):
        path.write_text(raw)
        with pytest.raises(calibration.CalibrationError):
            calibration.load_json(path)
    output = workspace / "output.json"

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(calibration.os, "link", fail)
    with pytest.raises(OSError, match="publication"):
        calibration.write_artifact(output, {"test": 1})
    assert not output.exists()
    assert list(workspace.iterdir()) == [path]


def test_artifact_and_staging_are_private_even_with_permissive_umask(workspace, monkeypatch):
    output = workspace / "private.json"
    original_link, original_replace = calibration.os.link, calibration.os.replace
    staged_modes = []

    def check_link(source, target):
        staged_modes.append(stat.S_IMODE(source.stat().st_mode))
        original_link(source, target)

    def check_replace(source, target):
        staged_modes.append(stat.S_IMODE(source.stat().st_mode))
        original_replace(source, target)

    monkeypatch.setattr(calibration.os, "link", check_link)
    monkeypatch.setattr(calibration.os, "replace", check_replace)
    previous = os.umask(0)
    try:
        calibration.write_artifact(output, {"account": "test"})
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        output.chmod(0o644)
        calibration.write_artifact(output, {"account": "replacement"}, overwrite=True)
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    finally:
        os.umask(previous)
    assert staged_modes == [0o600, 0o600]
