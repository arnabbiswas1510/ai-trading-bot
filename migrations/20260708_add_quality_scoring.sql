-- Phase 1: Add quality scoring columns
-- Run in Supabase SQL editor

-- daily_triggers: programmatic quality score, AI letter grade, and final combined score
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS quality_score  INTEGER;
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS ai_grade       TEXT;       -- A / B / C / D
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS final_score    INTEGER;    -- quality_score + ai_bonus

-- portfolio_positions: capture entry scores at buy time for future rotation analysis
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_quality_score  INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_ai_rating      INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_ai_grade       TEXT;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_final_score    INTEGER;
