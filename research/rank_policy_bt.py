"""Counterfactual replay: trigger-RANKING policy A (score-first) vs B (confirmed-first).

THE QUESTION
------------
run_market_open_buys() sorts daily_triggers purely by final_score. On 2026-08-12
that ranked DELL (PRE_BREAKOUT, 82) above URGN (BREAKOUT, 78), so the bot bought a
coil instead of a confirmed breakout. Should confirmed breakouts outrank coils
regardless of score?

WHY A PORTFOLIO SIM AND NOT PER-TRADE EXPECTANCY
-----------------------------------------------
Ranking only has an effect when candidates COMPETE for a scarce slot. With
MAX_POSITIONS slots and more triggers than slots, the ranking key decides which
subset of signals is actually traded. Per-trade expectancy by trigger_type is
necessary but NOT sufficient evidence -- it ignores capital velocity (a coil that
sits flat for 20 days blocks a slot). So we simulate the real slot constraint and
compare portfolio CAGR.

FIDELITY / KNOWN LIMITATIONS  (state these with any result)
-----------------------------------------------------------
1. Live final_score = 0.30*technical + 0.25*liquidity + 0.25*ai + 0.10*sentiment
   + 0.10*rs. Historical `ai` and `sentiment` were never archived (this is exactly
   why trigger_history was created). We hold both at a constant, so they cancel
   out of the WITHIN-DAY ordering -- the ranking comparison is therefore fair --
   but the absolute score floor is a proxy. We report floors-on and floors-off.
2. Daily bars only, entry at next open. Same convention as every other harness here.
3. The screener-passing universe (pass_names.txt) is a TODAY snapshot replayed over
   history, so it carries survivorship / look-ahead bias. Per the ADR rule, a
   result is only actionable if it holds in BOTH universes.
"""
import collections
import datetime
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bardata import daily as _daily
from breakout_bt import dyn_trail, BASE_STOP, PH_GAIN, PH_TRIG, PH_DUR, EMA_BUF
from boot_fixed import paired_block_bootstrap, cagr_from
from scoring import compute_rs_score, compute_liquidity_score
from technical_screener import (
    compute_quality_score,
    compute_pre_breakout_quality_score,
)

# ── production constants (mirrored from technical_screener / execution_agent) ──
SMA_W, VOL_W, ROLL_HIGH, MIN_HIST = 50, 50, 252, 50
VOL_SURGE, PIVOT_PROX = 1.50, 0.95
RS_MIN_GATE = 50
PRE_PROX, PRE_VOL_MAX, PRE_UPTREND_MIN = 0.08, 1.00, 2
MIN_TRIGGER_SCORE, MIN_PRE_BREAKOUT_SCORE = 60, 65
MAX_POSITIONS, COOLING_OFF_DAYS = 5, 7

# AI + sentiment are unavailable historically. Constant => cancels out of the
# within-day ordering, so policy comparison is unaffected by the value chosen.
AI_CONST, SENT_CONST = 70, 60


def _rs_series(spy_bars):
    """Point-in-time SPY 12-week (60 trading day) return, keyed by date."""
    out = {}
    for i, b in enumerate(spy_bars):
        j = i - 60
        out[b["date"][:10]] = ((b["close"] / spy_bars[j]["close"] - 1) * 100
                               if j >= 0 and spy_bars[j]["close"] else 0.0)
    return out


def _indicators(d):
    closes = [r["close"] for r in d]
    vols = [r["volume"] for r in d]
    highs = [r["high"] for r in d]
    n = len(d)
    sma, avgv, rh, ema = [None] * n, [None] * n, [None] * n, [None] * n
    k = 2 / 22
    csum = vsum = 0.0
    for i in range(n):
        csum += closes[i]
        vsum += vols[i]
        if i >= SMA_W:
            csum -= closes[i - SMA_W]
            vsum -= vols[i - SMA_W]
        if i >= SMA_W - 1:
            sma[i] = csum / SMA_W
            avgv[i] = vsum / VOL_W
        if i >= MIN_HIST - 1:
            rh[i] = max(highs[max(0, i - ROLL_HIGH + 1):i + 1])
        ema[i] = closes[i] if i == 0 else closes[i] * k + ema[i - 1] * (1 - k)
    return sma, avgv, rh, ema


