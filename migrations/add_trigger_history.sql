-- Migration: point-in-time breakout trigger archive + buy/skip decision log
-- Run once in Supabase SQL Editor.
--
-- WHY THIS EXISTS
-- ---------------
-- technical_screener.py truncates the entire `daily_triggers` table on every
-- run (delete().neq("ticker", "DUMMY_NEVER_MATCH")). At the time of writing the
-- live table held NINE rows -- a single day. Everything before it is gone.
--
-- That destroys the counterfactual, which is the most valuable research asset
-- the bot produces. Each morning the screener emits N triggers and at most a
-- few are bought (MAX_POSITIONS = 4). `trade_history` therefore contains only
-- candidates already judged good -- selection on the dependent variable. The
-- rejected candidates are the control group, and they are deleted.
--
-- Unanswerable without these tables:
--   * Does final_score predict forward return? Outcomes are observed only for
--     high scores that were bought, so the relationship is range-restricted.
--   * Is the D-grade AI veto correct? Vetoed names are never bought, so never
--     measured. (The AI evaluator costs money and is currently unvalidated.)
--   * What does MAX_POSITIONS = 4 cost? Needs triggers skipped purely for slots.
--   * Does PRE_BREAKOUT convert better than BREAKOUT?
--
-- `daily_triggers` keeps its current-state semantics and is NOT modified; the
-- live pipeline reads it and must stay exactly as it is.

-- ── 1. What the screener saw ────────────────────────────────────────────────
-- Written at truncate time, which captures the PREVIOUS run's rows. That is
-- deliberate: ai_evaluator.py updates daily_triggers with scores AFTER
-- technical_screener inserts, so archiving the incoming rows would store NULL
-- ai_rating / final_score / score_rationale. By truncate time the outgoing rows
-- are fully enriched and have already been acted upon.
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
    -- Scoring inputs. These are the whole point: without them a stored ticker
    -- cannot be linked back to why the bot rated it as it did.
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

COMMENT ON TABLE trigger_history IS
  'Append-only point-in-time record of breakout triggers, including those never '
  'bought. Never pruned. See migrations/add_trigger_history.sql.';

CREATE INDEX IF NOT EXISTS idx_trigger_history_date
    ON trigger_history (triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_history_ticker
    ON trigger_history (ticker, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_history_score
    ON trigger_history (final_score DESC);

-- ── 2. What the bot decided, and why ────────────────────────────────────────
-- Separate from trigger_history because a trigger can be re-evaluated on
-- several days within TRIGGER_LOOKBACK_DAYS and receive a different verdict
-- each day (skipped for slots on Monday, bought on Tuesday). The decision date
-- is therefore part of the key.
CREATE TABLE IF NOT EXISTS trigger_decisions (
    decision_date   DATE        NOT NULL,
    ticker          TEXT        NOT NULL,
    trigger_type    TEXT        NOT NULL DEFAULT 'BREAKOUT',
    triggered_at    DATE,
    decision        TEXT        NOT NULL,   -- BOUGHT | SKIPPED
    reason_code     TEXT        NOT NULL,   -- see trigger_audit.py constants
    reason_detail   TEXT,
    -- TRUE when the rejection was about capacity (no slot, no cash) rather than
    -- candidate quality. Analysis MUST separate these: a name skipped for lack
    -- of a slot says nothing about the quality model, but everything about the
    -- cost of MAX_POSITIONS.
    is_capacity     BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Scores AS EVALUATED at decision time. Snapshotted rather than joined,
    -- because the trigger row may be re-scored on a later run.
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

COMMENT ON TABLE trigger_decisions IS
  'Append-only log of every buy/skip verdict against a trigger, including the '
  'reason. The rejected rows are the control group that trade_history lacks.';

CREATE INDEX IF NOT EXISTS idx_trigger_decisions_date
    ON trigger_decisions (decision_date DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_decisions_reason
    ON trigger_decisions (reason_code, decision_date DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_decisions_ticker
    ON trigger_decisions (ticker, decision_date DESC);

-- Seed today's triggers so the record starts now rather than at the next run.
-- Idempotent: the primary key makes re-running a no-op.
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
