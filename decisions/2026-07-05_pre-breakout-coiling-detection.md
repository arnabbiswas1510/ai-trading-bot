# Decision: Pre-Breakout (VCP/Handle) Detection as Second Pass

**Date:** 2026-07-05 (approx)
**Commit:** `190afeb`
**Status:** Implemented

## Problem

The screener only detected confirmed breakouts (close within 2% of 52w high +
volume surge ≥ 40%). This missed stocks in the **coiling / VCP (Volatility
Contraction Pattern) / handle phase** — the setup that precedes the breakout.
These are high-conviction entries because you can buy before the breakout crowd
and catch the full move from the pivot.

## Decision

Add a second-pass screener pass producing `trigger_type = 'PRE_BREAKOUT'`
triggers alongside existing `trigger_type = 'BREAKOUT'` triggers.

**Five gates (all must pass):**
1. Within 8% of 52-week rolling high (proximity gate)
2. Close above SMA-50 (trend gate)
3. RS score ≥ 40 (relative strength gate)
4. Volume contracting — recent volume below 50-day average (contraction gate)
5. 2 of last 3 closes rising (uptrend gate)

**Quality score** (0–100) weights: proximity (40%) + contraction (40%) +
uptrend momentum (20%).

**AI boost:** `ai_evaluator.py` applies a +10pt bonus to `final_score` for
`PRE_BREAKOUT` triggers after all 5 component scores are computed.

**Telegram badge:** Notifications distinguish "✅ Confirmed Breakout" vs
"🔄 Coiling / Pre-Breakout" clearly.

## Why these specific gates?

- The 8% proximity threshold is tight enough to catch near-pivot stocks but
  loose enough not to miss handles that form slightly below the pivot.
- Contracting volume is the defining characteristic of a coil — expansion comes
  on the breakout itself.
- 2/3 rising closes is a soft uptrend check that tolerates one red day (common
  in handles).

## Files changed

- `technical_screener.py` — `check_pre_breakout_coil()` function, second pass
- `technical_screener.py` — `compute_pre_breakout_quality_score()`
- `ai_evaluator.py` — +10pt PRE_BREAKOUT bonus
- `telegram_notifier.py` — badge per trigger type
- `migrations/add_trigger_type.sql` — `trigger_type` column, default `'BREAKOUT'`
- `tests/test_pre_breakout.py` — 18 tests covering all 5 gates + quality score + boost