def find_triggers(d, spy_rs):
    """Replay BOTH screener detectors bar-by-bar, using production scoring.

    Returns list of (i, trigger_type, final_score_proxy, technical_score).
    """
    sma, avgv, rh, _ = _indicators(d)
    out = []
    for i in range(len(d)):
        if sma[i] is None or avgv[i] is None or rh[i] is None or not avgv[i] or not rh[i]:
            continue
        if i + 1 >= len(d):
            continue
        c, v = d[i]["close"], d[i]["volume"]
        if c <= sma[i]:
            continue

        j = max(0, i - 60)
        stock_12w = (c / d[j]["close"] - 1) * 100 if d[j]["close"] else 0.0
        rs = compute_rs_score(stock_12w, spy_rs.get(d[i]["date"][:10], 0.0))
        if rs < RS_MIN_GATE:
            continue

        liq = compute_liquidity_score(c, int(avgv[i]), "")
        dist_pct = (c / rh[i] - 1) * 100
        ttype = tech = None

        if v / avgv[i] >= VOL_SURGE and c >= rh[i] * PIVOT_PROX:
            ttype = "BREAKOUT"
            tech = compute_quality_score(v / avgv[i], dist_pct, c, sma[i])
        elif -PRE_PROX * 100 <= dist_pct < 0:
            v3 = sum(d[i - k]["volume"] for k in range(min(3, i + 1))) / min(3, i + 1)
            ratio = v3 / avgv[i]
            if ratio >= PRE_VOL_MAX:
                continue
            rising = sum(1 for k in range(min(3, i))
                         if d[i - k]["close"] > d[i - k - 1]["close"])
            if rising < PRE_UPTREND_MIN:
                continue
            ttype = "PRE_BREAKOUT"
            tech = compute_pre_breakout_quality_score(dist_pct, ratio, rising, c, sma[i])

        if ttype is None:
            continue
        final = round(tech * 0.30 + liq * 0.25 + AI_CONST * 0.25
                      + SENT_CONST * 0.10 + rs * 0.10)
        out.append((i, ttype, final, tech))
    return out


def build(universe):
    spy = _daily("SPY")
    spy_rs = _rs_series(sorted(spy, key=lambda r: r["date"]))
    sig = collections.defaultdict(list)
    bars, emas, dix = {}, {}, {}
    for sym in universe:
        d = _daily(sym)
        if not d or len(d) < 300:
            continue
        d = sorted(d, key=lambda r: r["date"])
        _, _, _, ema = _indicators(d)
        bars[sym], emas[sym] = d, ema
        dix[sym] = {b["date"][:10]: k for k, b in enumerate(d)}
        for i, ttype, final, tech in find_triggers(d, spy_rs):
            sig[d[i + 1]["date"][:10]].append((sym, i + 1, final, ttype))
    return sig, bars, emas, dix


def _rank_key(policy):
    if policy == "score_first":                      # current production
        return lambda t: (-t[2],)
    if policy == "confirmed_first":                  # candidate change
        return lambda t: (0 if t[3] == "BREAKOUT" else 1, -t[2])
    if policy == "breakout_only":                    # upper bound reference
        return lambda t: (-t[2],)
    raise ValueError(policy)


def simulate(cfg, sig, bars, emas, dix, alldates, policy):
    open_pos, closed = [], []
    blocked = {}
    slots = cfg.get("slots", MAX_POSITIONS)
    key = _rank_key(policy)
    for di, day in enumerate(alldates):
        still = []
        for p in open_pos:
            d, ema, ix = bars[p["sym"]], emas[p["sym"]], dix[p["sym"]]
            j = ix.get(day)
            if j is None:
                still.append(p)
                continue
            bar = d[j]
            held = j - p["e"]
            cal = (datetime.date.fromisoformat(day) - p["d0"]).days
            if bar["high"] > p["peak"]:
                p["peak"] = bar["high"]
                p["last_peak"] = j
            p["pg"] = max(p["pg"], (p["peak"] / p["entry"] - 1) * 100)
            if cfg["power_hold"]:
                if p["pg"] >= PH_GAIN and cal <= PH_TRIG:
                    p["ph"] = True
                if p["ph"] and cal > PH_DUR:
                    p["ph"] = False
            lvl = p["peak"] * (1 - p["trail"])
            if bar["low"] <= lvl:
                closed.append(((lvl / p["entry"] - 1) * 100, held, "trail", di, p["tt"]))
                blocked[p["sym"]] = di + COOLING_OFF_DAYS
                continue
            c = bar["close"]
            nt = dyn_trail(cfg, (c / p["entry"] - 1) * 100, cal, p["trail"])
            if nt:
                p["trail"] = nt
            st = cfg.get("stale")
            if st and not p["ph"] and held >= 7 and (j - p["last_peak"]) >= st:
                closed.append(((c / p["entry"] - 1) * 100, held, "stale", di, p["tt"]))
                blocked[p["sym"]] = di + COOLING_OFF_DAYS
                continue
            if (cfg.get("ema", True) and held >= 7 and not p["ph"]
                    and ema[j] and c < ema[j] * (1 - EMA_BUF)):
                closed.append(((c / p["entry"] - 1) * 100, held, "ema", di, p["tt"]))
                blocked[p["sym"]] = di + COOLING_OFF_DAYS
                continue
            still.append(p)
        open_pos = still

        src = sig.get(day, [])
        if policy == "breakout_only":
            src = [t for t in src if t[3] == "BREAKOUT"]
        if cfg.get("floors", True):
            src = [t for t in src
                   if t[2] >= (MIN_TRIGGER_SCORE if t[3] == "BREAKOUT"
                               else MIN_PRE_BREAKOUT_SCORE)]
        for sym, e, _sc, tt in sorted(src, key=key):
            if len(open_pos) >= slots:
                break
            if any(p["sym"] == sym for p in open_pos):
                continue
            if blocked.get(sym, -1) > di:
                continue
            d, ix = bars[sym], dix[sym]
            e = ix.get(day, e)
            if e >= len(d) or not d[e]["open"]:
                continue
            open_pos.append(dict(sym=sym, e=e, entry=d[e]["open"], peak=d[e]["open"],
                                 last_peak=e, trail=cfg.get("base_stop", BASE_STOP),
                                 pg=0.0, ph=False,
                                 d0=datetime.date.fromisoformat(day), tt=tt))
    return closed


