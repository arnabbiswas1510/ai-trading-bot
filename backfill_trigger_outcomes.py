"""
backfill_trigger_outcomes.py

Weekly job that links archived breakout triggers to what actually happened next.

WHY THIS EXISTS
---------------
`trigger_history` records what the screener SAW — final_score, ai_grade, the AI's
rationale — for every trigger, including the ones never bought. That is the
control group. But scores without outcomes answer nothing.

This job supplies the outcomes, which turns the archive into a test of the
question the AI evaluator has never been held to: **does final_score predict
forward return?** Today that is unanswerable, because `trade_history` only
contains candidates that passed every gate, so outcomes are observed solely for
high scores that were bought — a range-restricted sample.

DESIGN DECISIONS
----------------
1. **Entry reference is the NEXT session's open**, not the trigger close. The bot
   buys at market open the following morning. Measuring from the trigger close
   would credit the strategy with an overnight gap it never captured, which
   flatters every result and would be invisible in the output.

2. **Benchmark-relative.** A raw +5% during a +5% market is not edge. SPY over
   the identical window is fetched and `alpha_20d_pct` recorded. Judge the score
   on alpha, not on raw return.

3. **Only complete windows are measured.** A trigger is skipped until enough
   sessions have elapsed, and `outcome_bars` records how many were actually
   available, so a partially-elapsed window can never masquerade as a 20-day
   result.

4. **Resumable and idempotent.** Rows are selected on `outcomes_computed_at IS
   NULL`, so re-running is safe and an interrupted run resumes cleanly. Prices
   are fetched once per ticker and reused across that ticker's pending rows,
   which matters because the FMP plan has a daily request cap and no bulk
   endpoints.

Run: python backfill_trigger_outcomes.py [--dry-run] [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

import requests
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except ImportError:
    pass

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
_raw_key = os.environ.get("FMP_API_KEY")
FMP_API_KEY = _raw_key.strip().strip("'\"") if _raw_key else None
FMP_BASE_URL = "https://financialmodelingprep.com"

# Market dates must be New York, never the runner's local date. A GitHub Actions
# runner is UTC, so a naive local date after 8pm ET is already tomorrow and would
# shift the settle cutoff by a day.
NY_TZ = ZoneInfo("America/New_York")


def _today_ny():
    return datetime.datetime.now(NY_TZ).date()


BENCHMARK = "SPY"
HORIZONS = (1, 5, 20)
MAX_HORIZON = max(HORIZONS)

# Calendar days after which the LONGEST horizon is measurable, and therefore the
# point at which a row is considered COMPLETE. 20 trading days is ~28 calendar
# days; the extra margin absorbs holidays so a window is never measured short.
SETTLE_DAYS = 34

# Calendar days after which the SHORTEST horizon is measurable. fwd_1d needs a
# single session, so three calendar days clears a weekend.
#
# WHY THESE ARE SEPARATE (measured 2026-09-17)
# --------------------------------------------
# Selection used to run off SETTLE_DAYS alone, and a row was discarded outright
# unless all 20 sessions existed. Because fwd_1d, fwd_5d and fwd_20d were written
# as one all-or-nothing unit, the two short horizons were withheld for a month by
# the long one. On 2026-09-17 that left 16 of 233 archived triggers measured and
# **zero** BREAKOUT rows, while the data already supported:
#
#     fwd_1d   222/233 rows   34/37 BREAKOUT
#     fwd_5d   185/233 rows   24/37 BREAKOUT
#     fwd_20d   16/233 rows    0/37 BREAKOUT   <- the only one actually mature
#
# That gap is what blocked refitting the breakout failure penalty, whose "failures"
# are day-0/day-1 stop-outs -- exactly what fwd_1d and fwd_5d measure.
# Rows are now revisited until complete; see decisions/2026-09-17_per-horizon-outcomes.md.
MIN_SETTLE_DAYS = 3

# Sessions required before a row is worth writing at all: the shortest horizon.
MIN_BARS_REQUIRED = min(HORIZONS)
# Sessions required before a row is COMPLETE and stops being revisited.
COMPLETE_BARS_REQUIRED = MAX_HORIZON


def _pct(a, b):
    return None if not b else round(((a / b) - 1.0) * 100.0, 4)


def fetch_prices(ticker, start, end, session=None):
    """Daily OHLCV ascending, or [] on failure. Never raises."""
    if not FMP_API_KEY:
        return []
    url = (f"{FMP_BASE_URL}/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={start}&to={end}&apikey={FMP_API_KEY}")
    getter = (session or requests).get
    for attempt in range(3):
        try:
            r = getter(url, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            bars = data.get("historical", data) if isinstance(data, dict) else data
            if not isinstance(bars, list):
                return []
            # FMP returns newest-first; downstream logic assumes ascending.
            return sorted(bars, key=lambda b: b.get("date", ""))
        except Exception as e:
            if attempt == 2:
                print(f"   ⚠️ {ticker}: price fetch failed: {e}")
                return []
            time.sleep(2 ** attempt)
    return []


def compute_outcomes(bars, triggered_at, bench_bars=None):
    """Forward returns measured from the first session AFTER triggered_at.

    CONVENTIONS (both are easy to get subtly wrong, and neither failure would
    announce itself — the output would simply be plausible and wrong):

    * Entry is that session's OPEN, because the bot buys at market open the
      morning after a trigger. Measuring from the trigger close would credit an
      overnight gap the strategy never captured.

    * `fwd_Nd_pct` is the close of the Nth session OF HOLDING, with the entry
      session counted as session 1. So fwd_1d is the entry day's own close —
      matching how day-by-day position performance is discussed everywhere else
      in this project ("Day 1 -1.53%, Day 2 -2.18%").

    * Path metrics INCLUDE the entry session. The position is held through that
      day, so its high and low are part of the experience.

    Returns None when the trigger date is not covered or no session follows it,
    so the row stays unmeasured rather than being recorded on a wrong basis.
    """
    after = [b for b in bars if b.get("date", "") > triggered_at]
    if not after:
        return None

    entry = after[0]
    entry_price = float(entry.get("open") or 0) or float(entry.get("close") or 0)
    if entry_price <= 0:
        return None

    # Sessions 1..MAX_HORIZON of holding; forward[0] IS the entry session.
    forward = after[:MAX_HORIZON]
    out = {
        "entry_ref_price": round(entry_price, 4),
        "entry_ref_date": entry.get("date"),
        "outcome_bars": len(forward),
    }

    for h in HORIZONS:
        out[f"fwd_{h}d_pct"] = (_pct(float(forward[h - 1].get("close") or 0), entry_price)
                                if len(forward) >= h else None)

    highs = [float(b.get("high") or b.get("close") or 0) for b in forward]
    lows = [float(b.get("low") or b.get("close") or 0) for b in forward]
    highs = [h for h in highs if h > 0]
    lows = [l for l in lows if l > 0]

    # These three carry "20d" semantics, so they are written ONLY once all 20
    # sessions exist. A max drawdown taken over 5 bars is not a small version of
    # the 20-bar figure -- it is a different quantity, and storing it under the
    # 20d name would silently understate risk in every study that reads it.
    complete = len(forward) >= COMPLETE_BARS_REQUIRED
    if complete:
        out["max_gain_20d_pct"] = _pct(max(highs), entry_price) if highs else None
        out["max_drawdown_20d_pct"] = _pct(min(lows), entry_price) if lows else None
        # Mirrors the Thesis Stop's closed_above_entry latch: did it ever work?
        out["ever_above_entry"] = bool(highs and max(highs) > entry_price)

    if bench_bars and out.get("fwd_20d_pct") is not None:
        b = compute_outcomes(bench_bars, triggered_at)
        bench = b.get("fwd_20d_pct") if b else None
        out["bench_fwd_20d_pct"] = bench
        out["alpha_20d_pct"] = (round(out["fwd_20d_pct"] - bench, 4)
                                if bench is not None else None)

    return out


def fetch_pending(client, limit=None, force=False):
    """Triggers with at least one measurable horizon that are not yet complete.

    Selection runs off MIN_SETTLE_DAYS, not SETTLE_DAYS, so a row is picked up as
    soon as its SHORTEST horizon is measurable. Incomplete rows keep
    `outcomes_computed_at` NULL and are therefore re-selected on every subsequent
    run until all 20 sessions exist, at which point they are stamped and drop out.
    """
    cutoff = (_today_ny() - datetime.timedelta(days=MIN_SETTLE_DAYS)).isoformat()
    q = (client.table("trigger_history")
         .select("triggered_at,ticker,trigger_type,outcomes_computed_at")
         .lte("triggered_at", cutoff))
    if not force:
        q = q.is_("outcomes_computed_at", "null")
    q = q.order("triggered_at", desc=False)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def run(dry_run=False, limit=None, force=False):
    if not SUPABASE_URL or not SUPABASE_KEY or not FMP_API_KEY:
        print("❌ Missing SUPABASE_URL, SUPABASE_KEY or FMP_API_KEY.")
        return 1

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        pending = fetch_pending(client, limit=limit, force=force)
    except Exception as e:
        if "trigger_history" in str(e) or "PGRST" in str(e):
            print("❌ trigger_history missing — run migrations/20260809_add_trigger_history.sql "
                  "and migrations/20260809_add_trigger_outcomes.sql.")
            return 1
        print(f"❌ Could not read trigger_history: {e}")
        return 1

    if not pending:
        print("✅ No triggers awaiting outcome measurement.")
        return 0

    print(f"📊 {len(pending)} trigger row(s) awaiting outcomes.")

    by_ticker = {}
    for row in pending:
        by_ticker.setdefault(row["ticker"], []).append(row)

    dates = [r["triggered_at"] for r in pending if r.get("triggered_at")]
    start = min(dates)
    end = (datetime.date.fromisoformat(max(dates))
           + datetime.timedelta(days=SETTLE_DAYS + 10)).isoformat()

    session = requests.Session()
    bench_bars = fetch_prices(BENCHMARK, start, end, session)
    if not bench_bars:
        print(f"⚠️ No {BENCHMARK} data — alpha will be NULL for this batch.")

    updated = skipped = partial = 0
    for ticker, rows in sorted(by_ticker.items()):
        bars = fetch_prices(ticker, start, end, session)
        if not bars:
            skipped += len(rows)
            continue

        for row in rows:
            res = compute_outcomes(bars, row["triggered_at"], bench_bars)
            bars_have = (res or {}).get("outcome_bars") or 0
            if not res or bars_have < MIN_BARS_REQUIRED:
                # Not even the shortest horizon is measurable yet. Leave the row
                # untouched so a later run retries with more history.
                skipped += 1
                continue

            complete = bars_have >= COMPLETE_BARS_REQUIRED
            if complete:
                # Only a complete row is stamped. While the stamp stays NULL the
                # row is re-selected next run and its longer horizons filled in.
                res["outcomes_computed_at"] = datetime.datetime.now(
                    datetime.timezone.utc).isoformat()

            # Never write NULL over a column: a partial row must ADD what it now
            # knows, not erase what a previous pass established.
            payload = {k: v for k, v in res.items() if v is not None}

            if dry_run:
                state = "complete" if complete else f"partial {bars_have}/{COMPLETE_BARS_REQUIRED}b"
                print(f"   [dry-run] {ticker} {row['triggered_at']} [{state}]: "
                      f"1d={res.get('fwd_1d_pct')} 5d={res.get('fwd_5d_pct')} "
                      f"20d={res.get('fwd_20d_pct')} alpha={res.get('alpha_20d_pct')}")
                updated += 1
                partial += 0 if complete else 1
                continue

            try:
                (client.table("trigger_history").update(payload)
                 .eq("triggered_at", row["triggered_at"])
                 .eq("ticker", ticker)
                 .eq("trigger_type", row.get("trigger_type") or "BREAKOUT")
                 .execute())
                updated += 1
                partial += 0 if complete else 1
            except Exception as e:
                print(f"   ⚠️ {ticker} {row['triggered_at']}: update failed: {e}")
                skipped += 1

    print(f"✅ Outcomes written: {updated} ({partial} partial, will be revisited) "
          f"| skipped: {skipped}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print without writing.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap rows processed (FMP has a daily request cap).")
    ap.add_argument("--force", action="store_true",
                    help="Recompute rows that already have outcomes.")
    args = ap.parse_args()
    sys.exit(run(dry_run=args.dry_run, limit=args.limit, force=args.force))


if __name__ == "__main__":
    main()
