-- Migration: add hwm_price column to portfolio_positions
-- Purpose: Store the highest price seen since buy date so the Dashboard can
--          correctly display the trailing stop level (7% below HWM price, not buy price).
--          The actual IBKR trailing stop already uses the running peak tick-by-tick;
--          this column is for display accuracy only.
-- Run once in Supabase SQL Editor.

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS hwm_price NUMERIC DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.hwm_price IS
  'Highest intraday price seen since buy date. Written by execution_agent.py '
  'each monitoring cycle when current_price > stored hwm_price. '
  'Used by the Dashboard to display the correct trailing stop level '
  '(7% below hwm_price, not buy_price). Initialised to buy_price on first write.';

-- Back-fill existing positions: set hwm_price = buy_price where not yet set.
-- The agent will then update it upward as prices are polled each cycle.
UPDATE portfolio_positions
SET hwm_price = buy_price
WHERE hwm_price IS NULL AND buy_price IS NOT NULL;

-- Verification:
-- SELECT ticker, buy_price, hwm_price
-- FROM portfolio_positions
-- ORDER BY ticker;