def report(res, label, years, slots):
    if not res:
        print(f"{label:34}{'no trades':>60}")
        return
    pcts = [r[0] for r in res]
    w = [p for p in pcts if p > 0]
    l = [p for p in pcts if p <= 0]
    aw = statistics.mean(w) if w else 0
    al = statistics.mean(l) if l else 0
    eq = 1.0
    peak = dd = 0.0
    for p in pcts:
        eq *= (1 + p / 100 / slots)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    cagr = (eq ** (1 / years) - 1) * 100
    npb = sum(1 for r in res if r[4] == "PRE_BREAKOUT")
    print(f"{label:34}{len(pcts):>6}{100*npb/len(pcts):>7.0f}%{statistics.mean(pcts):>+8.2f}"
          f"{100*len(w)/len(pcts):>6.0f}%{aw/abs(al) if al else 0:>8.2f}"
          f"{statistics.mean([r[1] for r in res]):>7.0f}{cagr:>+9.1f}%{100*dd:>8.1f}%")


def per_type(res, label):
    by = collections.defaultdict(list)
    for r in res:
        by[r[4]].append(r)
    for tt in ("BREAKOUT", "PRE_BREAKOUT"):
        g = by.get(tt)
        if not g:
            continue
        p = [x[0] for x in g]
        w = [x for x in p if x > 0]
        print(f"    {label} {tt:14}n={len(p):<5} exp={statistics.mean(p):+6.2f}%  "
              f"win={100*len(w)/len(p):3.0f}%  hold={statistics.mean([x[1] for x in g]):3.0f}d")


HDR = (f"{'policy':34}{'n':>6}{'%pre':>8}{'exp%':>8}{'win':>7}"
       f"{'payoff':>8}{'hold':>7}{'CAGR':>10}{'maxDD':>9}")

BASE = dict(profit_tiers=[(50, 0.050), (30, 0.060), (20, 0.065)], time_tiers=[],
            power_hold=True, ema=True, base_stop=0.07, stale=None)


def run_universe(name, path, slots, floors, reps=2000):
    uni = [x.strip() for x in open(path) if x.strip()]
    sig, bars, emas, dix = build(uni)
    alldates = sorted({dt for s in bars for dt in dix[s]})
    years = ((datetime.date.fromisoformat(alldates[-1])
              - datetime.date.fromisoformat(alldates[0])).days / 365.25)
    ntrig = sum(len(v) for v in sig.values())
    npre = sum(1 for v in sig.values() for t in v if t[3] == "PRE_BREAKOUT")
    print(f"\n{'='*102}\nUNIVERSE {name}: {len(bars)} symbols  {len(alldates)} days  "
          f"{years:.2f}y  slots={slots}  floors={'ON' if floors else 'OFF'}")
    print(f"triggers generated: {ntrig}  ({100*npre/ntrig:.0f}% PRE_BREAKOUT)\n{'='*102}")
    cfg = dict(BASE, slots=slots, floors=floors)

    out = {}
    print(HDR)
    for pol, lbl in (("score_first", "A  score-first (PRODUCTION)"),
                     ("confirmed_first", "B  confirmed-first"),
                     ("breakout_only", "C  breakout-only (no coils)")):
        res = simulate(cfg, sig, bars, emas, dix, alldates, pol)
        out[pol] = res
        report(res, lbl, years, slots)

    print("\n  per-trigger-type expectancy (same slot constraint):")
    per_type(out["score_first"], "A")

    print("\n  paired stationary-block bootstrap on CAGR (2000 reps, 60d blocks):")
    nd = len(alldates)
    for pol, lbl in (("confirmed_first", "B - A  (confirmed-first minus production)"),
                     ("breakout_only", "C - A  (breakout-only minus production)")):
        med, lo, hi, pwin = paired_block_bootstrap(
            [(r[0], r[1], r[2], r[3]) for r in out[pol]],
            [(r[0], r[1], r[2], r[3]) for r in out["score_first"]],
            nd, years, slots, reps=reps)
        verdict = "SIGNIFICANT" if (lo > 0 or hi < 0) else "not significant"
        print(f"    {lbl:44}{med:+7.2f}pp  [{lo:+7.2f}, {hi:+7.2f}]  "
              f"P(B>A)={pwin:4.0f}%  {verdict}")
    return out


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for slots in (5, 4):
        for floors in (True, False):
            for name, f in (("BROAD (unselected)", "broad_names.txt"),
                            ("SCREENER-PASSING", "pass_names.txt")):
                run_universe(name, os.path.join(here, f), slots, floors)
