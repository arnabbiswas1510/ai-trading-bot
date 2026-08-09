-- Migration: forward-return outcome columns on trigger_history
-- Run once in Supabase SQL Editor. Requires add_trigger_history.sql first.
--
-- WHY THIS EXISTS
-- ---------------
-- trigger_history stores what the screener SAW (scores, grades, rationale) but
-- nothing about what subsequently HAPPENED. Without outcomes the archive cannot
-- answer the question it was built for: does final_score predict forward return?
--
-- These columns are populated by backfill_trigger_outcomes.py, run weekly from
-- GitHub Actions, once each measurement window has fully elapsed.
--
-- DESIGN NOTES
-- ------------
-- 1. Entry reference is the NEXT session's OPEN, not the trigger close. The bot
--    buys at market open the following morning, so measuring from the trigger
--    close would credit the strategy with an overnight gap it never captured.
--
-- 2. Benchmark-relative columns exist because a raw +5% in a +5% market is not
--    edge. Alpha is what distinguishes a good score from a rising tide.
--
-- 3. max_gain / max_drawdown matter as much as the endpoint return: they reveal
--    whether a trade ever worked at all, which is the same question the Thesis
--    Stop asks via closed_above_entry.

ALTER TABLE trigger_history
  ADD COLUMN IF NOT EXISTS entry_ref_price      FLOAT,
  ADD COLUMN IF NOT EXISTS entry_ref_date       DATE,
  ADD COLUMN IF NOT EXISTS fwd_1d_pct           FLOAT,
  ADD COLUMN IF NOT EXISTS fwd_5d_pct           FLOAT,
  ADD COLUMN IF NOT EXISTS fwd_20d_pct          FLOAT,
  ADD COLUMN IF NOT EXISTS max_gain_20d_pct     FLOAT,
  ADD COLUMN IF NOT EXISTS max_drawdown_20d_pct FLOAT,
  ADD COLUMN IF NOT EXISTS ever_above_entry     BOOLEAN,
  ADD COLUMN IF NOT EXISTS bench_fwd_20d_pct    FLOAT,
  ADD COLUMN IF NOT EXISTS alpha_20d_pct        FLOAT,
  ADD COLUMN IF NOT EXISTS outcome_bars         INT,
  ADD COLUMN IF NOT EXISTS outcomes_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN trigger_history.entry_ref_price IS
  'Open of the first session AFTER triggered_at. The bot buys at market open the '
  'next morning, so measuring from the trigger close would credit an overnight '
  'gap the strategy never captured.';

COMMENT ON COLUMN trigger_history.alpha_20d_pct IS
  'fwd_20d_pct minus SPY over the identical window. A raw +5% in a +5% market is '
  'not edge; this is the column that tests the score.';

COMMENT ON COLUMN trigger_history.ever_above_entry IS
  'TRUE if the high ever exceeded entry_ref_price within the window. Mirrors the '
  'closed_above_entry latch used by the Thesis Stop.';

COMMENT ON COLUMN trigger_history.outcome_bars IS
  'Trading sessions actually available after entry. Guards against treating a '
  'partially-elapsed window as a complete 20-day result.';

COMMENT ON COLUMN trigger_history.outcomes_computed_at IS
  'NULL means not yet measured. backfill_trigger_outcomes.py selects on this, so '
  'the job is resumable and safe to re-run.';

-- The backfill selects unmeasured rows whose window has elapsed.
CREATE INDEX IF NOT EXISTS idx_trigger_history_pending_outcomes
    ON trigger_history (outcomes_computed_at, triggered_at)
    WHERE outcomes_computed_at IS NULL;
