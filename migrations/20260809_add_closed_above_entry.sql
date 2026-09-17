-- Migration: follow-through latch for the Thesis Stop
-- Run once in Supabase SQL Editor.
--
-- The Thesis Stop exits breakouts that never worked: no close above entry AND
-- more than THESIS_STOP_ATR_MULT x ATR below it, between day 2 and day 5.
--
-- This column is what confines the rule to that population. Without it the
-- rule would degenerate into the old Intraday Loss Minimiser, which required
-- the intraday high to be AT OR ABOVE entry and therefore cut positions that
-- were already working — the reason it roughly halved expectancy and was
-- disabled (see decisions/2026-08-04_tune-exits-on-breakout-population.md).
--
-- Latches TRUE at the first EOD close above entry and is never cleared: a
-- breakout only has to confirm once.

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS closed_above_entry BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN portfolio_positions.closed_above_entry IS
  'TRUE once the position has closed above its entry price at least once. '
  'Latch for the Thesis Stop, which only fires while this is FALSE.';

-- Backfill: any position already showing a positive peak has demonstrably
-- traded above entry, so it must not be exposed to the thesis stop retroactively.
UPDATE portfolio_positions
   SET closed_above_entry = TRUE
 WHERE COALESCE(highest_unrealized_pct, 0) > 0;
