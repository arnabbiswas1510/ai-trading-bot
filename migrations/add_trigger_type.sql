-- Migration: add trigger_type column to daily_triggers
-- Values: 'BREAKOUT' (confirmed volume breakout) | 'PRE_BREAKOUT' (coiling toward pivot)
-- Default: 'BREAKOUT' preserves backwards-compatibility with all existing rows.

ALTER TABLE daily_triggers
  ADD COLUMN IF NOT EXISTS trigger_type TEXT DEFAULT 'BREAKOUT';

-- Update any existing rows that have no type to BREAKOUT
UPDATE daily_triggers
  SET trigger_type = 'BREAKOUT'
  WHERE trigger_type IS NULL;
