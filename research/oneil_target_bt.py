"""
oneil_target_bt.py — Is O'Neil's 20-25% profit target reachable by THIS bot?

Two separate questions, deliberately measured separately:

  A. BASE RATE (signal-level, exit-agnostic). Of every breakout signal, what
     fraction ever trades +25% above the entry before first trading -8% below
     it? This asks whether the STOCKS can do it, independent of what the bot's
     exits would have done. If this rate is high, the target is realistic and
     the bot's exits are leaving money on the table. If it is low, O'Neil's
     rule does not transfer to this signal set.

  B. COUNTERFACTUAL (portfolio-level). Does an O'Neil-style exit -- hard stop,
     take profit at +20/25%, optionally with the "8-week rule" -- actually beat
     the shipped trailing ladder when run through the same slots and signals?

Question A cannot answer B: a target can be frequently reachable and still be a
worse policy, because waiting for it gives back gains on the names that stall.
Only B is decision-relevant; A explains WHY B comes out the way it does.

Usage:  python3 research/oneil_target_bt.py
"""
import datetime
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atr_rank_bt import (build_with_atr, apply_ranking, rank_neutral,
                         _simulate_patched, SHIPPED_EXITS, SLOTS,
                         entry_stop_for, describe)
from boot_fixed import cagr_from, paired_block_bootstrap


# ── A. Base rate ─────────────────────────────────────────────────────────────

def base_rate(sig, bars, dix, targets=(10.0, 15.0, 20.0, 25.0), stop_pct=8.0,
              horizon=120):
    """For every signal, walk forward bar by bar and record which comes first:
    the target or the stop. Uses intraday high/low, and resolves an ambiguous
    bar (both touched) PESSIMISTICALLY as a stop -- the honest assumption when
    only daily bars are available."""
    rows = []
    for day, cands in sig.items():
        for sym, e, _sc, atr in cands:
            d, ix = bars[sym], dix[sym]
            e = ix.get(day, e)
            if e >= len(d) or not d[e]["open"]:
                continue
            entry = d[e]["open"]
            stop_lvl = entry * (1 - stop_pct / 100.0)
            hit = {t: None for t in targets}
            stopped_at = None
            peak = entry
            for k in range(e, min(e + horizon, len(d))):
                bar = d[k]
                peak = max(peak, bar["high"])
                if bar["low"] <= stop_lvl:
                    stopped_at = k - e
                    break
                for t in targets:
                    if hit[t] is None and bar["high"] >= entry * (1 + t / 100.0):
                        hit[t] = k - e
            rows.append(dict(sym=sym, atr=atr, stopped=stopped_at,
                             hit=hit, maxgain=(peak / entry - 1) * 100))
    return rows


def report_base_rate(rows, targets, stop_pct):
    n = len(rows)
    print(f"\n{'='*74}")
    print(f"A. BASE RATE — {n} breakout signals, {stop_pct:.0f}% hard stop, "
          f"120-session horizon")
    print(f"{'='*74}")
    print(f"{'target':>8} {'reached first':>15} {'rate':>8} {'median days':>13}")
    for t in targets:
        got = [r["hit"][t] for r in rows if r["hit"][t] is not None]
        # "reached first" = target touched and not stopped out before it
        first = [r["hit"][t] for r in rows
                 if r["hit"][t] is not None
                 and (r["stopped"] is None or r["stopped"] >= r["hit"][t])]
        med = f"{statistics.median(first):.0f}" if first else "—"
        print(f"{t:>7.0f}% {len(first):>15} {100*len(first)/n:>7.1f}% {med:>13}")

    mg = sorted(r["maxgain"] for r in rows)
    print(f"\n  max gain reached before the stop, percentiles:")
    for p in (50, 75, 90, 95, 99):
        print(f"    p{p:<3} {mg[int(len(mg)*p/100)-1]:>7.1f}%")
    stopped = sum(1 for r in rows if r["stopped"] is not None)
    print(f"\n  stopped out at -{stop_pct:.0f}% first: {stopped}/{n} "
          f"({100*stopped/n:.1f}%)")


