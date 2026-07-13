-- Migration: add 5-component scoring columns to daily_triggers
-- Run this in the Supabase SQL editor (Project → SQL Editor → New query)
-- Safe to re-run — all statements use ADD COLUMN IF NOT EXISTS

-- ── daily_triggers: new columns written by technical_screener.py ──────────────
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS avg_volume_50       INTEGER;     -- 50-day avg daily volume
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS technical_score     INTEGER;     -- alias of quality_score (breakout mechanics)
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS rs_score            INTEGER;     -- 12-week relative strength vs SPY (0-100)
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS atr_pct             FLOAT;       -- ATR-14 as % of price
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS est_days_to_target  INTEGER;     -- estimated trading days to +25% at ATR pace

-- ── daily_triggers: new columns written by ai_evaluator.py ────────────────────
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS liquidity_score     INTEGER;     -- price/volume/size composite (0-100)
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS sentiment_score     INTEGER;     -- news headline sentiment (0-100)
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS score_rationale     TEXT;        -- AI rationale text
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS ai_rating           INTEGER;     -- raw AI score (1-100), for backwards-compat

-- ── portfolio_positions: capture all entry scores at buy time ─────────────────
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_technical_score   INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_liquidity_score   INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_rs_score          INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_sentiment_score   INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_atr_pct           FLOAT;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_est_days_target   INTEGER;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS entry_score_rationale   TEXT;
