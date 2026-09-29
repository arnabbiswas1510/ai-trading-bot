"""
research/strategy_backtest.py — a strategy backtest whose EXITS are the LIVE code.

WHAT THIS IS, AND WHY IT IS DIFFERENT FROM THE OTHER HARNESSES
─────────────────────────────────────────────────────────────
This is the backtest-fidelity Phase 2 deliverable: a full-portfolio strategy
backtest that resolves every exit by calling the *actual* live exit engine —
``exit_core.evaluate_exit`` and the ``exit_rules`` primitives it orchestrates —
rather than re-implementing the exit rules in yet another place.

Every other backtester in this repo used to carry its OWN copy of the exit logic
and those copies drift silently from production:

  • ``research/atr_rank_bt.py``     hard-codes a ``SHIPPED_EXITS`` ladder;
  • ``research/exit_rule_replay.py`` mirrors the ``PROVE_IT_*`` constants inline.

Any profitability number a drifting copy produces answers a question about a
strategy the bot no longer runs. This harness removes that class of error for
the exit side: the daily-bar exit engine lives ONCE in the root module
``daily_exit_sim`` (imported here and by ``backend/backtester.py``) and calls
``exit_core`` and ``exit_rules`` directly, so the Prove-It Stop (Phase 1 band,
Phase 2 give-back floor), the dynamic trail ladder, the power-hold widening and
the partial scale-out are byte-for-byte the live rules. Change a threshold in
``exit_rules.py`` and every backtest changes with it.

WHY IT LIVES IN research/ (the container split is now closed)
─────────────────────────────────────────────────────────────
The exit engine used to be duplicated here because ``backend/backtester.py`` runs
inside the web/dashboard image, which is built ``COPY backend/ ./backend/`` and did
NOT contain the root modules (``config.py``, ``exit_rules.py``, ``exit_core.py`` —
see the NOTE ON CONTAINER LAYOUT in ``config.py``). "Option A"
(``decisions/2026-09-29_backtester-option-a-live-exits.md``) closed that gap: the
Dockerfile now COPYs those three modules plus ``daily_exit_sim`` into the image,
and the dashboard backtester calls the same engine. So the web button and this CLI
tool now run IDENTICAL exit code. This tool still lives in research/ because its
DATA source (``bardata`` over the committed offline dataset) and CLI reporting are
research-only; the exit parity itself is shared, not forked.

DATA: DAILY BARS, OFFLINE, NO FMP SUBSCRIPTION
──────────────────────────────────────────────
Prices come from the committed ``benchmark_data/`` parquet via ``bardata`` — 313
names, 2023-07 .. 2026-08, no network, no rate limit, reproducible. So this needs
no FMP key at all, let alone the intraday subscription.

FIDELITY — READ THIS BEFORE TRUSTING A DOLLAR FIGURE
────────────────────────────────────────────────────
This achieves RULE parity (which exit fires, and why) but NOT exact fill-price
fidelity, because daily bars cannot resolve intraday mechanics:

  • The live loss rules do not sell — they ``arm_exit()`` a tight 0.6% IBKR trail
    with a 3.25h deadline that resolves INTRADAY. On daily bars that bounce is
    invisible, so a Prove-It arm is modelled as a sell AT the level (or the open,
    if the bar gapped through it). This is the same daily-bar limitation
    documented in ``exit_rule_replay.py``; it is why that tool uses 5-minute bars.
  • The 15-minute poll is collapsed to a once-a-day sequence: peak/HWM and arming
    see the day's HIGH, downside stops resolve against the day's LOW, and the
    trail-tightening / scale-out / hard-stop updates are applied at the CLOSE and
    take effect the NEXT day (a ~one-cycle lag versus the live intraday placement).
  • Commission and slippage are NOT modelled (roadmap item #3). Gap-through fills
    are modelled pessimistically (fill at the open when the bar opens through the
    level) but partial-fill and queue effects are not.

Therefore: TRUST this for RELATIVE questions — does a rule fire, how often, does
a change help or hurt, do entries and exits behave like production. Do NOT read
its absolute P&L as a precise +EV/−EV verdict on the tight Prove-It exits; that
needs 5-minute bars, which drop in here with NO logic change (see
``resolve_position_day`` — swap the once-a-day OHLC resolution for a per-5min-bar
loop and the exit_core calls are identical). The register work-item
``intraday-fmp-exit-fidelity`` tracks that upgrade, gated on live usage showing
it is needed.

ENTRY PARITY IS A SEPARATE STEP
───────────────────────────────
The ENTRY scan below mirrors ``backend/backtester.py`` (20-day-high breakout,
above SMA50/200, ≥1.4x volume, SPY-above-EMA21 market filter) so this tool and
the web backtester choose the SAME entries. That mechanical scan is itself a
simplification of the live screen (which leans on fundamentals + AI grading, and
needs point-in-time data — roadmap item #4). Pointing entries at
``decision_core`` is a complementary follow-up; this deliverable is scoped to
EXIT parity.

USAGE
─────
    python3 research/strategy_backtest.py                 # headline + exit histogram
    python3 research/strategy_backtest.py --json out.json # machine-readable
    python3 research/strategy_backtest.py --start 2024-01-01 --end 2026-06-30
    python3 research/strategy_backtest.py --universe pass  # only research/pass_names.txt

No secrets required — reads the committed benchmark dataset only, writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import datetime

# research/ (for bardata) and repo root (for exit_core/exit_rules/config).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bardata  # noqa: E402
from config import MAX_POSITIONS  # noqa: E402

# The daily-bar exit engine is shared verbatim with backend/backtester.py so the
# research and dashboard backtesters can never drift apart — see
# decisions/2026-09-29_backtester-option-a-live-exits.md. Re-exported here so this
# module (and its tests) keep the same public names they always exposed.
from daily_exit_sim import (  # noqa: E402,F401
    build_exit_config,
    new_position,
    DayResult,
    _ladder_trail_pct,
    resolve_position_day,
)
from trade_costs import CostModel, build_cost_model  # noqa: E402

# ── Entry-side constants — mirror backend/backtester.py so entries match ──────────
MARKET_SYMBOL      = "SPY"    # dataset has SPY, not ^GSPC/QQQ; live gate is richer
EMA_WINDOW         = 21
SMA_FAST           = 50
SMA_SLOW           = 200
VOL_WINDOW         = 50
VOL_SURGE          = 1.4      # breakout volume must be >= 1.4x 50d average
HIGH_WINDOW        = 20       # break the prior 20-day high
RS_LOOKBACK        = 252      # 52-week window for the RS proxy (closeness to high)
INITIAL_CAPITAL    = 100_000.0


# ── Indicators over bardata list-of-dict bars (lowercase keys) ────────────────────
@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    sma_fast: float | None = None
    sma_slow: float | None = None
    ema: float | None = None
    vol_sma: float | None = None
    high_prior: float | None = None   # prior HIGH_WINDOW-day high, EXCLUDING today
    rs_max: float | None = None       # trailing RS_LOOKBACK-day high (incl today)


def _prepare(bars: list[dict]) -> list[Bar]:
    """Attach the indicators the entry scan needs. shift(1) on the 20d high so
    today's break of yesterday's high is legitimate (no look-ahead)."""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    vols = [float(b["volume"]) for b in bars]
    out: list[Bar] = []
    # EMA seed = first close
    ema_prev = closes[0]
    alpha = 2.0 / (EMA_WINDOW + 1.0)
    for i, b in enumerate(bars):
        ema_prev = closes[i] * alpha + ema_prev * (1 - alpha)
        sma_fast = statistics.fmean(closes[i - SMA_FAST + 1:i + 1]) if i >= SMA_FAST - 1 else None
        sma_slow = statistics.fmean(closes[i - SMA_SLOW + 1:i + 1]) if i >= SMA_SLOW - 1 else None
        vol_sma = statistics.fmean(vols[i - VOL_WINDOW + 1:i + 1]) if i >= VOL_WINDOW - 1 else None
        high_prior = max(highs[i - HIGH_WINDOW:i]) if i >= HIGH_WINDOW else None
        rs_max = max(closes[max(0, i - RS_LOOKBACK):i + 1])
        out.append(Bar(
            date=b["date"], open=float(b["open"]), high=highs[i], low=float(b["low"]),
            close=closes[i], volume=vols[i], sma_fast=sma_fast, sma_slow=sma_slow,
            ema=ema_prev, vol_sma=vol_sma, high_prior=high_prior, rs_max=rs_max,
        ))
    return out


# ── The portfolio simulation ─────────────────────────────────────────────────────
def _universe(kind: str) -> list[str]:
    if kind == "all":
        return [s for s in bardata.symbols() if s != MARKET_SYMBOL]
    path = os.path.join(_HERE, f"{kind}_names.txt")
    if not os.path.exists(path):
        raise SystemExit(f"Unknown universe '{kind}' (no {path})")
    names = [ln.strip().upper() for ln in open(path) if ln.strip() and not ln.startswith("#")]
    return [s for s in names if s != MARKET_SYMBOL]


def simulate(tickers: list[str], start: str, end: str,
             initial_capital: float = INITIAL_CAPITAL,
             max_positions: int = MAX_POSITIONS,
             costs: CostModel | None = None) -> dict:
    cfg = build_exit_config()
    costs = costs if costs is not None else build_cost_model()

    # Warm indicators with a year of lookback, then trim to the window.
    def _load(sym: str) -> list[Bar] | None:
        raw = bardata.daily(sym)
        if not raw or len(raw) < SMA_SLOW + 5:
            return None
        prepared = _prepare(sorted(raw, key=lambda r: r["date"]))
        return [b for b in prepared if start <= b.date <= end] or None

    data: dict[str, dict[str, Bar]] = {}
    ordered: dict[str, list[Bar]] = {}
    for sym in tickers:
        prepared = _load(sym)
        if prepared:
            ordered[sym] = prepared
            data[sym] = {b.date: b for b in prepared}

    market = _load(MARKET_SYMBOL)
    if not market:
        raise SystemExit(f"Market symbol {MARKET_SYMBOL} missing from dataset.")
    market_by_date = {b.date: b for b in market}
    all_dates = sorted(market_by_date)

    cash = float(initial_capital)
    positions: dict[str, dict] = {}
    pending: list[str] = []
    trades: list[dict] = []
    equity_curve: list[dict] = []
    total_commission = 0.0    # $ paid in commissions across all fills
    total_slippage = 0.0      # $ lost to adverse slippage across all fills

    for i, date in enumerate(all_dates):
        # ── A. Fill pending buys at today's OPEN (queued EOD yesterday) ──────────
        still_pending: list[str] = []
        if pending and i > 0:
            for tk in pending:
                if tk in positions or len(positions) >= max_positions:
                    continue
                bar = data.get(tk, {}).get(date)
                if bar is None or bar.open <= 0 or cash <= 0:
                    if bar is not None:
                        still_pending.append(tk)
                    continue
                remaining = max(1, max_positions - len(positions))
                held_value = sum(
                    p["shares"] * (data[t][date].close if (t in data and date in data[t]) else p["buy_price"])
                    for t, p in positions.items()
                )
                equity_now = cash + held_value
                alloc = min(cash / remaining, equity_now / max_positions)
                # Costs do NOT change which name is bought or the exit math: the
                # position's buy_price stays the unslipped OPEN quote (so exit
                # levels are identical with or without costs). Slippage + commission
                # are charged to CASH only. Size on the effective (slipped) fill so
                # a buy never overspends the allocation.
                buy_fill = costs.buy_fill(bar.open)
                shares = int(alloc // buy_fill)
                if shares <= 0:
                    continue
                commission = costs.commission(shares, buy_fill)
                cash -= shares * buy_fill + commission
                total_commission += commission
                total_slippage += (buy_fill - bar.open) * shares
                positions[tk] = new_position(tk, shares, bar.open, date, alloc, cfg)
                positions[tk]["buy_commission"] = commission
                positions[tk]["buy_slippage"] = (buy_fill - bar.open) * shares
        pending = still_pending

        # ── B. Resolve exits for every open position on today's bar ──────────────
        to_close: list[str] = []
        for tk, pos in positions.items():
            bar = data.get(tk, {}).get(date)
            if bar is None:
                continue
            buy_dt = datetime.strptime(pos["buy_date"], "%Y-%m-%d")
            cur_dt = datetime.strptime(date, "%Y-%m-%d")
            calendar_days = (cur_dt - buy_dt).days
            res = resolve_position_day(pos, bar, calendar_days, cfg)

            if res.scale_shares:
                sell_fill = costs.sell_fill(res.scale_price)
                commission = costs.commission(res.scale_shares, sell_fill)
                cash += res.scale_shares * sell_fill - commission
                total_commission += commission
                total_slippage += (res.scale_price - sell_fill) * res.scale_shares
                trades.append(_trade_record(pos, res.scale_price, date, calendar_days,
                                             "Partial scale-out (+trigger)", res.scale_shares,
                                             partial=True, costs=costs, cost_model_shares=res.scale_shares))
            if res.exit_price is not None:
                sell_fill = costs.sell_fill(res.exit_price)
                commission = costs.commission(pos["shares"], sell_fill)
                cash += pos["shares"] * sell_fill - commission
                total_commission += commission
                total_slippage += (res.exit_price - sell_fill) * pos["shares"]
                trades.append(_trade_record(pos, res.exit_price, date, calendar_days,
                                             res.exit_reason, pos["shares"],
                                             costs=costs, cost_model_shares=pos["shares"]))
                to_close.append(tk)
        for tk in to_close:
            positions.pop(tk)

        # ── C. Market filter — SPY above its EMA-21 ──────────────────────────────
        mkt = market_by_date[date]
        market_bullish = mkt.ema is None or mkt.close > mkt.ema

        # ── D. Breakout scan → queue for next-day open ───────────────────────────
        if market_bullish and (len(positions) + len(pending)) < max_positions:
            candidates: list[tuple[str, float]] = []
            for tk in ordered:
                if tk in positions or tk in pending:
                    continue
                bar = data.get(tk, {}).get(date)
                if bar is None:
                    continue
                if None in (bar.sma_fast, bar.sma_slow, bar.vol_sma, bar.high_prior):
                    continue
                is_breakout = bar.high > bar.high_prior
                is_above_ma = bar.close > bar.sma_fast and bar.close > bar.sma_slow
                is_high_vol = bar.volume > bar.vol_sma * VOL_SURGE
                if is_breakout and is_above_ma and is_high_vol:
                    dist = (bar.rs_max - bar.close) / bar.rs_max if bar.rs_max else 1.0
                    candidates.append((tk, dist))
            candidates.sort(key=lambda x: x[1])   # closest to 52w high first
            open_slots = max_positions - len(positions) - len(pending)
            for tk, _ in candidates[:open_slots]:
                if cash > 0:
                    pending.append(tk)

        # ── E. Record equity ─────────────────────────────────────────────────────
        equity = cash + sum(
            p["shares"] * (data[t][date].close if (t in data and date in data[t]) else p["buy_price"])
            for t, p in positions.items()
        )
        equity_curve.append({"date": date, "equity": round(equity, 2)})

    return _summarize(trades, equity_curve, initial_capital, all_dates, max_positions,
                      total_commission, total_slippage)


def _trade_record(pos: dict, sell_price: float, sell_date: str, hold_days: int,
                  reason: str, shares: int, partial: bool = False,
                  costs: CostModel | None = None, cost_model_shares: int | None = None) -> dict:
    buy = float(pos["buy_price"])
    pnl = (sell_price - buy) * shares          # GROSS: quote-to-quote, cost-free
    commission = 0.0
    slippage_cost = 0.0
    if costs is not None:
        sell_fill = costs.sell_fill(sell_price)
        commission = costs.commission(shares, sell_fill)
        slippage_cost = (sell_price - sell_fill) * shares
        if not partial:   # a full exit also carries the whole position's buy costs
            commission += float(pos.get("buy_commission", 0.0))
            slippage_cost += float(pos.get("buy_slippage", 0.0))
    net = pnl - commission - slippage_cost
    return {
        "ticker": pos["ticker"],
        "shares": shares,
        "buy_price": round(buy, 4),
        "buy_date": pos["buy_date"],
        "sell_price": round(sell_price, 4),
        "sell_date": sell_date,
        "profit_loss": round(pnl, 2),               # GROSS
        "commission": round(commission, 2),
        "slippage_cost": round(slippage_cost, 2),
        "net_profit_loss": round(net, 2),
        "percent_return": round((sell_price / buy - 1.0) * 100.0, 2),
        "hold_days": hold_days,
        "exit_reason": reason,
        "partial": partial,
    }


def _summarize(trades: list[dict], equity_curve: list[dict],
               initial_capital: float, all_dates: list[str], max_positions: int,
               total_commission: float = 0.0, total_slippage: float = 0.0) -> dict:
    closed = [t for t in trades if not t["partial"]]
    n = len(closed)
    wins = [t for t in closed if t["profit_loss"] > 0]
    total_pnl = round(sum(t["profit_loss"] for t in trades), 2)          # GROSS
    net_pnl = round(sum(t.get("net_profit_loss", t["profit_loss"]) for t in trades), 2)
    total_costs = round(total_commission + total_slippage, 2)
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    years = max(1e-9, (datetime.strptime(all_dates[-1], "%Y-%m-%d")
                       - datetime.strptime(all_dates[0], "%Y-%m-%d")).days / 365.25)
    cagr = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0 if initial_capital > 0 else 0.0

    reasons: dict[str, int] = {}
    for t in trades:
        key = _reason_bucket(t["exit_reason"])
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "summary": {
            "closed_trades": n,
            "win_rate": round(100.0 * len(wins) / n, 1) if n else 0.0,
            "avg_return_pct": round(statistics.fmean(t["percent_return"] for t in closed), 2) if n else 0.0,
            "total_pnl": total_pnl,                       # GROSS, quote-to-quote
            "net_pnl": net_pnl,                           # NET of commission + slippage
            "total_commission": round(total_commission, 2),
            "total_slippage": round(total_slippage, 2),
            "total_trading_costs": total_costs,
            "final_equity": round(final_equity, 2),       # NET (cash reflects costs)
            "cagr_pct": round(cagr, 2),
            "max_positions": max_positions,
            "window": [all_dates[0], all_dates[-1]] if all_dates else [],
        },
        "exit_reason_counts": reasons,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def _reason_bucket(reason: str | None) -> str:
    r = reason or ""
    if "Phase 1" in r:
        return "prove_it_phase1"
    if "Phase 2" in r:
        return "prove_it_phase2"
    if "Trailing stop" in r:
        return "trailing_ladder"
    if "Hard stop" in r:
        return "hard_stop"
    if "scale-out" in r:
        return "scale_out"
    return "other"


# ── CLI ───────────────────────────────────────────────────────────────────────────
def _print_report(res: dict) -> None:
    s = res["summary"]
    print("=" * 68)
    print("STRATEGY BACKTEST — exits driven by the LIVE exit_core / exit_rules")
    print("Daily bars (offline benchmark_data). RULE parity, not fill-price fidelity.")
    print("=" * 68)
    print(f"  window          {s['window'][0]} .. {s['window'][1]}" if s["window"] else "  (no data)")
    print(f"  slots           {s['max_positions']}")
    print(f"  closed trades   {s['closed_trades']}")
    print(f"  win rate        {s['win_rate']}%")
    print(f"  avg return      {s['avg_return_pct']}%")
    print(f"  gross P&L       ${s['total_pnl']:,.2f}  (quote-to-quote, cost-free)")
    print(f"  commissions     -${s.get('total_commission', 0.0):,.2f}")
    print(f"  slippage        -${s.get('total_slippage', 0.0):,.2f}")
    print(f"  trading costs   -${s.get('total_trading_costs', 0.0):,.2f}")
    print(f"  net P&L         ${s.get('net_pnl', s['total_pnl']):,.2f}  (after costs)")
    print(f"  final equity    ${s['final_equity']:,.2f}  (NET — cash reflects costs)")
    print(f"  CAGR            {s['cagr_pct']}%")
    print("-" * 68)
    print("  exit-reason counts (proves the LIVE rules fired, not the retired ones):")
    for k, v in sorted(res["exit_reason_counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:22s} {v}")
    print("=" * 68)
    print("  NOTE: absolute P&L is approximate — daily bars cannot model the 0.6%")
    print("  arm-trail bounce or 15-minute timing. Trust RELATIVE comparisons; a")
    print("  precise +EV verdict needs 5-minute bars (register: intraday-fmp-exit-fidelity).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else "")
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2026-08-04")
    ap.add_argument("--universe", default="all",
                    help="'all', or a name matching research/<name>_names.txt (e.g. 'pass')")
    ap.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    ap.add_argument("--slots", type=int, default=MAX_POSITIONS)
    ap.add_argument("--commission-per-share", type=float, default=None,
                    help="override commission per share (default from BACKTEST_COMMISSION_PER_SHARE / 0.0035)")
    ap.add_argument("--commission-min", type=float, default=None,
                    help="override per-order commission floor (default from BACKTEST_COMMISSION_MIN / 0.35)")
    ap.add_argument("--slippage-bps", type=float, default=None,
                    help="override slippage in basis points per fill (default from BACKTEST_SLIPPAGE_BPS / 5.0)")
    ap.add_argument("--no-costs", action="store_true",
                    help="disable commission + slippage (gross == net; for parity with pre-cost runs)")
    ap.add_argument("--json", default=None, help="write full result to this path")
    args = ap.parse_args(argv)

    if args.no_costs:
        costs = CostModel(commission_per_share=0.0, commission_min=0.0, slippage_bps=0.0)
    else:
        costs = build_cost_model()
        if args.commission_per_share is not None:
            costs = replace(costs, commission_per_share=args.commission_per_share)
        if args.commission_min is not None:
            costs = replace(costs, commission_min=args.commission_min)
        if args.slippage_bps is not None:
            costs = replace(costs, slippage_bps=args.slippage_bps)

    tickers = _universe(args.universe)
    res = simulate(tickers, args.start, args.end,
                   initial_capital=args.capital, max_positions=args.slots, costs=costs)
    _print_report(res)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2)
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
