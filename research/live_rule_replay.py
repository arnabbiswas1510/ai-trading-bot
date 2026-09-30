"""Offline, point-in-time shared-rule replay; never imports execution_agent.

The example in tests/fixtures/live_rule_replay_example.json is the input contract.
All configuration fields and nullable trigger inputs must be present. Prices are
observations, NOT OHLC bars: only supplied observations can trigger a broker stop.
The broker sees broker_quotes; entry sizing sees separate, possibly delayed,
entry_quotes. Monitor marks explicitly use the broker observation in this scope.

This shares decision primitives, NOT the entire production engine.
It is a shared live-rule / sampled-fill simulation, NOT exact IBKR replay.
The live monitor still orchestrates primitives independently of exit_core; its
golden parity tests remain necessary. Unsupported production paths fail closed.
Existing trigger_decisions / trade_history do not contain the required per-cycle
history. Missing historical inputs cannot be replaced with present-day snapshots.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cooling_off
import decision_core as dc
import exit_core as ec
import exit_rules as er
from market_calendar import trading_days_between
from trade_costs import CostModel

NY = ZoneInfo("America/New_York")
LABEL = "shared live-rule / sampled-fill simulation, NOT exact IBKR replay"
CAPTURE_REQUIREMENT = (
    "Requires recorded per-cycle point-in-time inputs; existing trigger_decisions "
    "and trade_history alone are insufficient. Do not backfill missing history "
    "from current snapshots."
)
SCOPE = {
    "initial_state": "flat_no_recent_sales",
    "execution": "sampled_full_fills",
    "external_activity": "none",
    "corporate_actions": "none",
    "monitor_price": "same_as_broker_observation",
}
RULE_NAMES = (
    "STOP_LOSS_PCT", "MAX_LOSS_PCT", "TRAIL_PROFIT_TIERS",
    "PROVE_IT_ENABLED", "PROVE_IT_P1_DAY0_PCT", "PROVE_IT_P1_LATER_PCT",
    "PROVE_IT_P1_DAY0_LAST_DAY", "PROVE_IT_P2_ARM_GAIN_PCT",
    "PROVE_IT_P2_FLOOR_PCT", "PROVE_IT_BACKSTOP_SLACK_PCT",
    "POWER_HOLD_ENABLED", "POWER_HOLD_GAIN_PCT", "POWER_HOLD_TRIGGER_DAYS",
    "POWER_HOLD_DURATION_DAYS", "POWER_HOLD_TRAIL_PCT",
)
TRIGGER_FIELDS = {
    "ticker", "observed_at", "triggered_at", "trigger_type", "final_score",
    "adjusted_score", "quality_score", "ai_rating", "ai_grade",
    "next_earnings_date", "volume_surge", "pivot_distance_pct", "close_price",
    "atr_pct",
}
CAVEATS = [
    "Synthetic example inputs are not historical evidence or profitability results.",
    "Only recorded observations exist: no inferred intrabar path or unobserved stop touch.",
    "Stops fill at the next observed crossing price with adverse slippage, not the stop level.",
    "Full immediate fills, successful bracket changes and immediate cash availability assumed; "
    "no order latency, broker partial fills, settlement, reconciliation or external exits.",
    "Rank & Replace is not implemented; potentially eligible EOD books are rejected.",
    "Initial portfolio must be flat with no prior sales in the cooling-off window.",
    "Recorded cycle-wide gate outcomes are exogenous and identical in both variants.",
    "Entry quotes can lag; monitor marks explicitly equal current sampled broker quotes.",
    "Position rule and cooldown-ledger basis rounds the entry fill to cents like buying.py. "
    "Sell fills remain unrounded; ledger gross P&L rounds to cents like selling.py. "
    "All entry commission belongs to the final close, none to partial sales. "
    "Cash/equity retain raw sampled fills and costs; sub-cent bookkeeping can differ "
    "from live persisted-basis P&L.",
    "Configuration is supplied by the input, not independently verified as deployed. "
    "Matching shared_exit_rules verifies current imports, not historical deployment provenance.",
    "Shared decision primitives, not whole-engine identity: monitoring.py does NOT call "
    "exit_core yet. tests/test_exit_core.py golden parity tests compare its mirrored ordering. "
    "EOD analytics that do not govern this scoped replay are not reproduced.",
    CAPTURE_REQUIREMENT,
]


class ReplayInputError(ValueError):
    """Invalid or unsupported capture, rather than a fabricated successful replay."""


@dataclass(frozen=True)
class ReplayConfig:
    cooling_off_days: int
    armed_exit_trail_pct: float
    atr_stop_max_pct: float
    trigger_lookback_days: int


def shared_rule_snapshot() -> dict:
    """JSON-normalized *effective* shared constants, including env overrides."""
    return json.loads(json.dumps({name: getattr(er, name) for name in RULE_NAMES}))


def _require(condition, message):
    if not condition:
        raise ReplayInputError(message)


def _keys(value, expected, path):
    _require(isinstance(value, dict), f"{path}: expected an object")
    missing, extra = set(expected) - value.keys(), value.keys() - set(expected)
    _require(not missing and not extra,
             f"{path}: missing {sorted(missing)}, unsupported {sorted(extra)}. "
             "Record all required inputs; use explicit null only for nullable fields. "
             + CAPTURE_REQUIREMENT)


def _number(value, path, *, minimum=None, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value),
             f"{path}: expected a finite number")
    _require(not positive or value > 0, f"{path}: must be positive")
    _require(minimum is None or value >= minimum, f"{path}: must be >= {minimum}")
    return value


def _integer(value, path, minimum=0):
    _require(type(value) is int and value >= minimum,
             f"{path}: expected an integer >= {minimum}")


def _boolean(value, path):
    _require(type(value) is bool, f"{path}: expected an explicit boolean")


def _timestamp(value, path):
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ReplayInputError(f"{path}: expected ISO timestamp with timezone") from None
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             f"{path}: timezone required")
    return result


def _date(value, path):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReplayInputError(f"{path}: expected YYYY-MM-DD") from None


def _observed(value, now, path):
    observed = _timestamp(value, path)
    _require(observed <= now, f"{path}: future observation; capture point-in-time inputs")
    return observed


def _ticker(value):
    _require(isinstance(value, str) and value and value == value.strip(),
             "ticker: expected a nonempty, unpadded string")


def _config(cls, value, path):
    _keys(value, {f.name for f in fields(cls)}, path)
    for key, number in value.items():
        if key == "scale_out_enabled":
            _boolean(number, f"{path}.{key}")
        else:
            _number(number, f"{path}.{key}",
                    minimum=None if key == "prove_it_p2_floor_pct" else 0)
    return cls(**value)


def _validate(data):
    _keys(data, {
        "schema_version", "dataset_label", "initial_cash", "initial_positions",
        "scope", "decision_config", "exit_config", "replay_config", "costs",
        "shared_exit_rules", "events",
    }, "dataset")
    _require(type(data["schema_version"]) is int and data["schema_version"] == 1,
             "schema_version: only version 1 is supported")
    _require(isinstance(data["dataset_label"], str) and data["dataset_label"].strip(),
             "dataset_label: give the capture a descriptive name")
    _number(data["initial_cash"], "initial_cash", positive=True)
    _require(data["initial_positions"] == [],
             "initial_positions: must be []. Start flat; historical bracket anchors "
             "and unresolved initial holdings cannot be reconstructed.")
    _require(data["scope"] == SCOPE,
             f"scope: unsupported conditions. Required scope: {SCOPE}. "
             "Manual/OCA exits, corporate actions, broker failures and prior cooldown "
             "state require a richer replay, not silently ignored inputs.")
    cfg = _config(dc.DecisionConfig, data["decision_config"], "decision_config")
    exit_cfg = _config(ec.ExitConfig, data["exit_config"], "exit_config")
    replay_cfg = _config(ReplayConfig, data["replay_config"], "replay_config")
    costs = _config(CostModel, data["costs"], "costs")
    for key in ("max_positions", "earnings_blackout_trading_days"):
        _integer(getattr(cfg, key), f"decision_config.{key}", 1 if key == "max_positions" else 0)
    for key in ("cooling_off_days", "trigger_lookback_days"):
        _integer(getattr(replay_cfg, key), f"replay_config.{key}")
    for key in ("stop_loss_pct", "power_hold_trail_pct", "scale_out_fraction"):
        _require(0 < getattr(exit_cfg, key) < 1, f"exit_config.{key}: must be in (0, 1)")
    _require(exit_cfg.armed_exit_deadline_hours > 0, "armed_exit_deadline_hours: must be positive")
    _require(0 < replay_cfg.armed_exit_trail_pct < 1, "armed_exit_trail_pct: must be in (0, 1)")
    _require(exit_cfg.stop_loss_pct <= replay_cfg.atr_stop_max_pct < 1,
             "atr_stop_max_pct: must be >= stop_loss_pct and < 1")
    _require(costs.slippage_bps < 10000, "costs.slippage_bps: must be < 10000")
    expected = shared_rule_snapshot()
    _require(data["shared_exit_rules"] == expected,
             "shared_exit_rules: recorded snapshot differs from imported exit_rules. "
             "Run with the matching code/environment; do not relabel an old configuration.")
    for field, name in (("stop_loss_pct", "STOP_LOSS_PCT"),
                        ("power_hold_trail_pct", "POWER_HOLD_TRAIL_PCT"),
                        ("prove_it_p2_floor_pct", "PROVE_IT_P2_FLOOR_PCT")):
        _require(getattr(exit_cfg, field) == expected[name],
                 f"exit_config.{field}: must match shared_exit_rules.{name}")
    events = data["events"]
    _require(isinstance(events, list) and events, "events: empty dataset; record a chronological stream")
    previous = None
    for i, event in enumerate(events):
        path = f"events[{i}]"
        _require(isinstance(event, dict), f"{path}: expected an object")
        kind = event.get("type")
        extra = {
            "quote": set(), "monitor": set(), "end_mark": set(),
            "buy_cycle": {"triggers", "entry_quotes", "cycle_gates"},
            "eod_latch": {"fresh_trigger_tickers", "observed_at"},
        }
        _require(kind in extra, f"{path}: unsupported event {kind!r}; "
                 "manual requests, rotation and external fills are not supported")
        _keys(event, {"type", "timestamp", "session", "broker_quotes"} | extra[kind], path)
        now = _timestamp(event["timestamp"], f"{path}.timestamp")
        _require(previous is None or now > previous,
                 f"{path}.timestamp: events must be strictly increasing")
        previous = now
        today = _date(event["session"], f"{path}.session")
        _require(today == now.astimezone(NY).date(), f"{path}.session: must match New York date")
        _require(trading_days_between(today, today + dt.timedelta(days=1)) == 1,
                 f"{path}.session: not a NYSE session in the shared calendar")
        clock = now.astimezone(NY).time()
        _require(dt.time(9, 30) <= clock <= dt.time(16),
                 f"{path}: only regular-session observations supported (09:30–16:00 New York)")
        quotes = event["broker_quotes"]
        _require(isinstance(quotes, dict), f"{path}.broker_quotes: expected a ticker map")
        for ticker, quote in quotes.items():
            _ticker(ticker)
            _keys(quote, {"price", "observed_at"}, f"{path}.broker_quotes.{ticker}")
            _number(quote["price"], f"{path}.broker_quotes.{ticker}.price", positive=True)
            _require(_observed(quote["observed_at"], now, path) == now,
                     f"{path}.broker_quotes.{ticker}: current observation required; "
                     "put delayed prices in entry_quotes")
        if kind == "quote":
            _require(bool(quotes), f"{path}: empty quote event")
        if kind == "buy_cycle":
            gates = event["cycle_gates"]
            _keys(gates, {"observed_at", "schema_ok", "margin_loan", "market_allowed"},
                  f"{path}.cycle_gates")
            _observed(gates["observed_at"], now, f"{path}.cycle_gates.observed_at")
            _boolean(gates["schema_ok"], f"{path}.cycle_gates.schema_ok")
            _boolean(gates["market_allowed"], f"{path}.cycle_gates.market_allowed")
            _number(gates["margin_loan"], f"{path}.cycle_gates.margin_loan", minimum=0)
            _require(isinstance(event["triggers"], list), f"{path}.triggers: expected a list")
            _require(isinstance(event["entry_quotes"], dict), f"{path}.entry_quotes: expected a map")
            tickers = []
            for trigger in event["triggers"]:
                _keys(trigger, TRIGGER_FIELDS, f"{path}.trigger")
                ticker = trigger["ticker"]
                _ticker(ticker)
                tickers.append(ticker)
                observed = _observed(trigger["observed_at"], now, f"{path}.{ticker}.observed_at")
                triggered = _observed(trigger["triggered_at"], observed, f"{path}.{ticker}.triggered_at")
                cutoff = today - dt.timedelta(days=replay_cfg.trigger_lookback_days)
                _require(triggered.astimezone(NY).date() >= cutoff,
                         f"{path}.{ticker}: trigger is outside trigger_lookback_days")
                _require(trigger["trigger_type"] in (None, "BREAKOUT", "PRE_BREAKOUT", "PRE_BREAKOUT_RELAXED"),
                         f"{path}.{ticker}: unsupported trigger_type")
                _require(trigger["ai_grade"] in (None, "A", "B", "C", "D", "F"),
                         f"{path}.{ticker}: invalid recorded ai_grade")
                for name in ("final_score", "adjusted_score", "quality_score", "ai_rating",
                             "volume_surge", "pivot_distance_pct", "atr_pct"):
                    if trigger[name] is not None:
                        _number(trigger[name], f"{path}.{ticker}.{name}")
                if trigger["next_earnings_date"] is not None:
                    _date(trigger["next_earnings_date"], f"{path}.{ticker}.next_earnings_date")
                _number(trigger["close_price"], f"{path}.{ticker}.close_price", positive=True)
                _require(ticker in event["entry_quotes"] and ticker in quotes,
                         f"{path}.{ticker}: record both entry_quotes and broker_quotes, "
                         "even for candidates the baseline skips")
                entry = event["entry_quotes"][ticker]
                _keys(entry, {"price", "observed_at"}, f"{path}.entry_quotes.{ticker}")
                _number(entry["price"], f"{path}.entry_quotes.{ticker}.price", positive=True)
                _observed(entry["observed_at"], now, f"{path}.entry_quotes.{ticker}.observed_at")
            _require(set(event["entry_quotes"]) == set(tickers),
                     f"{path}.entry_quotes: must contain exactly the candidate tickers")
        if kind == "eod_latch":
            _require(dt.time(15, 45) <= clock < dt.time(16), f"{path}: EOD window is 15:45–16:00")
            _observed(event["observed_at"], now, f"{path}.observed_at")
            fresh = event["fresh_trigger_tickers"]
            _require(isinstance(fresh, list), f"{path}.fresh_trigger_tickers: expected a list")
            for ticker in fresh:
                _ticker(ticker)
        _require(kind != "end_mark" or i == len(events) - 1,
                 f"{path}: end_mark must be the final event")
    _require(events[-1]["type"] == "end_mark",
             "events: finish with end_mark carrying current marks for every held ticker")
    _require(any(e["type"] == "buy_cycle" and e["triggers"] for e in events),
             "events: no candidate dataset; at least one nonempty buy_cycle is required")
    return cfg, exit_cfg, replay_cfg, costs


class _LedgerQuery:
    """Only the query operations compute_cooled_map needs; no database or I/O."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _columns):
        return self

    def gte(self, key, value):
        self.rows = [r for r in self.rows if r[key] >= value]
        return self

    def eq(self, key, value):
        self.rows = [r for r in self.rows if r[key] == value]
        return self

    def order(self, key, desc=False):
        self.rows.sort(key=lambda r: r[key], reverse=desc)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Ledger:
    def __init__(self):
        self.sales = []
        self.fills = []

    def table(self, name):
        return _LedgerQuery({"trade_history": self.sales, "ibkr_fills": self.fills}[name])


