"""Pure recorded-capture conversion and research comparison; no network or writes."""
from __future__ import annotations

import copy
import datetime as dt
import json

from research import live_rule_replay as core


CONFIG_KEYS = ("decision_config", "exit_config", "replay_config", "costs", "shared_exit_rules")
CONFIG_METADATA_KEYS = ("runtime", "git_commit", "costs_semantics")
MAX_COMPARISON_DAYS = 93
MAX_CAPTURE_ROWS = 50_000
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
SNAPSHOT_KINDS = ("portfolio_snapshot", "initial_state")
CONFIG_KINDS = ("config", "effective_config")
QUOTE_KINDS = ("quote_sample", "market_observation", "quote")
LIVE_KINDS = QUOTE_KINDS + ("buy_cycle", "buy_blocked", "monitor", "eod_latch", "end_mark")


class CaptureError(ValueError):
    """A recording cannot support a coherent comparison without invented inputs."""


def _require(condition, message):
    if not condition:
        raise CaptureError(message)


def _initial_positions(state):
    result = []
    for position in state["positions"]:
        ticker = position["ticker"]
        orders = [o for o in state["orders"] if o.get("ticker") == ticker]
        trail = [o for o in orders if o.get("order_type") == "TRAIL"]
        hard = [o for o in orders if o.get("order_type") == "STP"]
        _require(len(trail) == 1, f"{ticker}: unknown initial trailing anchor; record exactly one protective TRAIL")
        pct, stop = trail[0].get("trailing_percent"), trail[0].get("trail_stop_price")
        core._number(pct, f"{ticker}.trailing_percent", positive=True)
        core._number(stop, f"{ticker}.trail_stop_price", positive=True)
        _require(pct < 100 and stop < 1e100,
                 f"{ticker}: unknown/sentinel initial trailing anchor; cannot reconstruct")
        result.append(dict(
            position, buy_date=position["buy_date"][:10],
            broker_trail_pct=pct / 100, broker_anchor=stop / (1 - pct / 100),
            broker_hard_stop_price=hard[0].get("aux_price") if len(hard) == 1 else 0.0,
            broker_seed_source="sampled_order_seed_assumption",
            broker_seed_observed_at=state["timestamp"],
        ))
    return result


def _sessions(start, end):
    day, result = start, []
    while day <= end:
        if core.trading_days_between(day, day + dt.timedelta(days=1)):
            result.append(day.isoformat())
        day += dt.timedelta(days=1)
    return result


def _off_hours_technical_warning(record, now):
    local = now.astimezone(core.NY)
    if (dt.time(9, 30) <= local.time() <= dt.time(16)
            and core.trading_days_between(local.date(), local.date() + dt.timedelta(days=1))):
        return None
    kind, payload = record["kind"], record["payload"]
    technical_gap = kind in ("capture_gap", "capture_error", "outage") and payload.get("area") in (
        "broker_snapshot", "recorder", "worker", "quote_sample",
    )
    if kind not in QUOTE_KINDS and not technical_gap:
        return None
    return dict(
        kind="off_hours_coverage_warning", timestamp=record["occurred_at"],
        source_event_id=record["id"], source_kind=kind, source_area=payload.get("area"),
        description="Technical observation outside the regular-session replay profile was excluded; "
                    "no overnight price or fill was inferred. Financial activity is checked separately.",
    )


def _coverage(events, sessions):
    for session in sessions:
        day = [e for e in events if e["session"] == session]
        _require(day, f"{session}: missing NYSE session observations")
        observed = [core._timestamp(e["timestamp"], "timestamp") for e in day]
        opening = dt.datetime.combine(dt.date.fromisoformat(session), dt.time(9, 30), core.NY)
        closing = opening.replace(hour=16, minute=0)
        _require((observed[0] - opening).total_seconds() <= 60,
                 f"{session}: recording starts after session open; no reconstructed starting book")
        _require((closing - observed[-1]).total_seconds() <= core.QUOTE_MAX_AGE_SECONDS,
                 f"{session}: incomplete session; no closing observation within the 600-second freshness bound")
        for before, after in zip(observed, observed[1:]):
            _require((after - before).total_seconds() <= core.QUOTE_MAX_AGE_SECONDS,
                     f"{session}: observation outage exceeds 600 seconds ({before.isoformat()} → {after.isoformat()})")
        monitors = [core._timestamp(e["timestamp"], "monitor") for e in day if e["type"] == "monitor"]
        _require(monitors, f"{session}: missing monitor cycles")
        _require((closing - monitors[-1]).total_seconds() <= 1200,
                 f"{session}: missing final monitor cycle")
        _require(any(e["type"] in ("buy_cycle", "buy_blocked") for e in day),
                 f"{session}: missing recorded buy cycle and exogenous gate outcome")
        _require(sum(e["type"] == "eod_latch" for e in day) == 1,
                 f"{session}: missing or duplicate EOD latch/fresh-candidate snapshot")
    core._validate_recorded_cycles(events)


def _whole_shares(value, path):
    core._number(value, path, positive=True)
    _require(float(value).is_integer(), f"{path}: fractional shares are unsupported")
    return int(value)


