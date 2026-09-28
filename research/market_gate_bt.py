"""market_gate_bt.py — backtest the CAN SLIM 'M' (market-direction) gate.

Replays the exact gate logic in market_regime.is_market_bullish() over ~19 years
of index history and scores each (tickers, buffer, slope_days) configuration on
BOTH axes the operator cares about:

  * ACTIVITY   — share of sessions the gate permits buying.
  * PROFIT      — a gated-SPY equity curve: long SPY on every session the gate was
                 bullish AS OF THE PRIOR CLOSE, flat otherwise (no shorting). This
                 is the honest, strategy-agnostic proxy for "the bot is allowed to
                 hold" — see the caveat printed at the end and
                 decisions/2026-08-22_market-direction-gate-spy-qqq.md.
  * DRAWDOWN    — max peak-to-trough of that gated equity curve.
  * INSURANCE   — share of the worst-5% forward-20-session windows sat out.

The point is to test the user's hypothesis directly: does the MOST ACTIVE
configuration also make the most money? Run:

    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/market_gate_bt.py --insecure

Data is FMP daily EOD for SPY and QQQ, cached under /tmp so re-runs are instant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import ssl
from datetime import date

SMA_WINDOW = 200
FWD = 20  # forward-return horizon in sessions, matches the ADR


def _fetch(symbol: str, insecure: bool) -> list[dict]:
    cache = f"/tmp/mgate_{symbol}.json"
    if os.path.exists(cache):
        return json.load(open(cache))
    key = os.environ.get("FMP_API_KEY")
    if not key:
        sys.exit("FMP_API_KEY not set — run: set -a && . ~/.config/ai-trading-bot/secrets.env && set +a")
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={symbol}&from=2007-01-01&to={date.today()}&apikey={key}")
    ctx = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(url, context=ctx, timeout=30) as r:
        data = json.load(r)
    json.dump(data, open(cache, "w"))
    return data


def _closes(rows: list[dict]) -> dict[str, float]:
    """date -> close, ascending by date."""
    out = {}
    for d in rows:
        try:
            c = float(d["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if c > 0 and d.get("date"):
            out[str(d["date"])[:10]] = c
    return dict(sorted(out.items()))


def _sma(vals: list[float], w: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= w:
            s -= vals[i - w]
        if i >= w - 1:
            out[i] = s / w
    return out


def gate_series(dates, closes, sma, buffer_pct, slope_days):
    """Per-index (above_buffer, slope_ok) booleans aligned to `dates`."""
    above = [None] * len(dates)
    slope_ok = [None] * len(dates)
    for i in range(len(dates)):
        s_now = sma[i]
        if s_now is None:
            continue
        above[i] = closes[i] > s_now * (1 + buffer_pct)
        j = i - slope_days
        s_then = sma[j] if j >= 0 else None
        if s_then and s_then > 0:
            slope_ok[i] = (s_now - s_then) / s_then >= 0
    return above, slope_ok


def verdicts(dates, per_ticker, buffer_pct, slope_days):
    """Replicate is_market_bullish: ALL above-buffer AND ANY slope non-falling."""
    series = {}
    for tk, (dts, cls, sma) in per_ticker.items():
        series[tk] = gate_series(dts, cls, sma, buffer_pct, slope_days)
    out = [None] * len(dates)
    for i in range(len(dates)):
        aboves, slopes = [], []
        ok = True
        for tk in per_ticker:
            a, sl = series[tk][0][i], series[tk][1][i]
            if a is None or sl is None:
                ok = False
                break
            aboves.append(a)
            slopes.append(sl)
        if not ok:
            continue
        out[i] = all(aboves) and any(slopes)
    return out


def score(dates, spy_closes, verdict):
    """Return a dict of activity / profit / drawdown / insurance metrics.

    Equity model: hold SPY on session t when verdict[t-1] is True (gate was
    bullish as of the prior close — the bot acts at the next open), else flat.
    """
    n_eval = 0
    n_bull = 0
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    bull_fwd, bear_fwd = [], []
    all_fwd = []
    invested_days = 0
    for t in range(len(dates)):
        v = verdict[t]
        if v is None:
            continue
        n_eval += 1
        if v:
            n_bull += 1
        # forward-20 return from t (for edge / insurance)
        if t + FWD < len(dates):
            f = spy_closes[t + FWD] / spy_closes[t] - 1.0
            all_fwd.append((f, v))
            (bull_fwd if v else bear_fwd).append(f)
        # equity: today's return applied if gate was bullish yesterday
        if t > 0 and verdict[t - 1]:
            r = spy_closes[t] / spy_closes[t - 1] - 1.0
            eq *= (1 + r)
            invested_days += 1
            peak = max(peak, eq)
            maxdd = min(maxdd, eq / peak - 1.0)

    # buy & hold over the same evaluable span
    first = next(t for t in range(len(dates)) if verdict[t] is not None)
    bh = spy_closes[-1] / spy_closes[first] - 1.0
    # worst-5% forward windows avoided
    all_fwd.sort(key=lambda x: x[0])
    k = max(1, int(len(all_fwd) * 0.05))
    worst = all_fwd[:k]
    avoided = sum(1 for f, v in worst if not v) / len(worst) if worst else 0.0

    mean_bull = sum(bull_fwd) / len(bull_fwd) if bull_fwd else 0.0
    mean_bear = sum(bear_fwd) / len(bear_fwd) if bear_fwd else 0.0
    yrs = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[first])).days / 365.25
    cagr = (eq ** (1 / yrs) - 1) if yrs > 0 and eq > 0 else 0.0
    return {
        "activity": n_bull / n_eval if n_eval else 0.0,
        "total_return": eq - 1.0,
        "cagr": cagr,
        "maxdd": maxdd,
        "buyhold": bh,
        "edge_pct": (mean_bull - mean_bear) * 100,
        "mean_bull_pct": mean_bull * 100,
        "worst5_avoided": avoided,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true", help="skip TLS verify (local corp cert)")
    args = ap.parse_args()

    spy = _closes(_fetch("SPY", args.insecure))
    qqq = _closes(_fetch("QQQ", args.insecure))

    # common calendar
    common = sorted(set(spy) & set(qqq))
    dates = common
    spy_c = [spy[d] for d in dates]
    qqq_c = [qqq[d] for d in dates]
    per = {
        "SPY": (dates, spy_c, _sma(spy_c, SMA_WINDOW)),
        "QQQ": (dates, qqq_c, _sma(qqq_c, SMA_WINDOW)),
    }
    only_spy = {"SPY": per["SPY"]}

    print(f"Data: {dates[0]} → {dates[-1]}  ({len(dates)} common sessions)\n")

    buffers = [0.0, 0.005, 0.01, 0.015, 0.02, 0.03]
    slopes = [5, 10, 20, 30, 50]

    def run(label, tickers, buffer_pct, slope_days):
        v = verdicts(dates, tickers, buffer_pct, slope_days)
        s = score(dates, spy_c, v)
        return s

    # Baselines
    print("=== BASELINES (whole sample) ===")
    bh = score(dates, spy_c, [True] * len(dates))
    print(f"{'Buy & hold SPY (always in)':<34} "
          f"act=100.0%  ret={bh['total_return']*100:7.0f}%  cagr={bh['cagr']*100:5.1f}%  "
          f"maxDD={bh['maxdd']*100:6.1f}%")
    shipped = run("shipped", per, 0.01, 20)
    print(f"{'PREV SHIPPED SPY+QQQ b=1.0% slope20':<34} "
          f"act={shipped['activity']*100:5.1f}%  ret={shipped['total_return']*100:7.0f}%  "
          f"cagr={shipped['cagr']*100:5.1f}%  maxDD={shipped['maxdd']*100:6.1f}%  "
          f"ins={shipped['worst5_avoided']*100:4.0f}%")
    live = run("live", only_spy, 0.005, 20)
    print(f"{'LIVE  SPY b=0.5% slope=20 (shipped)':<34} "
          f"act={live['activity']*100:5.1f}%  ret={live['total_return']*100:7.0f}%  "
          f"cagr={live['cagr']*100:5.1f}%  maxDD={live['maxdd']*100:6.1f}%  "
          f"ins={live['worst5_avoided']*100:4.0f}%")

    print("\n=== SPY-ONLY GRID: buffer x slope_days ===")
    print(f"{'buffer':>7} {'slope':>6} {'active':>7} {'totRet':>8} {'cagr':>6} "
          f"{'maxDD':>7} {'edge%':>6} {'meanBull%':>9} {'ins%':>5}")
    rows = []
    for b in buffers:
        for sl in slopes:
            s = run("g", only_spy, b, sl)
            rows.append((b, sl, s))
            print(f"{b*100:6.2f}% {sl:6d} {s['activity']*100:6.1f}% "
                  f"{s['total_return']*100:7.0f}% {s['cagr']*100:5.1f}% "
                  f"{s['maxdd']*100:6.1f}% {s['edge_pct']:6.2f} "
                  f"{s['mean_bull_pct']:8.3f} {s['worst5_avoided']*100:4.0f}%")

    # Rank by total return and by CAGR/maxDD (return per unit drawdown)
    print("\n=== TOP 5 SPY-only by TOTAL RETURN ===")
    for b, sl, s in sorted(rows, key=lambda r: r[2]['total_return'], reverse=True)[:5]:
        print(f"  b={b*100:.2f}% slope={sl:>2}  ret={s['total_return']*100:5.0f}%  "
              f"cagr={s['cagr']*100:.1f}%  maxDD={s['maxdd']*100:.1f}%  act={s['activity']*100:.1f}%")

    print("\n=== TOP 5 SPY-only by RETURN/DRAWDOWN (cagr / |maxDD|) ===")
    def rd(s):
        return s['cagr'] / abs(s['maxdd']) if s['maxdd'] else 0
    for b, sl, s in sorted(rows, key=lambda r: rd(r[2]), reverse=True)[:5]:
        print(f"  b={b*100:.2f}% slope={sl:>2}  cagr/DD={rd(s):.2f}  "
              f"cagr={s['cagr']*100:.1f}%  maxDD={s['maxdd']*100:.1f}%  act={s['activity']*100:.1f}%")

    # Activity vs profit correlation — the user's core question
    print("\n=== DOES MORE ACTIVITY = MORE PROFIT?  (SPY-only grid) ===")
    acts = [s['activity'] for _, _, s in rows]
    rets = [s['total_return'] for _, _, s in rows]
    n = len(acts)
    ma, mr = sum(acts)/n, sum(rets)/n
    cov = sum((a-ma)*(r-mr) for a, r in zip(acts, rets))/n
    sa = (sum((a-ma)**2 for a in acts)/n) ** 0.5
    sr = (sum((r-mr)**2 for r in rets)/n) ** 0.5
    corr = cov/(sa*sr) if sa and sr else 0
    print(f"  corr(activity, total_return) = {corr:+.3f}  across {n} configs")

    print("\nCAVEAT: the equity curve is SPY-long-when-open, NOT this bot's "
          "breakouts.\nBreakouts fail worse in downtrends than SPY's mean implies, "
          "so the gate's\nreal value to THIS strategy is a lower bound of the "
          "insurance number above.")


if __name__ == "__main__":
    main()
