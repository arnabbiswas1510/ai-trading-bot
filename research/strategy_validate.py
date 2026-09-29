#!/usr/bin/env python3
"""Statistical-rigor harness for the strategy backtester (fidelity item #6).

The strategy backtester (research/strategy_backtest.py) reports ONE number over
ONE window. That is exactly how a lucky window gets shipped as an edge. Before any
backtest result is allowed to justify sizing real money, it has to survive the
four questions AGENTS.md mandates for every money decision:

  1. REPORT n FIRST.  A result on a handful of trades is noise. This harness
     prints the closed-trade count before anything else, every time.

  2. DOES THE STRATEGY BEAT A NULL?  The breakout selection is compared against a
     PERMUTATION baseline: the same slots, sizing, market filter, LIVE exits and
     costs, but entries chosen at RANDOM from the names that merely have valid
     indicators that day (research/strategy_backtest.simulate(rng=...)). If the
     real strategy does not clear the random distribution, the selection rule adds
     nothing and no exit tuning will save it.

  3. IS IT CARRIED BY ONE TRADE?  The real run is re-scored with its top-k
     winners removed. An edge that evaporates when the best 1-3 trades are dropped
     is an outlier, not a repeatable process.

  4. IS THE EDGE STABLE OUT OF SAMPLE?  Trades are grouped by buy_date into
     contiguous WALK-FORWARD folds. A strategy whose whole result comes from one
     six-week regime is not validated — it is clustered, the exact failure the
     52-trade exit sample already suffers from.

This harness deliberately does NOT declare a verdict by exit code (0 = ran,
1 = error). It prints the evidence for a human to read against the four questions.
It is fully OFFLINE — it runs on the committed benchmark_data daily bars, so it is
free, deterministic and needs no FMP key.

Usage:
    python3 research/strategy_validate.py
    python3 research/strategy_validate.py --start 2023-08-01 --end 2026-08-04 \
        --trials 200 --folds 4 --universe all
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import strategy_backtest as sb  # noqa: E402


def _net(trades: list[dict]) -> float:
    """Net P&L of a trade list (costs already baked into net_profit_loss)."""
    return round(sum(t.get("net_profit_loss", t["profit_loss"]) for t in trades), 2)


def _closed(trades: list[dict]) -> list[dict]:
    """Full exits only — a partial scale-out is not an independent trade."""
    return [t for t in trades if not t.get("partial")]


def _expectancy(trades: list[dict]) -> float:
    closed = _closed(trades)
    return round(_net(trades) / len(closed), 2) if closed else 0.0


def _percentile_of(value: float, distribution: list[float]) -> float:
    """Fraction of the null distribution at or below `value`, in percent.
    High = the real strategy beats most random runs."""
    if not distribution:
        return float("nan")
    below = sum(1 for x in distribution if x <= value)
    return round(100.0 * below / len(distribution), 1)


# ── Q2: permutation / random-entry null ───────────────────────────────────────

def random_null_distribution(universe, start, end, capital, slots, trials):
    """Net P&L of `trials` random-entry runs. Seeded per trial for reproducibility."""
    out = []
    for i in range(trials):
        res = sb.simulate(universe, start, end, initial_capital=capital,
                          max_positions=slots, rng=random.Random(10_000 + i))
        out.append(res["summary"]["net_pnl"])
    return out


# ── Q3: concentration / drop-top-k ────────────────────────────────────────────

def drop_top_k(trades: list[dict], k: int) -> tuple[float, list[str]]:
    """Net P&L with the k richest FULL exits removed, and which tickers they were.
    Scale-outs stay — they cannot exist without their parent full exit."""
    closed = _closed(trades)
    partials = [t for t in trades if t.get("partial")]
    ranked = sorted(closed, key=lambda t: t.get("net_profit_loss", t["profit_loss"]),
                    reverse=True)
    dropped = ranked[:k]
    dropped_ids = {id(t) for t in dropped}
    kept = [t for t in closed if id(t) not in dropped_ids] + partials
    names = [f"{t['ticker']} {t.get('net_profit_loss', t['profit_loss']):+.0f}"
             for t in dropped]
    return _net(kept), names


# ── Q4: walk-forward folds by buy_date ────────────────────────────────────────

def walk_forward_folds(trades: list[dict], folds: int) -> list[dict]:
    """Group CLOSED trades into `folds` contiguous buckets by buy_date order.
    Reports per-fold n, net and expectancy so a one-regime result is visible."""
    closed = sorted(_closed(trades), key=lambda t: t["buy_date"])
    if not closed:
        return []
    # attach the matching scale-outs to the fold of their parent by ticker+buy_date
    partials = [t for t in trades if t.get("partial")]
    per_key: dict[tuple, list[dict]] = {}
    for p in partials:
        per_key.setdefault((p["ticker"], p["buy_date"]), []).append(p)

    n = len(closed)
    size = max(1, (n + folds - 1) // folds)
    out = []
    for f in range(0, n, size):
        chunk = closed[f:f + size]
        members = list(chunk)
        for c in chunk:
            members += per_key.get((c["ticker"], c["buy_date"]), [])
        wins = [t for t in chunk if t.get("net_profit_loss", t["profit_loss"]) > 0]
        out.append({
            "window": (chunk[0]["buy_date"], chunk[-1]["buy_date"]),
            "n": len(chunk),
            "net": _net(members),
            "expectancy": round(_net(members) / len(chunk), 2),
            "win_rate": round(100.0 * len(wins) / len(chunk), 1),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2026-08-04")
    ap.add_argument("--universe", default="all")
    ap.add_argument("--capital", type=float, default=sb.INITIAL_CAPITAL)
    ap.add_argument("--slots", type=int, default=sb.MAX_POSITIONS)
    ap.add_argument("--trials", type=int, default=200, help="random-null trials")
    ap.add_argument("--folds", type=int, default=4, help="walk-forward folds")
    ap.add_argument("--drop-k", type=int, default=3, help="concentration: drop top-k winners")
    args = ap.parse_args(argv)

    universe = sb._universe(args.universe)
    real = sb.simulate(universe, args.start, args.end,
                       initial_capital=args.capital, max_positions=args.slots)
    trades = real["trades"]
    s = real["summary"]
    n = s["closed_trades"]

    print("=" * 72)
    print("STRATEGY VALIDATION — statistical rigor over offline daily bars (#6)")
    print("=" * 72)

    # ── Q1. n FIRST ──────────────────────────────────────────────────────────
    print(f"\nQ1  SAMPLE SIZE (report n first)")
    print(f"    window            {s['window'][0]} .. {s['window'][1]}")
    print(f"    closed trades     {n}")
    print(f"    gross P&L         ${s['total_pnl']:,.2f}")
    print(f"    net P&L           ${s['net_pnl']:,.2f}   (after ${s['total_trading_costs']:,.2f} costs)")
    print(f"    expectancy/trade  ${_expectancy(trades):,.2f} net")
    if n < 30:
        print(f"    ⚠️  n < 30 — below this, every number here is noise. Read accordingly.")

    # ── Q2. beat a null? ─────────────────────────────────────────────────────
    print(f"\nQ2  DOES THE BREAKOUT SELECTION BEAT A RANDOM-ENTRY NULL?  ({args.trials} trials)")
    null = random_null_distribution(universe, args.start, args.end,
                                    args.capital, args.slots, args.trials)
    pct = _percentile_of(s["net_pnl"], null)
    print(f"    real net P&L      ${s['net_pnl']:,.2f}")
    print(f"    null median       ${statistics.median(null):,.2f}")
    print(f"    null 5th/95th     ${_pctl(null, 5):,.2f} / ${_pctl(null, 95):,.2f}")
    print(f"    real percentile   {pct}%  (share of random runs the real strategy beats)")
    if pct >= 95:
        print(f"    → selection clears the null: the breakout rule is adding value here.")
    elif pct <= 50:
        print(f"    → selection does NOT beat random on this window. The edge, if any,")
        print(f"      is not in WHICH names are picked. Do not tune exits to rescue it.")
    else:
        print(f"    → inconclusive: real sits inside the null's bulk. Not validated.")

    # ── Q3. carried by one trade? ────────────────────────────────────────────
    print(f"\nQ3  IS THE RESULT CARRIED BY A FEW TRADES?  (drop top-{args.drop_k})")
    for k in range(1, args.drop_k + 1):
        net_k, names = drop_top_k(trades, k)
        print(f"    drop top-{k}         ${net_k:,.2f}   removed: {', '.join(names)}")
    net_k, _ = drop_top_k(trades, args.drop_k)
    if s["net_pnl"] > 0 and net_k <= 0:
        print(f"    → the entire edge is {args.drop_k} trades. It is an outlier, not a process.")
    elif s["net_pnl"] > 0:
        print(f"    → edge survives dropping the top {args.drop_k}. Broadly based.")
    else:
        print(f"    → net is already ≤ 0; concentration is moot until the base result is positive.")

    # ── Q4. stable out of sample? ────────────────────────────────────────────
    print(f"\nQ4  IS THE EDGE STABLE OUT OF SAMPLE?  (walk-forward, {args.folds} folds by buy_date)")
    folds = walk_forward_folds(trades, args.folds)
    print(f"    {'window':<25}{'n':>4}{'net':>14}{'exp/trade':>12}{'win%':>8}")
    pos_folds = 0
    for fd in folds:
        w = f"{fd['window'][0]}..{fd['window'][1]}"
        print(f"    {w:<25}{fd['n']:>4}${fd['net']:>12,.0f}${fd['expectancy']:>10,.0f}{fd['win_rate']:>8}")
        if fd["net"] > 0:
            pos_folds += 1
    print(f"    profitable folds  {pos_folds}/{len(folds)}")
    if folds and pos_folds <= len(folds) // 2:
        print(f"    → the result is clustered, not persistent. One regime is doing the work.")
    elif folds:
        print(f"    → positive across the majority of folds. Persistence is plausible.")

    print("\n" + "=" * 72)
    print("Read the four answers together. A result must clear ALL of them before it")
    print("is allowed to justify a change or more capital. n first, always.")
    print("=" * 72)
    return 0


def _pctl(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


if __name__ == "__main__":
    raise SystemExit(main())
