"""
Does preferring high-ATR candidates help or hurt, under the bot's ACTUAL exits?

WHY THIS EXISTS
---------------
`ai_evaluator.py` asks the model for "Probability the stock hits +25% WITHIN 2-6
WEEKS before -7% stop loss", and ranks on `EstDaysTo25%`, which
`technical_screener.py` computes as:

    est_days_to_target = int(round(25.0 / atr_pct))

That is a monotonic rescaling of ATR, so the rubric's velocity ladder ("ATR >=
1.7%/day: boost +10-15 pts") is simply a preference for volatile names. The
justification is that a volatile stock covers 25% sooner.

The bot does not hold for 25%. `TRAIL_PROFIT_TIERS` arms at **+5%** and then
trails **1.5%** — a fixed 1.5%, which does not scale with volatility. Meanwhile
the entry stop is `clamp(2.5 x ATR, 10%, 12%)`, which *caps out* at ATR 4.8%.
So room-in-ATR-units SHRINKS as ATR rises:

    ATR 2.0% -> stop 10.0% -> 5.0 ATR of room
    ATR 4.0% -> stop 10.0% -> 2.5 ATR of room
    ATR 6.0% -> stop 12.0% -> 2.0 ATR of room
    ATR 7.2% -> stop 12.0% -> 1.7 ATR of room

The prompt therefore selects for names that are structurally under-protected by
the exit ladder, in exchange for upside the ladder never collects.

WHAT THIS MEASURES
------------------
The AI rating does not act in isolation — it RANKS candidates for scarce slots.
Preferring a high-ATR name displaces a lower-ATR one. So the question is only
meaningful at portfolio level, which is what `port_sim.simulate()` models: it
takes candidates best-first by score whenever slots are contended.

This script varies ONLY the ranking score, holding the exit ladder, slot count,
cooling-off and universe fixed, and reports portfolio CAGR. Every configuration
sees exactly the same signals; they differ solely in which ones get the slots.

Run:
    python3 research/atr_rank_bt.py            # headline comparison
    python3 research/atr_rank_bt.py --grid     # full sweep over ATR weight
"""
import collections
import datetime
import statistics
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from bardata import daily as _daily
from breakout_bt import indicators, find_breakouts
from port_sim import simulate
from boot_fixed import paired_block_bootstrap, single_ci, cagr_from

SLOTS = 4

# ── The shipped exit ladder (execution_agent.py) ─────────────────────────────
# profit lock arms at +5% and tightens to a 1.5% trail; the time lever is off;
# plateau exit at 10 sessions without a new high; EMA-21 exit from day 7.
SHIPPED_EXITS = dict(
    profit_tiers=[(5.0, 0.015)],
    time_tiers=[],
    power_hold=True,
    minimiser=None,
    ema=True,
    stale=10,
    cool=7,
    slots=SLOTS,
)

# Entry stop: max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT, 2.5 x ATR))
STOP_FLOOR, STOP_CAP, ATR_MULT = 0.10, 0.12, 2.5


def entry_stop_for(atr_pct: float) -> float:
    if not atr_pct or atr_pct <= 0:
        return STOP_FLOOR
    return round(max(STOP_FLOOR, min(STOP_CAP, ATR_MULT * atr_pct / 100.0)), 4)


def atr_pct_at(d, i, window=14):
    """
    ATR% exactly as technical_screener.py computes it: a simple 14-period mean of
    True Range divided by the close. Not Wilder's smoothing — the point is to
    reproduce the number the live screener puts in front of the model, not to
    compute a better one.
    """
    if i < window:
        return None
    trs = []
    for k in range(i - window + 1, i + 1):
        prev_close = d[k - 1]["close"]
        trs.append(max(
            d[k]["high"] - d[k]["low"],
            abs(d[k]["high"] - prev_close),
            abs(d[k]["low"] - prev_close),
        ))
    close = d[i]["close"]
    if not close:
        return None
    return round(sum(trs) / window / close * 100.0, 2)


