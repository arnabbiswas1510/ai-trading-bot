-- Phase 1: Global Architecture Database Migration

-- Add global tracking columns to watchlist
ALTER TABLE watchlist 
ADD COLUMN IF NOT EXISTS tv_exchange TEXT,
ADD COLUMN IF NOT EXISTS ib_exchange TEXT,
ADD COLUMN IF NOT EXISTS currency TEXT,
ADD COLUMN IF NOT EXISTS fmp_ticker TEXT;

-- Add global tracking columns to daily_triggers
ALTER TABLE daily_triggers 
ADD COLUMN IF NOT EXISTS tv_exchange TEXT,
ADD COLUMN IF NOT EXISTS ib_exchange TEXT,
ADD COLUMN IF NOT EXISTS currency TEXT,
ADD COLUMN IF NOT EXISTS fmp_ticker TEXT;

-- Add global tracking columns to portfolio_positions
ALTER TABLE portfolio_positions 
ADD COLUMN IF NOT EXISTS tv_exchange TEXT,
ADD COLUMN IF NOT EXISTS ib_exchange TEXT,
ADD COLUMN IF NOT EXISTS currency TEXT,
ADD COLUMN IF NOT EXISTS fmp_ticker TEXT;

-- Update existing U.S. records with defaults so legacy code doesn't break
UPDATE watchlist 
SET tv_exchange = 'NASDAQ', ib_exchange = 'SMART', currency = 'USD', fmp_ticker = ticker
WHERE currency IS NULL;

UPDATE daily_triggers 
SET tv_exchange = 'NASDAQ', ib_exchange = 'SMART', currency = 'USD', fmp_ticker = ticker
WHERE currency IS NULL;

UPDATE portfolio_positions 
SET tv_exchange = 'NASDAQ', ib_exchange = 'SMART', currency = 'USD', fmp_ticker = ticker
WHERE currency IS NULL;
