"""Does an INTRADAY POKE above entry deserve to disarm the Thesis Stop?

THE QUESTION
------------
The Thesis Stop only fires while a position has never followed through above
entry. Production currently decides "followed through" using the FALLBACK in
execution_agent.py L2680-2686, because migrations/add_closed_above_entry.sql was
never applied:

    highest_unrealized_pct > 0  OR  hwm_price > buy_price  OR
    intraday_high_today > buy_price

All three are INTRADAY highs. So one tick above entry at any point permanently
disarms the rule. The shipped DESIGN latches on a daily CLOSE above entry, and
that is the definition the +18.8 dCAGR result in
decisions/2026-08-09_thesis-stop.md was measured on. The poke fallback has never
been backtested.

Both NBIX (entry 168.33) and DELL (entry 496.04) poked above entry intraday and
NEVER closed above it, so both were disarmed by the fallback and would NOT have
been disarmed by the shipped design.

VARIANTS
--------
  none          no follow-through gate at all (thesis stop as a pure ATR stop)
  poke          latch on intraday high > entry      (production fallback TODAY)
  close         latch on daily close > entry        (shipped design)
  close+0.25ATR latch on close > entry + 0.25 x ATR (stricter "meaningful" follow-through)
  close+0.50ATR latch on close > entry + 0.50 x ATR

Run in BOTH mandated universes with a paired stationary-block bootstrap, per the
standing rule that a result must hold in both to be actionable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from boot_fixed import paired_block_bootstrap
from thesis_bt import BASE, load, simulate, stats

VARIANTS = [
    ("baseline: thesis stop OFF", dict(BASE)),
    ("none  (no follow-through gate)", dict(BASE, thesis_atr=1.0, thesis_day=2, latch="none")),
    ("poke  (PROD FALLBACK today)", dict(BASE, thesis_atr=1.0, thesis_day=2, latch="high")),
    ("close (SHIPPED DESIGN)", dict(BASE, thesis_atr=1.0, thesis_day=2, latch="close")),
    ("close +0.25xATR margin", dict(BASE, thesis_atr=1.0, thesis_day=2,
                                    latch="close_margin", latch_margin_atr=0.25)),
    ("close +0.50xATR margin", dict(BASE, thesis_atr=1.0, thesis_day=2,
                                    latch="close_margin", latch_margin_atr=0.50)),
]

HDR = (f"{'variant':34}{'n':>5}{'exp%':>8}{'payoff':>8}{'avgloss':>9}"
       f"{'worst':>8}{'big20':>7}{'CAGR':>8}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    store = {}
    for fname, label in (("broad_names.txt", "BROAD"), ("pass_names.txt", "PASS")):
        sig, bars, emas, dix, alld, atrs, years = load(os.path.join(here, fname))
        print(f"\n===== {label}  symbols={len(bars)}  days={len(alld)}  years={years:.2f}  slots=4 =====")
        print(HDR)
        res = {}
        for name, cfg in VARIANTS:
            r = simulate(cfg, sig, bars, emas, dix, alld, atrs)
            res[name] = r
            s = stats(r, years)
            print(f"{name:34}{s['n']:>5}{s['exp']:>+8.2f}{s['payoff']:>8.2f}"
                  f"{s['avg_loss']:>+9.2f}{s['worst']:>+8.1f}{s['big20']:>6.1f}%{s['cagr']:>+8.1f}")
        store[label] = (res, len(alld), years)

        base = res["baseline: thesis stop OFF"]
        poke = res["poke  (PROD FALLBACK today)"]
        print(f"\n  paired stationary-block bootstrap (2000 reps, 60d blocks), slots=4:")
        for name in [n for n, _ in VARIANTS][1:]:
            med, lo, hi, pw = paired_block_bootstrap(res[name], base, len(alld), years, 4)
            flag = "SIG" if (lo > 0 or hi < 0) else "   "
            print(f"    {name:32} vs OFF   {med:+7.2f}pp  [{lo:+7.2f},{hi:+7.2f}]  P={pw:3.0f}%  {flag}")
        print()
        for name in ("close (SHIPPED DESIGN)", "close +0.25xATR margin", "close +0.50xATR margin",
                     "none  (no follow-through gate)"):
            med, lo, hi, pw = paired_block_bootstrap(res[name], poke, len(alld), years, 4)
            flag = "SIG" if (lo > 0 or hi < 0) else "   "
            print(f"    {name:32} vs POKE  {med:+7.2f}pp  [{lo:+7.2f},{hi:+7.2f}]  P={pw:3.0f}%  {flag}")

    print("\n" + "=" * 96)
    print("HOW OFTEN DOES THE LATCH DEFINITION EVEN MATTER? (thesis-stop exits taken)")
    print("=" * 96)
    for label, (res, nd, years) in store.items():
        row = {n: sum(1 for t in r if t[2] == "thesis") for n, r in res.items()}
        print(f"  {label}: " + "  ".join(f"{n.split('(')[0].strip()}={v}" for n, v in row.items() if v))


if __name__ == "__main__":
    main()
