"""Entry-selection backtest.

ADR 2026-08-04 finding #4: within a given universe, the breakout trigger has no
measurable edge over random entry. All demonstrable edge comes from WHICH stocks
are in the universe. That makes entry selection — not exit tuning — the highest-
value lever, and it is the direct fix for "bleeding by a thousand cuts": fewer,
better entries beat more, marginal ones.

Candidate filters (named in that ADR's follow-up #2):
  1. pivot clearance  — require the close to CLEAR the 52w high rather than sit
                        within 5% of it (the current PIVOT_PROXIMITY=0.95 admits
                        entries up to 5% BELOW the pivot, i.e. not breakouts)
  2. base tightness   — VCP-style: require the prior base to be quiet, measured
                        as (max-min)/mean of closes over the base window
  3. RS confirmation  — require the stock to be outperforming the market over 12
                        weeks, not merely rising
  4. volume conviction— raise the surge threshold

Each is tested alone and stacked, in BOTH universes.
"""
import datetime, statistics

from bardata import daily as _daily
from breakout_bt import indicators, SMA_WINDOW, VOL_WINDOW, ROLL_HIGH, MIN_HIST
from thesis_bt import simulate, stats, BASE, atr_pct_series
from boot_fixed import paired_block_bootstrap

BENCH = "SPY"


def bench_series():
    d = _daily(BENCH)
    if not d:
        return {}
    d = sorted(d, key=lambda r: r["date"])
    return {b["date"][:10]: b["close"] for b in d}


BENCH_CLOSE = bench_series()


def find_breakouts_f(d, cfg):
    """Breakout signals under a configurable entry filter."""
    sma, avgv, rh, _ = indicators(d)
    closes = [r["close"] for r in d]
    out, last = [], -10 ** 9
    cool = cfg.get("sig_cool", 20)
    prox = cfg.get("pivot_prox", 0.95)
    vsurge = cfg.get("vol_surge", 1.50)
    tight_w = cfg.get("tight_window")
    tight_max = cfg.get("tight_max")
    rs_min = cfg.get("rs_min")

    for i in range(len(d)):
        if sma[i] is None or avgv[i] is None or rh[i] is None or not avgv[i]:
            continue
        if i - last < cool or i + 1 >= len(d):
            continue
        c, v = d[i]["close"], d[i]["volume"]
        if not (c > sma[i] and v / avgv[i] >= vsurge and c >= rh[i] * prox):
            continue

        # ── base tightness (VCP proxy) ──────────────────────────────────────
        if tight_w and tight_max is not None:
            j0 = i - tight_w
            if j0 < 0:
                continue
            base = closes[j0:i]
            m = sum(base) / len(base)
            if not m:
                continue
            if (max(base) - min(base)) / m > tight_max:
                continue

        # ── RS vs benchmark over 12 weeks ───────────────────────────────────
        if rs_min is not None:
            k = i - 60
            if k < 0:
                continue
            if not closes[k]:
                continue
            stock_ret = c / closes[k] - 1.0
            dt_now, dt_then = d[i]["date"][:10], d[k]["date"][:10]
            b_now, b_then = BENCH_CLOSE.get(dt_now), BENCH_CLOSE.get(dt_then)
            if not b_now or not b_then:
                continue
            bench_ret = b_now / b_then - 1.0
            if (stock_ret - bench_ret) < rs_min:
                continue

        out.append(i)
        last = i
    return out


def build_f(universe, cfg):
    import collections
    sig = collections.defaultdict(list)
    bars, emas, dix = {}, {}, {}
    for sym in universe:
        d = _daily(sym)
        if not d or len(d) < 300:
            continue
        d = sorted(d, key=lambda r: r["date"])
        sma, avgv, rh, ema = indicators(d)
        bars[sym], emas[sym] = d, ema
        dix[sym] = {b["date"][:10]: k for k, b in enumerate(d)}
        for i in find_breakouts_f(d, cfg):
            if i + 1 >= len(d):
                continue
            c, v = d[i]["close"], d[i]["volume"]
            vol_r = min(3.0, v / avgv[i]) / 3.0 if avgv[i] else 0
            prox = min(1.0, c / rh[i]) if rh[i] else 0
            j = max(0, i - 60)
            rs = (c / d[j]["close"] - 1) if d[j]["close"] else 0
            score = 0.40 * vol_r + 0.35 * prox + 0.25 * max(0.0, min(1.0, rs))
            sig[d[i + 1]["date"][:10]].append((sym, i + 1, score))
    return sig, bars, emas, dix


T = dict(BASE, thesis_atr=1.00, thesis_day=2)

FILTERS = {
    "current (prox .95, vol 1.5)": {},
    "pivot clear .98":             dict(pivot_prox=0.98),
    "pivot clear 1.00":            dict(pivot_prox=1.00),
    "vol surge 2.0x":              dict(vol_surge=2.0),
    "tight base (25d < 15%)":      dict(tight_window=25, tight_max=0.15),
    "tight base (25d < 12%)":      dict(tight_window=25, tight_max=0.12),
    "RS > bench +5%":              dict(rs_min=0.05),
    "RS > bench +10%":             dict(rs_min=0.10),
    "STACK prox.98+RS5":           dict(pivot_prox=0.98, rs_min=0.05),
    "STACK prox.98+RS5+tight15":   dict(pivot_prox=0.98, rs_min=0.05,
                                        tight_window=25, tight_max=0.15),
    "STACK prox1.0+RS5+vol2":      dict(pivot_prox=1.00, rs_min=0.05, vol_surge=2.0),
}

if __name__ == "__main__":
    for fname, label in [("broad_names.txt", "BROAD"), ("pass_names.txt", "PASS")]:
        uni = [x.strip() for x in open(fname) if x.strip()]
        print(f"\n===== {label} — entry filters (thesis stop ON) =====")
        print(f"{'filter':30}{'n':>5}{'exp%':>8}{'win%':>7}{'payoff':>8}{'big20':>7}{'CAGR':>8}")
        ref = None
        for name, fcfg in FILTERS.items():
            sig, bars, emas, dix = build_f(uni, fcfg)
            if not bars:
                continue
            atrs = {s: atr_pct_series(bars[s]) for s in bars}
            alld = sorted({dt for s in bars for dt in dix[s]})
            years = (datetime.date.fromisoformat(alld[-1])
                     - datetime.date.fromisoformat(alld[0])).days / 365.25
            r = simulate(T, sig, bars, emas, dix, alld, atrs)
            s = stats(r, years)
            pcts = [x[0] for x in r]
            win = 100 * sum(1 for x in pcts if x > 0) / len(pcts) if pcts else 0
            print(f"{name:30}{s['n']:>5}{s['exp']:>+8.2f}{win:>6.0f}%"
                  f"{s['payoff']:>8.2f}{s['big20']:>6.1f}%{s['cagr']:>+8.1f}")
