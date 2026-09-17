# Relative strength is measured but not ranked — shadow `rs_percentile` column

**Date:** 2026-09-17
**Status:** Accepted — shadow mode, no behavioural change. Revisit 2026-10-19.

## Context

A post-mortem on five recent trades widened into a question the user framed as
"the most critical optimization now": *can the picker be made better at picking
winners?* The win rate is 44%.

Scanning all 233 archived rows in `trigger_history` surfaced a concrete defect in
the scorer rather than a vague one.

`compute_rs_score(stock_12w_return, spy_12w_return)` in `scoring.py` clips to a
flat **100** for any excess return at or above **+10%** vs SPY. The watchlist is
already pre-filtered to CAN SLIM growth names trading near their 52-week highs,
so almost every candidate clears that bar comfortably:

| `rs_score` | Rows | Share |
|---|---|---|
| exactly 100 | 176 | **76%** |
| everything else | 57 | 24% |

A component that is identical for three out of every four candidates cannot rank
them. `rs_score` carries 10% of `compute_final_score`, but for most of the field
it contributes a constant, so its *discriminating* weight is close to zero. Two
names as different as CDNA (+121% over 12 weeks) and a stock at +19% are
literally the same number to the live scorer.

That much is established. The obvious repair — replace the clipped score with a
percentile rank so the strongest name in each cohort actually outranks the
weakest — is **not** established, and this is the crux of the decision.

### Every available sample says the repair might point the wrong way

Within a universe *already filtered for momentum*, more relative strength was
associated with worse outcomes, not better:

1. **corr(`rs_score`, forward 20-day return) = −0.68** on the 16 triggers
   labelled so far.
2. **Losers entered stronger than winners** across 48 closed trades: mean
   12-week entry return 31.1% for losers vs 28.1% for winners.
3. **The single largest loss in the book entered the strongest.** CDNA came in
   at +121% over 12 weeks — the most extended name in the sample — and closed
   −$1,539.

This is a coherent story, not noise-shaped nonsense: past a point, high
trailing relative strength stops meaning "leadership" and starts meaning
"extended", and extended names are exactly what the 2026-09-09 buy-price drift
guard exists to avoid.

It is also a *weak* story. Point 1 rests on 16 rows that all share a single
trigger date (2026-08-14), which is effectively **one observation, not sixteen**.
Points 2 and 3 are dominated by the pre-Prove-It era.

So the honest summary is: the defect is real, the fix's **sign is unknown**, and
what evidence exists leans negative. Shipping a "correct" percentile that
promotes the highest-RS candidate could plausibly buy more CDNAs.

### The constraint that decides it

The user is explicit on two points: they are **rigid about cutting losers early**
after past large losses, and after two profitable weeks they do **not want to
jinx it**. Post-Prove-It performance (n=17) is net +$3,527, 59% win rate, worst
single loss −$280 — against a pre-era net of −$6,439 with eleven losses over
$500.

Changing the ranking that selects trades, on an unknown sign, against that
backdrop, to fix a defect whose repair direction is contradicted by every sample
we hold, is not a good trade.

## Decision

**Measure it without trading on it.**

Three nullable, research-only columns are added to `daily_triggers` and
`trigger_history`:

| Column | Meaning |
|---|---|
| `rs_12w_return` | The stock's raw 12-week return, as fed to `compute_rs_score` |
| `rs_excess_return` | 12-week excess vs SPY, **unclipped** |
| `rs_percentile` | 1–99 rank of `rs_excess_return` within that run's cohort |

`compute_rs_score` and `compute_final_score` are **not modified**. No buy gate,
sort order, position size or exit rule reads any of the three. The screener
computes and stores them; `backfill_trigger_outcomes.py` will pair them with
forward 5/10/20-day returns on its existing weekly schedule.

By approximately **2026-10-19** every one of the 233 archived triggers will carry
a labelled outcome (`SETTLE_DAYS = 34`, archive starts 2026-08-14), giving a real
multi-date sample. That date is registered in
`decisions/provisional_decisions.json`, so the monthly `decision_review.yml` cron
opens a GitHub issue rather than relying on anyone remembering.

### Design notes

- **Rank within the day's cohort, not the whole watchlist.** `final_score` exists
  to sort one morning's candidates against each other for a limited number of
  slots; that is the comparison that actually decides which ticker gets bought,
  so it is the comparison worth measuring. Full-watchlist ranking would require
  `check_technical_breakout` to return data for non-triggering tickers.
- **Store the raw inputs, not just the derived rank.** `rs_excess_return` is kept
  unclipped so the archive can be re-ranked later under a scheme nobody has
  thought of yet. Storing only the percentile would lock in today's guess about
  which transformation matters.
- **The insert cannot brick the screener.** If `migrations/20260917_add_rs_percentile.sql`
  has not been applied, `write_triggers_to_supabase` catches the rejection,
  prints a loud warning and retries with the three columns stripped. Losing a
  research annotation is survivable; losing a morning's buy candidates is not.
  This follows the precedent already set by `trigger_audit._upsert`, which is
  deliberately non-fatal.
- **A test pins the guarantee.** `tests/test_rs_percentile.py` asserts
  `compute_final_score`'s signature contains none of the shadow columns. If
  someone later wires the percentile into live scoring, that test fails and this
  ADR's premise is void.

## Consequences

- **No change to what the bot trades.** That is the point.
- One extra FMP-free computation per trigger (both inputs are already fetched).
- `migrations/20260917_add_rs_percentile.sql` must be applied in the Supabase SQL Editor.
  Until then the shadow columns are silently dropped and nothing else changes.
- The 2026-10-19 review can give a *negative* answer, and that is a real
  outcome: evidence that high relative strength predicts worse forward returns
  within this universe would argue for an RS **ceiling**, which is the opposite
  of the naive fix and would never have been found by shipping it.

## Alternatives rejected

- **Fix `compute_rs_score` directly.** Rejected: unknown sign, contradicted by
  all three available samples, and it changes live selection during a profitable
  run the user asked not to disturb.
- **Raise the clip from +10% to +50%.** A smaller version of the same unvalidated
  bet, and still arbitrary.
- **Drop the RS component.** Also a live behavioural change, and it discards a
  CAN SLIM primitive on the strength of 16 same-day rows.
- **Do nothing.** Rejected because the archive currently throws away
  `rs_excess_return` entirely — without capturing it, the question is
  unanswerable in 2026-10 for exactly the same reason it is unanswerable today.

## See also

- `decisions/2026-09-09_buy-price-drift-guard.md` — already fixed the
  gap-chasing/extension failure mode that the pre-era loser analysis surfaced.
- `docs/technical_triggers.md` — describes the live scoring path.
- `docs/retired_code.md` — nothing was deleted by this change.
