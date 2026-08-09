# Forward-return backfill for trigger_history (and the prune we did NOT build)

Date: 2026-08-09
Status: Accepted

## Context

`add_trigger_history.sql` and `trigger_decisions` now preserve every breakout
trigger and every buy/skip decision instead of truncating them. That fixes the
*input* side of the research problem, but a stored trigger on its own answers
nothing. To ask "is the AI score predictive?" or "is the fundamental screen
adding anything?" every archived trigger needs a **label**: what the stock
actually did afterwards.

Without labels, `trigger_history` is just a bigger version of the log we already
could not learn from.

## Decision

A weekly job (`backfill_trigger_outcomes.py`, Sundays 12:00 UTC) fetches daily
prices from FMP for settled triggers and writes forward-return columns back onto
`trigger_history`.

### Conventions (the part that is easy to get silently wrong)

1. **Entry = the next session's OPEN**, not the trigger close.
   The bot buys at market open the morning after a trigger fires. Measuring from
   the trigger close would credit the strategy with an overnight gap it never
   captured — flattering exactly the breakouts that gapped away from us.

2. **`fwd_Nd_pct` = close of the Nth session OF HOLDING, entry session counted
   as session 1.** So `fwd_1d` is the entry day's own close. This matches how
   day-by-day performance is discussed everywhere else in this project
   ("Day 1 -1.53%, Day 2 -2.18%") and, critically, matches the window the
   Thesis Stop operates on.

3. **Path metrics include the entry session.** `max_gain_20d_pct`,
   `max_drawdown_20d_pct` and `ever_above_entry` span the entry bar onward,
   because the position is held through that day. An earlier draft excluded it;
   the tests caught it. `ever_above_entry` is the direct empirical analogue of
   the Thesis Stop's `closed_above_entry` latch, so getting its window wrong
   would have biased any future evaluation of that stop.

4. **`alpha_20d_pct` vs SPY.** A raw +5% in a +5% market is not edge. Storing
   the benchmark leg alongside means the alpha can be recomputed if the
   benchmark choice is ever revisited.

5. **New-York dates.** The settle cutoff uses `America/New_York`, not the
   runner's clock. A GitHub Actions runner is UTC, where a naive local date
   after 8pm ET is already tomorrow and would shift the cutoff by a day. This is
   enforced by the repo-wide `test_timezone_usage` lint, which caught the first
   version of this file.

### Guards

- Only triggers older than `SETTLE_DAYS = 34` calendar days are processed — a
  proxy for 20 trading days with margin for holidays.
- Rows with fewer than `MIN_BARS_REQUIRED` bars are left unmeasured rather than
  recorded on a partial window, so a half-formed 20-day return never enters the
  dataset looking like a complete one.
- `compute_outcomes()` returns `None` (row skipped, retried next week) when the
  trigger date is not covered or no session follows it.
- `fetch_pending()` selects on `outcomes_computed_at IS NULL`, so the job is
  resumable and idempotent; a mid-run failure costs nothing.
- Prices are fetched once per ticker and reused across that ticker's pending
  rows, respecting FMP's daily request cap.

## Rejected: the 6-month rolling prune

The original request was a weekly Action deleting rows older than 6 months. It
was **not** built, for two reasons.

**The storage premise does not hold.** Measured row counts and sizes:

| table | rows/yr | size/yr |
|---|---|---|
| watchlist_history | 26,780 | ~8.0 MB |
| trigger_history | 2,340 | ~1.6 MB |
| trigger_decisions | 3,120 | ~0.8 MB |

~10 MB/year against a 500 MB free tier is roughly **50 years** of runway. There
is no problem to solve.

**A rolling window permanently caps the dataset.** With a 6-month prune the
archive never grows past 6 months, so the 12-month threshold analysis these
tables were built to enable becomes *permanently* unreachable — not delayed,
unreachable. The whole point of these tables is that they accumulate.

Note the irony that motivated this section: `save_screener_results()` already
carries a 56-day prune, and that same disk-thrift instinct is a direct
contributor to the unbacktestable-screen problem these three commits exist to
undo. Deleting research data to save megabytes is the expensive choice.

## Consequences

- After ~2 quarters, `trigger_history` supports questions that are currently
  unanswerable: does `ai_rating` correlate with `fwd_20d_pct`; do skipped
  candidates outperform bought ones; does `ever_above_entry` on day 1-2 predict
  eventual failure (the user's original observation about RSI and HWM).
- Adds `migrations/add_trigger_outcomes.sql` (additive; columns default NULL).
  Requires `migrations/add_trigger_history.sql` to be applied first — the new
  columns hang off that table.
- FMP is now a weekly dependency of the research pipeline, not just live pricing.

## Verification

- 22 unit tests; full suite 328 passing.
- Mutation test: changing entry from next-session open to trigger close fails
  4 tests.
- End-to-end against live FMP (NVDA, trigger 2026-01-05): entry 2026-01-06 @
  190.52, `fwd_20d_pct` -5.3433, independently recomputed by hand as -5.3433.