def build_with_atr(universe):
    """
    Same signals as port_sim.build(), plus entry ATR% carried on each candidate.

    The base quality score is unchanged from port_sim so that the ONLY thing
    varying between configurations below is the ATR term layered on top.
    """
    sig = collections.defaultdict(list)
    bars, emas, dix, atrs = {}, {}, {}, {}
    for sym in universe:
        d = _daily(sym)
        if not d or len(d) < 300:
            continue
        d = sorted(d, key=lambda r: r["date"])
        sma, avgv, rh, ema = indicators(d)
        bars[sym], emas[sym] = d, ema
        dix[sym] = {b["date"][:10]: k for k, b in enumerate(d)}
        for i in find_breakouts(d):
            if i + 1 >= len(d):
                continue
            a = atr_pct_at(d, i)
            if a is None or a <= 0:
                continue
            c, v = d[i]["close"], d[i]["volume"]
            vol_r = min(3.0, v / avgv[i]) / 3.0 if avgv[i] else 0
            prox = min(1.0, c / rh[i]) if rh[i] else 0
            j = max(0, i - 60)
            rs = (c / d[j]["close"] - 1) if d[j]["close"] else 0
            base = 0.40 * vol_r + 0.35 * prox + 0.25 * max(0.0, min(1.0, rs))
            key = d[i + 1]["date"][:10]
            sig[key].append((sym, i + 1, base, a))
            atrs[(sym, i + 1)] = a
    return sig, bars, emas, dix, atrs


# ── Ranking policies ─────────────────────────────────────────────────────────
# Each returns the score used to order contended candidates. `base` is the
# existing quality proxy in [0,1]; `atr` is entry ATR%.

def rank_neutral(base, atr):
    """No volatility preference at all — the control."""
    return base


def rank_atr_boost(weight):
    """
    Reproduces the live prompt's velocity ladder. The rubric awards up to +15 on
    a 100-point rating for ATR >= 1.7%/day, tapering to a 15-point penalty and a
    hard cap at ATR < 0.4%/day. Expressed on the 0-1 base-score scale.
    """
    def f(base, atr):
        if atr >= 1.70:
            bump = 0.15
        elif atr >= 0.83:
            bump = 0.0
        elif atr >= 0.42:
            bump = -0.15
        else:
            bump = -0.40
        # Within the "ideal" band the live ladder keeps rewarding speed, because
        # est_days = 25/atr keeps falling. Model that continued preference.
        if atr >= 1.70:
            bump += min(0.15, (atr - 1.70) * 0.03)
        return base + weight * bump
    return f


def rank_atr_penalty(weight):
    """Strictly prefer calmer names — the mirror image of the live ladder."""
    def f(base, atr):
        return base - weight * min(0.30, max(0.0, (atr - 1.70) * 0.03))
    return f


def rank_atr_band(lo, hi, weight):
    """
    Prefer a band. Enough daily range to clear the +5% arming threshold in a few
    sessions, but not so much that the fixed 1.5% locked trail is inside one
    day's noise.
    """
    def f(base, atr):
        if lo <= atr <= hi:
            return base + weight * 0.20
        dist = (lo - atr) if atr < lo else (atr - hi)
        return base + weight * (0.20 - min(0.40, dist * 0.08))
    return f


def apply_ranking(sig, policy):
    """Re-score every candidate; shape stays (sym, entry_idx, score)."""
    out = collections.defaultdict(list)
    for day, cands in sig.items():
        for sym, e, base, atr in cands:
            out[day].append((sym, e, policy(base, atr)))
    return out


def simulate_atr_stops(cfg, sig4, bars, emas, dix, alldates, atrs):
    """
    port_sim.simulate() with the per-position ATR-scaled entry stop.

    port_sim applies one flat `base_stop` to every position. The live bot does
    not: the stop is clamp(2.5 x ATR, 10%, 12%), so a volatile name gets a wider
    stop in percentage terms while getting a NARROWER one in ATR units. Since
    that interaction is the entire mechanism under test, it has to be modelled —
    a flat stop would hide the effect being measured.
    """
    import port_sim as _ps

    original = _ps.simulate.__globals__.get("_atr_lookup")
    _ps.simulate.__globals__["_atr_lookup"] = atrs
    try:
        return _simulate_patched(cfg, sig4, bars, emas, dix, alldates, atrs)
    finally:
        if original is None:
            _ps.simulate.__globals__.pop("_atr_lookup", None)
        else:
            _ps.simulate.__globals__["_atr_lookup"] = original


