"""Pure automatic research: bounded inputs, honest risk gates, real replay artifacts."""
import copy

import pytest

import intraday_replay as capture
import shadow_engine as shadow
from research import auto_calibration as automatic
from research import calibrate_intraday as calibration
from research import live_rule_replay as core
from test_intraday_calibration import shift_window
from test_intraday_replay import DAY, records  # noqa: F401
from test_shadow_calibration import training_and_holdout
from test_shadow_engine import durable_records, seed_and_frames


@pytest.fixture
def policy():
    return dict(
        min_completed_positions=2, min_distinct_sessions=2, min_improvement_usd=100,
        max_drawdown_increase_pp=0.2, max_worst_loss_increase_usd=10,
        min_positive_tickers=2, max_largest_contributor_fraction=0.7,
    )


def modeled_result():
    def trial(name, nets, drawdown, losses, delta, settings):
        positions = [
            dict(ticker=f"P{i}", buy_date="2026-09-28", realised_net=net,
                 completed=True, sale_rows=2)
            for i, net in enumerate(losses)
        ]
        return dict(
            name=name, status="modeled", effective_settings=settings,
            settings_diff=[] if name == "baseline" else [
                dict(field="decision_config.min_trigger_score", original=60, effective=63)],
            summary=dict(
                n_completed_positions=len(losses), n_distinct_sessions=2,
                final_equity_net=10000 + sum(nets.values()), net_profit=sum(nets.values()),
                equity_delta_vs_recorded_config_baseline=sum(delta.values()),
                max_sampled_drawdown_pct=drawdown, commission=1.4, slippage_cost=2,
                position_attribution=dict(
                    positions=positions, missing_position_key_rows=0,
                    completed_positions=len(losses), realised_net=sum(losses),
                    realised_loss_completed_positions=sum(min(0, net) for net in losses)),
                all_ticker_window_net=nets, all_ticker_equity_deltas=delta, warnings=[],
            ),
        )
    return dict(
        holdout={"sessions": ["2026-09-28", "2026-09-29"]},
        baseline=trial("baseline", {"A": 0, "B": 0, "C": -50}, 1, [-50, 0],
                       {"A": 0, "B": 0, "C": 0}, {"decision_config": {"min_trigger_score": 60},
                                                "exit_config": {"scale_out_enabled": False}}),
        frozen_candidate=trial("better", {"A": 120, "B": 80, "C": -100}, 1.1, [-55, 0],
                               {"A": 120, "B": 80, "C": -50},
                               {"decision_config": {"min_trigger_score": 63},
                                "exit_config": {"scale_out_enabled": False}}),
    )


def test_defaults_are_cadence_only_and_independently_copied():
    settings = automatic.validate_settings({})
    assert settings == dict(enabled=True, training_sessions=5, evaluation_sessions=5,
                            max_candidates=16, risk_policy=None)
    settings["enabled"] = False
    assert automatic.DEFAULT_SETTINGS["enabled"]
    assessed = automatic.assess_evaluation(modeled_result())
    assert not assessed["eligible"]
    assert "Configure numeric risk policy" in assessed["reasons"]
    assert not assessed["live_changes_allowed"]
    assert "NOT real-account P&L" in assessed["basis"]


@pytest.mark.parametrize("settings", [
    {"training_sessions": True}, {"evaluation_sessions": False}, {"max_candidates": True},
    {"enabled": 1}, {"training_sessions": 0}, {"evaluation_sessions": 41},
    {"training_sessions": 31, "evaluation_sessions": 30},
    {"max_candidates": 0}, {"max_candidates": 33}, {"max_candidates": float("inf")},
    {"evaluation_sessions": 2.0}, {"training_sessions": float("nan")},
    {"automatic_live_apply": True}, {"risk_policy": {}}, {"risk_policy": "be safe"},
])
def test_settings_reject_invalid_inputs(settings):
    with pytest.raises(calibration.CalibrationError):
        automatic.validate_settings(settings)


