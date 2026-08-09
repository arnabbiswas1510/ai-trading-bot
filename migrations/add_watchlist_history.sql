-- Migration: point-in-time watchlist history
-- Run once in Supabase SQL Editor.
--
-- WHY THIS EXISTS
-- ---------------
-- tv_api_screener.py truncates the entire `watchlist` table on every run
-- (delete().neq("ticker", "DUMMY_NEVER_MATCH")) and reinserts the current pass
-- list. A name that qualified in 2023 and later deteriorated is therefore gone
-- without a trace.
--
-- The consequence is that the fundamental screen CANNOT BE BACKTESTED. The only
-- available universe file, research/pass_names.txt, is a single snapshot of the
-- names passing the screen *today* replayed backwards over three years, which
-- carries survivorship and look-ahead bias (benchmark_data/README.md, "Known
-- limitations"). Any measured screen "edge" is confounded by construction.
--
-- This table is an append-only log: one immutable row per (snapshot_date,
-- ticker), retained forever. After enough weekly snapshots accumulate, the
-- screen can be evaluated point-in-time — using only the names it actually
-- returned on a given date, including those that later failed.
--
-- The raw metrics are stored alongside the ticker deliberately. That allows
-- alternative screen definitions (different EPS/revenue thresholds, ROE floors,
-- size buckets) to be re-cut offline WITHOUT a point-in-time fundamentals
-- vendor such as Norgate or Sharadar.
--
-- `watchlist` keeps its current-state semantics and is NOT modified; the live
-- trading pipeline reads it and must stay exactly as it is.

CREATE TABLE IF NOT EXISTS watchlist_history (
    snapshot_date    DATE        NOT NULL,
    ticker           TEXT        NOT NULL,
    company_name     TEXT,
    q_eps_growth     FLOAT,
    a_eps_growth     FLOAT,
    revenue_growth   FLOAT,
    analyst_rating   TEXT,
    float_shares     BIGINT,
    roe              FLOAT,
    company_size     TEXT,
    price            FLOAT,
    market_cap       FLOAT,
    volume           FLOAT,
    sector           TEXT,
    retention_period TEXT,
    source           TEXT        NOT NULL DEFAULT 'tv_api_screener',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, ticker)
);

COMMENT ON TABLE watchlist_history IS
  'Append-only point-in-time record of fundamental screener output. One row per '
  'ticker per snapshot date, never pruned. Exists so the screen itself can be '
  'backtested without survivorship bias -- see migrations/add_watchlist_history.sql.';

COMMENT ON COLUMN watchlist_history.snapshot_date IS
  'Date the screener returned this ticker. Half of the primary key, so a re-run '
  'on the same day overwrites rather than duplicating.';

COMMENT ON COLUMN watchlist_history.retention_period IS
  'Consecutive-qualification counter carried over from watchlist at snapshot '
  'time. Directly testable as a buy gate: do names qualifying many runs running '
  'outperform fresh entrants?';

-- Query patterns are "all tickers on date D" and "all dates for ticker T".
CREATE INDEX IF NOT EXISTS idx_watchlist_history_date
    ON watchlist_history (snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_history_ticker
    ON watchlist_history (ticker, snapshot_date DESC);

-- Seed today from the current watchlist so the record starts now rather than at
-- the next scheduled run. Safe to re-run: the primary key makes it idempotent.
INSERT INTO watchlist_history (
    snapshot_date, ticker, company_name, q_eps_growth, a_eps_growth,
    revenue_growth, analyst_rating, float_shares, roe, company_size, price,
    retention_period, source
)
SELECT
    CURRENT_DATE, ticker, company_name, q_eps_growth, a_eps_growth,
    revenue_growth, analyst_rating, float_shares, roe, company_size, price,
    retention_period, 'seed_from_watchlist'
FROM watchlist
ON CONFLICT (snapshot_date, ticker) DO NOTHING;