def _simulate_patched(cfg, sig, bars, emas, dix, alldates, atrs):
    """A copy of port_sim.simulate with a per-position entry stop."""
    from breakout_bt import dyn_trail, PH_GAIN, PH_TRIG, PH_DUR, EMA_BUF

    open_pos, closed = [], []
    blocked = {}
    slots = cfg.get("slots", SLOTS)
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
                closed.append(((lvl / p["entry"] - 1) * 100, held, "trail", di, p["atr"]))
                blocked[p["sym"]] = di + cfg.get("cool", 0)
                continue
            c = bar["close"]
            nt = dyn_trail(cfg, (c / p["entry"] - 1) * 100, cal, p["trail"])
            if nt:
                p["trail"] = nt
            if cfg.get("minimiser") and held >= 2 and not p["ph"]:
                p["rh"] = max(p.get("rh", 0.0), c)
                if p["rh"] >= p["entry"] * 0.995 and c <= p["rh"] * (1 - cfg["minimiser"]):
                    closed.append(((c / p["entry"] - 1) * 100, held, "minimiser", di, p["atr"]))
                    blocked[p["sym"]] = di + cfg.get("cool", 0)
                    continue
            st = cfg.get("stale")
            if st and not p["ph"] and held >= 7 and (j - p["last_peak"]) >= st:
                closed.append(((c / p["entry"] - 1) * 100, held, "stale", di, p["atr"]))
                blocked[p["sym"]] = di + cfg.get("cool", 0)
                continue
            if (cfg.get("ema", True) and held >= cfg.get("ema_day", 7) and not p["ph"]
                    and ema[j] and c < ema[j] * (1 - cfg.get("ema_buf", EMA_BUF))):
                closed.append(((c / p["entry"] - 1) * 100, held, "ema", di, p["atr"]))
                blocked[p["sym"]] = di + cfg.get("cool", 0)
                continue
            still.append(p)
        open_pos = still

        for sym, e, _sc in sorted(sig.get(day, []), key=lambda t: -t[2]):
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
            a = atrs.get((sym, e), 0.0)
            open_pos.append(dict(sym=sym, e=e, entry=d[e]["open"], peak=d[e]["open"],
                                 last_peak=e, trail=entry_stop_for(a), pg=0.0, ph=False,
                                 rh=0.0, atr=a,
                                 d0=datetime.date.fromisoformat(day)))
    return closed


def describe(res, label, years, ndays, baseline=None):
    pcts = [r[0] for r in res]
    if not pcts:
        print(f"{label:34}{'no trades':>60}")
        return None
    wins = [p for p in pcts if p > 0]
    cagr = cagr_from(pcts, years, SLOTS)
    atr_used = statistics.mean([r[4] for r in res]) if len(res[0]) > 4 else 0
    line = (f"{label:34}{len(pcts):>6}{statistics.mean(pcts):>+8.2f}"
            f"{100*len(wins)/len(pcts):>6.0f}%{atr_used:>8.2f}"
            f"{statistics.mean([r[1] for r in res]):>7.0f}{cagr:>+9.1f}%")
    if baseline is not None:
        m, lo, hi, p = paired_block_bootstrap(res, baseline, ndays, years, SLOTS)
        line += f"{m:>+9.1f}{f'[{lo:+.1f},{hi:+.1f}]':>18}{p:>7.0f}%"
    print(line)
    return res


