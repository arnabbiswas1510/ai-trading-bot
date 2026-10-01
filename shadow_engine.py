"""Pure incremental hypothetical portfolio; no brokerage, database or network I/O.

Only the seed describes an actual account. Subsequent positions, fills and
protection are hypothetical. The caller atomically persists frame, output and
checkpoint and enforces uniqueness of frame IDs across its durable history.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import intraday_replay as capture
from research import live_rule_replay as core
from market_calendar import session_bounds

VERSION = 1
ORIGIN = "decision_only_shadow"
SCOPE = {**core.RECORDED_SCOPE, "initial_state": "hypothetical_checkpoint_from_actual_seed"}
LIMITATIONS = [
    "Decision-only simulation; no real orders or fills.",
    "Actual account seed is evidence; all subsequent portfolios and protective orders are hypothetical.",
    "Conditional experiment: every variant starts each window at the SAME baseline shadow checkpoint, "
    "not an independent actual-account snapshot or a continuous candidate portfolio.",
    "Sampled full fills, adverse slippage, modeled commission and immediately available cash; "
    "no inferred intrabar touches, partial broker fills, latency, settlement or external activity.",
    "Unknown/manual/Smart OCA protection and potentially eligible Rank & Replace block simulation.",
]
ENGINE_FILES = (
    "shadow_engine.py", "research/live_rule_replay.py", "intraday_replay.py",
    "shadow_inputs.py", "research_configuration.py", "market_direction.py", "indicators.py",
    "decision_core.py", "exit_core.py", "exit_rules.py", "cooling_off.py",
    "market_calendar.py", "trade_costs.py", "trigger_audit.py", "config.py",
)
ShadowError = core.ReplayInputError


def _json(value):
    try:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ShadowError(f"Expected finite plain JSON: {exc}") from exc
    core._require(len(raw.encode()) <= capture.MAX_CAPTURE_BYTES, "Shadow input exceeds 64 MiB")
    return json.loads(raw)


def _digest(value):
    return hashlib.sha256(json.dumps(value, allow_nan=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def engine_fingerprint():
    root = Path(__file__).resolve().parent
    packages = {}
    for name in ("exchange-calendars", "pandas", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    constants = {}
    for module in (core.dc, core.ec, core.er, core.cooling_off):
        for name, value in vars(module).items():
            if name.isupper() and isinstance(value, (str, int, float, bool, list, tuple, dict, type(None))):
                constants[f"{module.__name__}.{name}"] = _json(value)
    return _digest(dict(files={p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                               for p in ENGINE_FILES}, constants=constants,
                        packages=packages, python=list(sys.version_info[:3]), version=VERSION))


def _seal(state):
    state.pop("state_sha256", None)
    state["state_sha256"] = _digest(state)
    return _json(state)


def checkpoint(state):
    """Return an independent, finite JSON checkpoint, rejecting incompatible state."""
    return restore(state)


def restore(saved):
    """Restore exact state. Digests detect corruption, not malicious rewriting."""
    state = _json(saved)
    core._require(state.get("version") == VERSION and state.get("origin") == ORIGIN,
                  "Incompatible shadow checkpoint version/origin")
    signature = state.pop("state_sha256", None)
    core._require(signature == _digest(state), "Shadow checkpoint integrity mismatch")
    core._require(state.get("engine_fingerprint") == engine_fingerprint(),
                  "Shadow engine fingerprint changed; cannot resume incompatible checkpoint")
    core._require(state.get("config_fingerprint") == _digest(state["effective_config"]),
                  "Shadow effective configuration fingerprint mismatch")
    core._require(state.get("seed_fingerprint") == _digest(state["seed"]),
                  "Shadow actual seed fingerprint mismatch")
    state["state_sha256"] = signature
    return state


def _seed_data(seed, effective_config):
    core._require(isinstance(seed, dict) and seed.get("origin") == "actual_account_seed",
                  "Shadow requires an explicitly labelled actual_account_seed")
    for name in ("manual_requests", "rotation", "unsupported_orders"):
        core._require(name in seed and not seed[name], f"Seed {name}: unsupported or unknown activity")
    core._require(seed.get("config") == effective_config, "Actual seed/effective configuration mismatch")
    core._require(set(capture.CONFIG_KEYS) <= effective_config.keys(), "Missing effective rule configuration")
    stamp = core._timestamp(seed["timestamp"], "seed.timestamp")
    evidence = seed.get("source_evidence")
    core._require(isinstance(evidence, dict), "Seed source cutoff evidence is required")
    cutoff = dt.datetime.combine(
        stamp.astimezone(core.NY).date() - dt.timedelta(
            days=effective_config["replay_config"]["cooling_off_days"]), dt.time(), core.NY)
    for source in ("broker", "portfolio_positions", "trade_history", "ibkr_fills"):
        proof = evidence.get(source, {})
        observed = core._observed(proof.get("received_at"), stamp, f"seed.{source}.received_at")
        core._require((stamp - observed).total_seconds() <= core.QUOTE_MAX_AGE_SECONDS,
                      f"Stale seed source: {source}")
        if source in ("trade_history", "ibkr_fills"):
            core._require(proof.get("filter_lte") == seed["timestamp"],
                          f"{source}: missing exact historical cutoff")
            core._require(core._timestamp(proof.get("filter_gte"), f"{source}.filter_gte") <= cutoff,
                          f"{source}: incomplete cooldown history")
    quantities = evidence.get("quantities")
    core._require(isinstance(quantities, list), "Independent actual account quantities are required")
    expected = {}
    for row in quantities:
        ticker = row.get("ticker")
        core._ticker(ticker)
        core._integer(row.get("shares"), f"{ticker}.quantity", 1)
        core._require(ticker not in expected and row.get("account") == seed["account"]["account_id"]
                      and row.get("sec_type") == "STK" and row.get("currency") == "USD",
                      "Unsupported or ambiguous independent account quantities")
        expected[ticker] = row["shares"]
    core._require(expected == {p["ticker"]: p["shares"] for p in seed["broker_positions"]},
                  "Actual account independent quantities disagree with marked holdings")
    for order in seed["orders"]:
        core._require(order.get("filled") == 0 and order.get("remaining") == order.get("shares"),
                      "Unknown or partial initial protective order fill")
        core._require(order.get("protection_source") == "bot",
                      "Manual/unknown/Smart OCA protective order is unsupported")
        core._require(order.get("outside_rth") is False and order.get("trigger_method") == 0,
                      "Unknown/non-default stop triggering or outside-RTH protection is unsupported")
    positions = capture._initial_positions(seed)
    cash = seed["account"]["net_liquidation"] - sum(
        p["shares"] * p["market_price"] for p in seed["broker_positions"])
    if "cash" in seed["account"]:
        core._number(seed["account"]["cash"], "seed.account.cash", minimum=0)
        core._require(abs(seed["account"]["cash"] - cash) < 0.011,
                      "Actual account cash must reconcile to NetLiquidation minus marked holdings")
    return dict(schema_version=2, dataset_label="actual seed for decision-only shadow",
                initial_cash=cash, initial_positions=positions, initial_state=copy.deepcopy(seed),
                capture_evidence={}, scope=copy.deepcopy(core.RECORDED_SCOPE), events=[],
                **{k: copy.deepcopy(effective_config[k]) for k in capture.CONFIG_KEYS})


def initialize(seed, effective_config):
    """Initialize from captured selected-account holdings, protection, cash and ledger."""
    seed, config = _json(seed), _json(effective_config)
    data = _seed_data(seed, config)
    engine = core._Replay(data, False, incremental=True)
    quotes = {p["ticker"]: dict(price=p["market_price"], observed_at=p["observed_at"],
                                source="IBKR", delayed=False) for p in seed["broker_positions"]}
    state = dict(
        version=VERSION, origin=ORIGIN, engine_fingerprint=engine_fingerprint(),
        config_fingerprint=_digest(config), seed_fingerprint=_digest(seed),
        effective_config=config, seed=seed, cash=engine.cash, positions=engine.positions,
        position_order=list(engine.positions),
        ledger_sales=engine.ledger.sales, ledger_fills=engine.ledger.fills,
        commission=0.0, slippage=0.0, closed_positions=0, previous=None,
        eod_session=None, pending_eod=False, chronology={}, last_quotes=quotes,
        last_timestamp=seed["timestamp"], last_frame_id=None, last_frame_digest=None,
        last_captured_at=None, last_output=None, frame_count=0,
        history_digests=[],
        universe=sorted(engine.positions), initial_equity=engine.initial_equity,
        equity=engine.initial_equity, equity_peak=engine.initial_equity, max_drawdown_pct=0.0,
    )
    bounds = session_bounds(stamp_day := core._timestamp(seed["timestamp"], "seed").astimezone(core.NY).date())
    stamp = core._timestamp(seed["timestamp"], "seed")
    if bounds and bounds[0] <= stamp <= bounds[1]:
        state["chronology"] = dict(
            seen=[], pending=None, session=stamp_day.isoformat(), last_end=stamp.isoformat(),
            last_monitor_end=stamp.isoformat(), buy_seconds=0)
    return _seal(state)


def _engine(state):
    engine = core._Replay(_seed_data(state["seed"], state["effective_config"]), False, incremental=True)
    for key in ("cash", "positions", "commission", "slippage", "closed_positions",
                "previous", "eod_session", "pending_eod"):
        setattr(engine, key, copy.deepcopy(state[key]))
    engine.ledger.sales = copy.deepcopy(state["ledger_sales"])
    engine.ledger.fills = copy.deepcopy(state["ledger_fills"])
    engine.positions = {ticker: engine.positions[ticker] for ticker in state["position_order"]}
    engine.equity_curve = []
    return engine


def advance(state, frame):
    """Return (new_state, output), atomically or raise; never mutates either input.

    Retrying the most recent identical frame returns its prior output. The caller
    must reject older duplicate IDs before invoking this function.
    """
    state, frame = restore(state), _json(frame)
    core._keys(frame, {"frame_id", "captured_at", "events"} |
               ({"source_evidence"} if "source_evidence" in frame else set()), "shadow frame")
    if "source_evidence" in frame:
        core._require(isinstance(frame["source_evidence"], dict),
                      "frame.source_evidence must be an object of captured provider/feature evidence")
    core._require(isinstance(frame["frame_id"], str) and frame["frame_id"], "Missing frame_id")
    digest = _digest(frame)
    if frame["frame_id"] == state["last_frame_id"]:
        core._require(digest == state["last_frame_digest"], "Conflicting duplicate shadow frame")
        return state, copy.deepcopy(state["last_output"])
    captured = core._timestamp(frame["captured_at"], "frame.captured_at")
    if state["last_captured_at"]:
        core._require(captured > core._timestamp(state["last_captured_at"], "last capture"),
                      "Out-of-order/stale shadow frame; older retries belong to the durable store")
    histories = frame.get("source_evidence", {}).get("daily_history", {})
    core._require(isinstance(histories, dict), "daily_history evidence must be a symbol map")
    known_histories = set(state["history_digests"])
    added_histories = set()
    for symbol, evidence in histories.items():
        core._ticker(symbol)
        core._require(isinstance(evidence, dict), f"{symbol}: missing daily history proof")
        digest_value = evidence.get("sha256")
        core._require(isinstance(digest_value, str) and len(digest_value) == 64,
                      f"{symbol}: missing daily history fingerprint")
        core._observed(evidence.get("available_at"), captured, f"{symbol}.history.available_at")
        if "data" in evidence:
            core._require(_digest(evidence["data"]) == digest_value,
                          f"{symbol}: daily history content fingerprint mismatch")
            core._require(isinstance(evidence["data"], dict)
                          and evidence["data"].get("available_at") == evidence["available_at"],
                          f"{symbol}: history availability provenance mismatch")
            added_histories.add(digest_value)
        else:
            core._require(evidence.get("reference") == "earlier_committed_frame_in_same_run"
                          and digest_value in known_histories,
                          f"{symbol}: missing earlier full history; a hash alone is not input evidence")
    events = frame["events"]
    core._require(isinstance(events, list) and 0 < len(events) <= 1000,
                  "A shadow frame must contain 1–1000 captured events")
    data = _seed_data(state["seed"], state["effective_config"])
    data["events"] = events
    core._validate(data, complete=False, validate_cycles=False, validate_seed=False, exchange_sessions=True)
    if state["previous"] is None:
        seeded = core._timestamp(state["seed"]["timestamp"], "actual seed timestamp")
        first = core._timestamp(events[0]["timestamp"], "first shadow observation")
        seed_day = seeded.astimezone(core.NY).date()
        bounds = session_bounds(seed_day)
        if bounds and seeded < bounds[1]:
            expected_start = max(seeded, bounds[0])
        else:
            days = capture._sessions(seed_day, first.astimezone(core.NY).date(), exchange_sessions=True)
            eligible = [session_bounds(day)[0] for day in days if session_bounds(day)[0] >= seeded]
            core._require(eligible, "Missing first eligible shadow session after actual seed")
            expected_start = eligible[0]
        core._require(0 <= (first - expected_start).total_seconds() <= 600,
                      "Missing first shadow observations after actual seed; no silent starting-book reset")
    if state["previous"] and state["previous"]["session"] != events[0]["session"]:
        last_day = state["previous"]["session"]
        prior_close = session_bounds(last_day)[1]
        prior_time = core._timestamp(state["last_timestamp"], "previous session timestamp")
        core._require(state["eod_session"] == last_day
                      and (prior_close - prior_time).total_seconds() <= 600,
                      "Missing prior-session EOD/closing observations, even for a flat shadow book")
        expected = capture._sessions(core._date(last_day, "prior session"),
                                     core._date(events[0]["session"], "next session"),
                                     exchange_sessions=True)
        core._require(len(expected) == 2, "Shadow stream crosses missing exchange sessions")
    previous = core._timestamp(state["last_timestamp"], "last timestamp")
    universe = set(state["universe"])
    for event in events:
        now = core._timestamp(event["timestamp"], "event.timestamp")
        core._require(previous <= now <= captured and (captured - now).total_seconds() <= 600,
                      "Future, stale or out-of-order shadow event")
        if state["previous"] and previous.astimezone(core.NY).date() == now.astimezone(core.NY).date():
            core._require((now - previous).total_seconds() <= 600,
                          "Shadow observation outage exceeds 600 seconds")
        previous = now
        universe.update(t["ticker"] for t in event.get("triggers", []))
        core._require(universe <= event["market_observations"].keys(),
                      "Missing shadow universe marks, including candidate-only positions")
        stamps = [t["observed_at"] for t in event.get("triggers", [])]
        stamps += [g["observed_at"] for g in event.get("gate_evidence", [])]
        if "cycle_gates" in event:
            stamps.append(event["cycle_gates"]["observed_at"])
        if "observed_at" in event:
            stamps.append(event["observed_at"])
        for stamp in stamps:
            observed = core._observed(stamp, now, "frame source observation")
            core._require((now - observed).total_seconds() <= 600, "Stale shadow decision source")
    chronology = core._validate_recorded_cycles(events, state["chronology"])
    last = events[-1]
    monitor_time = (core._timestamp(chronology["last_monitor_end"], "last monitor")
                    if chronology.get("session") == last["session"] and chronology.get("last_monitor_end")
                    else session_bounds(last["session"])[0])
    elapsed = (core._timestamp(last["timestamp"], "last event") - monitor_time).total_seconds()
    core._require(elapsed - chronology.get("buy_seconds", 0) <= 1200,
                  "Missing shadow monitor cycle: quote-only coverage cannot replace decisions")
    inputs = dict(frame_id=frame["frame_id"], captured_at=frame["captured_at"],
                  cash_before=state["cash"],
                  shares_before={t: p["shares"] for t, p in state["positions"].items()},
                  effective_config_sha256=state["config_fingerprint"],
                  actual_seed_sha256=state["seed_fingerprint"],
                  source_evidence_sha256=(_digest(frame["source_evidence"])
                                          if "source_evidence" in frame else None),
                  observation_contract="Raw source-labelled events are persisted with this frame")
    engine = _engine(state)
    for event in events:
        engine.advance_event(event)
    for key in ("cash", "positions", "commission", "slippage", "closed_positions",
                "previous", "eod_session", "pending_eod"):
        state[key] = copy.deepcopy(getattr(engine, key))
    # Only the cooling-off horizon can affect future decisions; outputs retain the full audit ledger.
    cutoff = engine.today - dt.timedelta(days=engine.cfg_replay.cooling_off_days + 1)
    for key, rows, stamp in (("ledger_sales", engine.ledger.sales, "sell_date"),
                             ("ledger_fills", engine.ledger.fills, "fill_time")):
        state[key] = [row for row in rows if core._timestamp(row[stamp], stamp).astimezone(
            core.NY).date() >= cutoff]
    for mark in engine.equity_curve:
        state["equity_peak"] = max(state["equity_peak"], mark["equity"])
        state["max_drawdown_pct"] = max(state["max_drawdown_pct"],
            (state["equity_peak"] - mark["equity"]) / state["equity_peak"] * 100)
    state.update(chronology=chronology, last_quotes=copy.deepcopy(engine.quotes),
                 last_timestamp=engine.now.isoformat(), equity=engine._marks(),
                 last_frame_id=frame["frame_id"], last_frame_digest=digest,
                 last_captured_at=frame["captured_at"], universe=sorted(universe),
                 frame_count=state["frame_count"] + 1)
    state["position_order"] = list(engine.positions)
    state["history_digests"] = sorted(known_histories | added_histories)
    decisions = copy.deepcopy(engine.decisions)
    # Stop executions otherwise only appear in the shared fill ledger. Name them explicitly.
    for fill in engine.fills:
        if fill["side"] == "SELL":
            decisions.append(dict(timestamp=fill["timestamp"], ticker=fill["ticker"],
                                  action="SELL", reason=fill["reason"], shares=fill["shares"],
                                  hypothetical=True))
    for decision in decisions:
        decision["hypothetical"] = True
        decision["portfolio_action"] = (
            "BUY" if decision["action"] == "BUY" else
            "SELL" if decision["action"] in ("SELL", "SELL_DEADLINE", "SCALE_OUT") else
            "SKIP" if decision["action"] in ("SKIP", "HALT_CAPACITY") else "HOLD")
    protection_events = [
        dict(**row, action="PLACE_OR_REPLACE_HYPOTHETICAL_PROTECTION", hypothetical=True)
        for row in engine.brackets]
    for sale in engine.position_sales:
        if not sale["partial"]:
            protection_events.append(dict(
                timestamp=sale["sell_date"], ticker=sale["ticker"],
                action="CANCEL_REMAINING_HYPOTHETICAL_PROTECTION", hypothetical=True,
                reason="Full sampled exit; remaining protective siblings are assumed cancelled"))
    output = dict(origin=ORIGIN, frame_id=frame["frame_id"], timestamp=state["last_timestamp"],
                  hypothetical=True, cash=state["cash"], equity=state["equity"],
                  positions=list(copy.deepcopy(state["positions"]).values()),
                  decisions=decisions, fills=engine.fills, brackets=engine.brackets,
                  protection_events=protection_events, input_evidence=inputs,
                  position_sales=engine.position_sales, equity_curve=engine.equity_curve,
                  inputs_sha256=digest, limitations=LIMITATIONS)
    state["last_output"] = _json(output)
    return _seal(state), _json(output)


def _records(records):
    records = _json(records)
    core._require(isinstance(records, list) and records, "Full prefix records from the actual seed are required")
    frames = []
    for row in records:
        core._require(isinstance(row, dict) and set(row) <= {"frame", "output", "checkpoint"}
                      and "frame" in row, "Shadow record must contain frame and optional output/checkpoint")
        frames.append(row["frame"])
    core._require(sum(len(f["events"]) for f in frames) <= capture.MAX_CAPTURE_ROWS,
                  "Shadow history exceeds 50000 events; do not truncate the required prefix")
    core._require(len({f["frame_id"] for f in frames}) == len(frames), "Duplicate durable shadow frame ID")
    return records


def export_shadow_dataset(seed, records, start_date, end_date):
    """Reproduce full baseline prefix, then export a labelled conditional window.

    Prefix observations are embedded, not replaced by a checkpoint hash. At most
    50000 events/64 MiB are accepted; longer runs must wait for a fresh actual seed.
    """
    records = _records(records)
    start = core._date(start_date, "start_date") if isinstance(start_date, str) else start_date
    end = core._date(end_date, "end_date") if isinstance(end_date, str) else end_date
    core._require(0 <= (end - start).days < capture.MAX_COMPARISON_DAYS, "Window must be 1–93 days")
    state = initialize(seed, seed["config"])
    prefix, selected, events = [], [], []
    window_state = None
    for row in records:
        frame = row["frame"]
        days = {core._date(e["session"], "session") for e in frame["events"]}
        core._require(len(days) == 1, "A shadow frame cannot span sessions")
        day = next(iter(days))
        if day > end:
            break
        if day >= start and window_state is None:
            window_state = checkpoint(state)
        state, output = advance(state, frame)
        if "output" in row:
            core._require(row["output"] == output, "Persisted shadow output fails input reproduction")
        if "checkpoint" in row:
            core._require(row["checkpoint"] == state, "Persisted checkpoint fails full-prefix reproduction")
        if day < start:
            prefix.append(copy.deepcopy(frame))
        else:
            selected.append(copy.deepcopy(frame))
            events.extend(copy.deepcopy(frame["events"]))
    core._require(window_state is not None and events, "No shadow observations in requested window")
    sessions = capture._sessions(start, end, exchange_sessions=True)
    capture._coverage(events, sessions, exchange_sessions=True)
    core._require(events[-1]["type"] == "end_mark", "Window must end with a captured closing end_mark")
    dataset = dict(schema_version=3, origin=ORIGIN,
                   dataset_label="Conditional shadow experiment from reproduced baseline checkpoint",
                   scope=copy.deepcopy(SCOPE), seed=copy.deepcopy(seed), prefix_frames=prefix,
                   window_frames=selected, window_checkpoint=window_state,
                   initial_cash=window_state["cash"],
                   initial_positions=list(copy.deepcopy(window_state["positions"]).values()),
                   events=events, capture_evidence=dict(
                       continuous_session_coverage=True, sessions=sessions,
                       actual_seed_sha256=_digest(seed), actual_seed_at=seed["timestamp"],
                       hypothetical_window_start=window_state["last_timestamp"],
                       prefix_reproduced=True, limitations=LIMITATIONS),
                   **{k: copy.deepcopy(seed["config"][k]) for k in capture.CONFIG_KEYS})
    return _json(dataset)


def validate_dataset(data):
    core._require(data.get("schema_version") == 3 and data.get("origin") == ORIGIN,
                  "Not a labelled shadow dataset; observer-only inputs remain unsupported")
    sessions = data["capture_evidence"]["sessions"]
    core._require(bool(sessions), "Missing shadow session coverage")
    reproduced = export_shadow_dataset(data["seed"],
        [{"frame": f} for f in data["prefix_frames"] + data["window_frames"]], sessions[0], sessions[-1])
    core._require(reproduced == data, "Shadow dataset/checkpoint does not reproduce from full prefix inputs")
    return data


def replay_window(data, *, decision_config=None, exit_config=None, disable_ai_veto=False,
                  _validated=False):
    """Run a conditional experiment from the same validated baseline checkpoint."""
    if not _validated:
        validate_dataset(data)
    state = restore(data["window_checkpoint"])
    engine = _engine(state)
    engine.cfg = core.dc.DecisionConfig(**(decision_config or data["decision_config"]))
    engine.exit_cfg = core.ec.ExitConfig(**(exit_config or data["exit_config"]))
    engine.disable_ai_veto = disable_ai_veto
    engine.cash = data["initial_cash"]
    engine.initial_equity = state["equity"]
    engine.commission = engine.slippage = 0.0
    engine.closed_positions = 0
    engine.equity_curve = [dict(timestamp=state["last_timestamp"], equity=state["equity"], cash=state["cash"])]
    engine.data = {**engine.data, "events": data["events"], "initial_cash": data["initial_cash"],
                   "initial_positions": data["initial_positions"]}
    for event in data["events"]:
        engine.advance_event(event)
    return engine.result()
