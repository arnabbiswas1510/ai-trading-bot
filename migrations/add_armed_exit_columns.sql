-- ============================================================
-- Migration: Armed Trailing Exit (Day 0-6 loss-cutting)
-- ============================================================
-- Supports the "Armed Trailing Exit" strategy: when a Day 0-6
-- sell signal fires (Early Loss Kill-switch or Intraday Loss
-- Minimiser), instead of selling instantly at the trigger price
-- (often a local trough), we arm a tight IBKR trailing stop and
-- track when it was armed so a hard deadline can force a market
-- sell if it hasn't already stopped out. See arm_exit() and the
-- "Armed Trailing Exit deadline check" in monitor_portfolio_intraday()
-- in execution_agent.py.
-- ============================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS exit_armed BOOLEAN DEFAULT FALSE;

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS exit_armed_at TIMESTAMPTZ;

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS exit_armed_reason TEXT;

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS exit_armed_price NUMERIC;
