# Write each forward-return horizon as soon as it matures

**Date:** 2026-09-17
**Status:** Accepted

## Context

`decisions/2026-09-17_failure-penalty-disabled.md` disabled the breakout failure
penalty after measuring that it scored **every** trade identically — mean 20.0 on
winners (n=10) and 20.0 on losers (n=6). Re-enabling it requires refitting its
match tolerances against forward outcomes, and that was recorded as blocked:
`trigger_history` had 16 of 233 rows labelled, **none of them BREAKOUT**.

The obvious reading was that the weekly backfill was failing. It was not. All
five scheduled runs succeeded honestly, and four of them had genuinely nothing to
do:

| Run | Cutoff (`run − 34d`) | Eligible |
|---|---|---|
| 08-16 | 07-13 | none — archive starts 08-14 |
| 08-23 | 07-20 | none |
| 08-30 | 07-27 | none |
| 09-06 | 08-03 | none |
| 09-13 | 08-10 | none |

Two settings combined to starve the archive:

1. **`SETTLE_DAYS = 34`** gated *selection*. A trigger was invisible for 34
   calendar days.
2. **`MIN_BARS_REQUIRED = MAX_HORIZON = 20`** discarded a row outright unless all
   20 sessions existed.

Because `fwd_1d`, `fwd_5d` and `fwd_20d` were written as a single all-or-nothing
unit, the two short horizons were withheld for a month by the long one — even
though `compute_outcomes()` already computed each horizon independently and
returned `None` for the ones that were not ready.

Measured 2026-09-17 across the 233-row archive:

| Horizon | Sessions needed | Rows measurable | BREAKOUT measurable |
|---|---|---|---|
| `fwd_1d` | 1 | **222/233** | **34/37** |
| `fwd_5d` | 5 | **185/233** | **24/37** |
| `fwd_20d` | 20 | 16/233 | 0/37 |
| *actually written* | — | *16/233* | *0/37* |

The refit was not blocked by data scarcity. It was blocked by an implementation
choice. And the horizons being withheld are the *relevant* ones: the six trades
the penalty called failures were day-0/day-1 stop-outs of −0.17% to −1.62% —
exactly what `fwd_1d` and `fwd_5d` measure. `fwd_20d` is the least informative
horizon for that question and was the only one gating the others.

## Decision

**Write each horizon as soon as it matures; revisit the row until it is complete.**

- Selection moves to a new `MIN_SETTLE_DAYS = 3` — enough for `fwd_1d` to clear a
  weekend. `SETTLE_DAYS = 34` is retained, now meaning *completion*.
- `MIN_BARS_REQUIRED` becomes `min(HORIZONS)` = 1. A new
  `COMPLETE_BARS_REQUIRED = max(HORIZONS)` = 20 defines "done".
- **`outcomes_computed_at` becomes a completion latch, stamped only when all 20
  sessions exist.** This is the load-bearing part. Resumability keys on that
  column being NULL, so stamping a partial row would retire it permanently and
  silently cap every row at its first measurement — strictly worse than the
  behaviour being fixed.
- Partial writes **strip NULLs** from the payload, so a later pass can only add
  knowledge, never erase an earlier pass's work.

### The 20d-named path metrics are withheld until 20 sessions exist

`max_gain_20d_pct`, `max_drawdown_20d_pct` and `ever_above_entry` were previously
computed over whatever bars were available. Under partial measurement that would
write a 5-bar drawdown under a `20d` column name.

That is not a smaller version of the same number; it is a different quantity, and
it would bias every study that reads the column toward understating risk — while
looking perfectly plausible. They are now gated on completeness.

## Results

Dry run then live run, 2026-09-17:

```
206 trigger row(s) awaiting outcomes.
Outcomes written: 206 (172 partial, will be revisited) | skipped: 0
```

| Column | Before | After | BREAKOUT before → after |
|---|---|---|---|
| `fwd_1d_pct` | 16 | **222** | 0 → **34/37** |
| `fwd_5d_pct` | 16 | **200** | 0 → **25/37** |
| `fwd_20d_pct` | 16 | 50 | 0 → 2/37 |
| stamped complete | 16 | 50 | — |

Integrity check after the live write: **0** rows carry a 20d path metric without a
20d return.

The 172 partial rows keep `outcomes_computed_at` NULL and will be topped up on
subsequent runs, reaching completeness on the original 34-day schedule.

## Consequences

- The failure-penalty refit is unblocked now rather than in mid-October. It has
  34 BREAKOUT rows at `fwd_1d` and 25 at `fwd_5d`, on the horizons that actually
  match the rule's failure mode.
- The weekly job now does real work every week instead of idling, and each run is
  larger (it revisits incomplete rows). Cost is FMP calls on a Sunday with no
  market contention; `skipped: 0` on a 206-row run.
- `fwd_20d`-based studies are unaffected in meaning — that column still appears
  only when genuinely mature. Studies that filter on `fwd_20d_pct IS NOT NULL`
  keep their previous semantics exactly.
- **A study reading `fwd_5d_pct` must not assume `fwd_20d_pct` is present.** Rows
  are now legitimately half-measured. `research/rs_percentile_review.py` already
  filters per column and is unaffected.
- Lowering `SETTLE_DAYS` was considered and rejected as the fix: it buys ~4 days,
  where per-horizon writing buys weeks, and it would have risked measuring a 20-day
  window short.

## Related

- `decisions/2026-09-17_failure-penalty-disabled.md` — the refit this unblocks.
- `decisions/2026-09-17_rs-percentile-shadow-column.md` — the 2026-10-19 RS review
  reads these same columns and benefits identically.
- `tests/test_backfill_outcomes.py` — pins the stamping contract and the
  20d-naming rule.
