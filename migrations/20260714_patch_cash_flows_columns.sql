-- Migration: Add missing columns to cash_flows table
-- The table was initially created by 20260624_twr_schema.sql with only
-- (id, date, amount, description). This migration adds the full schema
-- that flex_query_sync.py expects to write.
-- Safe to re-run — all statements use ADD COLUMN IF NOT EXISTS.

ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS transaction_id  TEXT        UNIQUE;
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS date_time       TIMESTAMP;
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS type            TEXT;
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS currency        TEXT        DEFAULT 'USD';
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS account_id      TEXT;
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS created_at      TIMESTAMPTZ DEFAULT NOW();

-- Create an index on transaction_id for upsert performance
CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_flows_transaction_id ON cash_flows (transaction_id)
    WHERE transaction_id IS NOT NULL;

-- Index for performance report queries filtering by date range
CREATE INDEX IF NOT EXISTS idx_cash_flows_date ON cash_flows (date DESC);