def _raw_snapshot(record):
    """Normalize a copied pre-action DB read and its adjacent cached broker state."""
    raw = record["payload"]
    _require(raw.get("complete") is True and not raw.get("errors"), "portfolio_snapshot: incomplete source reads")
    _require(raw.get("coherence") == "main_thread_adjacent_observations",
             "portfolio_snapshot: non-atomic worker-read holdings cannot establish actual starting state; "
             "require a copied main-thread portfolio read adjacent to the broker snapshot")
    broker = raw.get("broker_snapshot", {})
    _require(broker.get("complete") is True and broker.get("connected") is True,
             "portfolio_snapshot: incomplete/disconnected broker snapshot")
    stamp = core._timestamp(raw.get("snapshot_at"), "portfolio_snapshot.snapshot_at")
    _require(raw["snapshot_at"] == broker.get("snapshot_at"), "portfolio_snapshot: broker cutoff mismatch")
    source = raw.get("source_times", {}).get("portfolio_positions", {})
    received = core._timestamp(source.get("received_at"), "portfolio_positions.received_at")
    _require(source.get("source") == "live_main_thread_read" and 0 <= (stamp - received).total_seconds() <= 1,
             "portfolio_snapshot: DB/broker source observations are not adjacent pre-action observations")
    config = raw.get("effective_config")
    _require(isinstance(config, dict), "portfolio_snapshot: missing effective_config")
    cooling_days = config.get("replay_config", {}).get("cooling_off_days")
    core._integer(cooling_days, "recorded cooling_off_days")
    ledger_start = dt.datetime.combine(
        stamp.astimezone(core.NY).date() - dt.timedelta(days=cooling_days), dt.time(), core.NY)
    for table in ("portfolio_positions", "trade_history", "ibkr_fills"):
        _require(isinstance(raw.get(table), list), f"portfolio_snapshot: missing {table}")
    for table in ("trade_history", "ibkr_fills"):
        times = raw.get("source_times", {}).get(table, {})
        _require(times.get("filter_lte") == raw["snapshot_at"],
                 f"portfolio_snapshot.{table}: historical cutoff proof missing")
        _require(core._timestamp(times.get("filter_gte"), f"{table}.filter_gte") <= ledger_start,
                 f"portfolio_snapshot.{table}: recorded history does not cover the full cooldown window")
    account_id = broker.get("account")
    account_values = [v for v in broker.get("account_values", [])
                      if v.get("tag") == "NetLiquidation" and v.get("account") == account_id
                      and v.get("currency") == "USD"]
    _require(len(account_values) == 1, "portfolio_snapshot: need one recorded USD NetLiquidation")
    try:
        equity = float(account_values[0]["value"])
    except (TypeError, ValueError, KeyError):
        raise CaptureError("portfolio_snapshot: invalid recorded NetLiquidation") from None
    positions = []
    for row in raw["portfolio_positions"]:
        p = copy.deepcopy(row)
        _require("buy_commission" in p and p["buy_commission"] is not None,
                 f"{p.get('ticker')}: missing recorded remaining buy commission")
        p["entry_fee_remaining"] = p["buy_commission"]
        p["shares"] = _whole_shares(p.get("shares"), f"{p.get('ticker')}.shares")
        positions.append(p)
    broker_positions = []
    for row in broker.get("positions", []):
        contract = row.get("contract", {})
        broker_positions.append(dict(
            ticker=row.get("ticker"), shares=_whole_shares(row.get("position"), "broker.position"),
            account=row.get("account"), sec_type=contract.get("secType"),
            currency=contract.get("currency"), market_price=row.get("marketPrice"),
            observed_at=raw["snapshot_at"],
            observation_semantics="cached_broker_mark_read_at_snapshot; provider freshness unknown",
        ))
    quantities = broker.get("position_quantities")
    _require(isinstance(quantities, list), "portfolio_snapshot: independent broker quantities missing")
    quantity_map = {}
    for row in quantities:
        contract = row.get("contract", {})
        _require(row.get("ticker") not in quantity_map and row.get("account") == account_id
                 and contract.get("secType") == "STK" and contract.get("currency") == "USD",
                 "portfolio_snapshot: unsupported asset/account or duplicate independent broker quantity")
        quantity_map[row["ticker"]] = _whole_shares(row.get("position"), "broker.position_quantities")
    _require(quantity_map == {p["ticker"]: p["shares"] for p in broker_positions},
             "portfolio_snapshot: broker portfolio/position quantity mismatch")
    orders = []
    _require(isinstance(broker.get("open_orders"), list), "portfolio_snapshot: missing open order capture")
    for item in broker["open_orders"]:
        order, contract, status = item.get("order", {}), item.get("contract", {}), item.get("status", {})
        core._integer(order.get("clientId"), "initial order.clientId")
        _require(not status.get("filled"), "portfolio_snapshot: partially filled initial order unsupported")
        _require(status.get("remaining") == order.get("totalQuantity"),
                 "portfolio_snapshot: initial protective order remaining quantity is unknown or mismatched")
        orders.append(dict(
            ticker=item.get("ticker"), account=order.get("account"), sec_type=contract.get("secType"),
            action=order.get("action"), order_type=order.get("orderType"),
            shares=_whole_shares(order.get("totalQuantity"), "order.totalQuantity"),
            order_id=order.get("orderId"), parent_id=order.get("parentId"), status=status.get("status"),
            client_id=order["clientId"],
            tif=order.get("tif"), oca_type=order.get("ocaType"),
            oca_group=order.get("ocaGroup"), trailing_percent=order.get("trailingPercent"),
            trail_stop_price=order.get("trailStopPrice"), aux_price=order.get("auxPrice"),
        ))
        _require(contract.get("currency") == "USD", "portfolio_snapshot: unsupported order currency")
    value = sum(core._number(p["market_price"], "broker.marketPrice", positive=True) * p["shares"]
                for p in broker_positions)
    result = dict(
        timestamp=raw["snapshot_at"], complete=True,
        stock_only=all(p["sec_type"] == "STK" and p["currency"] == "USD" for p in broker_positions),
        account=dict(account_id=account_id, currency="USD", net_liquidation=equity,
                     positions_value=value, mark_source="recorded_IBKR_portfolio_cache",
                     mark_freshness="provider timestamp unavailable; cache observed at snapshot"),
        positions=positions, broker_positions=broker_positions, orders=orders,
        prior_trade_history=copy.deepcopy(raw["trade_history"]), prior_fills=copy.deepcopy(raw["ibkr_fills"]),
        config=copy.deepcopy(config), coherence=raw["coherence"],
    )
    return result


