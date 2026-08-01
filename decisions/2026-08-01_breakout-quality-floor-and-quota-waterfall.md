# Decision: Breakout Quality Floor + Quota Waterfall

**Date:** 2026-08-01  
**Status:** Accepted

## Problem

The trigger pipeline admitted too many weak breakouts (low volume confirmation,
permissive RS gate, no buy-loop score floor), while fully strict filtering risks
producing too few daily candidates to keep the strategy active.

## Decision

1. Tighten strict breakout quality gates:
   - `VOLUME_SURGE_MIN` default `1.20 -> 1.50`
   - `RS_MIN_GATE` default `40 -> 50`
2. Disable pre-breakout score inflation by default:
   - `PRE_BREAKOUT_SCORE_BOOST` default `10 -> 0`
3. Add buy-loop score floors in `run_market_open_buys()`:
   - `MIN_TRIGGER_SCORE=60` for standard breakouts
   - `MIN_PRE_BREAKOUT_SCORE=65` for strict pre-breakout triggers
   - `MIN_RELAXED_TRIGGER_SCORE=58` for controlled relaxed candidates
4. Add a trigger-count quota waterfall in screener:
   - Generate strict `BREAKOUT` + strict `PRE_BREAKOUT` first
   - If total triggers are below `DAILY_TRIGGER_TARGET=4`, allow a controlled
     fallback `PRE_BREAKOUT_RELAXED` with bounded relaxed thresholds
5. Wire market-direction gate in buy loop and fail closed on data errors:
   - If `MARKET_DIRECTION_FILTER_ENABLED` and market is bearish (or check fails),
     no new buys are opened.

## Rationale

- Strict defaults reduce false positives from weak volume and lagging RS names.
- Removing automatic pre-breakout boost stops unconfirmed setups from outranking
  confirmed breakouts by construction.
- Quota fallback preserves daily activity without fully dropping quality gates.
- Buy-loop floors ensure low-conviction rows cannot pass just because they exist
  in `daily_triggers`.

## Consequences

- Fewer low-quality entries in weak or noisy tape.
- More consistent daily candidate count around the target of 4 through controlled
  relaxed coiling fallback.
- Market-direction data failures now block buys (safer fail mode).
