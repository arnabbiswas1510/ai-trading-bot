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

# Calendar days to wait before measuring. 20 trading days is ~28 calendar days;
# the extra margin absorbs holidays so a window is never measured short.
SETTLE_DAYS = 34
# Sessions required after entry before a row is considered measurable at all.
MIN_BARS_REQUIRED = MAX_HORIZON


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
    """Triggers whose measurement window has fully elapsed and are unmeasured."""
    cutoff = (_today_ny() - datetime.timedelta(days=SETTLE_DAYS)).isoformat()
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
            print("❌ trigger_history missing — run migrations/add_trigger_history.sql "
                  "and migrations/add_trigger_outcomes.sql.")
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

    updated = skipped = 0
    for ticker, rows in sorted(by_ticker.items()):
        bars = fetch_prices(ticker, start, end, session)
        if not bars:
            skipped += len(rows)
            continue

        for row in rows:
            res = compute_outcomes(bars, row["triggered_at"], bench_bars)
            if not res or (res.get("outcome_bars") or 0) < MIN_BARS_REQUIRED:
                # Leave unmeasured so a later run can retry with more history,
                # rather than recording a short window as a complete result.
                skipped += 1
                continue

            res["outcomes_computed_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()

            if dry_run:
                print(f"   [dry-run] {ticker} {row['triggered_at']}: "
                      f"1d={res.get('fwd_1d_pct')} 5d={res.get('fwd_5d_pct')} "
                      f"20d={res.get('fwd_20d_pct')} alpha={res.get('alpha_20d_pct')}")
                updated += 1
                continue

            try:
                (client.table("trigger_history").update(res)
                 .eq("triggered_at", row["triggered_at"])
                 .eq("ticker", ticker)
                 .eq("trigger_type", row.get("trigger_type") or "BREAKOUT")
                 .execute())
                updated += 1
            except Exception as e:
                print(f"   ⚠️ {ticker} {row['triggered_at']}: update failed: {e}")
                skipped += 1

    print(f"✅ Outcomes written: {updated} | skipped: {skipped}")
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
