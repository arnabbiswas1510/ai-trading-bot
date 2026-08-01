# Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser

**Date:** 2026-08-01  
**Status:** Accepted

## Problem

Losses were sometimes reaching the wider trailing-stop band before the existing
intraday loss minimiser could engage. The prior minimiser was gated to
`days_held >= 4` and `breakout_verdict == FAIL`, leaving early-hold windows with
limited downside control.

## Decision

1. Add an immediate Day 0-1 hard-loss kill-switch:
   - Sell when live price falls to `EARLY_LOSS_STOP_PCT` below entry (default 2.0%).
2. Activate the intraday pullback minimiser from Day 2 onward for all positions:
   - Remove the `breakout_verdict == FAIL` gate for the Day-2+ pullback trigger.
3. Keep Day-7 hard fallback scoped to FAIL-verdict positions:
   - Preserve existing behavior for forced exits to avoid broad forced liquidation
     of otherwise healthy positions.

## Rationale

- Day 0-1 captures immediate failed entries where false breakouts commonly show.
- Day 2+ pullback monitoring now protects all positions without waiting for the
  Day-3 verdict pathway.
- Retaining FAIL-only fallback limits behavior change scope and avoids accidental
  over-aggression on winners.

## Implementation

- `execution_agent.py`
  - Added `EARLY_LOSS_STOP_PCT` (default `0.02`)
  - Added `INTRADAY_MINIMISER_START_DAY` (default `2`)
  - Added Day 0-1 kill-switch in `monitor_portfolio_intraday()`
  - Broadened Day-2+ pullback minimiser to all positions
  - Kept Day-7 fallback under FAIL verdict
- `tests/test_breakout_verdict.py`
  - Updated minimiser expectation tests for Day-2+ universal behavior
  - Added Day-0/1 kill-switch tests
- `docs/sell_logic.md`, `.env.template`
  - Documented new runtime controls and behavior

## Consequences

- Faster loss containment on fresh entries.
- More early exits in choppy recoveries due to universal Day-2+ pullback checks.
- Existing FAIL-verdict fallback remains unchanged to reduce unintended churn.
