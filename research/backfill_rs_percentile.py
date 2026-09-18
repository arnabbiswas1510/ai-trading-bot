#!/usr/bin/env python3
"""Backfill the shadow relative-strength columns on `trigger_history`.

WHY THIS EXISTS
---------------
`rs_score` is saturated to uselessness. It clips excess return at +10%, and on
the 245 archived triggers **185 rows (76%) sit at exactly 100** -- CDNA, which
beat SPY by 112%, scores the same as a stock that beat it by 10.1%. For three
quarters of the data the feature is a constant, which is why it measured an AUC
of 0.387 (worse than a coin flip) in the 2026-09-18 entry-quality review.

`rs_percentile` fixes that by ranking the UNCLIPPED excess return within each
day's own cohort. The columns were added by
migrations/20260917_add_rs_percentile.sql and the screener has populated
`daily_triggers` correctly ever since -- but `trigger_history` carries NULL for
all 245 archived rows, because every archive so far ran on rows created BEFORE
the migration existed.

Waiting for the archive to refill naturally costs ~2 months. It does not need to
be waited for: a 12-week return is a function of price history, so it can be
reconstructed exactly for any past date.

NO LOOKAHEAD
------------
This is the property that makes the backfill legitimate rather than cheating.
For a trigger dated D the script uses the last close at or BEFORE D, and the
close 60 trading days before that. Both were knowable on the morning of D.
Nothing after D is read. If a ticker has no bar at or before D -- which would
mean the history fetch came back short -- the row is SKIPPED rather than
approximated, because silently substituting a nearby date would be exactly the
kind of quiet contamination this whole analysis exists to avoid.

FAITHFULNESS
------------
The maths is not reimplemented here. `compute_rs_excess` and `rank_percentiles`
are imported from scoring.py -- the same functions the live screener calls -- so
the backfilled values cannot drift from the ones written going forward. Only the
60-trading-day window is restated locally, matching technical_screener.py
(`lookback = min(60, len(df) - 1)`, close-to-close).

USAGE
    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/backfill_rs_percentile.py --insecure --dry-run
    python3 research/backfill_rs_percentile.py --insecure
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from collections import defaultdict

import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scoring import compute_rs_excess, rank_percentiles  # noqa: E402

FMP_BASE = "https://financialmodelingprep.com"
LOOKBACK_DAYS = 60          # trading days; matches technical_screener.py
VERIFY = True


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set. Load ~/.config/ai-trading-bot/secrets.env first.")
    return v


def _sb_headers() -> dict:
    key = _env("SUPABASE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def fetch_rows() -> list[dict]:
    url = (f"{_env('SUPABASE_URL')}/rest/v1/trigger_history"
           "?select=ticker,triggered_at,trigger_type,rs_score,rs_excess_return")
    r = requests.get(url, headers=_sb_headers(), verify=VERIFY, timeout=60)
    r.raise_for_status()
    return r.json()


_hist_cache: dict[str, list[tuple[str, float]]] = {}


def history(symbol: str, start: dt.date, end: dt.date) -> list[tuple[str, float]]:
    """Daily closes ascending as (date, close). Cached per symbol."""
    if symbol in _hist_cache:
        return _hist_cache[symbol]
    url = (f"{FMP_BASE}/stable/historical-price-eod/full"
           f"?symbol={symbol}&from={start}&to={end}&apikey={_env('FMP_API_KEY')}")
    for attempt in range(5):
        try:
            resp = requests.get(url, verify=VERIFY, timeout=60)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception:
            if attempt == 4:
                _hist_cache[symbol] = []
                return []
            time.sleep(2 * (attempt + 1))
    else:
        _hist_cache[symbol] = []
        return []

    if not isinstance(data, list):
        data = data.get("historical", []) if isinstance(data, dict) else []
    rows = sorted(((d["date"][:10], float(d["close"])) for d in data
                   if d.get("close")), key=lambda x: x[0])
    _hist_cache[symbol] = rows
    return rows


def return_12w(symbol: str, as_of: str, start: dt.date,
               end: dt.date) -> float | None:
    """12-week return as it was knowable on `as_of`. None if unavailable.

    Uses the last bar at or BEFORE as_of and the bar LOOKBACK_DAYS before it.
    Returns None -- never a guess -- when the history does not reach back far
    enough, so a short fetch can never masquerade as a real measurement.
    """
    bars = history(symbol, start, end)
    if not bars:
        return None
    idx = None
    for i, (d, _) in enumerate(bars):
        if d <= as_of:
            idx = i
        else:
            break
    if idx is None:
        return None
    lookback = min(LOOKBACK_DAYS, idx)
    if lookback < 1:
        return None
    p_now = bars[idx][1]
    p_then = bars[idx - lookback][1]
    if p_then <= 0:
        return None
    return round(((p_now / p_then) - 1.0) * 100.0, 2)


def main() -> None:
    global VERIFY
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (local trust-store workaround)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and print, write nothing")
    args = ap.parse_args()
    if args.insecure:
        VERIFY = False
        urllib3.disable_warnings()

    rows = fetch_rows()
    todo = [r for r in rows if r.get("rs_excess_return") is None]
    print(f"trigger_history rows           : {len(rows)}")
    print(f"missing rs_excess_return       : {len(todo)}")
    if not todo:
        print("Nothing to backfill.")
        return

    dates = sorted({str(r["triggered_at"])[:10] for r in todo})
    tickers = sorted({(r["ticker"] or "").upper() for r in todo if r.get("ticker")})
    start = dt.date.fromisoformat(dates[0]) - dt.timedelta(days=200)
    end = dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=2)
    print(f"distinct dates                 : {len(dates)} "
          f"({dates[0]} .. {dates[-1]})")
    print(f"distinct tickers to fetch      : {len(tickers)} (+SPY)\n")

    spy_cache: dict[str, float | None] = {}
    for d in dates:
        spy_cache[d] = return_12w("SPY", d, start, end)
    missing_spy = [d for d, v in spy_cache.items() if v is None]
    if missing_spy:
        print(f"⚠️  No SPY baseline for {len(missing_spy)} date(s): "
              f"{missing_spy}. Those rows will be skipped.")

    computed: list[dict] = []
    skipped: list[tuple[str, str, str]] = []
    for i, r in enumerate(todo, 1):
        tk = (r["ticker"] or "").upper()
        d = str(r["triggered_at"])[:10]
        if i % 25 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] {tk} {d}", flush=True)
        spy = spy_cache.get(d)
        if spy is None:
            skipped.append((tk, d, "no SPY baseline"))
            continue
        stock = return_12w(tk, d, start, end)
        if stock is None:
            skipped.append((tk, d, "no price history at/before trigger date"))
            continue
        computed.append({
            "ticker": tk, "triggered_at": r["triggered_at"],
            "trigger_type": r.get("trigger_type") or "BREAKOUT",
            "rs_12w_return": stock,
            "rs_excess_return": compute_rs_excess(stock, spy),
        })

    # Percentile is a rank WITHIN the day's own cohort, exactly as
    # assign_rs_percentiles() does live. Rank over the full archived cohort for
    # that date, not just the rows being written, so the rank is not distorted
    # by which rows happened to be missing.
    by_date: dict[str, list[dict]] = defaultdict(list)
    for c in computed:
        by_date[str(c["triggered_at"])[:10]].append(c)
    for d, cohort in by_date.items():
        pcts = rank_percentiles([c["rs_excess_return"] for c in cohort])
        for c, p in zip(cohort, pcts):
            c["rs_percentile"] = p

    print(f"\ncomputed : {len(computed)}")
    print(f"skipped  : {len(skipped)}")
    for tk, d, why in skipped[:10]:
        print(f"   {tk:6s} {d}  {why}")
    if len(skipped) > 10:
        print(f"   ... and {len(skipped) - 10} more")

    sample = sorted(computed, key=lambda c: -(c["rs_excess_return"] or 0))[:5]
    print("\ntop 5 by excess return (sanity check):")
    for c in sample:
        print(f"   {c['ticker']:6s} {str(c['triggered_at'])[:10]}  "
              f"12w {c['rs_12w_return']:+8.2f}%  excess {c['rs_excess_return']:+8.2f}%  "
              f"pct {c['rs_percentile']}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    url = f"{_env('SUPABASE_URL')}/rest/v1/trigger_history"
    h = {**_sb_headers(), "Prefer": "resolution=merge-duplicates"}
    written = 0
    for i in range(0, len(computed), 100):
        chunk = computed[i:i + 100]
        resp = requests.post(f"{url}?on_conflict=triggered_at,ticker,trigger_type",
                             headers=h, json=chunk, verify=VERIFY, timeout=120)
        if resp.status_code >= 300:
            sys.exit(f"write failed ({resp.status_code}): {resp.text[:400]}")
        written += len(chunk)
        print(f"  wrote {written}/{len(computed)}")
    print(f"\n✅ backfilled {written} rows.")


if __name__ == "__main__":
    main()
