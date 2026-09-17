-- ============================================================
-- Migration: Simplified Exit Strategy — HWM Date Tracking
-- ============================================================
-- Adds hwm_date to track the last date a new price high was
-- observed during intraday polling. Used for plateau detection:
-- if today - hwm_date >= PLATEAU_DAYS, the position is stalled
-- and eligible for rotation when a fresh breakout exists.
--
-- IBKR tracks the actual HWM price internally for the trailing
-- stop. We only need the DATE, not the price.
-- ============================================================

-- Phase 1: Add the new column (safe to run immediately)
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS hwm_date DATE;

-- Backfill existing positions: set hwm_date = today so the
-- plateau clock starts now (not retroactively stale).
UPDATE portfolio_positions
  SET hwm_date = CURRENT_DATE
  WHERE hwm_date IS NULL;

-- ============================================================
-- Phase 2: Cleanup — run AFTER code is verified stable in prod
-- These columns are no longer written or read by the bot.
-- ============================================================

-- ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS high_water_mark;
-- ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS is_power_hold;
-- ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS power_hold_expiry;
-- ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS profit_target;
