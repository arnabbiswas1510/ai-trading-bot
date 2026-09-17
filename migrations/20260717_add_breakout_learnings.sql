-- Migration: add breakout_learnings table
-- Persists deterministic attributions when a held position exits.
-- Written by execution_agent.py at sell time (stop_loss, rotation, hard_stop, manual).
-- Read by technical_screener.py to compute failure_penalty for new triggers (Phase 2).

CREATE TABLE IF NOT EXISTS breakout_learnings (
  id                UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  ticker            TEXT    NOT NULL,
  buy_date          DATE    NOT NULL,
  exit_date         DATE    NOT NULL,
  exit_type         TEXT    NOT NULL,    -- 'rotation' | 'stop_loss' | 'hard_stop' | 'manual'
  entry_final_score INTEGER,
  failed_params     JSONB,               -- {param: {entry, current, drift, failed}}
  lesson_text       TEXT,                -- human-readable attribution written by bot
  market_regime     TEXT,                -- 'uptrend' | 'correction' | 'neutral'
  days_held         INTEGER,
  pnl_pct           NUMERIC(6,2),        -- % return at fill price
  created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bl_ticker
  ON breakout_learnings(ticker);

CREATE INDEX IF NOT EXISTS idx_bl_exit_date
  ON breakout_learnings(exit_date DESC);

-- GIN index enables efficient JSONB queries for the screener penalty computation:
-- e.g. "find all rows where volume_surge.failed = true"
CREATE INDEX IF NOT EXISTS idx_bl_failed_params
  ON breakout_learnings USING GIN(failed_params);
