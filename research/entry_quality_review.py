#!/usr/bin/env python3
"""Is the missing right tail an ENTRY problem or an EXIT problem?

The bot's realised distribution has no right tail: across 52 closed trades the
best result is +6.47% and nothing exceeds +7%, while nothing is worse than
-6.25%. Every attempt to fix that by loosening the exit has failed the
concentration test (see decisions/provisional_decisions.json ->
ladder-width-runon), which points upstream: either the screener is not
producing stocks that run, or the bot is choosing the wrong ones from those it
does produce.

This script answers that on the TRIGGER population rather than the traded one.
That population is several times larger AND it contains the counterfactual --
the breakouts the bot passed over -- which the trade history cannot show.

Four questions, in the order that matters:

  A. SELECTION. Do the triggers the bot TOOK underperform the ones it PASSED
     OVER? If they do, ranking is the whole problem and no exit change matters.
  B. PREDICTION. Does any feature observable at trigger time separate the
     >= +10% population?
  C. HONESTY. Does B survive a time split and beat a permutation baseline?
  D. SLOT COST. What did a full book cost in forgone winners? This is the
     blocking precondition on ladder-width-runon.

Usage:
    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/entry_quality_review.py --insecure

Exit codes: 0 = ran, 1 = error. It never returns a verdict by exit code --
read the report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import statistics
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

import requests
import urllib3

# Features recorded at trigger time. Anything computed later would be
# look-ahead and must never appear here.
FEATURES = [
    "quality_score", "technical_score", "final_score", "adjusted_score",
    "rs_score", "rs_percentile", "rs_12w_return", "rs_excess_return",
    "atr_pct", "volume_surge", "pivot_distance_pct", "liquidity_score",
    "sentiment_score", "failure_penalty",
]
# The outcome column under test. Switchable because the two horizons have very
# different sample shapes and the 20-day one CANNOT be accelerated -- a trigger
# has to actually live 20 trading days. As of 2026-09-18:
#   max_gain_20d_pct :  50 rows /  4 dates  (the honest but tiny sample)
#   fwd_5d_pct       : 200 rows / 19 dates  (shorter horizon, far better spread)
# The 5-day view answers a WEAKER question -- "did it go up soon" is not "did it
# become a big winner" -- but it is the only one with enough distinct dates to
# escape the single-week regime trap, so it is worth reading alongside.
OUTCOME = "max_gain_20d_pct"
TAIL_PCT = 10.0          # primary label: OUTCOME >= TAIL_PCT
BIG_TAIL_PCT = 20.0      # secondary
MIN_N_FOR_A_VERDICT = 150



def _today_et() -> dt.date:
    """Today in market time.

    The whole codebase is anchored to America/New_York (see the timezone guard in
    tests/test_timezone_usage.py). A timezone-naive local date would roll over at
    the wrong instant for anyone running this outside ET and could silently shift
    an open position's span by a day -- which is why the guard bans it outright.
    """
    return dt.datetime.now(ZoneInfo("America/New_York")).date()

def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"error: {name} is not set. "
                 f"Load ~/.config/ai-trading-bot/secrets.env first.")
    return v


def _get(path: str, params: dict, verify: bool) -> list[dict]:
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    r = requests.get(f"{url}/rest/v1/{path}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"},
                     params=params, timeout=60, verify=verify)
    if r.status_code not in (200, 206):
        sys.exit(f"error: Supabase HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Mann-Whitney U / ROC AUC. 0.5 = no discrimination, 1.0 = perfect.

    Hand-rolled rather than pulled from sklearn: this repo has no ML dependency
    and adding one for a rank statistic would be absurd. Ties are credited 0.5,
    which is what makes this equal the ROC area rather than approximate it.
    """
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return wins / (len(pos) * len(neg))


def discriminate(rows: list[dict], feature: str, thresh: float) -> dict | None:
    vals = [(float(r[feature]), float(r[OUTCOME]))
            for r in rows
            if r.get(feature) is not None and r.get(OUTCOME) is not None]
    if len(vals) < 20:
        return None
    pos = [v for v, g in vals if g >= thresh]
    neg = [v for v, g in vals if g < thresh]
    a = auc(pos, neg)
    if a is None:
        return None
    return {"feature": feature, "auc": a, "n": len(vals),
            "n_pos": len(pos), "n_neg": len(neg)}


def permutation_baseline(rows: list[dict], features: list[str],
                         thresh: float, trials: int = 200) -> float:
    """Best AUC this same search finds on SHUFFLED labels.

    With a dozen features the best in-sample AUC will look impressive on pure
    noise. Reporting the real best without this number is how a null result
    gets shipped as a finding.
    """
    rng = random.Random(20260918)
    gains = [r[OUTCOME] for r in rows
             if r.get(OUTCOME) is not None]
    best_per_trial = []
    for _ in range(trials):
        shuffled = gains[:]
        rng.shuffle(shuffled)
        fake = [dict(r, **{OUTCOME: g})
                for r, g in zip([r for r in rows
                                 if r.get(OUTCOME) is not None], shuffled)]
        best = 0.5
        for f in features:
            d = discriminate(fake, f, thresh)
            if d:
                best = max(best, max(d["auc"], 1 - d["auc"]))
        best_per_trial.append(best)
    return statistics.median(best_per_trial)


