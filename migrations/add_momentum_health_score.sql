-- Migration: Momentum Health Score columns for portfolio_positions
-- Run once in Supabase SQL Editor

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS momentum_health_score  NUMERIC  DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS live_sentiment_score   INTEGER  DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.momentum_health_score IS
  'Live Momentum Health Score Mₜ (0–100) computed EOD from RS decay (40%), volume ratio (35%), and FMP news sentiment via GPT-4o-mini (25%). Used as comparator in Rank & Replace instead of static entry_final_score.';

COMMENT ON COLUMN portfolio_positions.live_sentiment_score IS
  'Real-time sentiment score (1-100) scored by GPT-4o-mini against FMP stock_news headlines. Refreshed once per EOD cycle for held positions.';
