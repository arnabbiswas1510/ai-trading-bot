-- Migration: add highest_rs_score column to portfolio_positions
-- Run in Supabase SQL Editor

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS highest_rs_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.highest_rs_score IS
  'Peak Relative Strength (RS) score recorded since position entry. Used to track RS decay since the peak.';