class _Replay:
    def __init__(self, data, disable_ai_veto):
        self.cfg, self.exit_cfg, self.cfg_replay, self.costs = _validate(data)
        self.data = data
        self.disable_ai_veto = disable_ai_veto
        self.cash = float(data["initial_cash"])
        self.positions = {}
        self.ledger = _Ledger()
        self.fills = []
        self.decisions = []
        self.brackets = []
        self.commission = self.slippage = 0.0

    def _record(self, ticker, action, reason="", **values):
        self.decisions.append(dict(timestamp=self.now.isoformat(), ticker=ticker,
                                   action=action, reason=reason, **values))

    def _fill(self, ticker, side, shares, reason):
        quote = self.quotes[ticker]["price"]
        price = self.costs.buy_fill(quote) if side == "BUY" else self.costs.sell_fill(quote)
        fee = self.costs.commission(shares, price)
        self.commission += fee
        self.slippage += abs(price - quote) * shares
        self.cash += (-price * shares if side == "BUY" else price * shares) - fee
        fill = dict(timestamp=self.now.isoformat(), ticker=ticker, side=side,
                    shares=shares, quote=quote, price=price, commission=fee, reason=reason)
        self.fills.append(fill)
        return fill

    def _sell(self, ticker, shares, reason):
        pos = self.positions[ticker]
        fill = self._fill(ticker, "SELL", shares, reason)
        gross = round((fill["price"] - pos["buy_price"]) * shares, 2)
        # selling.py leaves the ENTIRE entry commission for the final close.
        partial = shares < pos["shares"]
        entry_fee = 0.0 if partial else pos["entry_fee_remaining"]
        pos["entry_fee_remaining"] -= entry_fee
        # Supabase's generated NUMERIC net column subtracts fees without rounding
        # again. Decimal prevents a spurious negative/positive sign at exact zero.
        net = float(Decimal(str(gross)) - Decimal(str(entry_fee))
                    - Decimal(str(fill["commission"])))
        # NY timestamps preserve the shared cooldown helper's local-date boundary.
        timestamp = self.now.astimezone(NY).isoformat()
        self.ledger.sales.append(dict(
            ticker=ticker, sell_date=timestamp, net_profit_loss=net,
            profit_loss=gross, sell_reason=reason, sell_price=fill["price"],
            buy_commission=None if partial else entry_fee, sell_commission=fill["commission"],
        ))
        self.ledger.fills.append(dict(ticker=ticker, fill_time=timestamp, side="SLD"))
        pos["shares"] -= shares
        if not pos["shares"]:
            del self.positions[ticker]

    def _bracket(self, ticker, trail, hard, *, persist_hard):
        pos = self.positions[ticker]
        # IBKR rounds the trailing percent; cancellation/replacement resets its anchor.
        pos["broker_trail_pct"] = round(trail * 100, 2) / 100
        pos["broker_anchor"] = self.quotes[ticker]["price"]
        pos["broker_hard_stop_price"] = round(hard, 2)
        if persist_hard:
            pos["hard_stop_price"] = round(hard, 2)
        self.brackets.append(dict(timestamp=self.now.isoformat(), ticker=ticker,
                                 trail_pct=pos["broker_trail_pct"], hard_price=pos["broker_hard_stop_price"],
                                 stored_hard_price=pos.get("hard_stop_price"),
                                 anchor=pos["broker_anchor"], shares=pos["shares"]))

    def _broker(self):
        for ticker, pos in list(self.positions.items()):
            if ticker not in self.quotes:
                continue
            price = self.quotes[ticker]["price"]
            pos["broker_anchor"] = max(price, pos["broker_anchor"])
            trail_level = pos["broker_anchor"] * (1 - pos["broker_trail_pct"])
            hard = pos["broker_hard_stop_price"]
            if price <= max(trail_level, hard):
                reason = "BROKER_HARD_STOP" if hard >= trail_level else "BROKER_TRAIL"
                self._sell(ticker, pos["shares"], reason)

    def _marks(self):
        missing = self.positions.keys() - self.quotes.keys()
        _require(not missing, f"{self.now.isoformat()}: missing current held marks {sorted(missing)}; "
                 "provide broker_quotes for ALL holdings, including variant-only positions")
        return self.cash + sum(p["shares"] * self.quotes[t]["price"]
                               for t, p in self.positions.items())

    def _buy(self, event):
        equity = self._marks()  # The live per-cycle equity cap is captured once.
        gates = event["cycle_gates"]
        block = ("SCHEMA_BLOCK" if not gates["schema_ok"] else
                 "MARGIN_BLOCK" if gates["margin_loan"] > 0 else
                 "MARKET_BLOCK" if not gates["market_allowed"] else None)
        triggers = dc.rank_triggers(event["triggers"])
        if block:
            for trigger in triggers:
                self._record(trigger["ticker"], dc.SKIP, block)
            return
        capacity = dc.evaluate_capacity(len(self.positions), self.cfg)
        if capacity.action == dc.HALT_CAPACITY:
            for trigger in triggers:
                self._record(trigger["ticker"], dc.SKIP, capacity.reason_code)
            return
        cooled = cooling_off.compute_cooled_map(
            self.ledger, self.today, self.cfg_replay.cooling_off_days)
        for i, original in enumerate(triggers):
            trigger = dict(original)
            ticker = trigger["ticker"]
            if self.disable_ai_veto and trigger["ai_grade"] == "D":
                trigger["ai_grade"] = None  # One ablation only; scores/ranking remain unchanged.
            earnings = trigger["next_earnings_date"]
            date = _date(earnings, "next_earnings_date") if earnings is not None else None
            days = trading_days_between(self.today, date) if date and date >= self.today else None
            decision = dc.evaluate_eligibility(
                trigger, held_tickers=self.positions, cooled_map=cooled,
                days_to_earnings=days, cfg=self.cfg)
            if decision.action != dc.PROCEED:
                self._record(ticker, decision.action, decision.reason_code)
                continue
            remaining = max(1, self.cfg.max_positions - len(self.positions))
            size = dc.equity_capped_position_size(self.cash, remaining, equity, self.cfg.max_positions)
            decision = dc.evaluate_capacity(len(self.positions), self.cfg)
            if decision.action == dc.HALT_CAPACITY:
                for rest in triggers[i:]:
                    self._record(rest["ticker"], dc.SKIP, decision.reason_code)
                break
            decision = dc.evaluate_cash(self.cash, remaining, self.cfg)
            if decision.action == dc.PROCEED:
                decision = dc.evaluate_market_gates(trigger, self.cfg)
            if decision.action == dc.PROCEED:
                decision = dc.evaluate_price_gates(
                    trigger, event["entry_quotes"][ticker]["price"],
                    trigger["close_price"], size, self.cfg)
            self._record(ticker, decision.action, decision.reason_code, shares=decision.shares)
            if decision.action != dc.BUY:
                continue
            fill_price = self.costs.buy_fill(self.quotes[ticker]["price"])
            debit = fill_price * decision.shares + self.costs.commission(decision.shares, fill_price)
            _require(debit <= self.cash + 1e-8,
                     f"{ticker}: delayed-price sized buy would need margin after sampled fill/costs. "
                     "Broker rejection/loan paths are unsupported; do not silently resize the order.")
            fill = self._fill(ticker, "BUY", decision.shares, "BOUGHT")
            # Live buying.py persists round(avgFillPrice, 2), not the raw execution.
            basis = round(fill["price"], 2)
            if basis <= 0:
                basis = event["entry_quotes"][ticker]["price"]
            atr = trigger["atr_pct"]
            trail = (round(max(self.exit_cfg.stop_loss_pct, min(
                self.cfg_replay.atr_stop_max_pct, round(2.5 * atr / 100, 4))), 4)
                if atr and atr > 0 else self.exit_cfg.stop_loss_pct)
            pos = dict(ticker=ticker, shares=decision.shares, buy_price=basis,
                       execution_buy_price=fill["price"],
                       buy_date=self.today.isoformat(), stop_loss_pct=trail,
                       highest_unrealized_pct=0.0, hwm_price=basis,
                       closed_above_entry=False, scaled_out=False, power_hold=False,
                       exit_armed=False, entry_fee_remaining=fill["commission"])
            self.positions[ticker] = pos
            self._bracket(ticker, trail, er.hard_stop_price(pos, basis, 0.0, False, 0),
                          persist_hard=True)
            pos["stop_loss_pct"] = pos["broker_trail_pct"]
            self._broker()  # A newly placed stop above the sampled market executes now.

    def _monitor(self):
        self._marks()
        for ticker, pos in list(self.positions.items()):
            price = self.quotes[ticker]["price"]
            bought = dt.date.fromisoformat(pos["buy_date"])
            age, calendar_age = trading_days_between(bought, self.today), (self.today - bought).days
            hours = ((self.now - _timestamp(pos["exit_armed_at"], "exit_armed_at")).total_seconds() / 3600
                     if pos["exit_armed"] else 0.0)
            if not pos["exit_armed"]:
                pos["highest_unrealized_pct"] = max(
                    pos["highest_unrealized_pct"], round((price / pos["buy_price"] - 1) * 100, 4))
                pos["hwm_price"] = max(pos["hwm_price"], price)
                if er.is_power_hold_active(pos, calendar_age):
                    pos["power_hold"] = True
            context = ec.ExitContext(price, age, calendar_age, hours_armed=hours)
            decision = ec.evaluate_exit(pos, context, self.exit_cfg)
            self._record(ticker, decision.action, decision.reason, days_held=age,
                         calendar_days=calendar_age, exit_decision=asdict(decision))
            if decision.action == ec.SELL_DEADLINE:
                self._sell(ticker, pos["shares"], decision.reason)
            elif decision.action == ec.ARM_PROVE_IT:
                pos.update(exit_armed=True, exit_armed_at=self.now.isoformat(),
                           exit_armed_reason=decision.reason)
                self._bracket(ticker, self.cfg_replay.armed_exit_trail_pct, 0.0, persist_hard=False)
            elif decision.action == ec.SCALE_OUT:
                self._sell(ticker, decision.scale_shares, "Partial scale-out")
                pos["scaled_out"] = True
                self._bracket(ticker, pos["stop_loss_pct"], er.hard_stop_price(
                    pos, pos["buy_price"], pos["highest_unrealized_pct"], False, age),
                    persist_hard=False)
            elif decision.action == ec.HOLD:
                if decision.new_trail_pct is not None or decision.hard_changed:
                    trail = (decision.new_trail_pct if decision.new_trail_pct is not None
                             else pos["stop_loss_pct"])
                    self._bracket(ticker, trail, decision.desired_hard, persist_hard=True)
                    pos["stop_loss_pct"] = pos["broker_trail_pct"]
        self._broker()

    def run(self):
        previous = None
        eod_session = None
        pending_eod = False
        for event in self.data["events"]:
            self.now = _timestamp(event["timestamp"], "timestamp")
            self.today = dt.date.fromisoformat(event["session"])
            self.quotes = event["broker_quotes"]
            kind = event["type"]
            _require(not pending_eod or kind == "eod_latch",
                     "EOD-window monitor must be followed immediately by eod_latch; "
                     "do not omit the live post-monitor proven latch / rotation check")
            if previous and previous["session"] != event["session"] and self.positions:
                _require(eod_session == previous["session"],
                         "Held overnight without prior eod_latch; capture the preceding EOD monitor/latch")
                _require(trading_days_between(_date(previous["session"], "session"), self.today) == 1,
                         "Held book crosses missing NYSE sessions; capture their monitoring/EOD inputs")
            self._broker()
            if kind == "buy_cycle":
                self._buy(event)
            elif kind == "monitor":
                self._monitor()
                pending_eod = dt.time(15, 45) <= self.now.astimezone(NY).time() < dt.time(16)
            elif kind == "eod_latch":
                _require(previous is not None and previous["type"] == "monitor" and pending_eod
                         and previous["session"] == event["session"],
                         "eod_latch requires the immediately preceding same-session EOD-window "
                         "intraday monitor; latching first changes the exit phase")
                self._marks()
                fresh = set(event["fresh_trigger_tickers"]) - self.positions.keys()
                older = any(trading_days_between(dt.date.fromisoformat(p["buy_date"]), self.today) >= 7
                            for p in self.positions.values())
                _require(not (fresh and older and len(self.positions) >= self.cfg.max_positions),
                         "Rank & Replace could apply: full book, age >=7 trading days and fresh "
                         "candidates. Rotation is unsupported; supply a narrower capture or implement it.")
                for ticker, pos in self.positions.items():
                    pos["closed_above_entry"] |= self.quotes[ticker]["price"] > pos["buy_price"]
                    self._record(ticker, "EOD_LATCH", closed_above_entry=pos["closed_above_entry"])
                eod_session, pending_eod = event["session"], False
            elif kind == "end_mark":
                self._marks()
            previous = event
        equity = self._marks()
        return {
            "variant": "without_ai_veto" if self.disable_ai_veto else "baseline",
            "initial_cash": self.data["initial_cash"], "cash": self.cash,
            "open_market_value": equity - self.cash, "final_equity_net": equity,
            "net_profit": equity - self.data["initial_cash"],
            "commission": self.commission, "slippage_cost": self.slippage,
            "open_positions": list(self.positions.values()), "fills": self.fills,
            "cooldown_ledger": self.ledger.sales,
            "decisions": self.decisions, "brackets": self.brackets,
        }


