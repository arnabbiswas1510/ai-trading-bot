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

    def describe(self) -> str:
        bits = []
        if self.pct is not None:
            span = "day 0" if self.pct_last_day == 0 else f"days 0-{self.pct_last_day}"
            bits.append(f"{self.pct}% {span}")
        if self.dollar is not None:
            bits.append(f"${self.dollar:.0f} days 0-{self.dollar_last_day}")
        if self.atr_mult is not None:
            bits.append(f"{self.atr_mult}xATR days {self.atr_start_day}-{self.atr_last_day}")
        bits.append(self.mode)
        return ", ".join(bits)


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
        trade.bars = fetch_5min(trade.ticker, trade.buy_ts, trade.sell_ts, api_key)
        if not trade.bars:
            continue
        days: list[str] = []
        for bar in trade.bars:
            if not days or days[-1] != bar["date"]:
                days.append(bar["date"])
        trade.trade_days = days
        trade.entry_atr_pct = fetch_entry_atr_pct(
            trade.ticker, trade.buy_ts.date(), api_key)
        hydrated.append(trade)
    return hydrated


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


def score(trades: list[Trade], cfg: ExitConfig) -> dict[str, Any]:
    """Aggregate one configuration into a comparable result."""
    loser_saved = winner_delta = 0.0
    losers_helped = losers_hurt = winners_hurt = 0
    per_trade = []

    for trade in trades:
        sim = simulate(trade, cfg)
        delta = 0.0 if sim is None else round(
            (sim["price"] - trade.sell_price) * trade.shares, 2)
        if abs(delta) > 0.005:
            per_trade.append({
                "ticker": trade.ticker,
                "actual_pl": round(trade.profit_loss, 2),
                "delta": delta,
                "reason": sim["reason"] if sim else None,
            })
        if trade.is_loser:
            if delta > 0:
                loser_saved += delta
                losers_helped += 1
            elif delta < 0:
                losers_hurt += 1
        else:
            winner_delta += delta
            if delta < 0:
                winners_hurt += 1

    return {
        "label": cfg.label,
        "config": cfg.describe(),
        "loser_saved": round(loser_saved, 2),
        "winner_delta": round(winner_delta, 2),
        "net": round(loser_saved + winner_delta, 2),
        "losers_helped": losers_helped,
        "losers_hurt": losers_hurt,
        "winners_hurt": winners_hurt,
        "per_trade": sorted(per_trade, key=lambda r: r["delta"]),
    }


# ── Candidate sets ────────────────────────────────────────────────────────────

def shipped_config() -> ExitConfig:
    """What the agent runs today. Every other result is relative to this."""
    return ExitConfig(
        label="SHIPPED (1.0% day 0 + $500 dollar stop + 1xATR thesis)",
        pct=1.0, pct_last_day=0,
        dollar=500.0, dollar_last_day=5,
        atr_mult=1.0, atr_start_day=2, atr_last_day=5,
    )


def headline_configs() -> list[ExitConfig]:
    """The comparisons that decided the shipped parameters, plus neighbours."""
    return [
        shipped_config(),
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
    header = f"{'configuration':<52}{'losers':>10}{'winners':>10}{'NET':>11}{'harmed':>8}"
    print(header)
    print("-" * len(header))

    ordered = sorted(results, key=lambda r: r["net"], reverse=True)
    if top:
        ordered = ordered[:top]
    for res in ordered:
        harmed = res["losers_hurt"] + res["winners_hurt"]
        print(f"{res['label'][:51]:<52}"
              f"{res['loser_saved']:>10,.0f}"
              f"{res['winner_delta']:>10,.0f}"
              f"{res['net']:>11,.0f}"
              f"{harmed:>8}")

    print()
    print("Per-trade detail for the top configuration "
          "(watch for a result carried by one trade):")
    best = ordered[0]
    if not best["per_trade"]:
        print("  (no trade would have exited differently)")
    for row in best["per_trade"]:
        sign = "+" if row["delta"] >= 0 else ""
        print(f"  {row['ticker']:<8} actual ${row['actual_pl']:>10,.2f}   "
              f"delta {sign}${row['delta']:>10,.2f}   [{row['reason']}]")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--grid", action="store_true",
                        help="run the full parameter sweep instead of the headline set")
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

    configs = grid_configs() if args.grid else headline_configs()
    results = [score(trades, cfg) for cfg in configs]
    report(results, trades, top=args.top if args.grid else None)

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