def main():
    grid = "--grid" in sys.argv
    universe = [x.strip() for x in open("research/broad_names.txt") if x.strip()]
    print(f"Building signals for {len(universe)} symbols...")
    sig, bars, emas, dix, atrs = build_with_atr(universe)
    alldates = sorted({dt for s in bars for dt in dix[s]})
    ndays = len(alldates)
    years = (datetime.date.fromisoformat(alldates[-1])
             - datetime.date.fromisoformat(alldates[0])).days / 365.25
    total_sig = sum(len(v) for v in sig.values())
    all_atr = [a for (_, _, _, a) in (c for v in sig.values() for c in v)]
    print(f"universe={len(bars)}  days={ndays}  years={years:.2f}  slots={SLOTS}")
    print(f"breakout signals={total_sig}  median entry ATR={statistics.median(all_atr):.2f}%  "
          f"p10={sorted(all_atr)[len(all_atr)//10]:.2f}%  p90={sorted(all_atr)[9*len(all_atr)//10]:.2f}%\n")

    hdr = (f"{'ranking policy':34}{'n':>6}{'exp%':>8}{'win':>7}{'ATR':>8}"
           f"{'hold':>7}{'CAGR':>10}{'vs base':>9}{'90% CI':>18}{'P(>)':>7}")

    print("=" * len(hdr))
    print("RANKING POLICY vs PORTFOLIO OUTCOME   (exit ladder held fixed at shipped)")
    print("=" * len(hdr))
    print(hdr)

    base_sig = apply_ranking(sig, rank_neutral)
    base_res = _simulate_patched(SHIPPED_EXITS, base_sig, bars, emas, dix, alldates, atrs)
    describe(base_res, "neutral (no ATR preference)", years, ndays)

    configs = [
        ("LIVE PROMPT: prefer high ATR", rank_atr_boost(1.0)),
        ("  (half strength)", rank_atr_boost(0.5)),
        ("  (double strength)", rank_atr_boost(2.0)),
        ("prefer low ATR", rank_atr_penalty(1.0)),
        ("  (double strength)", rank_atr_penalty(2.0)),
        ("prefer band 1.5-3.0%", rank_atr_band(1.5, 3.0, 1.0)),
        ("prefer band 2.0-4.0%", rank_atr_band(2.0, 4.0, 1.0)),
        ("prefer band 1.0-2.5%", rank_atr_band(1.0, 2.5, 1.0)),
    ]
    for label, pol in configs:
        res = _simulate_patched(SHIPPED_EXITS, apply_ranking(sig, pol),
                                bars, emas, dix, alldates, atrs)
        describe(res, label, years, ndays, baseline=base_res)

    # ── Unconditional view: outcome by ATR decile, ignoring ranking ──────────
    print("\n" + "=" * 78)
    print("OUTCOME BY ENTRY ATR DECILE   (neutral ranking, so selection is unbiased)")
    print("=" * 78)
    by_atr = sorted(base_res, key=lambda r: r[4])
    k = max(1, len(by_atr) // 10)
    print(f"{'decile':>8}{'ATR range':>16}{'n':>6}{'exp%':>9}{'win':>7}{'avg hold':>10}")
    for dnum in range(10):
        chunk = by_atr[dnum * k:(dnum + 1) * k] if dnum < 9 else by_atr[9 * k:]
        if not chunk:
            continue
        pcts = [r[0] for r in chunk]
        lo_a, hi_a = chunk[0][4], chunk[-1][4]
        print(f"{dnum+1:>8}{f'{lo_a:.2f}-{hi_a:.2f}%':>16}{len(chunk):>6}"
              f"{statistics.mean(pcts):>+9.2f}"
              f"{100*sum(1 for p in pcts if p>0)/len(pcts):>6.0f}%"
              f"{statistics.mean([r[1] for r in chunk]):>10.1f}")

    if grid:
        print("\n" + "=" * 78)
        print("GRID: ATR band edges  (weight 1.0, shipped exits)")
        print("=" * 78)
        print(f"{'band':>16}{'n':>7}{'exp%':>9}{'win':>7}{'CAGR':>10}{'vs base':>10}{'P(>)':>7}")
        best = []
        for lo in (0.8, 1.0, 1.2, 1.5, 2.0, 2.5):
            for hi in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
                if hi <= lo:
                    continue
                res = _simulate_patched(SHIPPED_EXITS,
                                        apply_ranking(sig, rank_atr_band(lo, hi, 1.0)),
                                        bars, emas, dix, alldates, atrs)
                pcts = [r[0] for r in res]
                c = cagr_from(pcts, years, SLOTS)
                m, blo, bhi, p = paired_block_bootstrap(res, base_res, ndays, years, SLOTS)
                print(f"{f'{lo}-{hi}%':>16}{len(pcts):>7}{statistics.mean(pcts):>+9.2f}"
                      f"{100*sum(1 for x in pcts if x>0)/len(pcts):>6.0f}%{c:>+10.1f}{m:>+10.1f}{p:>7.0f}%")
                best.append((m, p, lo, hi))
        best.sort(reverse=True)
        print(f"\nbest band by median CAGR delta: {best[0][2]}-{best[0][3]}%  "
              f"{best[0][0]:+.1f}pp  P(better)={best[0][1]:.0f}%")


if __name__ == "__main__":
    main()