def replay(data: dict, *, compare_without_ai_veto=False, disable_ai_veto=False) -> dict:
    """Replay one immutable input, optionally with exactly one entry-veto ablation."""
    _require(not (compare_without_ai_veto and disable_ai_veto),
             "choose comparison or single variant, not both")
    data = copy.deepcopy(data)
    baseline = _Replay(data, disable_ai_veto).run()
    result = {
        "simulation": LABEL, "dataset_label": data.get("dataset_label"),
        "configuration_provenance": "Input-supplied; deployed configuration not independently verified",
        "input_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "caveats": CAVEATS,
    }
    result["effective_config"] = {
        key: data[key] for key in ("decision_config", "exit_config", "replay_config",
                                  "costs", "shared_exit_rules", "scope")
    }
    result["variant" if disable_ai_veto else "baseline"] = baseline
    if compare_without_ai_veto:
        variant = _Replay(data, True).run()
        result["variant"] = variant
        result["net_final_equity_difference"] = variant["final_equity_net"] - baseline["final_equity_net"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Recorded point-in-time schema-v1 JSON (no network)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--compare-without-ai-veto", action="store_true",
                      help="Baseline and D-grade-veto-only ablation; compare NET final equity including open marks")
    mode.add_argument("--disable-ai-veto", action="store_true",
                      help="One variant only; retain ranking, score floors, earnings gates, sizing and exits")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text())
        result = replay(data, compare_without_ai_veto=args.compare_without_ai_veto,
                        disable_ai_veto=args.disable_ai_veto)
    except (ReplayInputError, OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Replay rejected: {exc}\n{CAPTURE_REQUIREMENT}\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
