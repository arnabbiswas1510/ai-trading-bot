-- Migration: Risk Optimization Columns for portfolio_positions
-- Run once in Supabase SQL Editor

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS highest_unrealized_pct NUMERIC DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS stop_loss_pct          NUMERIC DEFAULT 0.07,
  ADD COLUMN IF NOT EXISTS volume_distribution_flag BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS days_held              INTEGER DEFAULT 0;

COMMENT ON COLUMN portfolio_positions.highest_unrealized_pct IS
  'Highest unrealized profit percentage seen since buy date. Used for moving stop to break-even after hitting +5%.';

COMMENT ON COLUMN portfolio_positions.stop_loss_pct IS
  'The actual trailing stop percentage used (either dynamic 2.5x ATR or static fallback).';

COMMENT ON COLUMN portfolio_positions.volume_distribution_flag IS
  'True if the stock has had >= 2 distribution days (down on high volume) in a rolling 3-day window.';

COMMENT ON COLUMN portfolio_positions.days_held IS
  'Number of NYSE trading days this position has been held.';
