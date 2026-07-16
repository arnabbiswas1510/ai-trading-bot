-- ============================================================================
-- Migration: 3-Tier Plateau Rotation -- portfolio_positions new columns
-- Run once in Supabase SQL Editor (Dashboard -> SQL Editor -> New Query)
-- All columns are nullable so existing rows are unaffected.
-- ============================================================================

-- 1. days_since_hwm
--    Trading days elapsed since the last high-water-mark date (hwm_date).
--    Written by execution_agent.py at 3:45 PM EOD for every open position.
--    Drives the Plateau Days progress bar and all 3 tier checks.
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS days_since_hwm INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.days_since_hwm IS
  'NYSE trading days elapsed since hwm_date. Refreshed each EOD cycle. '
  'Tier 1 threshold: 3 days. Tier 2 threshold: 5 days. Tier 3 (auto-sell): 7 days.';


-- 2. live_rs_score
--    Relative Strength score computed fresh at each EOD cycle via FMP API.
--    Compared against entry_rs_score to detect RS decay (Tier 1 gate).
--    NULL for positions where FMP returned an error or entry_rs_score is absent.
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS live_rs_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.live_rs_score IS
  'Live RS score fetched from FMP at EOD. Tier 1 fires when '
  '(entry_rs_score - live_rs_score) >= RS_DECAY_GATE (default 15 pts) '
  'and days_since_hwm >= RS_DECAY_MIN_DAYS (default 3).';


-- 3. top_trigger_score
--    The highest final_score among unowned tickers in daily_triggers
--    as of the last EOD run. NULL when no fresh triggers exist that day.
--    Compared against entry_final_score to detect a score-upgrade opportunity (Tier 2).
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS top_trigger_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.top_trigger_score IS
  'Best final_score from unowned daily_triggers at last EOD. '
  'Tier 2 fires when (top_trigger_score - entry_final_score) >= SCORE_UPGRADE_GAP (default 20) '
  'and days_since_hwm >= SCORE_GAP_MIN_DAYS (default 5).';


-- 4. rotation_recommendation
--    Set by execution_agent.py when Tier 1 or Tier 2 conditions are met.
--    Cleared when the user approves/dismisses via the UI, or after Tier 3 auto-sell.
--    Valid values: 'TIER_1', 'TIER_2', NULL (no pending recommendation).
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS rotation_recommendation TEXT DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.rotation_recommendation IS
  'Pending rotation recommendation. TIER_1 = RS decay gate crossed. '
  'TIER_2 = score-upgrade opportunity. NULL = no action pending. '
  'Tier 3 auto-sells without setting this column.';


-- Optional index for fast UI polling (safe to omit at this scale)
CREATE INDEX IF NOT EXISTS idx_portfolio_positions_rotation_rec
  ON portfolio_positions (rotation_recommendation)
  WHERE rotation_recommendation IS NOT NULL;


-- ============================================================================
-- Verification query -- run after migration to confirm all 4 columns exist:
-- ============================================================================
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'portfolio_positions'
--   AND column_name IN (
--     'days_since_hwm', 'live_rs_score',
--     'top_trigger_score', 'rotation_recommendation'
--   )
-- ORDER BY column_name;
