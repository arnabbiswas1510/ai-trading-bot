-- migrations/add_exit_requests.sql
--
-- Smart OCA Managed Exit queue.
--
-- WHY THIS TABLE EXISTS
--   Before this, exiting a named position "intelligently" (ride the bounce
--   instead of dumping at the current print) meant running managed_exit.py or
--   force_sell.py by hand. Both require `docker compose stop execution-agent`
--   because sells must use clientId=1 — the session that placed the buys — or
--   IBKR treats them as opening a short. That leaves the ENTIRE portfolio
--   unmonitored while you babysit one exit.
--
--   This table lets the request be expressed as data. The execution-agent,
--   which already holds clientId=1, drains the queue on its normal 15-minute
--   cycle and places the OCA itself. Nothing has to be stopped.
--
-- WHY MODES INSTEAD OF PRICES
--   A request queued at 22:00 that carries limit_price = 489.89 is stale by
--   09:30. Storing the *intent* (BREAKEVEN / PCT_FROM_ENTRY / ABS ...) lets the
--   agent resolve the actual price at placement time, against a settled quote.
--
-- WHY A TABLE AND NOT COLUMNS ON portfolio_positions
--   portfolio_positions rows are DELETED when the position sells, which would
--   destroy the record of what was requested and what it achieved. This table
--   outlives the position and is the audit trail.

CREATE TABLE IF NOT EXISTS exit_requests (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT        NOT NULL,

    -- Upper leg (limit sell). NONE = no upper leg, trail only.
    -- ABS             : limit_value is a literal price
    -- BREAKEVEN       : entry price (limit_value ignored)
    -- PCT_FROM_ENTRY  : entry * (1 + limit_value/100)
    -- PCT_FROM_PRICE  : resolved price * (1 + limit_value/100)
    limit_mode          TEXT        NOT NULL DEFAULT 'BREAKEVEN',
    limit_value         NUMERIC,

    -- Lower leg (protective). TRAIL_PCT is strongly preferred over ABS:
    -- a static stop surrenders the entire bounce the upper leg is waiting for.
    -- TRAIL_PCT : IBKR native TRAIL, stop_value percent
    -- ATR_AUTO  : trail scaled to entry_atr_pct (stop_value ignored)
    -- ABS       : literal stop price
    -- MARKET    : not an OCA at all — sell at market on the next agent cycle.
    --             This is a force sell routed through the queue so it does not
    --             require stopping the execution-agent. limit_mode is ignored.
    stop_mode           TEXT        NOT NULL DEFAULT 'ATR_AUTO',
    stop_value          NUMERIC,

    -- Software backstops enforced by the agent each cycle, because an OCA
    -- alone can sit unfilled forever while the position bleeds.
    hard_floor_pct      NUMERIC,            -- market-exit if price falls this % below placement price
    expires_after_days  INT         NOT NULL DEFAULT 3,   -- trading days; then market-exit

    -- PENDING -> PLACED -> FILLED | EXPIRED | CANCELLED | FAILED
    status              TEXT        NOT NULL DEFAULT 'PENDING',

    oca_group           TEXT,
    placed_at           TIMESTAMPTZ,
    placed_price        NUMERIC,            -- reference price when the OCA went out
    placed_limit_price  NUMERIC,
    placed_stop_price   NUMERIC,            -- informational: trail anchor at placement
    placed_trail_pct    NUMERIC,

    filled_at           TIMESTAMPTZ,
    filled_price        NUMERIC,
    outcome             TEXT,               -- 'LIMIT' | 'TRAIL' | 'FLOOR' | 'EXPIRY'

    note                TEXT,
    requested_by        TEXT        DEFAULT 'manual',
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The agent's hot path: "give me everything still in flight".
CREATE INDEX IF NOT EXISTS idx_exit_requests_status
    ON exit_requests (status)
    WHERE status IN ('PENDING', 'PLACED');

CREATE INDEX IF NOT EXISTS idx_exit_requests_ticker
    ON exit_requests (ticker);

-- At most one in-flight request per ticker. Two OCA groups on the same shares
-- in a cash account is a guaranteed rejection, and the second would silently
-- cancel the first's protection.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_requests_one_active
    ON exit_requests (ticker)
    WHERE status IN ('PENDING', 'PLACED');

ALTER TABLE exit_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service role full access on exit_requests" ON exit_requests;
CREATE POLICY "service role full access on exit_requests"
    ON exit_requests FOR ALL
    USING (true) WITH CHECK (true);
