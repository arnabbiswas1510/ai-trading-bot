"""What does the Thesis Stop actually DO to the trades it cuts?

The portfolio sim cannot answer this directly: cutting a position frees a slot,
which changes which later entries are taken, so the two arms have different
trade lists and cannot be paired.

Removing the slot constraint restores a 1:1 correspondence -- every signal
becomes a trade in both arms -- which isolates the rule's DIRECT effect on the
positions it touches from its indirect capital-velocity effect. Both matter, but
only the direct effect tells us whether the rule is cutting losers or winners.
"""
import datetime
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from port_sim import build
from breakout_bt import dyn_trail, PH_GAIN, PH_TRIG, PH_DUR, EMA_BUF
from thesis_bt import atr_pct_series, armed_fill

CFG = dict(profit_tiers=[(50, .050), (30, .060), (20, .065)], time_tiers=[],
           base_stop=0.10, stale=10)


def one_trade(d, ema, atrs, e, thesis_atr, thesis_day=2, thesis_last=5,
              arm_path="conservative", armed_trail=0.006, deadline=1):
    """Simulate a single position from entry index *e*. Returns (pct, reason)."""
    if e >= len(d) or not d[e]["open"]:
        return None
    entry = d[e]["open"]
    d0 = datetime.date.fromisoformat(d[e]["date"][:10])
    p = dict(peak=entry, trail=CFG["base_stop"], pg=0.0, ph=False,
             closed_above=False, last_peak=e, armed=False, arm_peak=0.0,
             armed_at=0, atr0=atrs[e - 1] if e > 0 else None)

    for j in range(e, len(d)):
        bar = d[j]
        held = j - e
        cal = (datetime.date.fromisoformat(bar["date"][:10]) - d0).days

        if p["armed"]:
            px = armed_fill(p, bar, armed_trail, arm_path, held - p["armed_at"] == 1)
            if px is not None:
                return (px / entry - 1) * 100, "thesis"
            if held - p["armed_at"] >= deadline:
                return (bar["close"] / entry - 1) * 100, "thesis"
            continue

        if bar["high"] > p["peak"]:
            p["peak"] = bar["high"]
            p["last_peak"] = j
        p["pg"] = max(p["pg"], (p["peak"] / entry - 1) * 100)
        if p["pg"] >= PH_GAIN and cal <= PH_TRIG:
            p["ph"] = True
        if p["ph"] and cal > PH_DUR:
            p["ph"] = False

        lvl = p["peak"] * (1 - p["trail"])
        if bar["low"] <= lvl:
            return (lvl / entry - 1) * 100, "trail"

        c = bar["close"]
        if c > entry:
            p["closed_above"] = True
        nt = dyn_trail(CFG, (c / entry - 1) * 100, cal, p["trail"])
        if nt:
            p["trail"] = nt

        if (thesis_atr is not None and not p["ph"] and not p["closed_above"]
                and thesis_day <= held <= thesis_last and p["atr0"]):
            if (c / entry - 1) * 100 <= -thesis_atr * p["atr0"]:
                p["armed"] = True
                p["armed_at"] = held
                p["arm_peak"] = c
                continue

        if not p["ph"] and held >= 7 and (j - p["last_peak"]) >= CFG["stale"]:
            return (c / entry - 1) * 100, "stale"
        if not p["ph"] and held >= 7 and ema[j] and c < ema[j] * (1 - EMA_BUF):
            return (c / entry - 1) * 100, "ema"

    return (d[-1]["close"] / entry - 1) * 100, "open_end"


def run(fname, label, mult=1.0, day=2):
    uni = [x.strip() for x in open(fname) if x.strip()]
    sig, bars, emas, dix = build(uni)
    atrs = {s: atr_pct_series(bars[s]) for s in bars}

    pairs = []
    for _day, entries in sig.items():
        for sym, e, _sc in entries:
            d, ema, a = bars[sym], emas[sym], atrs[sym]
            off = one_trade(d, ema, a, e, None)
            on = one_trade(d, ema, a, e, mult, thesis_day=day)
            if off and on:
                pairs.append((sym, off, on))

    cut = [(s, o, n) for s, o, n in pairs if n[1] == "thesis"]
    print(f"\n===== {label}  thesis {mult}x from day{day}  "
          f"signals={len(pairs)}  cut={len(cut)} "
          f"({100*len(cut)/max(1,len(pairs)):.1f}%) =====")
    if not cut:
        return

    exit_r = [n[0] for _, _, n in cut]
    hold_r = [o[0] for _, o, _ in cut]
    delta = [n[0] - o[0] for _, o, n in cut]

    print(f"  on the {len(cut)} positions the rule cuts:")
    print(f"    thesis exit      mean {statistics.mean(exit_r):+7.2f}%   "
          f"median {statistics.median(exit_r):+7.2f}%")
    print(f"    if HELD instead  mean {statistics.mean(hold_r):+7.2f}%   "
          f"median {statistics.median(hold_r):+7.2f}%")
    print(f"    DIRECT EFFECT    mean {statistics.mean(delta):+7.2f}%   "
          f"median {statistics.median(delta):+7.2f}%")
    saved = sum(1 for x in delta if x > 0)
    print(f"    rule improved the outcome on {saved}/{len(cut)} "
          f"({100*saved/len(cut):.0f}%) of the cuts")

    prof = [o[0] for _, o, _ in cut if o[0] > 0]
    print(f"    {len(prof)} of the cut positions ({100*len(prof)/len(cut):.0f}%) "
          f"would have ended PROFITABLE"
          + (f" (mean {statistics.mean(prof):+.1f}%)" if prof else ""))
    big = [o[0] for _, o, _ in cut if o[0] >= 20]
    print(f"    {len(big)} would have reached >= +20%"
          + (f" (mean {statistics.mean(big):+.1f}%)" if big else ""))

    off_all = [o[0] for _, o, _ in pairs]
    on_all = [n[0] for _, _, n in pairs]
    print(f"  per-trade expectancy over ALL signals: "
          f"OFF {statistics.mean(off_all):+.2f}%   ON {statistics.mean(on_all):+.2f}%")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for f, l in (("broad_names.txt", "BROAD"), ("pass_names.txt", "PASS")):
        run(os.path.join(here, f), l)