@pytest.mark.parametrize("field,value", [
    ("min_completed_positions", True), ("min_completed_positions", 0),
    ("min_completed_positions", 10001), ("min_distinct_sessions", 1),
    ("min_distinct_sessions", 94), ("min_improvement_usd", 0),
    ("max_drawdown_increase_pp", -0.01), ("max_worst_loss_increase_usd", float("nan")),
    ("max_worst_loss_increase_usd", 10**400), ("min_positive_tickers", False),
    ("min_positive_tickers", 0), ("max_largest_contributor_fraction", 0),
    ("max_largest_contributor_fraction", 1.01), ("min_improvement_usd", float("inf")),
    ("min_improvement_usd", "100"), ("unknown", 1),
])
def test_numeric_policy_must_be_explicit_and_valid(policy, field, value):
    policy[field] = value
    with pytest.raises(calibration.CalibrationError):
        automatic.validate_settings({"risk_policy": policy})
    assert not automatic.assess_evaluation(modeled_result(), policy)["eligible"]


def test_settings_boundary_values(policy):
    settings = automatic.validate_settings(dict(
        enabled=False, training_sessions=40, evaluation_sessions=20, max_candidates=32,
        risk_policy={**policy, "min_completed_positions": 10000, "min_distinct_sessions": 93,
                     "max_drawdown_increase_pp": 0, "max_worst_loss_increase_usd": 0,
                     "max_largest_contributor_fraction": 1},
    ))
    assert settings["training_sessions"] + settings["evaluation_sessions"] == 60


def test_parameter_candidates_are_bounded_numeric_deterministic_and_immutable(records):
    data, _ = training_and_holdout(records)
    original = copy.deepcopy(data)
    candidates = automatic.parameter_candidates(data, 32)
    assert candidates == automatic.parameter_candidates(data, 32)
    assert len(candidates["experiments"]) <= 32
    seen = set()
    for row in candidates["experiments"]:
        assert not row["disable_ai_veto"]
        effective, changes = calibration.effective_settings(calibration.settings(data), row)
        assert len(changes) == 1
        for group in calibration.ALLOWLIST:
            for key, value in row[group].items():
                lower, upper = calibration.ALLOWLIST[group][key]
                assert type(value) in (int, float) and lower <= value <= upper
        assert calibration.digest(effective) not in seen
        seen.add(calibration.digest(effective))
    assert data == original
    default = automatic.parameter_candidates(data)["experiments"]
    assert {key for row in default for group in calibration.ALLOWLIST for key in row[group]} == {
        key for fields in calibration.ALLOWLIST.values() for key, bounds in fields.items()
        if bounds != "boolean"
    }


def test_zero_baseline_bounds_requested_priority_dedup_and_name_collisions(records):
    data, _ = training_and_holdout(records)
    data["decision_config"]["min_trigger_score"] = 0
    requests = [
        {"name": "approved", "disable_ai_veto": True},
        {"name": "duplicate", "disable_ai_veto": True},
        {"name": "auto_min_trigger_score_higher", "exit_config": {"scale_out_enabled": False}},
        {"name": "unchanged", "decision_config": {"min_trigger_score": 0}},
    ]
    candidates = automatic.parameter_candidates(data, 32, requests)["experiments"]
    assert [row["name"] for row in candidates[:2]] == ["approved", "auto_min_trigger_score_higher"]
    higher = next(row for row in candidates if row["name"] == "auto_min_trigger_score_higher_2")
    assert higher["decision_config"]["min_trigger_score"] == 1
    assert len(automatic.parameter_candidates(data, 3, requests)["experiments"]) == 3
    assert len(automatic.parameter_candidates(data, 1, requests)["experiments"]) == 1
    with pytest.raises(calibration.CalibrationError, match="reserved"):
        automatic.parameter_candidates(data, requested_experiments=[{"name": "baseline"}])
    with pytest.raises(calibration.CalibrationError, match="must be a list"):
        automatic.parameter_candidates(data, requested_experiments="turn off all stops")
    with pytest.raises(calibration.CalibrationError, match="unsupported"):
        automatic.parameter_candidates(data, requested_experiments=[
            {"name": "bad", "exit_config": {"prove_it_enabled": False}}])


