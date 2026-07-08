-- Migration: Create cash_flows table
-- Purpose: Stores IBKR cash deposits and withdrawals fetched via Flex Query.
--          The performance report UI reads this table to allow users to
--          include or exclude cash deposits from return calculations,
--          preventing deposits from being conflated with trading gains.
--
-- Run this in the Supabase SQL editor before deploying flex_query_sync.py.

CREATE TABLE IF NOT EXISTS cash_flows (
    -- IBKR transaction ID — unique per cash event, used as upsert conflict key
    transaction_id  TEXT        PRIMARY KEY,

    -- Date of transaction (YYYY-MM-DD)
    date            DATE        NOT NULL,

    -- Full timestamp with time of day (ISO format)
    date_time       TIMESTAMP,

    -- Amount in account currency (positive = deposit, negative = withdrawal)
    amount          NUMERIC     NOT NULL,

    -- IBKR transaction type (e.g. "Deposits/Withdrawals")
    type            TEXT,

    -- Human-readable description from IBKR (e.g. "ADJUSTMENT: DEPOSIT ADVANCE")
    description     TEXT,

    -- Currency code (e.g. "USD")
    currency        TEXT        DEFAULT 'USD',

    -- IBKR account ID (e.g. "U12941651")
    account_id      TEXT,

    -- Record-keeping
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for performance report queries that filter by date range
CREATE INDEX IF NOT EXISTS idx_cash_flows_date ON cash_flows (date DESC);

-- Enable Row Level Security (match existing table policy patterns)
ALTER TABLE cash_flows ENABLE ROW LEVEL SECURITY;

-- Allow service role full access (used by flex_query_sync.py)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'cash_flows' AND policyname = 'Service role full access'
    ) THEN
        CREATE POLICY "Service role full access"
            ON cash_flows
            FOR ALL
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;
