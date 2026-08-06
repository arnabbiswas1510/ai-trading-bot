"""Re-run every comparison the noise-floor ADR called 'noise', under BOTH the
original (unpaired, iid-over-trades) bootstrap and the corrected
(paired, stationary-block-over-calendar) bootstrap.
"""
import datetime, sys
from port_sim import build, simulate
from boot_fixed import paired_block_bootstrap, unpaired_trade_bootstrap, single_ci, cagr_from

SLOTS = 4
F = dict(profit_tiers=[(50, .050), (30, .060), (20, .065)], time_tiers=[],
         power_hold=True, minimiser=None, base_stop=0.10, cool=7, slots=SLOTS,
         ema=True, stale=10)

CONFIGS = {
    "shipped":                     dict(F),
    "EMA exit OFF":                dict(F, ema=False),
    "plateau(stale) exit OFF":     dict(F, stale=None),
    "minimiser ON @2%":            dict(F, minimiser=0.02),
    "tight profit ladder":         dict(F, profit_tiers=[(20, .020), (14, .030), (8, .040), (3, .050)]),
    "base stop 7%":                dict(F, base_stop=0.07),
    "base stop 12%":               dict(F, base_stop=0.12),
    "cooling-off 1d":              dict(F, cool=1),
    "5 slots":                     dict(F, slots=5),
    "6 slots":                     dict(F, slots=6),
}

uni = [x.strip() for x in open("pass_names.txt") if x.strip()]
sig, bars, emas, dix = build(uni)
alld = sorted({dt for s in bars for dt in dix[s]})
ndays = len(alld)
years = (datetime.date.fromisoformat(alld[-1]) - datetime.date.fromisoformat(alld[0])).days / 365.25

print(f"universe={len(bars)}  days={ndays}  years={years:.2f}\n")

res = {}
for lbl, cfg in CONFIGS.items():
    res[lbl] = (simulate(cfg, sig, bars, emas, dix, alld), cfg.get("slots", SLOTS))

print("=" * 104)
print("POINT ESTIMATES + single-config 90% CI (corrected block bootstrap)")
print("=" * 104)
print(f"{'config':28}{'n':>6}{'CAGR':>9}{'5th':>9}{'95th':>9}{'CI width':>11}")
for lbl, (r, k) in res.items():
    pt = cagr_from([t[0] for t in r], years, k)
    m, lo, hi = single_ci(r, ndays, years, k)
    print(f"{lbl:28}{len(r):>6}{pt:>+9.1f}{lo:>+9.1f}{hi:>+9.1f}{hi-lo:>10.1f}pp")

base_r, base_k = res["shipped"]
print()
print("=" * 104)
print("PAIRED DIFFERENCES vs shipped   —   ORIGINAL (buggy) vs CORRECTED")
print("=" * 104)
print(f"{'config':28}{'ORIG 90% CI':>26}{'width':>9}   |{'CORRECTED 90% CI':>26}{'width':>9}{'P(better)':>11}")
for lbl, (r, k) in res.items():
    if lbl == "shipped":
        continue
    om, olo, ohi, op = unpaired_trade_bootstrap(r, base_r, years, k)
    pm, plo, phi, pp = paired_block_bootstrap(r, base_r, ndays, years, k)
    print(f"{lbl:28}{f'{om:+.1f} [{olo:+.1f},{ohi:+.1f}]':>26}{ohi-olo:>8.1f}pp   |"
          f"{f'{pm:+.1f} [{plo:+.1f},{phi:+.1f}]':>26}{phi-plo:>8.1f}pp{pp:>10.0f}%")
