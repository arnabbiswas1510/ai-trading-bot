# Decision: Breakout Verdict + Intraday Loss Minimiser

**Date:** 2026-06-28 (approx)
**Commit:** `0473d02`
**Status:** Implemented

## Problem

Once a position was opened there was no mechanism to distinguish "this is a
real breakout that will follow through" from "this was a fake breakout that
will reverse". Positions would run for 7 days on a timer regardless of what
price action said. This led to holding losing positions too long.

## Decision

**Day 3 EOD Breakout Verdict:**
- PASS: close > entry + 1% AND Day-3 volume ≥ 75% of average. Position
  continues normally.
- FAIL: either condition missed. Activates the Intraday Loss Minimiser for
  Day 4+.

**Intraday Loss Minimiser (Day 4+ after FAIL):**
- Each intraday cycle, if the position's intraday high ≥ 99.5% of entry price,
  sell on a 0.5% pullback from that intraday high.
- Hard fallback: force-sell on Day 7 if no qualifying intraday rally occurs.

**Rank and Replace:** Restricted to Day 7+ PASS positions only (previously
could trigger on Days 3–6 — removed).

## What was removed

- HWM Break-Even Stop (overly conservative, triggered on normal volatility)
- Mandatory 7-day time-stop for all positions (replaced by conditional logic)
- Days 3–6 Rank and Replace (too aggressive, sold good positions prematurely)
- Progress Deficit flag (added complexity without clear signal value)

## Why the specific thresholds?

- +1% close threshold: enough to confirm the move isn't pure noise but loose
  enough not to disqualify legitimate consolidations.
- 75% volume on Day 3: institutional participation confirmation without
  requiring a full repeat of Day 1 volume.
- 0.5% loss-minimiser trigger: tight enough to lock in near-entry exits without
  triggering on normal intraday spread noise.

## Files changed

- `execution_agent.py` — `check_breakout_verdict()`, `intraday_loss_minimiser()`
- `migrations/add_breakout_verdict.sql`
- `tests/test_breakout_verdict.py` — 10 tests
