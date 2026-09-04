"""Replay the bot's OWN closed trades against alternative exit-rule parameters.

This harness is different in kind from the others in `research/`. Those backtest
the strategy over a synthetic universe of screener-generated entries. This one
takes the trades the bot *actually placed*, replays each one on 5-minute bars,
and reports what a different set of exit parameters would have done — expressed
as a dollar delta against the exit that really happened.

That framing is the point. It cannot tell you whether the strategy is good; it
can only tell you whether the exit parameters are leaving money on the table on
the entries the strategy is already choosing. For periodic parameter review that
is exactly the question, and it has no simulation-universe bias: every entry is
real, every comparison baseline is a real fill.

WHY 5-MINUTE BARS
-----------------
The agent evaluates rules on a 15-minute cycle and its loss rules do not sell —
they call `arm_exit()`, which places a tight (0.6%) IBKR trailing stop with a
3.25-hour deadline. On daily bars that mechanism cannot be modelled at all: the
armed trail resolves intraday, usually on the same day it was placed. Replaying
on daily bars silently converts every armed exit into a next-day market sell,
which is both wrong and pessimistic.

So this harness:
  - evaluates rules ONLY on 15-minute boundaries (:00, :15, :30, :45)
  - on a trigger, arms a trail at that bar's CLOSE (never its high — see below)
  - resolves the trail against every subsequent 5-minute bar
  - forces a market sell at the first 15-minute boundary past the deadline

ANCHOR CORRECTNESS (read before changing)
-----------------------------------------
`arm_exit()` places the stop at the moment the trigger fires, so the trail can
only ever ratchet from the price at THAT moment. Seeding the peak with the
trigger bar's HIGH back-dates the stop to a price that printed before it existed
and books exits better than were reachable. This is the same look-ahead bug
documented in decisions/2026-08-17_armed-exit-backtest-lookahead.md; do not
reintroduce it here. The trail is seeded with the trigger bar's close.

Gap handling is deliberately pessimistic: if the first bar after arming OPENS
through the trail level, the fill is the open, not the level.

LIMITATIONS
-----------
  - Fills are modelled at the trail level when a bar's low reaches it. Real fills
    are marginally worse.
  - Commission and slippage are not modelled. They are near-identical across the
    configurations being compared, so they cancel in the delta.
  - Every trade comes from one market regime. A parameter that wins here has not
    been shown to generalise.
  - Sample size is small. Check `n` in the output before believing anything: a
    result driven by one or two trades is noise. The report prints per-trade
    deltas precisely so this is visible rather than hidden in an average.

USAGE
-----
    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/exit_rule_replay.py                  # headline comparison
    python3 research/exit_rule_replay.py --grid           # full parameter sweep
    python3 research/exit_rule_replay.py --json out.json  # machine-readable

Requires SUPABASE_URL, SUPABASE_KEY and FMP_API_KEY in the environment. Reads
Supabase and FMP only — it never writes anything anywhere.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import requests

# ── Live agent defaults these experiments are measured against ────────────────
# Mirrors of execution_agent.py. If a default changes there, change it here and
# say so, otherwise "current config" in the report is a lie.
ARMED_EXIT_TRAIL_PCT = 0.006
ARMED_EXIT_DEADLINE_HOURS = 3.25
CHECK_MINUTES = (0, 15, 30, 45)

FMP_5MIN = "https://financialmodelingprep.com/stable/historical-chart/5min"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"Missing {name}. Load credentials first:\n"
            "  set -a && . ~/.config/ai-trading-bot/secrets.env && set +a"
        )
    return val


@dataclass
class ExitConfig:
    """One candidate parameterisation of the early-exit rules."""

    label: str
    # Percentage kill-switch
    pct: float | None = None          # e.g. 1.0 for -1%
    pct_last_day: int = 0
    # Flat dollar stop
    dollar: float | None = None
    dollar_last_day: int = 5
    # ATR-normalised thesis stop
    atr_mult: float | None = None
    atr_start_day: int = 2
    atr_last_day: int = 5
    # Mechanism
    mode: str = "armed"               # "armed" (live behaviour) or "market"
    trail: float = ARMED_EXIT_TRAIL_PCT
    deadline_h: float = ARMED_EXIT_DEADLINE_HOURS

    # ── Prove-It Stop ────────────────────────────────────────────────────────
    # When `proveit` is True the trade is replayed by simulate_proveit() instead
    # of simulate(), and the three entry-anchored rules above are ignored: the
    # whole point of the design is that Phase 1 REPLACES them.
    proveit: bool = False
    # Phase 1 (position has never closed above entry) — entry-anchored tiers,
    # ordered, as ((last_day, pct), ...). The first tier whose last_day >= the
    # current day index wins, so ((1, 1.0), (99, 1.5)) means "1% on days 0-1,
    # then 1.5% from day 2 onwards".
    p1_tiers: tuple[tuple[int, float], ...] | None = None
    # True  = fire the instant price TOUCHES the level (models a resting IBKR
    #         stop; wick-sensitive).
    # False = fire only if a 15-minute CLOSE is at/below the level (models the
    #         agent's monitoring cycle; ignores wicks).
    p1_touch: bool = False
    # Phase 2 (position HAS closed above entry) — peak-anchored give-back.
    p2_enabled: bool = True
    # Minimum peak gain % before the breakeven floor arms. Below this a floor
    # would sit inside spread/tick noise and shake the position out; see INCY.
    p2_arm_gain: float = 2.0
    # Where the floor sits, as % above entry. 0.0 == breakeven.
    p2_floor_pct: float = 0.0
    # Above this gain the existing tight ladder rung takes over instead.
    p2_ladder_gain: float = 5.0
    p2_ladder_trail: float = 0.015

    def describe(self) -> str:
        bits = []
        if self.proveit:
            if self.p1_tiers:
                spans = []
                prev = 0
                for last_day, pct in self.p1_tiers:
                    span = (f"d{prev}" if last_day >= 90 or last_day == prev
                            else f"d{prev}-{last_day}")
                    span = f"d{prev}+" if last_day >= 90 else span
                    spans.append(f"{span}:{pct}%")
                    prev = last_day + 1
                bits.append("P1[" + " ".join(spans) + "]")
                bits.append("touch" if self.p1_touch else "close")
            if self.p2_enabled:
                bits.append(f"P2[arm>={self.p2_arm_gain}% floor=+{self.p2_floor_pct}% "
                            f"ladder>={self.p2_ladder_gain}%@{self.p2_ladder_trail*100:.1f}%]")
            bits.append(self.mode)
            return ", ".join(bits)
        if self.pct is not None:
            span = "day 0" if self.pct_last_day == 0 else f"days 0-{self.pct_last_day}"
            bits.append(f"{self.pct}% {span}")
        if self.dollar is not None:
            bits.append(f"${self.dollar:.0f} days 0-{self.dollar_last_day}")
        if self.atr_mult is not None:
            bits.append(f"{self.atr_mult}xATR days {self.atr_start_day}-{self.atr_last_day}")
        bits.append(self.mode)
        return ", ".join(bits)

    def p1_pct_for_day(self, day: int) -> float | None:
        if not self.p1_tiers:
            return None
        for last_day, pct in self.p1_tiers:
            if day <= last_day:
                return pct
        return None


@dataclass
class Trade:
    ticker: str
    buy_price: float
    buy_ts: dt.datetime
    sell_price: float
    sell_ts: dt.datetime
    shares: int
    profit_loss: float
    sell_reason: str | None
    bars: list[dict] = field(default_factory=list)
    trade_days: list[str] = field(default_factory=list)
    entry_atr_pct: float | None = None

    @property
    def is_loser(self) -> bool:
        return self.profit_loss < 0


# ── Data loading ──────────────────────────────────────────────────────────────

def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_trades(verify_tls: bool = True) -> list[Trade]:
    """Every closed trade from Supabase `trade_history`."""
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    resp = requests.get(
        f"{url}/rest/v1/trade_history",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={
            "select": "ticker,buy_price,buy_date,sell_price,sell_date,shares,"
                      "profit_loss,sell_reason",
            "order": "sell_date.asc",
        },
        timeout=30,
        verify=verify_tls,
    )
    resp.raise_for_status()
    out = []
    for row in resp.json():
        if not row.get("sell_date") or not row.get("buy_date"):
            continue
        out.append(Trade(
            ticker=row["ticker"],
            buy_price=float(row["buy_price"]),
            buy_ts=_parse_ts(row["buy_date"]),
            sell_price=float(row["sell_price"]),
            sell_ts=_parse_ts(row["sell_date"]),
            shares=int(row["shares"]),
            profit_loss=float(row.get("profit_loss") or 0.0),
            sell_reason=row.get("sell_reason"),
        ))
    return out


def fetch_5min(symbol: str, start: dt.datetime, end: dt.datetime, api_key: str) -> list[dict]:
    """5-minute bars covering [start, end]. FMP caps the span, so chunk it."""
    collected: dict[str, dict] = {}
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        chunk_end = min(cursor + dt.timedelta(days=5), last)
        resp = requests.get(
            FMP_5MIN,
            params={
                "symbol": symbol,
                "from": cursor.isoformat(),
                "to": chunk_end.isoformat(),
                "apikey": api_key,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            for row in payload:
                collected[row["date"]] = row
        cursor = chunk_end + dt.timedelta(days=1)

    bars = []
    for stamp in sorted(collected):
        row = collected[stamp]
        ts = dt.datetime.fromisoformat(row["date"].replace(" ", "T") + "+00:00")
        if start <= ts <= end:
            bars.append({
                "ts": ts,
                "date": ts.date().isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    bars.sort(key=lambda b: b["ts"])
    return bars


def fetch_entry_atr_pct(symbol: str, buy_date: dt.date, api_key: str,
                        window: int = 14) -> float | None:
    """Wilder ATR% on the daily bars strictly BEFORE entry (no look-ahead)."""
    resp = requests.get(
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        params={
            "symbol": symbol,
            "from": (buy_date - dt.timedelta(days=120)).isoformat(),
            "to": buy_date.isoformat(),
            "apikey": api_key,
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return None
    rows = sorted((r for r in rows if r["date"] < buy_date.isoformat()),
                  key=lambda r: r["date"])

    atr = None
    prev_close = None
    trs: list[float] = []
    result = None
    for i, row in enumerate(rows):
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        tr = (high - low) if prev_close is None else max(
            high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        if i == window - 1:
            atr = sum(trs[:window]) / window
        elif i >= window:
            atr = (atr * (window - 1) + tr) / window
        if atr is not None and close:
            result = atr / close * 100.0
        prev_close = close
    return result


def hydrate(trades: list[Trade], api_key: str, quiet: bool = False) -> list[Trade]:
    """Attach price history. This is the slow part — one FMP call per chunk."""
    hydrated = []
    for i, trade in enumerate(trades, 1):
        if not quiet:
            print(f"  [{i}/{len(trades)}] {trade.ticker}", file=sys.stderr)
        # Widen to whole-day boundaries. Supabase stores buy_date/sell_date at
        # midnight, so a same-day round trip yields a zero-width window and the
        # trade is silently dropped -- which excluded exactly the fastest,
        # largest same-day losers (OII, FROG) that Phase 1 is meant to catch.
        window_start = trade.buy_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = trade.sell_ts.replace(hour=23, minute=59, second=59, microsecond=0)
        trade.bars = fetch_5min(trade.ticker, window_start, window_end, api_key)
        if not trade.bars:
            continue
        days: list[str] = []
        for bar in trade.bars:
            if not days or days[-1] != bar["date"]:
                days.append(bar["date"])
        trade.trade_days = days
        _correct_split(trade)
        trade.entry_atr_pct = fetch_entry_atr_pct(
            trade.ticker, trade.buy_ts.date(), api_key)
        hydrated.append(trade)
    return hydrated


# Common corporate-action ratios. FMP's intraday history is split-ADJUSTED,
# but trade_history stores the unadjusted price actually paid, so any trade
# spanning a split replays against prices on a different scale. APH (2-for-1)
# produced a fictitious -$11,650 loss before this guard existed.
_SPLIT_RATIOS = (1/10, 1/7, 1/5, 1/4, 1/3, 1/2, 2/3, 3/2, 2, 3, 4, 5, 7, 10)


def _correct_split(trade: Trade) -> None:
    """Rescale split-adjusted bars back onto the price actually paid."""
    if not trade.bars:
        return
    ref = min(trade.bars, key=lambda b: abs(b["ts"] - trade.buy_ts))["close"]
    if ref <= 0:
        return
    raw = trade.buy_price / ref
    if 0.8 <= raw <= 1.25:
        return
    ratio = min(_SPLIT_RATIOS, key=lambda r: abs(r - raw))
    if abs(ratio - raw) / raw > 0.15:
        print(f"  ! {trade.ticker}: price scale {raw:.3f} matches no known "
              f"split ratio -- excluding from replay", file=sys.stderr)
        trade.bars = []
        return
    print(f"  ~ {trade.ticker}: rescaling bars by {ratio:g} "
          f"(split-adjusted history)", file=sys.stderr)
    for bar in trade.bars:
        for field_name in ("open", "high", "low", "close"):
            bar[field_name] *= ratio


# ── The replay itself ─────────────────────────────────────────────────────────

def simulate(trade: Trade, cfg: ExitConfig) -> dict | None:
    """Replay one trade. Returns the modelled exit, or None if nothing fired."""
    entry = trade.buy_price
    day_index = {d: i for i, d in enumerate(trade.trade_days)}
    armed: dict | None = None
    closed_above_entry = False
    day_bars: list[dict] = []
    current_date: str | None = None

    for bar in trade.bars:
        # ── Resolve an armed exit before considering new triggers ────────────
        if armed:
            level = armed["peak"] * (1 - cfg.trail)
            if armed["first"] and bar["open"] <= level:
                return {"price": bar["open"], "reason": f"{armed['reason']}_gap"}
            if bar["low"] <= level:
                return {"price": level, "reason": f"{armed['reason']}_trail"}
            armed["peak"] = max(armed["peak"], bar["high"])
            armed["first"] = False
            if bar["ts"].minute in CHECK_MINUTES:
                held_h = (bar["ts"] - armed["at"]).total_seconds() / 3600.0
                if held_h >= cfg.deadline_h:
                    return {"price": bar["close"], "reason": f"{armed['reason']}_deadline"}

        # ── Track the daily-close follow-through latch the thesis stop uses ──
        if current_date != bar["date"]:
            if day_bars and day_bars[-1]["close"] > entry:
                closed_above_entry = True
            day_bars = []
            current_date = bar["date"]
        day_bars.append(bar)

        if bar["ts"].minute not in CHECK_MINUTES or bar["ts"] < trade.buy_ts:
            continue

        day = day_index[bar["date"]]
        close = bar["close"]
        triggered = None

        # Evaluation order mirrors monitor_portfolio_intraday().
        if (cfg.pct is not None and day <= cfg.pct_last_day
                and close <= entry * (1 - cfg.pct / 100.0)):
            triggered = "pct"
        elif (cfg.dollar is not None and day <= cfg.dollar_last_day
                and trade.shares * (close - entry) <= -cfg.dollar):
            triggered = "dollar"
        elif (cfg.atr_mult is not None
                and cfg.atr_start_day <= day <= cfg.atr_last_day
                and not closed_above_entry and trade.entry_atr_pct):
            threshold = cfg.atr_mult * trade.entry_atr_pct
            if (close / entry - 1) * 100.0 <= -threshold:
                triggered = "thesis"

        if not triggered:
            continue
        if cfg.mode == "market":
            return {"price": close, "reason": f"{triggered}_market"}
        armed = {"at": bar["ts"], "peak": close, "first": True, "reason": triggered}

    return None


def simulate_proveit(trade: Trade, cfg: ExitConfig) -> dict | None:
    """Replay one trade under the two-phase Prove-It Stop.

    The governing question is asked once per bar: has this position ever CLOSED
    above entry?

      No  -> PHASE 1. Entry-anchored tiered stop. Fires via arm_exit() (a 0.6%
             trail with a deadline) exactly like the live kill-switch, because
             the replay already showed arming beats a market sell by ~$600.
      Yes -> PHASE 2. Peak-anchored give-back. Below `p2_ladder_gain` the stop
             is a flat floor at/above entry so a green trade cannot become a
             loss; above it, the existing tight ladder rung takes over.

    Phase 2 is modelled as a RESTING stop (filled at the level, or at the open
    when a bar gaps through it) because that is how it is intended to live at
    IBKR. Phase 1 respects `p1_touch`: touch = resting stop, close = the agent's
    15-minute cycle.

    The Phase 2 stop is one-way. It never moves down, mirroring the live
    `_compute_dynamic_trail_pct()` contract.
    """
    entry = trade.buy_price
    day_index = {d: i for i, d in enumerate(trade.trade_days)}
    armed: dict | None = None
    proven = False
    peak = entry
    stop_price: float | None = None
    day_bars: list[dict] = []
    current_date: str | None = None

    for bar in trade.bars:
        # ── Resolve an armed Phase 1 exit before anything else ───────────────
        if armed:
            level = armed["peak"] * (1 - cfg.trail)
            if armed["first"] and bar["open"] <= level:
                return {"price": bar["open"], "reason": f"{armed['reason']}_gap"}
            if bar["low"] <= level:
                return {"price": level, "reason": f"{armed['reason']}_trail"}
            armed["peak"] = max(armed["peak"], bar["high"])
            armed["first"] = False
            if bar["ts"].minute in CHECK_MINUTES:
                held_h = (bar["ts"] - armed["at"]).total_seconds() / 3600.0
                if held_h >= cfg.deadline_h:
                    return {"price": bar["close"], "reason": f"{armed['reason']}_deadline"}
            continue

        # ── Daily rollover: a position becomes "proven" on a close above entry
        if current_date != bar["date"]:
            if day_bars and day_bars[-1]["close"] > entry:
                proven = True
            day_bars = []
            current_date = bar["date"]
        day_bars.append(bar)

        if bar["ts"] < trade.buy_ts:
            continue
        day = day_index[bar["date"]]

        if proven and cfg.p2_enabled:
            # Level is derived from the peak BEFORE this bar, then this bar's
            # low is tested against it. Raising the stop with the same bar's
            # high and then filling on its low would be look-ahead.
            peak_gain = (peak / entry - 1) * 100.0
            candidate: float | None = None
            if peak_gain >= cfg.p2_ladder_gain:
                candidate = peak * (1 - cfg.p2_ladder_trail)
            elif peak_gain >= cfg.p2_arm_gain:
                candidate = entry * (1 + cfg.p2_floor_pct / 100.0)
            if candidate is not None:
                stop_price = candidate if stop_price is None else max(stop_price, candidate)

            if stop_price is not None:
                if bar["open"] <= stop_price:
                    return {"price": bar["open"], "reason": "p2_gap"}
                if bar["low"] <= stop_price:
                    return {"price": stop_price, "reason": "p2_floor"}

        elif not proven:
            pct = cfg.p1_pct_for_day(day)
            if pct is not None:
                level = entry * (1 - pct / 100.0)
                hit = False
                fill = level
                if cfg.p1_touch:
                    if bar["open"] <= level:
                        hit, fill = True, bar["open"]
                    elif bar["low"] <= level:
                        hit, fill = True, level
                elif bar["ts"].minute in CHECK_MINUTES and bar["close"] <= level:
                    hit, fill = True, bar["close"]

                if hit:
                    if cfg.mode == "market":
                        return {"price": fill, "reason": "p1_market"}
                    armed = {"at": bar["ts"], "peak": fill,
                             "first": True, "reason": "p1"}
                    continue

        peak = max(peak, bar["high"])

    return None


def score(trades: list[Trade], cfg: ExitConfig) -> dict[str, Any]:
    """Aggregate one configuration into a comparable result.

    `net` is the ALL-IN sum of every per-trade delta — including losers the
    configuration made worse. An earlier version summed only `loser_saved +
    winner_delta`, silently dropping negative deltas on losing trades. That
    flattered every aggressive configuration (the shipped stack scored +$186
    when its true all-in figure is -$272) because tightening a loss rule most
    often makes some losers slightly worse while making a few much better.
    Do not reintroduce a `net` that ignores a sign.
    """
    loser_saved = loser_cost = winner_delta = 0.0
    losers_helped = losers_hurt = winners_hurt = 0
    per_trade = []
    worst_loss = 0.0
    over_300 = 0
    over_500 = 0

    for trade in trades:
        sim = simulate_proveit(trade, cfg) if cfg.proveit else simulate(trade, cfg)
        delta = 0.0 if sim is None else round(
            (sim["price"] - trade.sell_price) * trade.shares, 2)
        # Resulting P&L for this trade under `cfg`. This is what answers "how
        # big is my worst loss", which an aggregate net deliberately hides.
        result_pl = trade.profit_loss + delta
        worst_loss = min(worst_loss, result_pl)
        if result_pl < -300:
            over_300 += 1
        if result_pl < -500:
            over_500 += 1
        if abs(delta) > 0.005:
            per_trade.append({
                "ticker": trade.ticker,
                "actual_pl": round(trade.profit_loss, 2),
                "delta": delta,
                "result_pl": round(result_pl, 2),
                "reason": sim["reason"] if sim else None,
            })
        if trade.is_loser:
            if delta > 0:
                loser_saved += delta
                losers_helped += 1
            elif delta < 0:
                loser_cost += delta
                losers_hurt += 1
        else:
            winner_delta += delta
            if delta < 0:
                winners_hurt += 1

    return {
        "label": cfg.label,
        "config": cfg.describe(),
        "loser_saved": round(loser_saved, 2),
        "loser_cost": round(loser_cost, 2),
        "loser_net": round(loser_saved + loser_cost, 2),
        "winner_delta": round(winner_delta, 2),
        "net": round(loser_saved + loser_cost + winner_delta, 2),
        "losers_helped": losers_helped,
        "losers_hurt": losers_hurt,
        "winners_hurt": winners_hurt,
        "worst_loss": round(worst_loss, 2),
        "over_300": over_300,
        "over_500": over_500,
        "per_trade": sorted(per_trade, key=lambda r: r["delta"]),
    }


# ── Candidate sets ────────────────────────────────────────────────────────────

def shipped_config() -> ExitConfig:
    """What the agent runs today. Every other result is relative to this.

    The live dollar stop is no longer a flat amount: it resolves to
    (equity / EFFECTIVE_POSITION_SLOTS) x EARLY_DOLLAR_STOP_PCT, i.e.
    (equity / 4) x 6%. At the ~$100K equity these trades were placed under that
    is $1,500, which is what is modelled here. If equity has moved materially
    since, recompute this before quoting the result — a stale figure here makes
    every "vs shipped" comparison wrong.
    See decisions/2026-08-20_slot-derived-early-dollar-stop.md.
    """
    return ExitConfig(
        label="SHIPPED (1.0% day 0 + $1500 slot-derived dollar stop + 1xATR thesis)",
        pct=1.0, pct_last_day=0,
        dollar=1500.0, dollar_last_day=5,
        atr_mult=1.0, atr_start_day=2, atr_last_day=5,
    )


def headline_configs() -> list[ExitConfig]:
    """The comparisons that decided the shipped parameters, plus neighbours."""
    return [
        shipped_config(),
        # Like-for-like FULL-STACK comparisons. Single-rule rows below measure a
        # rule in isolation, which overstates any rule whose saves are also
        # reachable by a faster rule running alongside it. Only these rows answer
        # "what would the agent as a whole have done".
        ExitConfig("PREV STACK (2.0% days 0-1 + flat $500 + 1xATR)",
                   pct=2.0, pct_last_day=1,
                   dollar=500.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK minus dollar stop (1.0% day 0 + 1xATR)",
                   pct=1.0, pct_last_day=0,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK minus thesis stop (1.0% day 0 + $1500)",
                   pct=1.0, pct_last_day=0,
                   dollar=1500.0, dollar_last_day=5),
        ExitConfig("SUPERSEDED STACK, flat $500 dollar stop",
                   pct=1.0, pct_last_day=0,
                   dollar=500.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK, dollar stop at $1000",
                   pct=1.0, pct_last_day=0,
                   dollar=1000.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("Kill-switch only: 1.0% day 0", pct=1.0, pct_last_day=0),
        ExitConfig("Kill-switch only: 0.75% day 0", pct=0.75, pct_last_day=0),
        ExitConfig("Kill-switch only: 1.5% day 0", pct=1.5, pct_last_day=0),
        ExitConfig("Kill-switch only: 1.0% days 0-1", pct=1.0, pct_last_day=1),
        ExitConfig("PREVIOUS: 2.0% days 0-1", pct=2.0, pct_last_day=1),
        ExitConfig("Market-sell instead of arming", pct=1.0, pct_last_day=0, mode="market"),
        ExitConfig("Dollar stop only ($500)", dollar=500.0),
        ExitConfig("Thesis stop only (1xATR)", atr_mult=1.0),
    ]


def grid_configs() -> list[ExitConfig]:
    """Full sweep. Use when a headline result looks unstable."""
    out = []
    for pct in (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        for last_day in (0, 1, 2):
            for mode in ("armed", "market"):
                out.append(ExitConfig(
                    f"pct={pct} day<={last_day} {mode}",
                    pct=pct, pct_last_day=last_day, mode=mode))
    for dollar in (200, 300, 400, 500, 600, 800):
        for last_day in (0, 1, 3, 5):
            out.append(ExitConfig(
                f"${dollar} day<={last_day}", dollar=float(dollar),
                dollar_last_day=last_day))
    for mult in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        for last_day in (4, 5, 6):
            out.append(ExitConfig(
                f"{mult}xATR days 2-{last_day}", atr_mult=mult,
                atr_last_day=last_day))
    return out


# ── Reporting ─────────────────────────────────────────────────────────────────

def proveit_configs() -> list[ExitConfig]:
    """The Prove-It Stop sweep.

    Every row here REPLACES the kill-switch, dollar stop and thesis stop with a
    single two-phase rule, so these are whole-stack comparisons against the
    exits that actually happened — directly comparable to the SHIPPED row.

    Two things are being measured:
      1. Phase 1 shape — does widening the tier after day 1 help or hurt, and
         does firing on an intraday TOUCH cost more than waiting for a 15-minute
         CLOSE? (INCY is the motivating case: it wicked to -1.84% on day 1 and
         still finished at -1.09%.)
      2. Phase 2 arming gain — how far must a trade advance before a breakeven
         floor is safe to place? Too low and normal noise stops it out.
    """
    out = [shipped_config()]

    tier_shapes: list[tuple[str, tuple[tuple[int, float], ...]]] = [
        ("1.0%/d0-1 then 1.5%", ((1, 1.0), (99, 1.5))),
        ("1.0%/d0-1 then 2.0%", ((1, 1.0), (99, 2.0))),
        ("1.0%/d0   then 1.5%", ((0, 1.0), (99, 1.5))),
        ("1.0%/d0   then 2.0%", ((0, 1.0), (99, 2.0))),
        ("flat 1.0%",           ((99, 1.0),)),
        ("flat 1.5%",           ((99, 1.5),)),
        ("flat 2.0%",           ((99, 2.0),)),
    ]

    for name, tiers in tier_shapes:
        for touch in (False, True):
            mech = "touch" if touch else "close"
            out.append(ExitConfig(
                f"ProveIt P1 {name} [{mech}] + P2 arm2%",
                proveit=True, p1_tiers=tiers, p1_touch=touch,
                p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=0.0))

    # Phase 2 arming sensitivity, held against the leading Phase 1 shape.
    for arm in (1.0, 1.5, 2.0, 3.0, 4.0):
        for floor in (0.0, 0.5):
            out.append(ExitConfig(
                f"ProveIt P1 1.0/1.5 [close] + P2 arm{arm}% floor+{floor}%",
                proveit=True, p1_tiers=((1, 1.0), (99, 1.5)), p1_touch=False,
                p2_enabled=True, p2_arm_gain=arm, p2_floor_pct=floor))

    # Isolate each phase so a headline result cannot be misread as coming from
    # the half that did not actually produce it.
    out.append(ExitConfig(
        "ProveIt PHASE 1 ONLY (1.0/1.5, close)",
        proveit=True, p1_tiers=((1, 1.0), (99, 1.5)), p1_touch=False,
        p2_enabled=False))
    out.append(ExitConfig(
        "ProveIt PHASE 2 ONLY (arm 2%, breakeven floor)",
        proveit=True, p1_tiers=None, p2_enabled=True, p2_arm_gain=2.0))

    return out


def report(results: list[dict], trades: list[Trade], top: int | None = None) -> None:
    losers = [t for t in trades if t.is_loser]
    total_loss = sum(t.profit_loss for t in losers)
    print()
    print("=" * 92)
    print(f"Closed trades replayed : {len(trades)}  "
          f"({len(losers)} losers, {len(trades) - len(losers)} winners)")
    print(f"Total realised loss    : ${total_loss:,.2f}")
    if len(trades) < 30:
        print()
        print(f"  ⚠  n={len(trades)} is small. Treat differences under ~$500, or driven by")
        print("     fewer than 3 trades, as noise. Check the per-trade deltas below.")
    print("=" * 92)
    print()
    print("All figures are deltas against the exits that ACTUALLY happened.")
    print("A positive net means the configuration would have made more money.")
    print()
    header = (f"{'configuration':<50}{'losers':>9}{'winners':>9}"
              f"{'NET':>10}{'worst':>9}{'>300':>6}{'harmed':>8}")
    print(header)
    print("-" * len(header))

    ordered = sorted(results, key=lambda r: r["net"], reverse=True)
    if top:
        ordered = ordered[:top]
    for res in ordered:
        harmed = res["losers_hurt"] + res["winners_hurt"]
        print(f"{res['label'][:49]:<50}"
              f"{res['loser_net']:>9,.0f}"
              f"{res['winner_delta']:>9,.0f}"
              f"{res['net']:>10,.0f}"
              f"{res.get('worst_loss', 0):>9,.0f}"
              f"{res.get('over_300', 0):>6}"
              f"{harmed:>8}")

    print()
    print("  worst = largest single-trade loss under that configuration")
    print("  >300  = number of trades losing more than $300")
    print()
    print("Per-trade detail for the top configuration "
          "(watch for a result carried by one trade):")
    best = ordered[0]
    if not best["per_trade"]:
        print("  (no trade would have exited differently)")
    for row in best["per_trade"]:
        sign = "+" if row["delta"] >= 0 else ""
        result = row.get("result_pl")
        tail = f"   -> ${result:>9,.2f}" if result is not None else ""
        print(f"  {row['ticker']:<8} actual ${row['actual_pl']:>10,.2f}   "
              f"delta {sign}${row['delta']:>10,.2f}{tail}   [{row['reason']}]")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--grid", action="store_true",
                        help="run the full parameter sweep instead of the headline set")
    parser.add_argument("--proveit", action="store_true",
                        help="run the two-phase Prove-It Stop sweep")
    parser.add_argument("--top", type=int, default=25,
                        help="rows to print when using --grid (default 25)")
    parser.add_argument("--json", metavar="PATH",
                        help="also write full results as JSON")
    parser.add_argument("--insecure", action="store_true",
                        help="skip TLS verification for Supabase (local trust-store issues)")
    args = parser.parse_args()

    api_key = _env("FMP_API_KEY")

    print("Loading closed trades from Supabase...", file=sys.stderr)
    trades = load_trades(verify_tls=not args.insecure)
    print(f"Fetching 5-minute bars for {len(trades)} trades...", file=sys.stderr)
    trades = hydrate(trades, api_key)
    if not trades:
        sys.exit("No trades with usable price history — nothing to replay.")

    if args.proveit:
        configs = proveit_configs()
    elif args.grid:
        configs = grid_configs()
    else:
        configs = headline_configs()
    results = [score(trades, cfg) for cfg in configs]
    report(results, trades, top=args.top if (args.grid or args.proveit) else None)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                "n_trades": len(trades),
                "results": results,
            }, fh, indent=2)
        print(f"Full results written to {args.json}")


if __name__ == "__main__":
    main()
