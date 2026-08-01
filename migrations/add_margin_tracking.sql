-- ============================================================================
-- Migration: Add margin tracking columns to account_balances
-- Run once in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
--
-- Background (2026-07-21 TRV incident):
--   The execution agent previously read AvailableFunds from IBKR to size
--   positions. On a margin account, AvailableFunds includes buying power
--   backed by IBKR margin loans, not just deposited equity. This caused the
--   bot to deploy ~$60K into TRV (using ~$35K of borrowed money) instead of
--   the intended ~$25K.
--
--   The fix records two new values every reconciliation cycle:
--     • ibkr_own_cash    — own deposited cash (TotalCashValue ≥ 0)
--     • ibkr_margin_loan — amount borrowed from IBKR (0 when no loan)
--
--   These columns let the dashboard surface a "margin exposure" time-series
--   and allow post-mortem review of when margin crept in.
-- ============================================================================

-- 1. ibkr_own_cash
--    IBKR TotalCashValue (USD), clamped to >= 0.
--    Represents only the account owner's deposited / settled cash.
--    This is the value used for position sizing after the fix.
ALTER TABLE account_balances
  ADD COLUMN IF NOT EXISTS ibkr_own_cash NUMERIC DEFAULT NULL;

COMMENT ON COLUMN account_balances.ibkr_own_cash IS
  'Own deposited cash in USD (IBKR TotalCashValue, clamped to 0 when negative). '
  'Used for buy position sizing — never includes borrowed margin funds. '
  'Added 2026-07-21 after TRV over-buy incident.';


-- 2. ibkr_margin_loan
--    Amount borrowed from IBKR on margin, in USD.
--    0 when no margin loan is active.
--    Positive value means the account is in debt to IBKR by that amount.
ALTER TABLE account_balances
  ADD COLUMN IF NOT EXISTS ibkr_margin_loan NUMERIC DEFAULT NULL;

COMMENT ON COLUMN account_balances.ibkr_margin_loan IS
  'Margin loan balance in USD (abs(TotalCashValue) when TotalCashValue < 0, else 0). '
  'Non-zero values trigger a Telegram alert and block all new buys. '
  'Added 2026-07-21 after TRV over-buy incident.';


-- ============================================================================
-- Optional: index for quick dashboard queries on days with margin exposure
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_account_balances_margin_loan
  ON account_balances (ibkr_margin_loan)
  WHERE ibkr_margin_loan > 0;


-- ============================================================================
-- Verification query — run after migration to confirm columns exist:
-- ============================================================================
-- SELECT column_name, data_type, is_nullable, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'account_balances'
--   AND column_name IN ('ibkr_own_cash', 'ibkr_margin_loan')
-- ORDER BY column_name;