def test_bounded_rotation_reaches_both_directions_across_training_inputs(records):
    data, _ = training_and_holdout(records)
    all_candidates = automatic.parameter_candidates(data, 32)["experiments"]
    expected = {row["name"] for row in all_candidates}
    seen, first_candidates = set(), set()
    for index in range(64):
        # Captured input content, not global randomness or wall-clock time,
        # determines which bounded subset a prospective campaign will investigate.
        changed = {**data, "dataset_label": f"Training dataset {index}"}
        rows = automatic.parameter_candidates(changed, 1)["experiments"]
        first_candidates.add(rows[0]["name"])
        seen.update(row["name"] for row in automatic.parameter_candidates(changed)["experiments"])
    assert seen == expected
    assert "auto_armed_exit_deadline_hours_higher" in first_candidates
    assert "auto_scale_out_trigger_pct_higher" in first_candidates
    assert "auto_scale_out_fraction_higher" in first_candidates
    requested = [{"name": "approved_rule", "exit_config": {"scale_out_enabled": False}}]
    assert all(automatic.parameter_candidates(
        {**data, "dataset_label": f"Campaign {index}"}, 2, requested)["experiments"][0]["name"]
               == "approved_rule" for index in range(4))


@pytest.mark.parametrize("field", [
    "min_trigger_score", "min_pre_breakout_score", "min_relaxed_trigger_score",
])
def test_fractional_requested_scores_reject_before_any_training_trial(records, monkeypatch, field):
    data, _ = training_and_holdout(records)

    def forbidden(*args, **kwargs):
        pytest.fail("An invalid requested score must not consume any replay budget")

    monkeypatch.setattr(calibration, "select", forbidden)
    with pytest.raises(calibration.CalibrationError, match="integer score"):
        automatic.freeze_selection(data, {"max_candidates": 1}, [
            {"name": "first", "disable_ai_veto": True},
            {"name": "fractional", "decision_config": {field: 65.5}},
        ])
    candidates = automatic.parameter_candidates(
        data, 1, [{"name": "integral", "decision_config": {field: 70.0}}])
    assert type(candidates["experiments"][0]["decision_config"][field]) is int


def test_risk_policy_uses_positive_total_not_misleading_net_denominator(policy):
    result = automatic.assess_evaluation(modeled_result(), policy)
    assert result["eligible"], result["reasons"]
    metrics = result["metrics"]
    assert metrics["net_improvement_usd"] == 150
    assert metrics["total_positive_delta_usd"] == 200
    assert metrics["largest_contributor_fraction"] == 0.6  # 120/200, not 120/150.
    assert metrics["drawdown_increase_pp"] == pytest.approx(0.1)
    assert metrics["worst_loss_increase_usd"] == 5
    assert metrics["baseline_completed_positions"] == metrics["candidate_completed_positions"] == 2
    assert result["warnings"] and result["requires_manual_approval"]
    assert not result["live_changes_allowed"]


@pytest.mark.parametrize("which", ["baseline", "frozen_candidate"])
def test_both_portfolios_need_independent_position_count(policy, which):
    result = modeled_result()
    summary = result[which]["summary"]
    summary["position_attribution"]["positions"].pop()  # Remove zero-profit position.
    summary["position_attribution"]["completed_positions"] = summary["n_completed_positions"] = 1
    assessed = automatic.assess_evaluation(result, policy)
    assert not assessed["eligible"]
    label = "Baseline" if which == "baseline" else "Candidate"
    assert any(reason.startswith(f"{label} completed positions 1 below required 2")
               for reason in assessed["reasons"])


@pytest.mark.parametrize("field,value,reason", [
    ("min_distinct_sessions", 3, "Distinct sessions"),
    ("min_improvement_usd", 151, "After-cost improvement"),
    ("max_drawdown_increase_pp", 0.01, "Drawdown increase"),
    ("max_worst_loss_increase_usd", 4, "Worst completed-position loss"),
    ("min_positive_tickers", 3, "Positive ticker contributors"),
    ("max_largest_contributor_fraction", 0.59, "Largest positive contributor"),
])
def test_every_numeric_gate_is_enforced(policy, field, value, reason):
    policy[field] = value
    assessed = automatic.assess_evaluation(modeled_result(), policy)
    assert not assessed["eligible"]
    assert any(text.startswith(reason) for text in assessed["reasons"])