def _normalize_raw_stream(selected):
    """Assemble complete raw hook cycles; never manufacture unevaluated gate inputs."""
    candidates = [r for r in selected if r["kind"] in SNAPSHOT_KINDS
                  and r["payload"].get("coherence") == "main_thread_adjacent_observations"]
    _require(candidates, "portfolio_snapshot: no coherent main-thread starting snapshot; "
             "non-atomic worker-read holdings cannot establish actual starting state")
    first = min(candidates, key=lambda r: core._timestamp(r["payload"]["snapshot_at"], "snapshot_at"))
    state = _raw_snapshot(first)
    cutoff = core._timestamp(state["timestamp"], "initial timestamp")
    _require(cutoff.astimezone(core.NY).date().isoformat() == selected[0]["session"],
             "no usable initial snapshot in first requested session; cannot silently start later")
    config = state["config"]
    _require(set(CONFIG_KEYS) <= config.keys(), "recorded effective_config: missing replay costs or rule configuration")
    all_cycles = {r["payload"].get("cycle_id") for r in selected
                  if r["payload"].get("marker_only") and r["payload"].get("stage") == "start"}
    all_cycles.discard(None)
    initial_order_keys = {(o["client_id"], o["order_id"]) for o in state["orders"]}
    bot_order_keys = set(initial_order_keys)
    submit_stages = {"market_buy_submitted", "market_sell_submitted", "trailing_stop_submitted",
                     "protective_trail_submitted", "protective_hard_stop_submitted",
                     "scale_out_submitted"}
    for r in selected:
        if r["kind"] == "order_event" and r["payload"].get("stage") in submit_stages:
            _require(r["payload"].get("cycle_id") in all_cycles,
                     "order_event: bot submission outside captured cycle; possible manual activity")
            order_id = r["payload"].get("order", {}).get("orderId")
            client_id = r["payload"].get("order", {}).get("clientId")
            core._integer(order_id, "bot orderId", 1)
            core._integer(client_id, "bot clientId")
            bot_order_keys.add((client_id, order_id))
    output, cache, cycles, known = [], {}, {}, {p["ticker"] for p in state["positions"]}
    pending_buy, seen_cycles = None, set()
    quote_error_frames = []
    window_symbols = known | {
        t["ticker"] for r in selected if r["kind"] == "candidate_universe"
        for t in r["payload"].get("triggers", []) if isinstance(t, dict) and "ticker" in t
    }
    assumptions = [{
        "kind": "initial_broker_cache_valuation",
        "description": "Initial cash uses recorded broker portfolio cache marks and NetLiquidation; "
                       "cache read time is recorded, underlying broker tick freshness is unknown.",
    }, {
        "kind": "sampled_cycle_completion",
        "description": "Complete raw cycles are replayed at their recorded completion with the latest "
                       "available provider-timestamped samples, not exact intra-cycle live execution prices.",
    }]

    def append(source, kind, payload, timestamp=None):
        stamp = timestamp or source["occurred_at"]
        output.append(dict(id=source["id"] + ":" + kind, run_id=source["run_id"], sequence=0,
                           session=core._timestamp(stamp, "normalized timestamp").astimezone(core.NY).date().isoformat(),
                           occurred_at=stamp, kind=kind, payload=payload))

    append(first, "portfolio_snapshot", state, state["timestamp"])

    def samples_at(stamp):
        now = core._timestamp(stamp, "cycle timestamp")
        result = {}
        for ticker in known:
            _require(ticker in cache, f"{ticker}: missing prior quote sample; future quotes forbidden")
            core._validate_sample(cache[ticker], now, f"{ticker}.recorded_sample")
            result[ticker] = copy.deepcopy(cache[ticker])
        return result

    for record in selected:
        kind, p = record["kind"], record["payload"]
        now = core._timestamp(record["occurred_at"], "raw timestamp")
        _require(not p.get("rotation") and not p.get("manual_requests") and not p.get("unsupported_orders")
                 and p.get("origin") != "manual",
                 f"{kind}: manual/rotation/unsupported activity blocks replay")
        warning = _off_hours_technical_warning(record, now)
        if warning:
            assumptions.append(warning)
            if kind in ("capture_gap", "capture_error", "outage"):
                quote_error_frames.clear()
            continue
        if kind == "capture_gap" and quote_error_frames:
            if (p.get("area") == "recorder"
                    and type(p.get("error_count")) is int
                    and p["error_count"] == len(quote_error_frames)
                    and p.get("reason") == f"quote coverage incomplete: {quote_error_frames[-1]} errors"):
                assumptions.append(dict(
                    kind="quote_coverage_warning", timestamp=record["occurred_at"],
                    related_capture_gap_id=record["id"],
                    description="Recorder warning accounts exactly for already-validated quote frames; "
                                "no required replay observation was missing.",
                ))
                quote_error_frames.clear()
                continue
        if kind in ("capture_gap", "capture_error", "outage", "unsupported", "manual", "rotation"):
            raise CaptureError(f"{kind}: {p.get('reason', 'capture incomplete')}")
        if kind in CONFIG_KINDS:
            _require(p == config, "runtime config changed within window; split window at configuration change")
            continue
        if kind in SNAPSHOT_KINDS:
            _require(p.get("effective_config") == config,
                     "runtime config changed within window; split window at configuration change")
            continue
        if kind == "fill":
            _require(p.get("origin") in ("bot", "protective_order"),
                     "fill: manual/unknown actual activity")
            if now >= cutoff:
                append(record, kind, copy.deepcopy(p))
            continue
        if kind in ("fill_event", "order_event", "sell_event"):
            if now < cutoff:
                continue
            reason = str(p.get("reason", "")).lower()
            _require(not any(word in reason for word in ("manual", "rotation", "rank & replace", "smart oca")),
                     f"{kind}: actual manual/rotation activity is unsupported")
            if kind == "sell_event":
                _require(p.get("cycle_id") in all_cycles,
                         "sell_event: manual/unknown sell outside captured cycle")
            else:
                order = p.get("execution" if kind == "fill_event" else "order", {})
                order_key = (order.get("clientId"), order.get("orderId"))
                _require(order_key in bot_order_keys,
                         f"{kind}: manual/unknown order identity; cannot classify actual activity")
                _require(order.get("acctNumber" if kind == "fill_event" else "account") ==
                         state["account"]["account_id"],
                         f"{kind}: account mismatch")
            initial_order = kind != "sell_event" and order_key in initial_order_keys
            append(record, kind, dict(p, origin="protective_order" if initial_order else "bot"))
            continue
        if kind in QUOTE_KINDS:
            _require(type(p.get("complete")) is bool, "quote_sample: missing completeness flag")
            _require(isinstance(p.get("quotes"), list), "quote_sample: quotes list missing")
            missing, errors = p.get("missing_quotes", []), p.get("errors", [])
            _require(isinstance(missing, list) and isinstance(errors, list),
                     "quote_sample: malformed missing-quote/error evidence")
            requested = p.get("requested_symbols", [])
            _require(isinstance(requested, list), "quote_sample: malformed requested-symbol universe")
            for ticker in missing + requested:
                core._ticker(ticker)
            unavailable = set(missing)
            for error in errors:
                _require(isinstance(error, dict) and error.get("ticker"),
                         "quote_sample: unscoped provider failure; cannot prove required coverage")
                core._ticker(error["ticker"])
                unavailable.add(error["ticker"])
            available = set()
            for quote in p["quotes"]:
                ticker = quote.get("ticker")
                core._ticker(ticker)
                _require(ticker not in available, f"quote_sample.{ticker}: duplicate provider observation")
                observed = quote.get("provider_timestamp")
                sample = dict(price=quote.get("price"), source=quote.get("source"),
                              observed_at=observed, delayed=True)
                try:
                    core._validate_sample(sample, now, f"quote_sample.{ticker}")
                except core.ReplayInputError:
                    if ticker in window_symbols or ticker in known:
                        raise
                    unavailable.add(ticker)
                    continue
                available.add(ticker)
                cache[ticker] = sample
            unavailable.update(set(requested) - available)
            _require(not (known - available) and not (known & unavailable),
                     f"quote_sample: missing required replay quotes {sorted((known - available) | (known & unavailable))}; "
                     "do not drop held or candidate names")
            _require(p["complete"] or unavailable,
                     "quote_sample: unexplained incomplete provider observations")
            if unavailable:
                assumptions.append(dict(
                    kind="quote_coverage_warning", timestamp=record["occurred_at"],
                    missing_irrelevant_symbols=sorted(unavailable - window_symbols),
                    missing_not_yet_required_symbols=sorted(unavailable & window_symbols),
                    description="Retained-universe quote gaps do not remove candidates or holdings; "
                                "every currently required replay symbol has its own valid source observation.",
                ))
            if errors:
                quote_error_frames.append(len(errors))
            if now >= cutoff and known and known <= cache.keys():
                append(record, "quote_sample", dict(complete=True, market_observations=samples_at(record["occurred_at"]),
                                                    missing_quotes=[]))
            continue
        if kind in ("buy_cycle", "monitor", "eod_latch") and p.get("marker_only"):
            cycle_id = p.get("cycle_id")
            _require(cycle_id, f"{kind}: missing raw cycle_id")
            stage = p.get("stage")
            if kind == "eod_latch":
                _require(cycle_id in cycles and cycles[cycle_id]["kind"] == "monitor",
                         "eod_latch: missing enclosing monitor")
                cycles[cycle_id]["eod_" + str(stage)] = record
                continue
            if stage == "start":
                _require(cycle_id not in seen_cycles, f"{kind}: reused raw cycle_id")
                seen_cycles.add(cycle_id)
                _require(not cycles, "overlapping/nested cycles; rotation is unsupported")
                if kind == "monitor" and now >= cutoff:
                    _require(pending_buy is not None,
                             "monitor: missing matching preceding buy cycle; load its recorded context, "
                             "including any buy attempt before the initial snapshot")
                    buy_end = core._timestamp(pending_buy["completed_at"], "preceding buy completion")
                    _require(0 <= (now - buy_end).total_seconds() <= core.QUOTE_MAX_AGE_SECONDS
                             and buy_end.astimezone(core.NY).date() == now.astimezone(core.NY).date(),
                             "monitor: recorded preceding buy cycle is not adjacent")
                cycles[cycle_id] = dict(kind=kind, records=[], start=record)
                if kind == "monitor":
                    cycles[cycle_id]["preceding_buy_cycle_id"] = pending_buy["cycle_id"] if pending_buy else None
                    pending_buy = None
                continue
            _require(stage == "end" and cycle_id in cycles, f"{kind}: missing cycle start")
            cycle = cycles.pop(cycle_id)
            _require(cycle["kind"] == kind, f"{kind}: mismatched raw cycle start/end kind")
            _require(p.get("complete") is True and p.get("status") == "returned",
                     f"{kind}: incomplete or failed raw cycle")
            if now < cutoff:
                continue
            rows = cycle["records"]
            context = dict(cycle_id=cycle_id, started_at=cycle["start"]["occurred_at"],
                           completed_at=record["occurred_at"])
            if kind == "buy_cycle":
                gate_rows = [r for r in rows if r["kind"] == "buy_gate"]
                evidence = [
                    dict(gate=g["payload"].get("gate"), passed=g["payload"].get("passed"),
                         observed_at=g["occurred_at"],
                         **({"margin_loan": g["payload"].get("margin_loan")}
                            if g["payload"].get("gate") == "margin" else {}))
                    for g in gate_rows
                ]
                if any(g["passed"] is False for g in evidence):
                    reason = core._blocked_buy_reason(evidence, now)
                    _require(not any(r["kind"] in ("buy_context", "eligibility", "candidate_quote")
                                     or (r["kind"] == "candidate_universe" and r["payload"].get("phase") == "buy")
                                     for r in rows),
                             "buy_cycle: inputs after a failed exogenous gate contradict early return")
                    append(record, "buy_blocked", dict(
                        complete=True, cycle_context=context, market_observations=samples_at(record["occurred_at"]),
                        block_reason=reason, gate_evidence=evidence,
                    ))
                    assumptions.append(dict(
                        kind="recorded_no_entry_gate", cycle_id=cycle_id, reason=reason,
                        candidate_inputs="not evaluated; no candidate universe or later gate values invented",
                    ))
                    pending_buy = context
                    continue
                gates = {r["payload"].get("gate"): r for r in gate_rows}
                _require(set(gates) == {"schema", "margin", "market"}
                         and sum(r["kind"] == "buy_gate" for r in rows) == 3,
                         "buy_cycle: unevaluated/missing exogenous gates; do not invent early-return inputs")
                universes = [r for r in rows if r["kind"] == "candidate_universe"
                             and r["payload"].get("phase") == "buy"]
                _require(len(universes) == 1 and universes[0]["payload"].get("complete") is True,
                         "buy_cycle: missing complete candidate universe")
                universe = universes[0]
                triggers = []
                for trigger in universe["payload"]["triggers"]:
                    required = core.TRIGGER_FIELDS - {"observed_at"}
                    _require(required <= trigger.keys(),
                             f"buy_cycle trigger {trigger.get('ticker')}: missing recorded fields {sorted(required - trigger.keys())}")
                    triggers.append({**{k: copy.deepcopy(trigger[k]) for k in required},
                                     "observed_at": universe["occurred_at"]})
                    known.add(trigger["ticker"])
                quotes = samples_at(record["occurred_at"])
                entry = {t["ticker"]: dict(quotes[t["ticker"]], hypothetical_entry_pricing=True)
                         for t in triggers}
                append(record, kind, dict(
                    complete=True, cycle_context=context,
                    market_observations=quotes, triggers=triggers, entry_quotes=entry,
                    cycle_gates=dict(observed_at=record["occurred_at"],
                                     schema_ok=gates["schema"]["payload"].get("passed"),
                                     margin_loan=gates["margin"]["payload"].get("margin_loan"),
                                     market_allowed=gates["market"]["payload"].get("passed")),
                ))
                assumptions.append(dict(kind="hypothetical_entry_pricing", timestamp=record["occurred_at"],
                                        tickers=sorted(entry), reason="provider-timestamped sample instead of unverified cached entry quote"))
                pending_buy = context
            else:
                contexts = [r for r in rows if r["kind"] == "monitor_context"]
                _require(len(contexts) == 1, "monitor: missing recorded context")
                monitor_context = contexts[0]["payload"]
                _require(monitor_context.get("oca_managed") == [], "monitor: manual Smart OCA positions unsupported")
                observations = [r["payload"] for r in rows if r["kind"] == "monitor_observation"]
                _require(not any(o.get("oca_managed") for o in observations),
                         "monitor: manual Smart OCA observation unsupported")
                _require({o.get("ticker") for o in observations} == {p["ticker"] for p in monitor_context["positions"]},
                         "monitor: incomplete actual position observations")
                _require(all(type(o.get("price")) in (int, float) and o["price"] > 0 for o in observations),
                         "monitor: missing actual position price")
                quotes = samples_at(record["occurred_at"])
                context["preceding_buy_cycle_id"] = cycle["preceding_buy_cycle_id"]
                append(record, kind, dict(complete=True, cycle_context=context, market_observations=quotes))
                if "eod_start" in cycle or "eod_end" in cycle:
                    _require("eod_start" in cycle and "eod_end" in cycle
                             and cycle["eod_end"]["payload"].get("complete") is True
                             and cycle["eod_start"]["sequence"] < cycle["eod_end"]["sequence"],
                             "eod_latch: incomplete EOD cycle")
                    universes = [r for r in rows if r["kind"] == "candidate_universe"
                                 and r["payload"].get("phase") == "eod"]
                    _require(len(universes) == 1 and universes[0]["payload"].get("complete") is True,
                             "eod_latch: missing fresh trigger universe")
                    append(record, "eod_latch", dict(
                        complete=True, market_observations=copy.deepcopy(quotes),
                        observed_at=universes[0]["occurred_at"],
                        fresh_trigger_tickers=[t["ticker"] for t in universes[0]["payload"]["triggers"]],
                    ))
            continue
        cycle_id = p.get("cycle_id")
        if cycle_id in cycles:
            cycles[cycle_id]["records"].append(record)
    _require(not cycles, "incomplete raw cycle: end marker missing")
    output.sort(key=lambda r: core._timestamp(r["occurred_at"], "normalized timestamp"))
    for index, record in enumerate(output):
        record["sequence"] = index
    return output, assumptions


