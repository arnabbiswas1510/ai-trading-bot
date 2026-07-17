-- Migration: Add hwm_rs_score column to portfolio_positions
--
-- hwm_rs_score stores the RS score on the day a position last made a new HWM.
-- Rule 1 (RS Decay) compares live_rs_score vs hwm_rs_score to detect breakdown.
-- This is more accurate than anchoring to entry_rs_score because a stock that
-- ran hard after entry has higher RS at its peak than at the original buy date.
--
-- Written by the EOD metrics loop in execution_agent.py whenever days_since_hwm == 0
-- (i.e. the position made a new HWM today). Preserved across stall periods.

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS hwm_rs_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.hwm_rs_score IS
  'RS score on the day the position last set a new high-water mark. '
  'Rule 1 (RS Decay) fires when live_rs_score drops >= RS_DECAY_GATE pts below this. '
  'Updated each EOD cycle when days_since_hwm = 0. NULL = no RS data, Rule 1 skipped.';