# ── B. Counterfactual portfolio ──────────────────────────────────────────────

def simulate_oneil(cfg, sig, bars, emas, dix, alldates, atrs):
    """Portfolio sim with a hard stop + fixed profit target (O'Neil style).

    cfg keys: stop (fraction), target (pct), eight_week (bool), cool, slots.
    The 8-week rule: a position up >= 20% within 3 weeks is HELD for 8 weeks
    instead of being sold at the target -- O'Neil's own exception, and the one
    that produces his big winners.
    """
    open_pos, closed = [], []
    blocked = {}
    slots = cfg.get("slots", SLOTS)
    tgt = cfg["target"] / 100.0
    for di, day in enumerate(alldates):
        still = []
        for p in open_pos:
            d, ix = bars[p["sym"]], dix[p["sym"]]
            j = ix.get(day)
            if j is None:
                still.append(p)
                continue
            bar = d[j]
            held = j - p["e"]
            if bar["high"] > p["peak"]:
                p["peak"] = bar["high"]
            gain = (bar["high"] / p["entry"] - 1)
            if cfg.get("eight_week") and not p["hold8"] and held <= 15 and gain >= 0.20:
                p["hold8"] = True
            # stop first (pessimistic on ambiguous bars)
            stop_lvl = p["entry"] * (1 - p["stop"])
            if bar["low"] <= stop_lvl:
                closed.append(((stop_lvl / p["entry"] - 1) * 100, held, "stop", di, p["atr"]))
                blocked[p["sym"]] = di + cfg.get("cool", 0)
                continue
            if p["hold8"]:
                if held >= 40:      # ~8 weeks
                    closed.append(((bar["close"] / p["entry"] - 1) * 100, held, "8wk", di, p["atr"]))
                    blocked[p["sym"]] = di + cfg.get("cool", 0)
                    continue
            elif gain >= tgt:
                closed.append((cfg["target"], held, "target", di, p["atr"]))
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
                                 stop=cfg.get("stop", 0.08), atr=a, hold8=False))
    return closed


def main():
    uni = [x.strip() for x in open(
        os.path.join(os.path.dirname(__file__), 'broad_names.txt')) if x.strip()]
    sig, bars, emas, dix, atrs = build_with_atr(uni)
    alld = sorted({dt for s in bars for dt in dix[s]})
    years = (datetime.date.fromisoformat(alld[-1])
             - datetime.date.fromisoformat(alld[0])).days / 365.25
    ndays = len(alld)

    targets = (10.0, 15.0, 20.0, 25.0)
    rows = base_rate(sig, bars, dix, targets=targets, stop_pct=8.0)
    report_base_rate(rows, targets, 8.0)

    print(f"\n{'='*74}")
    print("B. COUNTERFACTUAL — same signals, same 4 slots, different exit policy")
    print(f"{'='*74}")
    neutral = apply_ranking(sig, rank_neutral)
    shipped = _simulate_patched(SHIPPED_EXITS, neutral, bars, emas, dix, alld, atrs)
    describe(shipped, "SHIPPED ladder (+5% lock, 1.5% trail)", years, ndays)

    for label, cfg in [
        ("O'Neil 25% target / 8% stop", dict(target=25.0, stop=0.08, cool=7, slots=SLOTS)),
        ("O'Neil 20% target / 8% stop", dict(target=20.0, stop=0.08, cool=7, slots=SLOTS)),
        ("O'Neil 25% / 8% + 8-week rule", dict(target=25.0, stop=0.08, cool=7,
                                               slots=SLOTS, eight_week=True)),
        ("O'Neil 25% / 7% stop", dict(target=25.0, stop=0.07, cool=7, slots=SLOTS)),
        ("Take profit at +10% / 8% stop", dict(target=10.0, stop=0.08, cool=7, slots=SLOTS)),
    ]:
        res = simulate_oneil(cfg, neutral, bars, emas, dix, alld, atrs)
        describe(res, label, years, ndays, baseline=shipped)


if __name__ == "__main__":
    main()
