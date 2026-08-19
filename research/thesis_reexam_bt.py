"""Re-examination of the Thesis Stop after the look-ahead correction.

WHY THIS EXISTS
---------------
The thesis stop shipped on evidence of +18.8 dCAGR [+7.1, +33.0] P=100% on the
screener-passing universe. That measurement came through the armed-exit
look-ahead bug corrected in decisions/2026-08-17_armed-exit-backtest-lookahead.md.
Corrected, the same test gives +10.3 [-1.2, +23.4] P=92% on PASS and -9.4
[-25.1, +7.0] P=15% on BROAD -- neither significant. This harness re-derives the
parameters from scratch on the corrected model.

THE DECISION RULE (fixed BEFORE looking at results)
---------------------------------------------------
A configuration is actionable only if it clears ALL FOUR slices:

    {BROAD, PASS} x {conservative path, optimistic path}

The two universes are the standing project rule. The two intra-bar paths are
added because daily bars cannot reveal whether a bar's high or low printed
first, and for a stop resting below the market that ordering decides whether it
fills at all. A config that needs a favourable coin-flip on every bar is not a
config, it is a wish.

Additionally reported, because a parameter sweep over 10 configs x 4 slices will
produce apparent winners by chance alone:

  * MONOTONICITY -- a real volatility-threshold effect should vary smoothly with
    the multiplier. A sweep that zig-zags (a trough at 1.5x flanked by peaks at
    1.25x and 2.0x) is reading noise. The shipped ADR already flagged this
    signature and then picked from the sweep anyway.
  * PERIOD STABILITY -- CAGR in each third of the sample. A config carried by a
    single period is not durable. Per config.py's plateau-exit precedent, the
    best WORST-period result is preferred over the best full-period result.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from boot_fixed import paired_block_bootstrap, cagr_from
from thesis_bt import BASE, load, simulate, stats

PATHS = ("conservative", "optimistic")
MULTS = (0.75, 1.0, 1.25, 1.5, 2.0)
DAYS = (2, 3)


def period_cagrs(res, ndays, years, slots=4, n=3):
    """CAGR within each of *n* equal calendar slices of the sample."""
    out = []
    edge = ndays / n
    for k in range(n):
        lo, hi = k * edge, (k + 1) * edge
        pl = [t[0] for t in res if lo <= t[3] < hi]
        out.append(cagr_from(pl, years / n, slots))
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    grid = [(f"{m}x d{d}", dict(BASE, thesis_atr=m, thesis_day=d))
            for m in MULTS for d in DAYS]

    # slice -> {config name: (median, lo, hi, P)}
    boot = {}
    cagr = {}

    for fname, uni in (("broad_names.txt", "BROAD"), ("pass_names.txt", "PASS")):
        sig, bars, emas, dix, alld, atrs, years = load(os.path.join(here, fname))
        nd = len(alld)
        for path in PATHS:
            slice_id = f"{uni}/{path[:4]}"
            base = simulate(dict(BASE, arm_path=path), sig, bars, emas, dix, alld, atrs)
            b = {}
            c = {"OFF": stats(base, years)["cagr"]}
            for name, cfg in grid:
                r = simulate(dict(cfg, arm_path=path), sig, bars, emas, dix, alld, atrs)
                b[name] = paired_block_bootstrap(r, base, nd, years, 4)
                c[name] = stats(r, years)["cagr"]
            boot[slice_id] = b
            cagr[slice_id] = c

        # period stability, conservative path only (the binding case)
        base_c = simulate(dict(BASE, arm_path="conservative"),
                          sig, bars, emas, dix, alld, atrs)
        cagr[f"{uni}/periods"] = {
            "OFF": period_cagrs(base_c, nd, years),
            **{name: period_cagrs(
                simulate(dict(cfg, arm_path="conservative"),
                         sig, bars, emas, dix, alld, atrs), nd, years)
               for name, cfg in grid},
        }

    slices = [f"{u}/{p[:4]}" for u in ("BROAD", "PASS") for p in PATHS]

    print("=" * 100)
    print("THESIS STOP vs OFF -- dCAGR (paired stationary-block bootstrap, 2000 reps)")
    print("=" * 100)
    print(f"{'config':10}" + "".join(f"{s:>22}" for s in slices))
    for name, _ in grid:
        row = f"{name:10}"
        for s in slices:
            med, lo, hi, pw = boot[s][name]
            sig_ = "*" if (lo > 0 or hi < 0) else " "
            row += f"{med:>+9.1f} [{lo:+5.1f},{hi:+5.1f}]{sig_}"
        print(row)

    print()
    print("=" * 100)
    print("DECISION RULE: positive median in ALL FOUR slices?")
    print("=" * 100)
    survivors = []
    for name, _ in grid:
        meds = [boot[s][name][0] for s in slices]
        ok = all(m > 0 for m in meds)
        sigs = sum(1 for s in slices if boot[s][name][1] > 0 or boot[s][name][2] < 0)
        worst = min(meds)
        print(f"  {name:10} {'PASS' if ok else 'FAIL':5} "
              f"worst slice {worst:+7.2f}pp   significant in {sigs}/4 slices")
        if ok:
            survivors.append(name)

    print()
    print("=" * 100)
    print("MONOTONICITY -- absolute CAGR by multiplier (conservative path)")
    print("  a real threshold effect varies smoothly; zig-zag = noise")
    print("=" * 100)
    for uni in ("BROAD", "PASS"):
        c = cagr[f"{uni}/cons"]
        for d in DAYS:
            row = "  ".join(f"{m}x:{c[f'{m}x d{d}']:+6.1f}" for m in MULTS)
            print(f"  {uni:6} day{d}  OFF:{c['OFF']:+6.1f}   {row}")

    print()
    print("=" * 100)
    print("PERIOD STABILITY -- CAGR per third (conservative path)")
    print("=" * 100)
    for uni in ("BROAD", "PASS"):
        p = cagr[f"{uni}/periods"]
        print(f"  {uni}")
        for name in ["OFF"] + [n for n, _ in grid]:
            v = p[name]
            print(f"    {name:10} " + "  ".join(f"P{i+1}:{x:+7.1f}" for i, x in enumerate(v))
                  + f"   worst {min(v):+7.1f}")

    print()
    print("=" * 100)
    print(f"SURVIVORS: {survivors or 'NONE'}")
    print("=" * 100)


if __name__ == "__main__":
    main()