def _date(value) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (local only)")
    ap.add_argument("--horizon", choices=("20d", "5d"), default="20d",
                    help="20d = max_gain_20d_pct (50 rows/4 dates, the real "
                         "question, tiny sample). 5d = fwd_5d_pct (200 rows/19 "
                         "dates, weaker question, far better date spread).")
    ap.add_argument("--tail", type=float, default=None,
                    help="right-tail threshold in %% (default 10 at 20d, 5 at 5d)")
    args = ap.parse_args()
    verify = not args.insecure
    if args.insecure:
        urllib3.disable_warnings()

    global OUTCOME, TAIL_PCT, BIG_TAIL_PCT
    if args.horizon == "5d":
        OUTCOME = "fwd_5d_pct"
        # A 5-day move cannot be compared to a 20-day MAXIMUM, so the labels
        # move with the horizon. +5%/+10% in five sessions is the rough
        # equivalent of the +10%/+20% tail being sought at 20 days.
        TAIL_PCT, BIG_TAIL_PCT = 5.0, 10.0
    if args.tail is not None:
        TAIL_PCT = args.tail

    trig = _get("trigger_history",
                {"select": "*", OUTCOME: "not.is.null",
                 "order": "triggered_at.asc"}, verify)
    trades = _get("trade_history",
                  {"select": "ticker,buy_date,sell_date,profit_loss,"
                             "net_profit_loss,percent_return"}, verify)

    print("=" * 78)
    print(f"ENTRY QUALITY REVIEW — {_today_et()}")
    print("=" * 78)
    trig_dates = sorted({_date(r.get("triggered_at")) for r in trig} - {None})
    print(f"matured trigger rows ({OUTCOME} present):{'':<{max(0,18-len(OUTCOME))}} {len(trig)}")
    print(f"closed trades:                                   {len(trades)}")
    print(f"DISTINCT trigger dates in that sample:           {len(trig_dates)}")
    if trig_dates:
        print(f"  spanning {trig_dates[0]} .. {trig_dates[-1]}")
    # Row count is NOT sample size. 50 rows drawn from 4 scan days are 4
    # observations of market regime wearing 50 hats: every row on one day shares
    # the same tape, the same sector rotation and the same breadth. A feature
    # can look predictive purely because the day it fired on happened to run.
    if trig_dates and len(trig_dates) < 20:
        print()
        print(f"  ⚠️  ONLY {len(trig_dates)} DISTINCT DATES. Rows are clustered within")
        print("      days, so the EFFECTIVE sample is far smaller than the row")
        print("      count. Do not read per-feature AUCs as independent evidence")
        print("      until this reaches ~20+ dates, whatever the row count says.")
    if len(trig) < MIN_N_FOR_A_VERDICT:
        print()
        print(f"  ⚠️  SAMPLE TOO SMALL FOR A VERDICT (need >= {MIN_N_FOR_A_VERDICT}).")
        print("      Everything below is shape-only. Do NOT act on it, and do")
        print("      not record a verdict in the register from this run.")
    print()

    # ── A. SELECTION ────────────────────────────────────────────────────────
    # A trigger counts as TAKEN if a trade exists on the same ticker bought
    # within two calendar days of the trigger. The window absorbs a trigger
    # fired after the close and bought the next open.
    bought: dict[str, list[dt.date]] = defaultdict(list)
    for t in trades:
        d = _date(t.get("buy_date"))
        if d:
            bought[(t.get("ticker") or "").upper()].append(d)

    taken, passed = [], []
    for r in trig:
        td = _date(r.get("triggered_at"))
        tk = (r.get("ticker") or "").upper()
        hit = td and any(0 <= (b - td).days <= 2 for b in bought.get(tk, []))
        (taken if hit else passed).append(r)

    print("── A. SELECTION: did the bot pick the right triggers? " + "─" * 24)
    for label, grp in (("TAKEN", taken), ("PASSED OVER", passed)):
        if not grp:
            print(f"  {label:<12} n=0")
            continue
        g = [float(r[OUTCOME]) for r in grp]
        print(f"  {label:<12} n={len(g):3}  median {statistics.median(g):+6.2f}%  "
              f"mean {statistics.mean(g):+6.2f}%  "
              f">=+{TAIL_PCT:.0f}% {len([x for x in g if x >= TAIL_PCT]):3} "
              f"({len([x for x in g if x >= TAIL_PCT]) / len(g) * 100:3.0f}%)  "
              f">=+{BIG_TAIL_PCT:.0f}% {len([x for x in g if x >= BIG_TAIL_PCT]):3}")
    if taken and passed:
        tg = [float(r[OUTCOME]) for r in taken]
        pg = [float(r[OUTCOME]) for r in passed]
        a = auc(tg, pg)
        print()
        print(f"  AUC(taken > passed) = {a:.3f}")
        if a is not None:
            if a < 0.45:
                print("  ⚠️  BELOW 0.5: the bot's picks are WORSE than the ones it")
                print("      passed over. Trigger ranking is then the highest-leverage")
                print("      thing in the system and no exit change can compensate.")
            elif a > 0.55:
                print("  ✓  Selection is adding value.")
            else:
                print("  =  Indistinguishable from random selection.")
    print()

    # ── B/C. PREDICTION, time-split and permutation-checked ─────────────────
    print(f"── B. PREDICTION: which features separate >= +{TAIL_PCT:.0f}%? " + "─" * 19)
    rows = [r for r in trig if r.get(OUTCOME) is not None]
    rows.sort(key=lambda r: str(r.get("triggered_at") or ""))
    half = len(rows) // 2
    older, newer = rows[:half], rows[half:]

    results = []
    for f in FEATURES:
        full = discriminate(rows, f, TAIL_PCT)
        if not full:
            continue
        held = discriminate(newer, f, TAIL_PCT)
        results.append((full, held))
    results.sort(key=lambda p: -max(p[0]["auc"], 1 - p[0]["auc"]))

    if not results:
        print("  No feature has enough non-null values to score.")
    else:
        print(f"  {'feature':<22}{'AUC(all)':>10}{'AUC(held-out)':>15}"
              f"{'n':>6}{'n_pos':>7}")
        print("  " + "-" * 60)
        for full, held in results:
            h = f"{held['auc']:.3f}" if held else "  n/a"
            print(f"  {full['feature']:<22}{full['auc']:>10.3f}{h:>15}"
                  f"{full['n']:>6}{full['n_pos']:>7}")

        print()
        print("── C. HONESTY: what does the same search find on NOISE? " + "─" * 22)
        base = permutation_baseline(rows, FEATURES, TAIL_PCT)
        best = max(max(f["auc"], 1 - f["auc"]) for f, _ in results)
        print(f"  best real AUC (either direction):     {best:.3f}")
        print(f"  median best AUC on shuffled labels:   {base:.3f}")
        if best <= base + 0.02:
            print("  ⚠️  NO SIGNAL. The best feature does not beat what this search")
            print("      finds on pure noise. Report 'no signal' and stop -- do not")
            print("      ship a ranker on this.")
        else:
            print(f"  margin over noise: {best - base:+.3f}")
            print("  A feature is actionable only if it ALSO holds AUC >= 0.60 on")
            print("  the held-out half with monotone decile lift.")
    print()

    # ── D. SLOT COST ────────────────────────────────────────────────────────
    # Reconstruct how many positions were open on each trigger date from the
    # trade history, then value the triggers that fired while the book was full.
    print("── D. SLOT COST: what did a full book cost? " + "─" * 34)
    try:
        from config import MAX_POSITIONS
    except Exception:
        MAX_POSITIONS = 5
    # A SLOT is occupied by a TICKER, not by a trade_history row. Scale-outs and
    # same-day round trips write several rows for one position (NTRA has three),
    # so counting rows reports more positions open than MAX_POSITIONS allows --
    # which is both impossible and a silent corruption of this whole section.
    spans = []
    for t in trades:
        b, sd = _date(t.get("buy_date")), _date(t.get("sell_date"))
        if b:
            spans.append(((t.get("ticker") or "").upper(), b,
                          sd or _today_et()))

    blocked, open_slots = [], []
    for r in trig:
        td = _date(r.get("triggered_at"))
        if not td:
            continue
        n_open = len({tk for tk, b, sd in spans if b <= td <= sd})
        (blocked if n_open >= MAX_POSITIONS else open_slots).append(
            (r, n_open))
    print(f"  MAX_POSITIONS = {MAX_POSITIONS}")
    for label, grp in (("book FULL", blocked), ("slot free", open_slots)):
        if not grp:
            print(f"  {label:<12} n=0")
            continue
        g = [float(r[OUTCOME]) for r, _ in grp]
        big = [x for x in g if x >= TAIL_PCT]
        print(f"  {label:<12} n={len(g):3}  median {statistics.median(g):+6.2f}%  "
              f">=+{TAIL_PCT:.0f}%: {len(big):3}")
    if blocked:
        g = [float(r[OUTCOME]) for r, _ in blocked]
        print()
        print(f"  {len([x for x in g if x >= TAIL_PCT])} trigger(s) reaching "
              f"+{TAIL_PCT:.0f}% fired while the book was full and could not be")
        print("  bought. THIS is the opportunity cost of holding winners longer,")
        print("  and it is the blocking precondition on `ladder-width-runon`.")
        print("  NOTE: this is an upper bound -- it assumes every blocked trigger")
        print("  was otherwise buyable (passed the market gate, had cash, was not")
        print("  in cooling-off). Treat it as a ceiling on the cost, not the cost.")
    print()
    print("=" * 78)
    print("Record the verdict in decisions/provisional_decisions.json under")
    print("`entry-quality-right-tail` -> history[], then close the GitHub issue.")
    print("=" * 78)


if __name__ == "__main__":
    main()