@pytest.mark.parametrize("damage", [
    "missing_key", "missing_ticker", "wrong_deltas", "missing_attribution", "nonfinite",
    "boolean_metric", "duplicate_partial", "wrong_position_count", "rejected", "duplicate_sessions",
])
def test_malformed_or_missing_evidence_fails_closed(policy, damage):
    result = modeled_result()
    summary = result["frozen_candidate"]["summary"]
    if damage == "missing_key":
        summary["position_attribution"]["missing_position_key_rows"] = 1
    elif damage == "missing_ticker":
        summary["position_attribution"]["positions"][0].pop("ticker")
    elif damage == "wrong_deltas":
        summary["all_ticker_equity_deltas"] = {"A": 150}
    elif damage == "missing_attribution":
        summary.pop("all_ticker_window_net")
    elif damage == "nonfinite":
        summary["commission"] = float("nan")
    elif damage == "boolean_metric":
        summary["max_sampled_drawdown_pct"] = True
    elif damage == "duplicate_partial":
        summary["position_attribution"]["positions"].append(
            copy.deepcopy(summary["position_attribution"]["positions"][0]))
    elif damage == "wrong_position_count":
        summary["n_completed_positions"] = 4
    elif damage == "rejected":
        result["frozen_candidate"].update(status="rejected", reason="unsupported path")
    else:
        result["holdout"]["sessions"][1] = result["holdout"]["sessions"][0]
    assessed = automatic.assess_evaluation(result, policy)
    assert not assessed["eligible"]
    assert any(reason.startswith("Unusable modeled portfolio evidence") for reason in assessed["reasons"])


def test_baseline_and_no_actual_settings_change_never_qualify(policy):
    result = modeled_result()
    result["frozen_candidate"]["effective_settings"] = copy.deepcopy(result["baseline"]["effective_settings"])
    assert not automatic.assess_evaluation(result, policy)["eligible"]
    result["frozen_candidate"] = copy.deepcopy(result["baseline"])
    assert not automatic.assess_evaluation(result, policy)["eligible"]


def test_rejected_training_attempts_stay_visible_and_plan_remains_verifiable(records, monkeypatch):
    data, _ = training_and_holdout(records)
    original = calibration._run

    def reject(dataset, experiment):
        if experiment["name"] == "unsupported":
            raise core.ReplayInputError("Unsupported replacement path")
        return original(dataset, experiment)

    monkeypatch.setattr(calibration, "_run", reject)
    frozen = automatic.freeze_selection(data, {"max_candidates": 2},
                                        [{"name": "unsupported", "disable_ai_veto": True}])
    plan = frozen["selection"]
    calibration.verify_plan(plan)
    assert len(frozen["search_trials"]) == plan["total_trials_including_baseline"] == 3
    assert plan["candidate_trial_count"] == 2
    assert frozen["search_trials"][1]["status"] == "rejected"
    assert "Unsupported replacement path" in frozen["search_trials"][1]["reason"]
    assert not frozen["search_trials"][1]["eligibility"]["eligible"]


