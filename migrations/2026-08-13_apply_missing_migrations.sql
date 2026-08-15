-- =============================================================================
-- CONSOLIDATED PATCH: apply all migrations missing from the live Supabase DB
-- Project: yhaynfrmsjzbybbehfjs   Audited: 2026-08-13
-- Run once in the Supabase SQL Editor. Safe to re-run (fully idempotent).
-- =============================================================================
--
-- HOW THIS LIST WAS DERIVED
-- -------------------------
-- Every CREATE TABLE / ADD COLUMN target in migrations/*.sql was probed against
-- the live PostgREST API. Only genuinely absent objects are included below.
-- Everything else (cash_flows, ibkr_fills, breakout_learnings, the 15
-- daily_triggers columns, the other 26 portfolio_positions columns, margin,
-- power-hold, plateau, armed-exit, TWR, retention, RLS) is already applied and
-- is deliberately NOT repeated here.
--
-- MISSING TABLES  (4)
--   trigger_history      <- add_trigger_history.sql
--   trigger_decisions    <- add_trigger_history.sql
--   trigger_outcomes*    <- add_trigger_outcomes.sql   (*columns on trigger_history)
--   watchlist_history    <- add_watchlist_history.sql
--
-- MISSING COLUMNS (3, all on portfolio_positions)
--   closed_above_entry   <- add_closed_above_entry.sql
--   highest_rs_score     <- add_highest_rs_score.sql
--   hwm_rs_score         <- add_hwm_rs_score.sql
--
-- IMPACT OF THE GAP
-- -----------------
-- * closed_above_entry missing => the Thesis Stop cannot read its follow-through
--   latch and falls back to the conservative path. This is the column implicated
--   in the NBIX post-mortem.
-- * highest_rs_score / hwm_rs_score missing => Rule 1 (RS Decay) has no anchor
--   and is skipped, so RS breakdown never triggers an exit.
-- * trigger_history / trigger_decisions / watchlist_history missing => the
--   screener and watchlist truncate on every run with no archive, so the
--   counterfactual (rejected candidates) is destroyed daily. This is what
--   blocked the forward-looking half of the PRE_BREAKOUT-vs-BREAKOUT study in
--   decisions/2026-08-13_reject-confirmed-breakout-first-ranking.md.
--
-- Ordering matters: section 2 adds columns to the table created in section 1.
-- =============================================================================


-- =============================================================================
-- 1. trigger_history + trigger_decisions      (from add_trigger_history.sql)
-- =============================================================================

CREATE TABLE IF NOT EXISTS trigger_history (
    triggered_at       DATE        NOT NULL,
    ticker             TEXT        NOT NULL,
    trigger_type       TEXT        NOT NULL DEFAULT 'BREAKOUT',
    close_price        FLOAT,
    volume_surge       FLOAT,
    sma_50             FLOAT,
    rolling_high_52w   FLOAT,
    pivot_distance_pct FLOAT,
    retention_period   TEXT,
    avg_volume_50      FLOAT,
    atr_pct            FLOAT,
    est_days_to_target INT,
    ai_rating          FLOAT,
    ai_grade           TEXT,
    quality_score      FLOAT,
    technical_score    FLOAT,
    rs_score           FLOAT,
    liquidity_score    FLOAT,
    sentiment_score    FLOAT,
    final_score        FLOAT,
    adjusted_score     FLOAT,
    failure_penalty    FLOAT,
    penalty_reason     TEXT,
    score_rationale    TEXT,
    archived_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (triggered_at, ticker, trigger_type)
);

COMMENT ON TABLE trigger_history IS 'Append-only point-in-time record of breakout triggers, including those never bought. Never pruned. See migrations/add_trigger_history.sql.';

CREATE INDEX IF NOT EXISTS idx_trigger_history_date
    ON trigger_history (triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_history_ticker
    ON trigger_history (ticker, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_history_score
    ON trigger_history (final_score DESC);

CREATE TABLE IF NOT EXISTS trigger_decisions (
    decision_date   DATE        NOT NULL,
    ticker          TEXT        NOT NULL,
    trigger_type    TEXT        NOT NULL DEFAULT 'BREAKOUT',
    triggered_at    DATE,
    decision        TEXT        NOT NULL,   -- BOUGHT | SKIPPED
    reason_code     TEXT        NOT NULL,   -- see trigger_audit.py constants
    reason_detail   TEXT,
    is_capacity     BOOLEAN     NOT NULL DEFAULT FALSE,
    final_score     FLOAT,
    adjusted_score  FLOAT,
    quality_score   FLOAT,
    ai_grade        TEXT,
    candidate_score FLOAT,
    min_score       FLOAT,
    price           FLOAT,
    extension_pct   FLOAT,
    slots_free      INT,
    available_cash  FLOAT,
    shares          INT,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (decision_date, ticker, trigger_type)
);

COMMENT ON TABLE trigger_decisions IS 'Append-only log of every buy/skip verdict against a trigger, including the reason. The rejected rows are the control group that trade_history lacks.';

CREATE INDEX IF NOT EXISTS idx_trigger_decisions_date
    ON trigger_decisions (decision_date DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_decisions_reason
    ON trigger_decisions (reason_code, decision_date DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_decisions_ticker
    ON trigger_decisions (ticker, decision_date DESC);


-- =============================================================================
-- 2. forward-return outcome columns on trigger_history
--                                             (from add_trigger_outcomes.sql)
-- Populated weekly by backfill_trigger_outcomes.py.
-- =============================================================================

ALTER TABLE trigger_history
  ADD COLUMN IF NOT EXISTS entry_ref_price      FLOAT,
  ADD COLUMN IF NOT EXISTS entry_ref_date       DATE,
  ADD COLUMN IF NOT EXISTS fwd_1d_pct           FLOAT,
  ADD COLUMN IF NOT EXISTS fwd_5d_pct           FLOAT,
  ADD COLUMN IF NOT EXISTS fwd_20d_pct          FLOAT,
  ADD COLUMN IF NOT EXISTS max_gain_20d_pct     FLOAT,
  ADD COLUMN IF NOT EXISTS max_drawdown_20d_pct FLOAT,
  ADD COLUMN IF NOT EXISTS ever_above_entry     BOOLEAN,
  ADD COLUMN IF NOT EXISTS bench_fwd_20d_pct    FLOAT,
  ADD COLUMN IF NOT EXISTS alpha_20d_pct        FLOAT,
  ADD COLUMN IF NOT EXISTS outcome_bars         INT,
  ADD COLUMN IF NOT EXISTS outcomes_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN trigger_history.entry_ref_price IS 'Open of the first session AFTER triggered_at. The bot buys at market open the next morning, so measuring from the trigger close would credit an overnight gap the strategy never captured.';

COMMENT ON COLUMN trigger_history.alpha_20d_pct IS 'fwd_20d_pct minus SPY over the identical window. A raw +5% in a +5% market is not edge; this is the column that tests the score.';

COMMENT ON COLUMN trigger_history.ever_above_entry IS 'TRUE if the high ever exceeded entry_ref_price within the window. Mirrors the closed_above_entry latch used by the Thesis Stop.';

COMMENT ON COLUMN trigger_history.outcome_bars IS 'Trading sessions actually available after entry. Guards against treating a partially-elapsed window as a complete 20-day result.';

COMMENT ON COLUMN trigger_history.outcomes_computed_at IS 'NULL means not yet measured. backfill_trigger_outcomes.py selects on this, so the job is resumable and safe to re-run.';

CREATE INDEX IF NOT EXISTS idx_trigger_history_pending_outcomes
    ON trigger_history (outcomes_computed_at, triggered_at)
    WHERE outcomes_computed_at IS NULL;


-- =============================================================================
-- 3. watchlist_history                       (from add_watchlist_history.sql)
-- =============================================================================

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

COMMENT ON TABLE watchlist_history IS 'Append-only point-in-time record of fundamental screener output. One row per ticker per snapshot date, never pruned. Exists so the screen itself can be backtested without survivorship bias -- see migrations/add_watchlist_history.sql.';

COMMENT ON COLUMN watchlist_history.snapshot_date IS 'Date the screener returned this ticker. Half of the primary key, so a re-run on the same day overwrites rather than duplicating.';

COMMENT ON COLUMN watchlist_history.retention_period IS 'Consecutive-qualification counter carried over from watchlist at snapshot time. Directly testable as a buy gate: do names qualifying many runs running outperform fresh entrants?';

CREATE INDEX IF NOT EXISTS idx_watchlist_history_date
    ON watchlist_history (snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_history_ticker
    ON watchlist_history (ticker, snapshot_date DESC);


-- =============================================================================
-- 4. portfolio_positions: Thesis Stop latch   (from add_closed_above_entry.sql)
-- NBIX post-mortem: without this the Thesis Stop cannot confine itself to
-- breakouts that never followed through.
-- =============================================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS closed_above_entry BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN portfolio_positions.closed_above_entry IS 'TRUE once the position has closed above its entry price at least once. Latch for the Thesis Stop, which only fires while this is FALSE.';

-- Backfill. This deliberately does NOT use highest_unrealized_pct.
--
-- That column is derived from the LIVE intraday price in
-- monitor_portfolio_intraday(), so it records intraday pokes, not closes. Using
-- it here would reproduce the exact defect this latch exists to eliminate: NBIX
-- and DELL both poked above entry intraday and never once closed above it, and
-- an intraday-based backfill would permanently disarm the Thesis Stop on them.
--
-- The latch is close-based and is written every EOD cycle from get_live_price()
-- in the 15:45-16:00 window, so the correct value re-establishes itself within
-- one session. We therefore only pre-set TRUE for positions that are already
-- PAST the thesis window (days_held > 5), where the flag can no longer change
-- any behaviour. Positions still inside the window are left FALSE so the rule is
-- armed and the next EOD close decides the truth.
--
-- Leaving an in-window position FALSE is safe: the Thesis Stop additionally
-- requires price <= -1x ATR below entry, which a position that is genuinely
-- working is not.
UPDATE portfolio_positions
   SET closed_above_entry = TRUE
 WHERE COALESCE(days_held, 0) > 5
   AND COALESCE(closed_above_entry, FALSE) = FALSE;


-- =============================================================================
-- 5. portfolio_positions: RS decay anchors
--                  (from add_highest_rs_score.sql + add_hwm_rs_score.sql)
-- Rule 1 (RS Decay) is inert until both of these exist.
-- =============================================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS highest_rs_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.highest_rs_score IS 'Peak Relative Strength (RS) score recorded since position entry. Used to track RS decay since the peak.';

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS hwm_rs_score INTEGER DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.hwm_rs_score IS 'RS score on the day the position last set a new high-water mark. Rule 1 (RS Decay) fires when live_rs_score drops >= RS_DECAY_GATE pts below this. Updated each EOD cycle when days_since_hwm = 0. NULL = no RS data, Rule 1 skipped.';


-- =============================================================================
-- 6. Row Level Security on the new tables
-- Matches the convention in enable_rls_all_tables.sql. The service role key the
-- bot uses bypasses RLS, so this does not affect the pipeline; it stops the anon
-- key reading live trading data if it is ever exposed.
-- =============================================================================

ALTER TABLE trigger_history ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'trigger_history' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON trigger_history FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

ALTER TABLE trigger_decisions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'trigger_decisions' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON trigger_decisions FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

ALTER TABLE watchlist_history ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'watchlist_history' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON watchlist_history FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;


-- =============================================================================
-- 7. Seed the archives from current state
-- Runs LAST so it cannot block the schema changes above. Both are idempotent by
-- primary key. Without this the archives start empty at the next scheduled run
-- and today's rows are lost at the next truncate.
-- =============================================================================

INSERT INTO trigger_history (
    triggered_at, ticker, trigger_type, close_price, volume_surge, sma_50,
    rolling_high_52w, pivot_distance_pct, retention_period, avg_volume_50,
    atr_pct, est_days_to_target, ai_rating, ai_grade, quality_score,
    technical_score, rs_score, liquidity_score, sentiment_score, final_score,
    adjusted_score, failure_penalty, penalty_reason, score_rationale
)
SELECT
    triggered_at, ticker, COALESCE(trigger_type, 'BREAKOUT'), close_price,
    volume_surge, sma_50, rolling_high_52w, pivot_distance_pct,
    retention_period, avg_volume_50, atr_pct, est_days_to_target, ai_rating,
    ai_grade, quality_score, technical_score, rs_score, liquidity_score,
    sentiment_score, final_score, adjusted_score, failure_penalty,
    penalty_reason, score_rationale
FROM daily_triggers
WHERE triggered_at IS NOT NULL
ON CONFLICT (triggered_at, ticker, trigger_type) DO NOTHING;

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


-- =============================================================================
-- 8. VERIFICATION -- run this after the script; every row must read OK
-- =============================================================================

SELECT 'trigger_history'                AS object, CASE WHEN to_regclass('public.trigger_history')   IS NOT NULL THEN 'OK' ELSE 'FAIL' END AS status
UNION ALL SELECT 'trigger_decisions',   CASE WHEN to_regclass('public.trigger_decisions') IS NOT NULL THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'watchlist_history',   CASE WHEN to_regclass('public.watchlist_history') IS NOT NULL THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'trigger_history.alpha_20d_pct',         CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='trigger_history'    AND column_name='alpha_20d_pct')        THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'trigger_history.outcomes_computed_at',  CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='trigger_history'    AND column_name='outcomes_computed_at') THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'portfolio_positions.closed_above_entry',CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='portfolio_positions' AND column_name='closed_above_entry')   THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'portfolio_positions.highest_rs_score',  CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='portfolio_positions' AND column_name='highest_rs_score')     THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'portfolio_positions.hwm_rs_score',      CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='portfolio_positions' AND column_name='hwm_rs_score')         THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'seeded trigger_history rows',   COALESCE((SELECT COUNT(*)::text FROM trigger_history), '0')
UNION ALL SELECT 'seeded watchlist_history rows', COALESCE((SELECT COUNT(*)::text FROM watchlist_history), '0')
UNION ALL SELECT 'positions latched closed_above_entry', COALESCE((SELECT COUNT(*)::text FROM portfolio_positions WHERE closed_above_entry), '0');
