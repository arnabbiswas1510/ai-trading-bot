#!/usr/bin/env python3
"""exit_shadow_review.py — reads exit_shadow_log and reports what the two
register-tracked exit candidates WOULD have done in production.

This is the `review_command` for the `exit-shadow-log` entry in
decisions/provisional_decisions.json. It reads the side-effect-free shadow log
written by monitor_portfolio_intraday() (see exit_shadow.py) and answers, per
candidate, how often it diverged from the live Prove-It rule and — crucially for
Q2 — whether those divergences were WICKS the live rule cut needlessly or GENUINE
give-backs the live rule correctly avoided.

The experiment and its HARD LIMIT are recorded in
decisions/2026-09-27_exit-shadow-log.md. Read that first. In particular: a live
shadow only sees divergence UP TO the real exit, so it captures the wick/timing
cost of these rules but NOT the run-on upside of holding a winner past the live
exit. Do not conclude "Q2 helps overall" from this data — only "Q2's
wick-avoidance helps/hurts". The overall hold-longer question stays with
ladder-width-runon and --runon.

Usage:
    python3 research/exit_shadow_review.py [--insecure] [--json]

Exit codes:
    0   ran cleanly and produced a verdict
    2   ran cleanly but the sample is too thin to interpret
    1   error — fail LOUD rather than quietly reporting nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import requests
import urllib3

# Below this many distinct open positions across more than one day the sample is
# one cohort wearing a disguise — report the counts and skip rather than read
# noise. Mirrors the MIN_DISTINCT_DATES guard in rs_percentile_review.py.
MIN_DISTINCT_POSITIONS = 12
MIN_DISTINCT_DAYS = 5

SHADOW_TABLE = "exit_shadow_log"


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"error: required environment variable {name} is not set. "
                 "Load it with: set -a && . ~/.config/ai-trading-bot/secrets.env && set +a")
    return val


def fetch_rows(insecure: bool) -> list:
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    out, offset, page = [], 0, 1000
    while True:
        try:
            r = requests.get(
                f"{url}/rest/v1/{SHADOW_TABLE}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params={"select": "*", "offset": offset, "limit": page,
                        "order": "cycle_ts.asc"},
                timeout=30, verify=not insecure,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            body = getattr(e.response, "text", "") or ""
            if SHADOW_TABLE in body or "42P01" in body or "PGRST" in body:
                sys.exit(
                    f"error: {SHADOW_TABLE} does not exist yet.\n"
                    "       Apply migrations/20260927_add_exit_shadow_log.sql in the "
                    "Supabase SQL Editor, then wait for\n"
                    "       the execution agent to run a few monitor cycles before "
                    "reviewing.\n"
                    f"       (server said: {body.strip()[:200]})")
            sys.exit(f"error: could not reach Supabase ({e})")
        batch = r.json()
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page


def _b(v):
    return bool(v) is True


def analyse(rows: list) -> dict:
    positions = set(r.get("ticker") for r in rows)
    days = set((r.get("cycle_ts") or "")[:10] for r in rows)

    q1_div = [r for r in rows if _b(r.get("q1_diverges"))]
    q2_div = [r for r in rows if _b(r.get("q2_diverges"))]

    # Q2 wick-vs-genuine-giveback split: cycles where live would cut but the 5%
    # trail held. We cannot know the eventual outcome per-cycle here without the
    # realised exit; report the raw divergence counts and the tickers, and defer
    # the outcome join to a follow-up once trades close.
    q2_held_where_live_cut = [
        r for r in q2_div
        if _b(r.get("live_would_exit")) and not _b(r.get("q2_would_exit"))
    ]

    by_ticker_q1 = defaultdict(int)
    for r in q1_div:
        by_ticker_q1[r.get("ticker")] += 1
    by_ticker_q2 = defaultdict(int)
    for r in q2_held_where_live_cut:
        by_ticker_q2[r.get("ticker")] += 1

    return {
        "rows": len(rows),
        "distinct_positions": len(positions),
        "distinct_days": len(days),
        "q1_divergence_cycles": len(q1_div),
        "q1_divergence_by_ticker": dict(by_ticker_q1),
        "q2_divergence_cycles": len(q2_div),
        "q2_held_where_live_cut_cycles": len(q2_held_where_live_cut),
        "q2_held_where_live_cut_by_ticker": dict(by_ticker_q2),
        "thin": (len(positions) < MIN_DISTINCT_POSITIONS
                 or len(days) < MIN_DISTINCT_DAYS),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification for Supabase")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    rows = fetch_rows(args.insecure)
    res = analyse(rows)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"exit_shadow_log: {res['rows']} rows over "
              f"{res['distinct_positions']} positions, {res['distinct_days']} days")
        print()
        print(f"Q1 (arm@+3%) — FULLY observable live")
        print(f"  divergence cycles: {res['q1_divergence_cycles']}")
        for t, n in sorted(res['q1_divergence_by_ticker'].items(),
                           key=lambda x: -x[1]):
            print(f"    {t}: {n}")
        print()
        print(f"Q2 (5% trail) — PARTIALLY observable (wick-avoidance only; "
              f"hold-longer upside NOT visible)")
        print(f"  divergence cycles: {res['q2_divergence_cycles']}")
        print(f"  held where live would cut: {res['q2_held_where_live_cut_cycles']}")
        for t, n in sorted(res['q2_held_where_live_cut_by_ticker'].items(),
                           key=lambda x: -x[1]):
            print(f"    {t}: {n}")
        print()
        if res["thin"]:
            print(f"VERDICT: sample too thin "
                  f"(need >= {MIN_DISTINCT_POSITIONS} positions and "
                  f">= {MIN_DISTINCT_DAYS} days). Skip, do not read noise.")

    if res["thin"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
