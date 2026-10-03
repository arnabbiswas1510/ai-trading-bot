"""Pure, bounded research orchestration. No credentials, persistence or live writes.

Requested experiments must come from the caller's approved investigation store,
never free-form feedback. Eligibility means numeric research-policy compliance,
not deployment approval. The underlying calibration artifacts retain their real
engine fingerprint, original input evidence and chronological verification.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import inspect
import math
from pathlib import Path

from research import calibrate_intraday as calibration

VERSION = 1
DEFAULT_SETTINGS = {
    "enabled": True,
    "training_sessions": 5,
    "evaluation_sessions": 5,
    "max_candidates": 16,
    "risk_policy": None,
}
RISK_FIELDS = {
    "min_completed_positions", "min_distinct_sessions", "min_improvement_usd",
    "max_drawdown_increase_pp", "max_worst_loss_increase_usd",
    "min_positive_tickers", "max_largest_contributor_fraction",
}
BASIS = (
    "After-cost modeled candidate equity versus the modeled recorded-configuration "
    "baseline, NOT real-account P&L. Passing a numeric research policy is not proof "
    "of profitability or permission to deploy; exact human approval remains required."
)
SMALL_SAMPLE = (
    "Small sample: fewer than 30 completed positions in at least one portfolio; "
    "results remain exploratory. Positions from the same session are not independent."
)


def automatic_fingerprint():
    """Bind automatic search/risk code and loaded functions, not just replay code."""
    content = {
        "version": VERSION,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "callable_sources": {
            name: hashlib.sha256(inspect.getsource(value).encode()).hexdigest()
            for name, value in globals().items()
            if inspect.isfunction(value) and value.__module__ == __name__
        },
        "defaults": copy.deepcopy(DEFAULT_SETTINGS),
        "risk_fields": sorted(RISK_FIELDS),
        "basis": BASIS, "small_sample_warning": SMALL_SAMPLE,
    }
    return {"sha256": calibration.digest(content), **content}


def _number(value, name, minimum=None, maximum=None, integer=False):
    valid = type(value) is int if integer else type(value) in (int, float)
    try:
        valid = valid and math.isfinite(value)
    except OverflowError:
        valid = False
    calibration.require(valid, f"{name} must be a finite {'integer' if integer else 'number'}")
    calibration.require(minimum is None or value >= minimum, f"{name} must be >= {minimum}")
    calibration.require(maximum is None or value <= maximum, f"{name} must be <= {maximum}")
    return value


def _risk_policy(value):
    if value is None:
        return None
    calibration.require(isinstance(value, dict) and set(value) == RISK_FIELDS,
                        "risk_policy must contain exactly the documented numeric risk fields")
    _number(value["min_completed_positions"], "min_completed_positions", 1, 10000, True)
    _number(value["min_distinct_sessions"], "min_distinct_sessions", 2, 93, True)
    _number(value["min_positive_tickers"], "min_positive_tickers", 1, integer=True)
    for key in ("min_improvement_usd", "max_drawdown_increase_pp",
                "max_worst_loss_increase_usd", "max_largest_contributor_fraction"):
        _number(value[key], key, 0, 1 if key == "max_largest_contributor_fraction" else None)
    for key in ("min_improvement_usd", "max_largest_contributor_fraction"):
        calibration.require(value[key] > 0, f"{key} must be > 0")
    return copy.deepcopy(value)


def validate_settings(value):
    """Merge cadence defaults, rejecting unknown settings and invented risk policy."""
    calibration.require(isinstance(value, dict), "Settings must be an object")
    calibration.require(not (value.keys() - DEFAULT_SETTINGS.keys()), "Unknown automatic calibration settings")
    result = copy.deepcopy(DEFAULT_SETTINGS)
    result.update(copy.deepcopy(value))
    calibration.require(type(result["enabled"]) is bool, "enabled must be boolean")
    for key in ("training_sessions", "evaluation_sessions"):
        _number(result[key], key, 1, 40, True)
    calibration.require(result["training_sessions"] + result["evaluation_sessions"] <= 60,
                        "Combined training/evaluation sessions must be <= 60")
    _number(result["max_candidates"], "max_candidates", 1, 32, True)
    result["risk_policy"] = _risk_policy(result["risk_policy"])
    return result


def parameter_candidates(data, max_candidates=16, requested_experiments=None):
    """Prioritize approved requests; rotate numeric neighbors by immutable input hash."""
    _number(max_candidates, "max_candidates", 1, 32, True)
    requested = [] if requested_experiments is None else requested_experiments
    calibration.require(isinstance(requested, list), "Approved requested_experiments must be a list")
    normalized = calibration.experiments({"experiments": requested}) if requested else []
    for row in normalized:
        for key, value in row["decision_config"].items():
            if key.endswith("_score"):
                calibration.require(value == int(value), f"{row['name']}.{key} must be an integer score")
                row["decision_config"][key] = int(value)
    original = calibration.settings(data)
    rows, seen = [], set()
    reserved = {"baseline", *(row["name"] for row in normalized)}

    def append(row):
        effective, changes = calibration.effective_settings(original, row)
        key = calibration.digest(effective)
        if changes and key not in seen and len(rows) < max_candidates:
            rows.append(row)
            seen.add(key)

    for row in normalized:
        append(row)
    proposals, proposed_settings = [], set(seen)
    for direction, sign in (("lower", -1), ("higher", 1)):
        for group, field, bounds in (
            (group, field, bounds) for group, fields in calibration.ALLOWLIST.items()
            for field, bounds in fields.items()
        ):
            if bounds == "boolean":
                continue
            old = original[group][field]
            _number(old, f"{group}.{field}", *bounds)
            # Five percent of the current value; a zero baseline uses 1% of its
            # permitted range. Score thresholds move by at least one score point.
            step = abs(old) * 0.05 if old else (bounds[1] - bounds[0]) * 0.01
            if field.endswith("_score"):
                step = max(1, round(step))
            value = round(max(bounds[0], min(bounds[1], old + sign * step)), 10)
            if field.endswith("_score"):
                value = int(round(value))
            if value == old:
                continue
            name = f"auto_{field}_{direction}"
            suffix = 2
            while name in reserved:
                name = f"auto_{field}_{direction}_{suffix}"
                suffix += 1
            reserved.add(name)
            row = calibration.experiments({"experiments": [
                {"name": name, group: {field: value}}]})[0]
            effective, changes = calibration.effective_settings(original, row)
            key = calibration.digest(effective)
            if changes and key not in proposed_settings:
                proposals.append(row)
                proposed_settings.add(key)
    if proposals:
        offset = int(calibration.digest(data), 16) % len(proposals)
        for row in proposals[offset:] + proposals[:offset]:
            append(row)
    calibration.require(rows, "No changed, supported parameter candidates remain")
    return {"experiments": rows}


def _hypotheses(data=None, baseline=None):
    """Evidence-based requests for permission, never executable feedback."""
    result = []
    if data is not None:
        vetoed = sorted({trigger["ticker"] for event in data["events"]
                         for trigger in event.get("triggers", [])
                         if trigger.get("ai_grade") == "D" and trigger.get("ticker")})
        if vetoed:
            result.append({
                "id": "investigate_ai_veto", "status": "investigation_required",
                "requires_investigation_approval": True,
                "title": "Investigate the recorded AI D-grade veto",
                "evidence": {"recorded_d_grade_tickers": vetoed},
                "rationale": "D-grade candidates occur in the inputs. Investigate their quality before "
                             "authorizing any veto-removal experiment; their presence proves no benefit.",
                "experiment": {"name": "approved_no_ai_veto", "disable_ai_veto": True},
            })
    if baseline and baseline.get("status") == "modeled":
        summary = baseline["summary"]
        partials = [row for row in summary["position_attribution"]["positions"]
                    if row["sale_rows"] > 1 or not row["completed"]]
        if partials and baseline["effective_settings"]["exit_config"]["scale_out_enabled"]:
            result.append({
                "id": "investigate_scale_out", "status": "investigation_required",
                "requires_investigation_approval": True,
                "title": "Investigate the scale-out rule",
                "evidence": {"positions_with_partial_sales": len(partials)},
                "rationale": "Modeled partial sales occurred. Investigate their profit and risk effects "
                             "before authorizing a no-scale-out comparison; partials alone prove no harm.",
                "experiment": {"name": "approved_no_scale_out",
                               "exit_config": {"scale_out_enabled": False}},
            })
    for hypothesis in result:
        hypothesis["experiment"] = calibration.experiments({
            "experiments": [hypothesis["experiment"]],
        })[0]
    return result


def freeze_selection(data, settings, requested_experiments=None):
    """Freeze the existing profit-ranked plan without hiding rejected attempts.

    The verified selector ranks profit only. Risk assessments are separately
    recorded, not smuggled into its immutable ranking or trial counts.
    """
    settings = validate_settings(settings)
    candidates = parameter_candidates(data, settings["max_candidates"], requested_experiments)
    selection = calibration.select(data, candidates)
    trials = copy.deepcopy(selection["training_trials"])
    for trial in trials:
        trial["eligibility"] = assess_evaluation({
            "baseline": selection["training_trials"][0],
            "frozen_candidate": trial, "holdout": selection["training"],
        }, settings["risk_policy"])
    return {
        "selection": selection, "search_trials": trials, "settings": settings,
        "automatic_engine": automatic_fingerprint(),
        "risk_policy_sha256": calibration.digest(settings["risk_policy"]),
        "hypotheses": _hypotheses(data, selection["training_trials"][0]),
    }


def _portfolio(trial, label, sessions):
    calibration.require(isinstance(trial, dict) and trial.get("status") == "modeled",
                        f"{label} portfolio was not modeled: {trial.get('reason', 'missing report')}"
                        if isinstance(trial, dict) else f"{label} portfolio is missing")
    summary = trial["summary"]
    calibration.canonical(summary)  # Reject any nonfinite field, not just displayed metrics.
    for field in ("final_equity_net", "net_profit", "equity_delta_vs_recorded_config_baseline",
                  "max_sampled_drawdown_pct", "commission", "slippage_cost"):
        _number(summary[field], f"{label}.{field}",
                0 if field in ("max_sampled_drawdown_pct", "commission", "slippage_cost") else None)
    _number(summary["n_distinct_sessions"], f"{label}.n_distinct_sessions", 1, integer=True)
    calibration.require(summary["n_distinct_sessions"] == len(sessions),
                        f"{label} distinct-session count does not match the holdout")
    attribution = summary["position_attribution"]
    _number(attribution["missing_position_key_rows"], f"{label}.missing_position_key_rows", 0, integer=True)
    calibration.require(attribution["missing_position_key_rows"] == 0,
                        f"{label} attribution has missing position keys")
    calibration.require(isinstance(attribution["positions"], list), f"{label} position attribution is missing")
    keys, completed, nets = set(), [], []
    for row in attribution["positions"]:
        ticker, buy_date = row["ticker"], row["buy_date"]
        calibration.require(isinstance(ticker, str) and bool(ticker.strip())
                            and isinstance(buy_date, str) and bool(buy_date.strip()),
                            f"{label} attribution has missing position keys")
        key = ticker, buy_date
        calibration.require(key not in keys, f"{label} partial sales were not aggregated by position")
        keys.add(key)
        calibration.require(type(row["completed"]) is bool, f"{label} completed flag must be boolean")
        _number(row["sale_rows"], f"{label}.sale_rows", 1, integer=True)
        net = _number(row["realised_net"], f"{label}.realised_net")
        nets.append(net)
        if row["completed"]:
            completed.append(net)
    for value in (attribution["completed_positions"], summary["n_completed_positions"]):
        _number(value, f"{label}.completed_positions", 0, integer=True)
        calibration.require(value == len(completed), f"{label} completed-position counts disagree")
    for field, expected in (
        ("realised_net", sum(nets)),
        ("realised_loss_completed_positions", sum(min(0, net) for net in completed)),
    ):
        _number(attribution[field], f"{label}.{field}")
        calibration.require(math.isclose(attribution[field], expected, rel_tol=1e-10, abs_tol=1e-6),
                            f"{label} position attribution does not reconcile")
    for field in ("all_ticker_window_net", "all_ticker_equity_deltas"):
        values = summary[field]
        calibration.require(isinstance(values, dict), f"{label} ticker attribution is missing")
        for ticker, value in values.items():
            calibration.require(isinstance(ticker, str) and bool(ticker.strip()),
                                f"{label} ticker attribution has missing keys")
            _number(value, f"{label}.{field}.{ticker}")
        expected = summary["net_profit" if field == "all_ticker_window_net"
                           else "equity_delta_vs_recorded_config_baseline"]
        calibration.require(math.isclose(sum(values.values()), expected, rel_tol=1e-10, abs_tol=1e-6),
                            f"{label} ticker attribution does not reconcile")
    return summary, len(completed), max([0, *(-net for net in completed)])


def assess_evaluation(result, policy=None):
    """Check holdout and reported training risk; verify frozen-policy binding separately."""
    reasons, warnings, metrics = [], [], {}
    try:
        policy = _risk_policy(policy)
    except calibration.CalibrationError as exc:
        reasons.append(f"Invalid numeric risk policy: {exc}")
        policy = None
    if policy is None:
        reasons.append("Configure numeric risk policy")
    try:
        sessions = result["holdout"]["sessions"]
        calibration.require(isinstance(sessions, list) and bool(sessions),
                            "Distinct holdout sessions are missing")
        calibration.require(all(isinstance(day, str) and dt.date.fromisoformat(day).isoformat() == day
                                for day in sessions), "Invalid holdout sessions")
        calibration.require(sessions == sorted(set(sessions)), "Holdout sessions must be unique and ordered")
        baseline = result["baseline"]
        candidate = result["frozen_candidate"]
        b, b_count, b_worst = _portfolio(baseline, "Baseline", sessions)
        c, c_count, c_worst = _portfolio(candidate, "Candidate", sessions)
        if min(b_count, c_count) < 30:
            warnings.append(SMALL_SAMPLE)
        calibration.require(isinstance(baseline["effective_settings"], dict)
                            and isinstance(candidate["effective_settings"], dict),
                            "Modeled settings are missing")
        changed = baseline["effective_settings"] != candidate["effective_settings"]
        calibration.require(isinstance(candidate["settings_diff"], list), "Candidate settings diff is missing")
        if (candidate["name"] == "baseline" or not changed or not candidate["settings_diff"]):
            reasons.append("Frozen candidate does not change the modeled baseline settings")
        net = c["final_equity_net"] - b["final_equity_net"]
        calibration.require(b["equity_delta_vs_recorded_config_baseline"] == 0,
                            "Baseline is not the modeled recorded-configuration baseline")
        calibration.require(all(value == 0 for value in b["all_ticker_equity_deltas"].values()),
                            "Baseline ticker deltas must be zero")
        calibration.require(math.isclose(net, c["equity_delta_vs_recorded_config_baseline"], abs_tol=1e-6)
                            and math.isclose(net, c["net_profit"] - b["net_profit"], abs_tol=1e-6),
                            "Candidate equity change does not reconcile to the modeled baseline")
        tickers = b["all_ticker_window_net"].keys() | c["all_ticker_window_net"].keys()
        deltas = {ticker: c["all_ticker_window_net"].get(ticker, 0)
                  - b["all_ticker_window_net"].get(ticker, 0) for ticker in tickers}
        calibration.require(c["all_ticker_equity_deltas"].keys() == deltas.keys()
                            and all(math.isclose(value, c["all_ticker_equity_deltas"][ticker], abs_tol=1e-6)
                                    for ticker, value in deltas.items()),
                            "Candidate ticker delta attribution is incomplete or inconsistent")
        positive = [value for value in deltas.values() if value > 0]
        positive_total = sum(positive)
        concentration = max(positive) / positive_total if positive else None
        # Both engines emit drawdown already multiplied by 100: subtract directly
        # to obtain percentage points, never multiply this difference by 100 again.
        metrics.update(
            net_improvement_usd=net,
            drawdown_increase_pp=c["max_sampled_drawdown_pct"] - b["max_sampled_drawdown_pct"],
            worst_loss_increase_usd=c_worst - b_worst,
            positive_tickers=len(positive), largest_contributor_fraction=concentration,
            total_positive_delta_usd=positive_total, all_ticker_equity_deltas=deltas,
            baseline_completed_positions=b_count, candidate_completed_positions=c_count,
            distinct_sessions=len(sessions),
            baseline_drawdown_pct=b["max_sampled_drawdown_pct"],
            candidate_drawdown_pct=c["max_sampled_drawdown_pct"],
            baseline_worst_loss_usd=b_worst, candidate_worst_loss_usd=c_worst,
            baseline_net_profit_usd=b["net_profit"], candidate_net_profit_usd=c["net_profit"],
        )
        calibration.canonical(metrics)
        if policy:
            checks = [
                (b_count >= policy["min_completed_positions"],
                 f"Baseline completed positions {b_count} below required {policy['min_completed_positions']}"),
                (c_count >= policy["min_completed_positions"],
                 f"Candidate completed positions {c_count} below required {policy['min_completed_positions']}"),
                (len(sessions) >= policy["min_distinct_sessions"],
                 f"Distinct sessions {len(sessions)} below required {policy['min_distinct_sessions']}"),
                (net >= policy["min_improvement_usd"],
                 f"After-cost improvement ${net:.2f} below required ${policy['min_improvement_usd']:.2f}"),
                (metrics["drawdown_increase_pp"] <= policy["max_drawdown_increase_pp"],
                 f"Drawdown increase {metrics['drawdown_increase_pp']:.4f} pp exceeds policy "
                 f"{policy['max_drawdown_increase_pp']:.4f} pp"),
                (metrics["worst_loss_increase_usd"] <= policy["max_worst_loss_increase_usd"],
                 f"Worst completed-position loss increase ${metrics['worst_loss_increase_usd']:.2f} exceeds "
                 f"policy ${policy['max_worst_loss_increase_usd']:.2f}"),
                (len(positive) >= policy["min_positive_tickers"],
                 f"Positive ticker contributors {len(positive)} below required {policy['min_positive_tickers']}"),
                (concentration is not None and concentration <= policy["max_largest_contributor_fraction"],
                 f"Largest positive contributor fraction {concentration} exceeds policy "
                 f"{policy['max_largest_contributor_fraction']} or positive attribution is absent"),
            ]
            reasons.extend(reason for passed, reason in checks if not passed)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        reasons.append(f"Unusable modeled portfolio evidence: {exc}")
    if isinstance(result, dict) and (
        result.get("artifact_type") == "intraday_calibration_holdout"
        or "training_baseline" in result or "training_selected" in result
    ):
        training = assess_evaluation({
            "baseline": result.get("training_baseline"),
            "frozen_candidate": result.get("training_selected"), "holdout": result.get("training"),
        }, policy)
        warnings.extend(f"Training: {warning}" for warning in training["warnings"])
        # Selection remains the verified profit winner. Do not silently re-rank
        # or hide its excessive training risk behind an attractive later holdout.
        for reason in training["reasons"]:
            if reason.startswith(("Unusable modeled portfolio", "Drawdown increase",
                                  "Worst completed-position loss")):
                reasons.append(f"Selected training candidate: {reason}")
    return {
        "eligible": not reasons, "reasons": reasons, "metrics": metrics, "warnings": warnings,
        "requires_manual_approval": True, "live_changes_allowed": False, "basis": BASIS,
    }


def evaluate_selection(frozen, holdout, policy=None):
    """Evaluate the fixed holdout under the policy declared before seeing its data."""
    calibration.require(frozen.get("automatic_engine") == automatic_fingerprint(),
                        "Automatic calibration code/config fingerprint changed; "
                        "start a new prospective campaign")
    settings = validate_settings(frozen["settings"])
    policy_digest = calibration.digest(settings["risk_policy"])
    calibration.require(frozen.get("risk_policy_sha256") == policy_digest,
                        "Frozen risk policy fingerprint mismatch; start a new prospective campaign")
    if policy is not None:
        calibration.require(calibration.digest(_risk_policy(policy)) == policy_digest,
                            "Risk policy differs from the frozen campaign; start a new campaign "
                            "with a future holdout, not a retrospective policy change")
    policy = settings["risk_policy"]
    evaluation = calibration.evaluate(frozen["selection"], holdout)
    eligibility = assess_evaluation(evaluation, policy)
    hypotheses = copy.deepcopy(frozen.get("hypotheses", []))
    known = {row["id"] for row in hypotheses}
    hypotheses.extend(row for row in _hypotheses(baseline=evaluation["baseline"]) if row["id"] not in known)
    return {"evaluation": evaluation, "eligibility": eligibility, "hypotheses": hypotheses}
