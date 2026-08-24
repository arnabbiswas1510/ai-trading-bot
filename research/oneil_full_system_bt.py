"""
oneil_full_system_bt.py — a FAIR test of O'Neil's 20-25% target.

`oneil_target_bt.py` bolted O'Neil's EXIT rule onto this bot's entry process and
ran it always-invested. That is not CAN SLIM, and the -32pp result was partly an
artefact of testing one rule of his system in isolation. O'Neil's 4-5 position
concentration is inseparable from two other things he insists on:

  M — MARKET DIRECTION. "Three out of four stocks follow the market." He tells
      you to be in CASH during corrections. Always-invested is his single
      biggest documented no.
  SELECTIVITY. He takes a handful of entries a year at proper pivots from proper
      bases in leading groups. This harness fires ~750 signals/year.

So this script re-tests the target with those restored, as far as the offline
data allows:

  - market filter: enter only when SPY closes above its 50-day MA (proxy for a
    "confirmed uptrend"; the live bot's gate is richer)
  - selectivity: cap entries per day, and/or use the screener-passing universe
  - the 8-week rule, which is how O'Neil's big winners actually happen

The CONTROL matters more than the treatment: the shipped ladder is run through
the identical filter every time. If market timing lifts both equally it has not
rescued the target -- it has just improved the entries.

Usage:  python3 research/oneil_full_system_bt.py
"""
import datetime
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bardata
from atr_rank_bt import (build_with_atr, apply_ranking, rank_neutral,
                         _simulate_patched, SHIPPED_EXITS)
from oneil_target_bt import simulate_oneil
from boot_fixed import cagr_from, paired_block_bootstrap


def spy_uptrend_days(ma=50):
    """Days on which SPY closed above its `ma`-day simple moving average."""
    rows = bardata.daily("SPY")
    closes = [r["close"] for r in rows]
    out = set()
    for i in range(len(rows)):
        if i < ma:
            continue
        avg = sum(closes[i - ma:i]) / ma
        if closes[i] > avg:
            out.add(rows[i]["date"][:10] if len(rows[i]["date"]) > 10 else rows[i]["date"])
    return out


def filter_signals(sig, allowed_days=None, top_k=None):
    """Restrict which signals may be acted on. Does not change exits."""
    out = {}
    for day, cands in sig.items():
        if allowed_days is not None and day not in allowed_days:
            continue
        c = sorted(cands, key=lambda t: -t[2])
        out[day] = c[:top_k] if top_k else c
    return out


def run(label, exits_kind, sig, bars, emas, dix, alld, atrs, years, slots,
        baseline=None):
    alld_g = alld
    ranked = apply_ranking(sig, rank_neutral)
    if exits_kind == "shipped":
        cfg = dict(SHIPPED_EXITS)
        cfg["slots"] = slots
        res = _simulate_patched(cfg, ranked, bars, emas, dix, alld, atrs)
    else:
        res = simulate_oneil(dict(target=25.0, stop=0.08, cool=7, slots=slots,
                                  eight_week=True),
                             ranked, bars, emas, dix, alld, atrs)
    rets = [r[0] for r in res]
    if not rets:
        print(f"  {label:<44} {'no trades':>38}")
        return res
    cagr = cagr_from(rets, years, slots)
    hold = statistics.median(r[1] for r in res)
    win = 100 * sum(1 for r in rets if r > 0) / len(rets)
    line = (f"  {label:<44} {len(rets):>5} {statistics.mean(rets):>+7.2f}% "
            f"{win:>5.0f}% {hold:>6.0f}d {cagr:>+8.1f}%")
    if baseline is not None:
        # returns CAGR_a - CAGR_b, so treatment first = "treatment vs control"
        d, lo, hi, p = paired_block_bootstrap(
            res, baseline, len(alld_g), years, slots)
        line += f" {d:>+8.1f} [{lo:>+6.1f},{hi:>+6.1f}] {p:>4.0f}%"
    print(line)
    return res


def main():
    uni_broad = [x.strip() for x in open(
        os.path.join(os.path.dirname(__file__), 'broad_names.txt')) if x.strip()]
    uni_pass = [x.strip() for x in open(
        os.path.join(os.path.dirname(__file__), 'pass_names.txt')) if x.strip()]

    up = spy_uptrend_days(50)
    print(f"SPY above its 50-day MA on {len(up)} sessions.")

    for uname, uni in (("BROAD universe (256 names)", uni_broad),
                       ("SCREENER-PASSING universe (79 names)", uni_pass)):
        sig, bars, emas, dix, atrs = build_with_atr(uni)
        alld = sorted({dt for s in bars for dt in dix[s]})
        years = (datetime.date.fromisoformat(alld[-1])
                 - datetime.date.fromisoformat(alld[0])).days / 365.25
        nsig = sum(len(v) for v in sig.values())
        inup = sum(len(v) for d, v in sig.items() if d in up)
        print(f"\n{'='*104}")
        print(f"{uname} — {nsig} signals, {inup} ({100*inup/max(nsig,1):.0f}%) "
              f"in a SPY uptrend, {years:.2f}y, 4 slots")
        print(f"{'='*104}")
        print(f"  {'configuration':<44} {'n':>5} {'exp':>8} {'win':>5} "
              f"{'hold':>7} {'CAGR':>9} {'vs ctrl':>8} {'   95% CI':>16} {'P>':>5}")

        for fname, fsig in (
            ("always invested", filter_signals(sig)),
            ("M: SPY > 50d MA", filter_signals(sig, allowed_days=up)),
            ("M + selective (top 1/day)", filter_signals(sig, allowed_days=up, top_k=1)),
        ):
            print(f"  --- {fname} ---")
            ctrl = run(f"    SHIPPED ladder", "shipped", fsig, bars, emas, dix,
                       alld, atrs, years, 4)
            run(f"    O'Neil +25% / 8% + 8-week", "oneil", fsig, bars, emas, dix,
                alld, atrs, years, 4, baseline=ctrl)


if __name__ == "__main__":
    main()
