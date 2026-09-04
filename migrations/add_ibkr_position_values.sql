-- Migration: persist IBKR's own position valuation on portfolio_positions
-- Purpose: The dashboard previously valued open positions as
--              shares (Supabase) x price (FMP quote)
--          which never matched the broker. Worse, it added those live FMP
--          prices to an IBKR cash balance that is only refreshed once per agent
--          cycle, so the reported total mixed two different vintages of data.
--
--          IBKR's own valuation is already available for free: ib.portfolio()
--          returns PortfolioItem objects carrying marketPrice, marketValue and
--          unrealizedPNL, and reconcile_with_ibkr() already calls it. These
--          columns persist those numbers so the read-only web container -- which
--          deliberately has no brokerage access -- can render exactly what IBKR
--          reports.
--
--          NOTE: this does NOT use ib.reqTickers(). The existing comment in
--          execution_agent.py warns that reqTickers() blocks when the ushmds
--          data farm is down; portfolio() reads the account update stream
--          instead and is not affected.
--
-- See decisions/2026-09-03_ibkr-sourced-position-values.md
-- Run once in Supabase SQL Editor.

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS current_price   NUMERIC     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_value    NUMERIC     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS unrealized_pnl  NUMERIC     DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS ibkr_synced_at  TIMESTAMPTZ DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.current_price IS
  'IBKR PortfolioItem.marketPrice, written by execution_agent.reconcile_with_ibkr(). '
  'This is the broker''s own mark, NOT an FMP quote. Never write an FMP price here.';

COMMENT ON COLUMN portfolio_positions.market_value IS
  'IBKR PortfolioItem.marketValue. Stored rather than recomputed as shares x price '
  'because IBKR is the authority on both factors; recomputing would reintroduce the '
  'share-count drift this column exists to eliminate.';

COMMENT ON COLUMN portfolio_positions.unrealized_pnl IS
  'IBKR PortfolioItem.unrealizedPNL. Uses IBKR''s average cost, which can differ from '
  'our buy_price after partial fills, so it is not derivable from our own columns.';

COMMENT ON COLUMN portfolio_positions.ibkr_synced_at IS
  'When the three columns above were last written from IBKR. The dashboard renders '
  'this as an "as of" timestamp. NULL means never synced -- callers must fall back to '
  'cost basis and say so, never silently substitute a live quote from another vendor.';

-- No back-fill. These columns are deliberately left NULL until the agent's next
-- reconcile cycle writes real broker data: seeding them with buy_price would make
-- a never-synced position indistinguishable from one marked flat by IBKR, which is
-- exactly the class of ambiguity this migration removes.

-- Verification:
-- SELECT ticker, shares, buy_price, current_price, market_value,
--        unrealized_pnl, ibkr_synced_at
-- FROM portfolio_positions
-- ORDER BY ticker;
