-- migrations/add_ibkr_fills.sql
--
-- Persistent fill store: every IBKR execution is written here in real-time
-- by the execDetailsEvent hook in execution_agent.py.
--
-- Purpose: replace the ephemeral reqExecutions() session cache as the primary
-- source of truth for sell prices in reconcile_with_ibkr() Case 1.
-- Survives agent restarts, container restarts, and IB Gateway session resets.
--
-- Indexed on (ticker, side, fill_time DESC) for efficient Case 1 lookups.

CREATE TABLE IF NOT EXISTS ibkr_fills (
    exec_id     TEXT        PRIMARY KEY,             -- IBKR execution ID (unique per partial fill)
    ticker      TEXT        NOT NULL,
    side        TEXT        NOT NULL CHECK (side IN ('BOT', 'SLD')),
    shares      NUMERIC     NOT NULL,
    price       NUMERIC     NOT NULL,
    commission  NUMERIC     NOT NULL DEFAULT 0,
    fill_time   TIMESTAMPTZ NOT NULL,
    account_id  TEXT,
    order_id    INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ibkr_fills_ticker_side_time
    ON ibkr_fills (ticker, side, fill_time DESC);

-- Optional: auto-purge fills older than 90 days (keeps the table lean).
-- Enable this if you add a pg_cron job; leave disabled if you prefer to keep full history.
-- ALTER TABLE ibkr_fills ENABLE ROW LEVEL SECURITY;
