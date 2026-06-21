-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: Add detailed indicator columns to watchlist table in Supabase
-- Run this in your Supabase SQL Editor.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS price NUMERIC DEFAULT 0.0;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS rs_rating NUMERIC DEFAULT 85.0;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS roe NUMERIC DEFAULT 22.0;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS sma50 NUMERIC DEFAULT 0.0;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS n_pct_from_high NUMERIC DEFAULT 3.5;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS s_acc_days INTEGER DEFAULT 12;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS s_dist_days INTEGER DEFAULT 6;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS total_score NUMERIC DEFAULT 0.0;