def _build_dataset(records, start_date, end_date):
    start = core._date(str(start_date), "start_date")
    end = core._date(str(end_date), "end_date")
    _require(start <= end, "start_date must not be later than end_date")
    _require((end - start).days + 1 <= MAX_COMPARISON_DAYS,
             "comparison exceeds 93 calendar days; select a shorter window")
    _require(isinstance(records, list) and records, "empty capture; no valid recorded starting portfolio")
    _require(len(records) <= MAX_CAPTURE_ROWS, "capture exceeds 50000 raw rows; select a shorter window")
    try:
        size = len(json.dumps(records, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CaptureError(f"capture must contain finite JSON data: {exc}") from exc
    _require(size <= MAX_CAPTURE_BYTES, "capture exceeds 64 MiB; select a shorter window")
    selected = []
    for record in copy.deepcopy(records):
        _require(isinstance(record, dict), "capture record must be an object")
        core._date(record.get("session"), "capture.session")
        if start.isoformat() <= record["session"] <= end.isoformat():
            selected.append(record)
    _require(selected, "empty capture in requested dates")
    for r in selected:
        _require({"id", "run_id", "sequence", "session", "occurred_at", "kind", "payload"} <= r.keys(),
                 "capture record missing required envelope fields")
        core._integer(r["sequence"], "capture.sequence")
        _require(all(isinstance(r[key], str) and r[key] for key in ("id", "run_id", "kind")),
                 "capture id/run_id/kind must be nonempty strings")
        _require(isinstance(r["payload"], dict), "capture payload must be an object")
        _require(r["payload"].get("capture_mode") != "observer",
                 "observer-only data lacks live decision inputs; export raw observations "
                 "for analysis, not a strategy replay")
    _require(len({r["run_id"] for r in selected}) == 1,
             "multiple recorder runs in selected window; split window at restart and take a fresh actual snapshot")
    selected.sort(key=lambda r: r["sequence"])
    _require(len({r["id"] for r in selected}) == len(selected), "duplicate capture event id")
    for before, after in zip(selected, selected[1:]):
        _require(after["sequence"] == before["sequence"] + 1,
                 "missing capture sequence; incomplete recording/outage")
        _require(core._timestamp(after["occurred_at"], "occurred_at") >=
                 core._timestamp(before["occurred_at"], "occurred_at"),
                 "capture sequence timestamps move backwards")
    for r in selected:
        now = core._timestamp(r["occurred_at"], "capture.occurred_at")
        _require(now.astimezone(core.NY).date().isoformat() == r["session"],
                 "capture session does not match New York occurrence date")
    raw_evidence = dict(records_count=len(selected), first_sequence=selected[0]["sequence"],
                        last_sequence=selected[-1]["sequence"])
    raw_assumptions = []
    if any(r["kind"] in SNAPSHOT_KINDS and "broker_snapshot" in r["payload"] for r in selected):
        selected, raw_assumptions = _normalize_raw_stream(selected)
    snapshots = [r for r in selected if r["kind"] in SNAPSHOT_KINDS]
    _require(snapshots, "missing recorded initial_state (portfolio_snapshot); cannot invent a flat/cash-only starting book")
    first = snapshots[0]
    _require(first["payload"].get("atomic") is not False,
             "portfolio_snapshot: broker/DB snapshot is explicitly non-atomic; worker-read holdings "
             "cannot establish actual state at an earlier broker cutoff. Record a coherent starting snapshot.")
    _require(first is selected[0] or all(
        r["kind"] not in LIVE_KINDS + ("fill",) or _off_hours_technical_warning(
            r, core._timestamp(r["occurred_at"], "initial context timestamp"))
        for r in selected[:selected.index(first)]),
             "initial_state must precede all market/cycle events")
    state = copy.deepcopy(first["payload"])
    _require(state.get("timestamp") == first["occurred_at"],
             "initial_state timestamp must be the actual snapshot occurrence time")
    config = state.pop("config", None)
    if config is None:
        preceding = [r for r in selected[:selected.index(first)] if r["kind"] in CONFIG_KINDS]
        if not preceding:
            preceding = [
                r for r in records
                if r.get("kind") in CONFIG_KINDS and r.get("run_id") == first["run_id"]
                and core._timestamp(r.get("occurred_at"), "config.occurred_at") <=
                core._timestamp(first["occurred_at"], "initial_state.occurred_at")
            ]
            preceding.sort(key=lambda r: core._timestamp(r["occurred_at"], "config.occurred_at"))
        _require(preceding, "missing recorded runtime configuration")
        config = preceding[-1]["payload"]
    _require(isinstance(config, dict) and set(CONFIG_KEYS) <= config.keys(),
             "recorded runtime config: all decision_config, exit_config, replay_config, costs "
             "and shared_exit_rules are required; no current-runtime defaults may be substituted")
    _require(not (config.keys() - set(CONFIG_KEYS) - set(CONFIG_METADATA_KEYS)),
             "recorded runtime config: unsupported configuration fields")
    _require(state.get("complete") is True, "initial_state is incomplete")
    for key in ("positions", "broker_positions", "orders", "prior_trade_history", "prior_fills"):
        _require(isinstance(state.get(key), list), f"initial_state.{key}: complete recorded list required")
    account = state.get("account", {})
    equity = core._number(account.get("net_liquidation"), "recorded NetLiquidation", positive=True)
    value = sum(core._number(p.get("shares"), "broker shares", positive=True)
                * core._number(p.get("market_price"), "broker mark", positive=True)
                for p in state["broker_positions"])
    cash = equity - value
    _require(cash >= 0, "initial account implies a loan/unsupported negative cash; cannot invent cash")
    events, audit, assumptions = [], [], raw_assumptions
    last_samples = {}
    known = {p["ticker"] for p in state["positions"]}
    for record in selected:
        kind, payload = record["kind"], record["payload"]
        now = core._timestamp(record["occurred_at"], "occurred_at")
        _require(not payload.get("manual_requests") and not payload.get("rotation")
                 and not payload.get("unsupported_orders") and payload.get("origin") != "manual",
                 f"{kind}: actual manual/unknown requests, rotation or unsupported orders block replay")
        warning = _off_hours_technical_warning(record, now)
        if warning:
            assumptions.append(warning)
            continue
        if kind in ("capture_error", "capture_gap", "unsupported", "outage", "manual", "rotation"):
            raise CaptureError(f"capture {kind}: {payload.get('reason', 'unsupported or incomplete recording')}")
        if kind in CONFIG_KINDS:
            _require(payload == config, "runtime config changed within window; split window at configuration change")
            continue
        if kind in SNAPSHOT_KINDS:
            if record is not first:
                _require(payload.get("config") == config,
                         "runtime config changed within window; split window at configuration change")
            continue
        if kind in ("fill", "fill_event", "sell_event"):
            _require(payload.get("origin") in ("bot", "protective_order"),
                     "manual/unknown actual fill: unsupported external activity; split window")
            _require(not payload.get("rotation"), "actual rotation fill is unsupported; split window")
            audit.append(copy.deepcopy(record))
            continue
        if kind == "order_event":
            _require(payload.get("origin") in ("bot", "protective_order"),
                     "manual/unknown actual order event: unsupported external activity; split window")
            audit.append(copy.deepcopy(record))
            continue
        if kind not in LIVE_KINDS:
            audit.append(copy.deepcopy(record))
            continue
        if "config" in payload:
            _require(payload["config"] == config,
                     "runtime config changed within window; split window at configuration change")
        _require(not payload.get("marker_only"),
                 f"{kind}: phase marker is not a complete replay cycle; assemble recorded gates, "
                 "trigger universe and timestamped observations before comparison")
        _require(payload.get("complete") is True, f"{kind}: incomplete cycle event")
        _require(not payload.get("missing_quotes"), f"{kind}: missing recorded quotes {payload.get('missing_quotes')}")
        _require(not payload.get("manual_requests") and not payload.get("rotation")
                 and not payload.get("unsupported_orders"),
                 f"{kind}: actual manual requests/rotation/unsupported orders block replay")
        quotes = copy.deepcopy(payload.get("market_observations"))
        _require(isinstance(quotes, dict), f"{kind}: missing market_observations")
        for ticker, quote in quotes.items():
            core._validate_sample(quote, now, f"{kind}.{ticker}")
            last_samples[ticker] = quote
        event = dict(type="quote" if kind in QUOTE_KINDS else kind,
                     timestamp=record["occurred_at"], session=record["session"],
                     market_observations=quotes)
        if kind in ("buy_cycle", "buy_blocked", "monitor"):
            event["cycle_context"] = copy.deepcopy(payload.get("cycle_context"))
        if kind == "buy_cycle":
            _require(isinstance(payload.get("triggers"), list), "buy_cycle: missing trigger snapshot")
            event["triggers"] = copy.deepcopy(payload["triggers"])
            event["cycle_gates"] = copy.deepcopy(payload.get("cycle_gates"))
            entry = copy.deepcopy(payload.get("entry_quotes", {}))
            _require(isinstance(entry, dict), "buy_cycle.entry_quotes: expected map")
            for trigger in event["triggers"]:
                ticker = trigger["ticker"]
                known.add(ticker)
                if ticker not in entry:
                    _require(ticker in last_samples, f"{ticker}: missing historical entry sample; future quotes forbidden")
                    quote = copy.deepcopy(last_samples[ticker])
                    core._validate_sample(quote, now, f"{ticker}.hypothetical_entry")
                    entry[ticker] = dict(quote, hypothetical_entry_pricing=True)
                    assumptions.append(dict(ticker=ticker, timestamp=event["timestamp"],
                                            kind="hypothetical_entry_pricing", observation=quote))
                else:
                    entry[ticker].setdefault("hypothetical_entry_pricing", False)
            event["entry_quotes"] = entry
        elif kind == "buy_blocked":
            event["block_reason"] = payload.get("block_reason")
            event["gate_evidence"] = copy.deepcopy(payload.get("gate_evidence"))
        elif kind == "eod_latch":
            _require("fresh_trigger_tickers" in payload and "observed_at" in payload,
                     "eod_latch: fresh trigger universe must be captured separately from actual held names")
            event["fresh_trigger_tickers"] = copy.deepcopy(payload["fresh_trigger_tickers"])
            event["observed_at"] = payload["observed_at"]
        missing = known - quotes.keys()
        _require(not missing, f"{kind}: missing replay-universe observations {sorted(missing)}; do not drop names")
        events.append(event)
    _require(events, "empty valid capture: no sampled events")
    expected_sessions = _sessions(start, end)
    _require(expected_sessions, "requested range contains no NYSE sessions")
    _coverage(events, expected_sessions)
    if events[-1]["type"] != "end_mark":
        events.append(dict(type="end_mark", timestamp=events[-1]["timestamp"],
                           session=events[-1]["session"],
                           market_observations=copy.deepcopy(events[-1]["market_observations"])))
    dataset = dict(
        schema_version=2, dataset_label=f"Recorded actual start {start_date}–{end_date}",
        initial_cash=cash, initial_positions=_initial_positions(state), initial_state=state,
        scope=copy.deepcopy(core.RECORDED_SCOPE), events=events,
        **{key: copy.deepcopy(config[key]) for key in CONFIG_KEYS},
        capture_evidence=dict(
            run_id=first["run_id"], initial_event_id=first["id"],
            **raw_evidence, sessions=expected_sessions,
            actual_fills_audit=[r for r in audit if r["kind"] in ("fill", "fill_event", "sell_event")],
            audit_events=audit,
            coverage_warnings=[a for a in assumptions if a.get("kind") in (
                "quote_coverage_warning", "off_hours_coverage_warning")],
            assumptions=assumptions, observations_max_age_seconds=core.QUOTE_MAX_AGE_SECONDS,
            continuous_session_coverage=True, recorded_configuration_metadata={
                key: copy.deepcopy(config[key]) for key in CONFIG_METADATA_KEYS if key in config
            },
        ),
    )
    core._validate(dataset)
    return dataset


def build_dataset(records, start_date, end_date):
    """Build schema 2 exclusively from captured records; never query today's state."""
    try:
        return _build_dataset(records, start_date, end_date)
    except CaptureError:
        raise
    except (core.ReplayInputError, KeyError, TypeError, IndexError, OverflowError) as exc:
        raise CaptureError(f"Incomplete or unsupported capture: {exc}") from exc


def run_comparison(records, start_date, end_date):
    """Compare identical recorded starting books; no approval or live mutation."""
    dataset = build_dataset(records, start_date, end_date)
    try:
        return core.replay(dataset, compare_without_ai_veto=True)
    except core.ReplayInputError as exc:
        raise CaptureError(str(exc)) from exc
