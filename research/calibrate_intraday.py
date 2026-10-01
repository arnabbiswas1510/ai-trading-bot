"""Bounded OFFLINE calibration; proposals require human approval, never live writes.

  python3 research/calibrate_intraday.py select training.json candidates.json --output plan.json
  python3 research/calibrate_intraday.py evaluate plan.json holdout.json --output report.json

Candidates: {"experiments": [{"name": "score70", "decision_config":
{"min_trigger_score": 70}}, {"name": "no_veto", "disable_ai_veto": true}]}.
Only ALLOWLIST fields are experimental; shared exit_rules globals are not tunable.
Plan digests detect accidental alteration, not malicious rewriting/signatures.
Keep plans and inputs for audit. Reusing holdout data invalidates its independence.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import intraday_replay as capture
from research import live_rule_replay as core

VERSION = 1
MAX_EXPERIMENTS = 32
MAX_BYTES = 64 * 1024 * 1024
CONFIG_KEYS = (*capture.CONFIG_KEYS, "scope")
# Fractions, not percentage points; score floors are on the recorded 0–100 scale.
ALLOWLIST = {
    "decision_config": {
        "min_trigger_score": (0, 100),
        "min_pre_breakout_score": (0, 100),
        "min_relaxed_trigger_score": (0, 100),
        "min_vol_surge_gate": (0, 10),
        "max_pivot_extension": (0, 0.2),
        "max_pivot_breakdown": (0, 0.2),
        "max_pre_breakout_pivot_dist": (0, 0.2),
    },
    "exit_config": {
        "armed_exit_deadline_hours": (0.25, 24),
        "scale_out_enabled": "boolean",
        "scale_out_trigger_pct": (0.005, 0.5),
        "scale_out_fraction": (0.01, 0.99),
    },
}
BASELINE = {"name": "baseline", "disable_ai_veto": False,
            "decision_config": {}, "exit_config": {}}
LIMITATIONS = [
    "Research-only proposal requiring human approval; no settings or orders are changed.",
    "Not proof of profitability: sampled-fill simulation, not exact brokerage execution.",
    "Candidate selection tests multiple hypotheses; more trials increase the chance of a lucky winner.",
    "Positions observed in the same session share market conditions; position count is not an "
    "independent sample size. Distinct-session count exposes this date clustering, without an approval gate.",
    "Holdout means later data not used to choose settings. This tool cannot detect prior human inspection "
    "or reuse in another plan; repeated evaluation destroys that independence.",
    "Each window starts from its own fresh actual account. Windows are independent simulations, "
    "not a continuous candidate portfolio; do not sum their gains.",
    "The recorded-configuration baseline is simulated after costs, not the account's realised live return.",
    "Top-one/top-three exclusions subtract the largest positive ticker contributions only, not portfolio re-simulations; "
    "they do not price replacement trades or blocked slots.",
    "No Sharpe ratio or annualised performance is inferred from the sampled cadence.",
    "Shared exit_rules globals (Prove-It bands/floor, profit-trail ladder, power-hold rules), "
    "sizing/slots, costs, cooling-off, rotation, manual/external activity and capture quality "
    "cannot be overridden. Unsupported candidate paths reject the whole candidate, not selected rows.",
    "Initial holdings and protective orders remain the recorded actual state, even when later "
    "simulated decisions use experimental settings.",
    "Recorded trailing-order anchors are sampled seeds, not independently verified broker high-water marks. "
    "IBKR/FMP/delayed observations retain their source labels; no unobserved intrabar prices are inferred.",
    "Fills assume immediate full execution, successful bracket changes and immediately available cash. "
    "Recorded commission/slippage inputs are modeling assumptions, not measured future costs.",
    "Shared-rule snapshot compatibility does not independently prove historical deployment-code provenance. "
    "Recorded exogenous gate outcomes remain fixed in every trial.",
]
ENGINE_FILES = (
    "research/calibrate_intraday.py", "research/live_rule_replay.py", "intraday_replay.py",
    "shadow_engine.py",
    "shadow_inputs.py", "research_configuration.py", "market_direction.py", "indicators.py",
    "decision_core.py", "exit_core.py", "exit_rules.py", "cooling_off.py",
    "market_calendar.py", "trade_costs.py", "trigger_audit.py", "config.py",
)


class CalibrationError(ValueError):
    """Unsafe, incompatible or modified experiment input."""


def require(condition, message):
    if not condition:
        raise CalibrationError(message)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, OverflowError) as exc:
        raise CalibrationError(f"Expected finite JSON: {exc}") from exc


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Input exceeds 64 MiB")
    try:
        value = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as exc:
        raise CalibrationError(f"Invalid JSON: {exc}") from exc
    canonical(value)
    return value


def engine_fingerprint():
    root = Path(__file__).resolve().parents[1]
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ENGINE_FILES}
    # Include effective imported constants, not just source: env overrides can change math.
    constants = {}
    for module in (core, core.dc, core.ec, core.er, core.cooling_off):
        for name, value in vars(module).items():
            if name.isupper() and isinstance(value, (str, int, float, bool, list, tuple, dict, type(None))):
                try:
                    constants[f"{module.__name__}.{name}"] = json.loads(canonical(value))
                except CalibrationError:
                    pass
    packages = {}
    for name in ("exchange-calendars", "pandas", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    content = dict(files=files, constants=constants, packages=packages,
                   callable_sources={
                       f"{module.__name__}.{name}": hashlib.sha256(inspect.getsource(value).encode()).hexdigest()
                       for module in (core, core.dc, core.ec, core.er, core.cooling_off)
                       for name, value in vars(module).items()
                       if inspect.isfunction(value) and value.__module__ == module.__name__
                   },
                   replay_methods={
                       name: hashlib.sha256(inspect.getsource(value).encode()).hexdigest()
                       for name, value in vars(core._Replay).items() if inspect.isfunction(value)
                   },
                   python=list(sys.version_info[:3]), format_version=VERSION)
    return dict(sha256=digest(content), **content)


def experiments(config):
    require(isinstance(config, dict) and set(config) == {"experiments"},
            "Candidate file must contain only experiments")
    rows = config["experiments"]
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_EXPERIMENTS,
            "Supply 1–32 explicitly named experiments")
    result, names = [], {"baseline"}
    for row in rows:
        require(isinstance(row, dict), "Experiment must be an object")
        require(not (row.keys() - {"name", "disable_ai_veto", *ALLOWLIST}),
                "Unknown experiment keys; shared rules/replay/cost overrides are unsupported")
        name = row.get("name")
        require(isinstance(name, str) and name == name.strip() and 1 <= len(name) <= 80,
                "Experiment name must be nonempty, unpadded and at most 80 characters")
        require(name not in names, f"Duplicate/reserved experiment name: {name}")
        names.add(name)
        normalized = copy.deepcopy(BASELINE)
        normalized["name"] = name
        value = row.get("disable_ai_veto", False)
        require(type(value) is bool, f"{name}.disable_ai_veto must be boolean")
        normalized["disable_ai_veto"] = value
        for group, fields in ALLOWLIST.items():
            overrides = row.get(group, {})
            require(isinstance(overrides, dict), f"{name}.{group} must be an object")
            require(not (overrides.keys() - fields.keys()),
                    f"{name}.{group}: unsupported overrides {sorted(overrides.keys() - fields.keys())}; "
                    "shared exit_rules globals are not parameterised")
            for key, value in overrides.items():
                limits = fields[key]
                if limits == "boolean":
                    require(type(value) is bool, f"{name}.{key} must be boolean")
                else:
                    require(type(value) in (int, float) and limits[0] <= value <= limits[1]
                            and math.isfinite(value),
                            f"{name}.{key} must be a finite number in {limits}")
            normalized[group] = copy.deepcopy(overrides)
        result.append(normalized)
    return result


def settings(data):
    return {key: copy.deepcopy(data[key]) for key in CONFIG_KEYS}


def effective_settings(original, experiment):
    result = copy.deepcopy(original)
    changes = []
    for group in ALLOWLIST:
        for key, value in experiment[group].items():
            old = result[group][key]
            result[group][key] = value
            if value != old:
                changes.append(dict(field=f"{group}.{key}", original=old, effective=value))
    result["disable_ai_veto"] = experiment["disable_ai_veto"]
    if experiment["disable_ai_veto"]:
        changes.append(dict(field="disable_ai_veto", original=False, effective=True))
    return result, changes


def validate_dataset(data):
    if isinstance(data, dict) and data.get("schema_version") == 3:
        return _validate_shadow_dataset(data)
    require(isinstance(data, dict) and data.get("schema_version") == 2,
            "Require exported actual-account schema2 dataset; observer-only/raw samples lack "
            "recorded buy/monitor decision cycles and cannot be fabricated into a replay")
    require(len(canonical(data)) <= MAX_BYTES, "Dataset exceeds 64 MiB")
    require(isinstance(data.get("events"), list) and 0 < len(data["events"]) <= capture.MAX_CAPTURE_ROWS,
            "Dataset must contain 1–50000 recorded events")
    core._validate(data)  # Original recorded configuration MUST pass before applying experiments.
    evidence = data["capture_evidence"]
    require(isinstance(evidence, dict) and evidence.get("continuous_session_coverage") is True,
            "Export must prove continuous session coverage; observer-only data is unsupported")
    account = data["initial_state"]["account"]["account_id"]
    require(isinstance(account, str) and account == account.strip(), "Invalid selected account")
    events = data["events"]
    start = core._timestamp(data["initial_state"]["timestamp"], "snapshot timestamp")
    end = core._timestamp(events[-1]["timestamp"], "last frame")
    require(0 <= (end - start).total_seconds() <= capture.MAX_COMPARISON_DAYS * 86400,
            "Actual window must be ordered and at most 93 calendar days")
    sessions = capture._sessions(start.astimezone(core.NY).date(), end.astimezone(core.NY).date())
    require(evidence.get("sessions") == sessions, "Capture session labels disagree with observed bounds")
    capture._coverage(events, sessions)
    # These are observation times, not historical trigger/buy/ledger dates.
    stamps = [start, end]
    for mark in data["initial_state"]["broker_positions"]:
        stamps.append(core._timestamp(mark["observed_at"], "snapshot mark"))
    for event in events:
        stamps.append(core._timestamp(event["timestamp"], "frame timestamp"))
        for group in ("market_observations", "entry_quotes"):
            stamps.extend(core._timestamp(q["observed_at"], "quote observation")
                          for q in event.get(group, {}).values())
        stamps.extend(core._timestamp(t["observed_at"], "trigger observation")
                      for t in event.get("triggers", []))
        context = event.get("cycle_context", {})
        for key in ("started_at", "completed_at"):
            if key in context:
                stamps.append(core._timestamp(context[key], key))
        gates = event.get("gate_evidence", []) + ([event["cycle_gates"]] if "cycle_gates" in event else [])
        stamps.extend(core._timestamp(g["observed_at"], "gate observation") for g in gates)
        if "observed_at" in event:
            stamps.append(core._timestamp(event["observed_at"], "frame observation"))
    # All candidate names remain priced throughout, even if baseline skips them.
    known = {p["ticker"] for p in data["initial_positions"]}
    for event in events:
        known.update(t["ticker"] for t in event.get("triggers", []))
        require(known <= event["market_observations"].keys(),
                "Missing replay-universe observations; do not drop candidate-only names")
    return dict(account_id=account, snapshot_at=start.isoformat(),
                observed_start=min(stamps).isoformat(), observed_end=max(stamps).isoformat(),
                sessions=sessions, input_sha256=digest(data))


def _validate_shadow_dataset(data):
    import shadow_engine as shadow
    require(len(canonical(data)) <= MAX_BYTES, "Dataset exceeds 64 MiB")
    shadow.validate_dataset(data)  # Re-executes the actual-seed prefix; a hash alone is not proof.
    stamps = []
    for event in data["events"]:
        stamps.append(core._timestamp(event["timestamp"], "event timestamp"))
        for group in ("market_observations", "entry_quotes"):
            stamps.extend(core._timestamp(q["observed_at"], "quote timestamp")
                         for q in event.get(group, {}).values())
        stamps.extend(core._timestamp(t["observed_at"], "trigger timestamp")
                     for t in event.get("triggers", []))
        stamps.extend(core._timestamp(g["observed_at"], "gate timestamp")
                     for g in event.get("gate_evidence", []))
        if "cycle_gates" in event:
            stamps.append(core._timestamp(event["cycle_gates"]["observed_at"], "gate timestamp"))
        if "observed_at" in event:
            stamps.append(core._timestamp(event["observed_at"], "source timestamp"))
        if "cycle_context" in event:
            stamps.append(core._timestamp(event["cycle_context"]["started_at"], "cycle start"))
    return dict(origin=shadow.ORIGIN, conditional_experiment=True,
                account_id=data["seed"]["account"]["account_id"],
                actual_seed_sha256=data["capture_evidence"]["actual_seed_sha256"],
                snapshot_at=data["window_checkpoint"]["last_timestamp"],
                observed_start=min(stamps).isoformat(), observed_end=max(stamps).isoformat(),
                sessions=data["capture_evidence"]["sessions"], input_sha256=digest(data),
                lineage_sha256=digest(data["prefix_frames"] + data["window_frames"]),
                lineage_frame_count=len(data["prefix_frames"]) + len(data["window_frames"]))


def _run(data, experiment):
    effective, changes = effective_settings(settings(data), experiment)
    if data.get("schema_version") == 3:
        import shadow_engine as shadow
        return shadow.replay_window(
            data, decision_config=effective["decision_config"], exit_config=effective["exit_config"],
            disable_ai_veto=experiment["disable_ai_veto"], _validated=True), effective, changes
    # _Replay validates the original again. Replace only per-instance pure configs,
    # never capture evidence, original settings or exit_rules module globals.
    engine = core._Replay(copy.deepcopy(data), experiment["disable_ai_veto"])
    engine.cfg = core.dc.DecisionConfig(**effective["decision_config"])
    engine.exit_cfg = core.ec.ExitConfig(**effective["exit_config"])
    run = engine.run()
    return run, effective, changes


def position_attribution(run):
    groups = {}
    missing = 0
    for sale in run.get("position_sales", []):
        if not sale.get("ticker") or not sale.get("buy_date"):
            missing += 1
            continue
        key = (sale["ticker"], sale["buy_date"])
        group = groups.setdefault(key, dict(ticker=key[0], buy_date=key[1],
                                           realised_net=0.0, completed=False, sale_rows=0))
        group["realised_net"] += sale["net_profit_loss"]
        group["completed"] |= not sale["partial"]
        group["sale_rows"] += 1
    rows = [groups[key] for key in sorted(groups)]
    completed = [row for row in rows if row["completed"]]
    return dict(positions=rows, missing_position_key_rows=missing,
                completed_positions=len(completed),
                realised_loss_completed_positions=sum(min(0, r["realised_net"]) for r in completed),
                realised_net=sum(r["realised_net"] for r in rows),
                caveat="Recorded buy_date groups simulated partial/final sales. Initial pre-window "
                       "partials are not counted; their P&L is outside this window. Missing keys "
                       "are not inferred. Entry commission remains charged on the final sale.")


def equity_attribution(data, run):
    """Exact window equity bridge by ticker, including initial/final holdings and costs."""
    values = {}
    if data.get("schema_version") == 3:
        checkpoint = data["window_checkpoint"]
        for position in data["initial_positions"]:
            ticker = position["ticker"]
            values[ticker] = -position["shares"] * checkpoint["last_quotes"][ticker]["price"]
    else:
        for mark in data["initial_state"]["broker_positions"]:
            values[mark["ticker"]] = -mark["shares"] * mark["market_price"]
    for fill in run["fills"]:
        ticker = fill["ticker"]
        values[ticker] = values.get(ticker, 0) + (
            (1 if fill["side"] == "SELL" else -1) * fill["shares"] * fill["price"] - fill["commission"])
    marks = data["events"][-1]["market_observations"]
    for position in run["open_positions"]:
        ticker = position["ticker"]
        values[ticker] = values.get(ticker, 0) + position["shares"] * marks[ticker]["price"]
    require(math.isclose(sum(values.values()), run["net_profit"], abs_tol=1e-6),
            "Ticker attribution does not reconcile to account equity")
    return values


def summary(data, run, baseline):
    values = equity_attribution(data, run)
    original = equity_attribution(data, baseline)
    differences = {t: values.get(t, 0) - original.get(t, 0) for t in values.keys() | original.keys()}
    ordered = sorted(values, key=lambda t: (-values[t], t))
    delta_order = sorted(differences, key=lambda t: (-differences[t], t))
    delta = run["final_equity_net"] - baseline["final_equity_net"]
    attribution = position_attribution(run)
    result = dict(
        n_completed_positions=run["closed_position_count"],
        n_distinct_sessions=run["sessions_count"],
        starting_cash=data["initial_cash"], final_equity_net=run["final_equity_net"],
        final_cash=run["cash"], final_holdings_value=run["open_market_value"],
        open_position_count=len(run["open_positions"]), net_profit=run["net_profit"],
        equity_delta_vs_recorded_config_baseline=delta,
        max_sampled_drawdown_pct=run["max_drawdown_pct"],
        commission=run["commission"], slippage_cost=run["slippage_cost"],
        position_attribution=attribution,
        largest_contributing_tickers=[dict(ticker=t, window_net=values[t]) for t in ordered[:3]],
        largest_delta_contributors=[dict(ticker=t, equity_delta=differences[t]) for t in delta_order[:3]],
        all_ticker_window_net=values, all_ticker_equity_deltas=differences,
        attribution_only_excluding_top1_net=run["net_profit"] - sum(max(0, values[t]) for t in ordered[:1]),
        attribution_only_excluding_top3_net=run["net_profit"] - sum(max(0, values[t]) for t in ordered[:3]),
        attribution_only_excluding_top1_delta=delta - sum(max(0, differences[t]) for t in delta_order[:1]),
        attribution_only_excluding_top3_delta=delta - sum(max(0, differences[t]) for t in delta_order[:3]),
        warnings=(["Small sample: fewer than 30 completed positions; differences are exploratory, "
                   "not an invented approval gate."] if run["closed_position_count"] < 30 else []),
    )
    if data.get("schema_version") == 3:
        result.update(
            starting_hypothetical_account=dict(
                account_id=data["seed"]["account"]["account_id"], currency="USD",
                net_liquidation=data["window_checkpoint"]["equity"]),
            actual_seed_at=data["seed"]["timestamp"], conditional_experiment=True,
            starting_state_label="reproduced baseline shadow checkpoint; NOT actual account")
    else:
        result["starting_actual_account"] = copy.deepcopy(data["initial_state"]["account"])
    return result


def limitations(data):
    if data.get("schema_version") != 3:
        return LIMITATIONS
    from shadow_engine import LIMITATIONS as shadow_limitations
    return [item for item in LIMITATIONS if not (
        item.startswith("Each window starts") or item.startswith("Initial holdings and protective"))] + shadow_limitations


def _trial(data, experiment, baseline=None):
    run, effective, changes = _run(data, experiment)
    report = dict(name=experiment["name"], status="modeled", effective_settings=effective,
                  settings_diff=changes, summary=summary(data, run, run if baseline is None else baseline))
    return run, report


def winner(trials):
    eligible = [trial for trial in trials if trial["status"] == "modeled"]
    return min(eligible, key=lambda trial: (
        -trial["summary"]["equity_delta_vs_recorded_config_baseline"],
        bool(trial["settings_diff"]), trial["name"] != "baseline", trial["name"]))["name"]


def seal(document):
    result = copy.deepcopy(document)
    result["artifact_sha256"] = digest(document)
    return result


def select(data, candidates):
    window = validate_dataset(data)
    rows = experiments(candidates)
    baseline, report = _trial(data, BASELINE)
    trials = [report]
    for experiment in rows:
        try:
            _, trial = _trial(data, experiment, baseline)
        except core.ReplayInputError as exc:
            effective, changes = effective_settings(settings(data), experiment)
            trial = dict(name=experiment["name"], status="rejected", reason=str(exc),
                         effective_settings=effective, settings_diff=changes)
        trials.append(trial)
    chosen = winner(trials)
    document = dict(
        artifact_type="intraday_calibration_selection", version=VERSION,
        recommendation="research_only_manual_approval", engine=engine_fingerprint(),
        candidate_config=copy.deepcopy(candidates), candidate_config_sha256=digest(candidates),
        candidate_trial_count=len(rows), total_trials_including_baseline=len(trials),
        original_settings=settings(data), training=window,
        original_capture_evidence=copy.deepcopy(data["capture_evidence"]),
        training_trials=trials, selected_name=chosen,
        frozen_experiment=copy.deepcopy(next(r for r in [BASELINE, *rows] if r["name"] == chosen)),
        limitations=limitations(data),
    )
    return seal(document)


def verify_plan(plan):
    require(isinstance(plan, dict), "Plan must be an object")
    require(set(plan) == {
        "artifact_type", "version", "recommendation", "engine", "candidate_config",
        "candidate_config_sha256", "candidate_trial_count", "total_trials_including_baseline",
        "original_settings", "training", "original_capture_evidence", "training_trials",
        "selected_name", "frozen_experiment", "limitations", "artifact_sha256",
    }, "Selection plan has missing or unknown keys")
    unsigned = {k: v for k, v in plan.items() if k != "artifact_sha256"}
    require(plan.get("artifact_sha256") == digest(unsigned), "Plan fingerprint mismatch")
    require(plan.get("artifact_type") == "intraday_calibration_selection" and plan.get("version") == VERSION,
            "Unsupported selection plan")
    require(plan.get("engine") == engine_fingerprint(), "Replay engine/config fingerprint changed; select anew")
    config = plan.get("candidate_config")
    require(plan.get("candidate_config_sha256") == digest(config), "Candidate config fingerprint mismatch")
    rows = [BASELINE, *experiments(config)]
    trials = plan["training_trials"]
    require([t["name"] for t in trials] == [r["name"] for r in rows], "Plan trials differ from candidate config")
    require(plan["candidate_trial_count"] == len(rows) - 1
            and plan["total_trials_including_baseline"] == len(rows), "Plan trial count mismatch")
    for row, trial in zip(rows, trials):
        effective, changes = effective_settings(plan["original_settings"], row)
        require(trial["effective_settings"] == effective and trial["settings_diff"] == changes,
                "Plan experimental settings mismatch")
        require(trial["status"] in ("modeled", "rejected"), "Invalid trial status")
        if trial["status"] == "modeled":
            value = trial["summary"]["equity_delta_vs_recorded_config_baseline"]
            require(type(value) in (int, float) and math.isfinite(value), "Invalid training equity")
            summary_row = trial["summary"]
            require(math.isclose(
                value, summary_row["final_equity_net"] - trials[0]["summary"]["final_equity_net"],
                abs_tol=1e-8), "Plan training equity delta mismatch")
    require(trials[0]["status"] == "modeled"
            and trials[0]["summary"]["equity_delta_vs_recorded_config_baseline"] == 0,
            "Plan has no recorded-config baseline")
    chosen = winner(trials)
    require(plan["selected_name"] == chosen
            and plan["frozen_experiment"] == next(row for row in rows if row["name"] == chosen),
            "Plan selection does not match frozen training ranking")


def evaluate(plan, holdout):
    verify_plan(plan)  # No holdout input is consulted in training selection.
    window = validate_dataset(holdout)
    require(window["account_id"] == plan["training"]["account_id"], "Holdout selected account differs")
    require(window.get("origin") == plan["training"].get("origin"),
            "Cannot mix actual-account and conditional shadow windows")
    if window.get("origin"):
        require(window["actual_seed_sha256"] == plan["training"]["actual_seed_sha256"],
                "Shadow holdout must descend from the SAME captured actual seed")
        count = plan["training"]["lineage_frame_count"]
        require(len(holdout["prefix_frames"]) >= count
                and digest(holdout["prefix_frames"][:count]) == plan["training"]["lineage_sha256"],
                "Holdout baseline prefix differs from the frozen training history")
    require(settings(holdout) == plan["original_settings"],
            "Holdout recorded live configuration/costs differ from training; do not relabel either capture")
    require(core._timestamp(window["observed_start"], "holdout observed start")
            > core._timestamp(plan["training"]["observed_end"], "training observed end"),
            "Holdout must be strictly later and nonoverlapping, including snapshot/quote observation timestamps")
    require(window["input_sha256"] != plan["training"]["input_sha256"], "Training dataset reused as holdout")
    baseline, baseline_report = _trial(holdout, BASELINE)
    experiment = plan["frozen_experiment"]
    if experiment["name"] == "baseline":
        candidate_report = copy.deepcopy(baseline_report)
    else:
        try:
            _, candidate_report = _trial(holdout, experiment, baseline)
        except core.ReplayInputError as exc:
            effective, changes = effective_settings(settings(holdout), experiment)
            candidate_report = dict(name=experiment["name"], status="rejected", reason=str(exc),
                                    effective_settings=effective, settings_diff=changes)
    return seal(dict(
        artifact_type="intraday_calibration_holdout", version=VERSION,
        recommendation="research_only_manual_approval", selection_plan_sha256=plan["artifact_sha256"],
        engine=engine_fingerprint(), selected_name=plan["selected_name"],
        candidate_trial_count=plan["candidate_trial_count"],
        holdout_candidates_tested=int(experiment["name"] != "baseline"),
        training=copy.deepcopy(plan["training"]),
        training_baseline=copy.deepcopy(plan["training_trials"][0]),
        training_selected=copy.deepcopy(next(t for t in plan["training_trials"]
                                             if t["name"] == plan["selected_name"])),
        holdout=window, original_settings=settings(holdout),
        original_capture_evidence=copy.deepcopy(holdout["capture_evidence"]),
        baseline=baseline_report, frozen_candidate=candidate_report, limitations=limitations(holdout),
    ))


def write_artifact(path, document, *, overwrite=False):
    path = Path(path)
    require(not path.exists() or overwrite, f"Refusing to overwrite {path}")
    raw = canonical(document) + b"\n"
    require(len(raw) <= MAX_BYTES, "Artifact exceeds 64 MiB")
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.staging")
    try:
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(staging, path)
        else:
            # Atomic no-clobber publication: another process cannot win between check/write.
            os.link(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def console_report(document):
    if document["artifact_type"] == "intraday_calibration_selection":
        trials = document["training_trials"]
        print(f"n={trials[0]['summary']['n_completed_positions']} completed baseline positions "
              f"(not scale-out rows), {trials[0]['summary']['n_distinct_sessions']} distinct sessions; "
              f"{document['candidate_trial_count']} candidate trials.")
    else:
        print(f"n={document['baseline']['summary']['n_completed_positions']} completed holdout baseline positions "
              f"(not scale-out rows), {document['baseline']['summary']['n_distinct_sessions']} distinct sessions; "
              f"frozen candidate only, {document['candidate_trial_count']} training trials.")
        trials = []
        for window, keys in (("training", ("training_baseline", "training_selected")),
                             ("holdout", ("baseline", "frozen_candidate"))):
            for key in keys:
                trial = copy.deepcopy(document[key])
                trial["name"] = f"{window}/{trial['name']}"
                trials.append(trial)
    for trial in trials:
        if trial["status"] != "modeled":
            print(f"{trial['name']}: REJECTED — {trial['reason']}")
            continue
        s = trial["summary"]
        account = s.get("starting_hypothetical_account", s.get("starting_actual_account"))
        label = "hypothetical checkpoint" if s.get("conditional_experiment") else "actual start"
        print(f"{trial['name']}: n={s['n_completed_positions']}, "
              f"{s['n_distinct_sessions']} distinct sessions, {label} "
              f"${account['net_liquidation']:.2f}, final including holdings "
              f"${s['final_equity_net']:.2f}, after-cost change ${s['net_profit']:+.2f}, "
              f"vs recorded-config baseline ${s['equity_delta_vs_recorded_config_baseline']:+.2f}; "
              f"sampled drawdown {s['max_sampled_drawdown_pct']:.2f}%, realised completed-position "
              f"loss ${s['position_attribution']['realised_loss_completed_positions']:.2f}.")
        print("  Largest ticker contributions: " + json.dumps(s["largest_contributing_tickers"]))
        print(f"  Attribution-only net excluding top1/top3: "
              f"${s['attribution_only_excluding_top1_net']:+.2f}/${s['attribution_only_excluding_top3_net']:+.2f}.")
        for warning in s["warnings"]:
            print(f"  WARNING: {warning}")
    print(f"Frozen proposal: {document['selected_name']}. Human approval required; no live changes.")
    for limitation in document["limitations"]:
        print(limitation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, first, second in (("select", "training", "candidates"), ("evaluate", "plan", "holdout")):
        command = commands.add_parser(name)
        command.add_argument(first, type=Path)
        command.add_argument(second, type=Path)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--overwrite", action="store_true",
                             help="Explicitly replace an artifact; default refuses overwrite")
    args = parser.parse_args(argv)
    inputs = [args.training, args.candidates] if args.command == "select" else [args.plan, args.holdout]
    try:
        require(args.output.resolve() not in {p.resolve() for p in inputs}, "Output cannot replace an input")
        first, second = (load_json(p) for p in inputs)
        document = select(first, second) if args.command == "select" else evaluate(first, second)
        write_artifact(args.output, document, overwrite=args.overwrite)
    except (CalibrationError, core.ReplayInputError, capture.CaptureError,
            OSError, KeyError, TypeError, IndexError, OverflowError) as exc:
        parser.exit(2, f"Calibration rejected: {exc}\n")
    console_report(document)
    print(f"Artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