def test_actual_frozen_chronological_shadow_evaluation_preserves_inputs(records, monkeypatch):
    training, holdout = training_and_holdout(records)
    original_inputs = copy.deepcopy((training, holdout))
    frozen = automatic.freeze_selection(training, {"max_candidates": 1},
                                        [{"name": "approved_veto_test", "disable_ai_veto": True}])
    plan = frozen["selection"]
    assert plan["selected_name"] == "approved_veto_test"
    assert plan["engine"] == calibration.engine_fingerprint()
    assert frozen["automatic_engine"] == automatic.automatic_fingerprint()
    assert frozen["hypotheses"][0]["requires_investigation_approval"]
    for hypothesis in frozen["hypotheses"]:
        assert hypothesis["experiment"] == calibration.experiments({
            "experiments": [hypothesis["experiment"]],
        })[0]
    calls, original = [], calibration._run

    def record(dataset, experiment):
        calls.append((dataset["capture_evidence"]["sessions"], experiment["name"]))
        return original(dataset, experiment)

    monkeypatch.setattr(calibration, "_run", record)
    report = automatic.evaluate_selection(frozen, holdout)
    assert calls == [(["2026-09-29"], "baseline"), (["2026-09-29"], "approved_veto_test")]
    assert report["evaluation"]["frozen_candidate"]["summary"]["equity_delta_vs_recorded_config_baseline"] < 0
    assert not report["eligibility"]["eligible"]
    assert "Configure numeric risk policy" in report["eligibility"]["reasons"]
    assert plan["selected_name"] == "approved_veto_test"
    assert (training, holdout) == original_inputs
    with pytest.raises(calibration.CalibrationError, match="prefix|later|nonoverlapping"):
        automatic.evaluate_selection(frozen, training)


def test_frozen_engine_fingerprint_cannot_be_substituted(records, monkeypatch):
    training, holdout = training_and_holdout(records)
    frozen = automatic.freeze_selection(training, {"max_candidates": 1})
    fingerprint = calibration.engine_fingerprint()
    fingerprint["sha256"] = "not-the-actual-engine"
    monkeypatch.setattr(calibration, "engine_fingerprint", lambda: fingerprint)
    with pytest.raises(calibration.CalibrationError, match="engine/config fingerprint"):
        automatic.evaluate_selection(frozen, holdout)


def test_multisession_holdout_keeps_hypothetical_holdings_continuously(records, monkeypatch):
    source = capture.build_dataset(records, DAY, DAY)
    frames, seed = [], None
    for offset, price in enumerate((104, 101, 102)):
        day = shift_window(source, days=offset)
        for event in day["events"]:
            context = event.get("cycle_context", {})
            for key in ("cycle_id", "preceding_buy_cycle_id"):
                if key in context:
                    context[key] = f"day{offset}-" + context[key]
            if event["timestamp"][11:16] >= "09:35":
                event["market_observations"]["CANDIDATE"]["price"] = price
        initial, more = seed_and_frames(day)
        if seed is None:
            seed = initial
        frames.extend(more)
    _, rows = durable_records(seed, frames)
    training = shadow.export_shadow_dataset(seed, rows, DAY, DAY)
    holdout = shadow.export_shadow_dataset(seed, rows, "2026-09-29", "2026-09-30")
    frozen = automatic.freeze_selection(training, {"max_candidates": 1, "evaluation_sessions": 2},
                                        [{"name": "approved", "disable_ai_veto": True}])
    calls, original = [], shadow.replay_window

    def observe(dataset, **kwargs):
        run = original(dataset, **kwargs)
        calls.append((dataset["capture_evidence"]["sessions"], kwargs["disable_ai_veto"], run))
        return run

    monkeypatch.setattr(shadow, "replay_window", observe)
    result = automatic.evaluate_selection(frozen, holdout)
    assert len(calls) == 2  # One full-window run per portfolio, not one reset per day.
    assert all(call[0] == ["2026-09-29", "2026-09-30"] for call in calls)
    candidate_run = next(run for _, disabled, run in calls if disabled)
    purchases = [fill for fill in candidate_run["fills"]
                 if fill["ticker"] == "CANDIDATE" and fill["side"] == "BUY"]
    assert len(purchases) == 1
    assert purchases[0]["timestamp"].startswith("2026-09-29")
    assert any(position["ticker"] == "CANDIDATE" for position in candidate_run["open_positions"])
    assert result["eligibility"]["metrics"]["distinct_sessions"] == 2


def test_training_risk_rejection_cannot_be_hidden_by_good_holdout(policy, monkeypatch):
    result = modeled_result()
    result["training"] = copy.deepcopy(result["holdout"])
    result["training_baseline"] = copy.deepcopy(result["baseline"])
    result["training_selected"] = copy.deepcopy(result["frozen_candidate"])
    result["training_selected"]["summary"]["max_sampled_drawdown_pct"] = 2
    monkeypatch.setattr(calibration, "evaluate", lambda plan, holdout: result)
    frozen = dict(selection={}, settings={"risk_policy": policy}, hypotheses=[],
                  risk_policy_sha256=calibration.digest(policy),
                  automatic_engine=automatic.automatic_fingerprint())
    evaluated = automatic.evaluate_selection(frozen, {})
    assert not evaluated["eligibility"]["eligible"]
    assert any(reason.startswith("Selected training candidate: Drawdown increase")
               for reason in evaluated["eligibility"]["reasons"])
    assert not automatic.assess_evaluation(result, policy)["eligible"]
    holdout_only = {key: result[key] for key in ("baseline", "frozen_candidate", "holdout")}
    assert automatic.assess_evaluation(holdout_only, policy)["eligible"]


def test_full_evaluation_cannot_omit_training_risk_evidence(policy):
    result = modeled_result()
    result["artifact_type"] = "intraday_calibration_holdout"
    assessment = automatic.assess_evaluation(result, policy)
    assert not assessment["eligible"]
    assert any(reason.startswith("Selected training candidate: Unusable modeled portfolio")
               for reason in assessment["reasons"])


def test_no_policy_at_selection_cannot_be_retroactively_qualified(records, policy):
    training, holdout = training_and_holdout(records)
    frozen = automatic.freeze_selection(training, {"max_candidates": 1})
    assert frozen["risk_policy_sha256"] == calibration.digest(None)
    with pytest.raises(calibration.CalibrationError, match="future holdout"):
        automatic.evaluate_selection(frozen, holdout, policy)
    frozen["settings"]["risk_policy"] = policy
    with pytest.raises(calibration.CalibrationError, match="Frozen risk policy fingerprint"):
        automatic.evaluate_selection(frozen, holdout)


def test_frozen_numeric_policy_cannot_be_relaxed_after_selection(records, policy):
    training, holdout = training_and_holdout(records)
    frozen = automatic.freeze_selection(training, {"max_candidates": 1, "risk_policy": policy})
    assert frozen["risk_policy_sha256"] == calibration.digest(policy)
    changed = {**policy, "min_improvement_usd": policy["min_improvement_usd"] / 2}
    with pytest.raises(calibration.CalibrationError, match="future holdout"):
        automatic.evaluate_selection(frozen, holdout, changed)
    accepted = automatic.evaluate_selection(frozen, holdout, copy.deepcopy(policy))
    assert not accepted["eligibility"]["eligible"]  # Same policy allowed, insufficient evidence remains.


@pytest.mark.parametrize("change", ["version", "loaded_function", "defaults"])
def test_automatic_logic_fingerprint_invalidates_old_campaigns(records, monkeypatch, change):
    training, holdout = training_and_holdout(records)
    frozen = automatic.freeze_selection(training, {"max_candidates": 1})
    if change == "version":
        monkeypatch.setattr(automatic, "VERSION", automatic.VERSION + 1)
    elif change == "loaded_function":
        original = automatic._number

        def replacement(*args, **kwargs):
            return original(*args, **kwargs)

        monkeypatch.setattr(automatic, "_number", replacement)
    else:
        monkeypatch.setitem(automatic.DEFAULT_SETTINGS, "max_candidates", 8)
    with pytest.raises(calibration.CalibrationError, match="Automatic calibration code/config fingerprint"):
        automatic.evaluate_selection(frozen, holdout)


def test_runtime_budget_base_exception_aborts_instead_of_rejecting_one_trial(records, monkeypatch):
    training, _ = training_and_holdout(records)
    original = calibration._run

    class BudgetExpired(BaseException):
        pass

    def expire(dataset, experiment):
        if experiment["name"] != "baseline":
            raise BudgetExpired("Campaign wall-clock budget exhausted")
        return original(dataset, experiment)

    monkeypatch.setattr(calibration, "_run", expire)
    with pytest.raises(BudgetExpired, match="wall-clock budget"):
        automatic.freeze_selection(training, {"max_candidates": 2})
