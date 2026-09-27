-- 20260927_add_exit_shadow_log.sql
--
-- Shadow-decision log for two register-tracked exit candidates, written every
-- 15-minute monitor cycle by monitor_portfolio_intraday() via exit_shadow.py.
-- It records what the LIVE Prove-It rule and two hypothetical alternatives
-- WOULD do to each open position — with zero effect on live orders.
--
-- The two candidates under measurement:
--   • Q1 arm-timing: Phase 2 arms at +3% peak instead of the live +2%
--     (register: exit-parameters-proveit / PROVE_IT_P2_ARM_GAIN_PCT).
--   • Q2 give-back trail: a 5% trail from the high-water mark instead of the
--     live 1.5% profit-lock (register: ladder-width-runon).
--
-- Purpose is forward, live evidence the 5-minute single-regime backtest cannot
-- produce: sub-5-minute wick shakeouts and out-of-regime behaviour. It captures
-- only divergence UP TO the real exit (once the live rule sells there are no
-- shares to watch), so the run-on upside of holding a winner past the live exit
-- stays harness-only. See exit_shadow.py and
-- decisions/2026-09-27_exit-shadow-log.md.
--
-- Idempotent: safe to run more than once (CREATE ... IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS exit_shadow_log (
    id              BIGSERIAL PRIMARY KEY,
    cycle_ts        TIMESTAMPTZ NOT NULL,
    ticker          TEXT        NOT NULL,
    days_held       INTEGER,
    price           NUMERIC,
    current_pct     NUMERIC,
    peak_pct        NUMERIC,
    -- live Prove-It rule this cycle
    live_would_exit BOOLEAN,
    live_level      NUMERIC,
    live_phase      TEXT,
    -- Q1: Phase 2 arms at +3% instead of +2%
    q1_would_exit   BOOLEAN,
    q1_level        NUMERIC,
    q1_state        TEXT,
    -- Q2: 5% give-back trail from the high-water mark
    q2_would_exit   BOOLEAN,
    q2_level        NUMERIC,
    q2_state        TEXT,
    -- divergences: where a candidate would act differently from live
    q1_diverges     BOOLEAN,
    q2_diverges     BOOLEAN,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Access patterns: "this ticker's shadow trail over time" and "all divergences".
CREATE INDEX IF NOT EXISTS idx_exit_shadow_ticker_ts
    ON exit_shadow_log (ticker, cycle_ts DESC);
CREATE INDEX IF NOT EXISTS idx_exit_shadow_cycle_ts
    ON exit_shadow_log (cycle_ts DESC);

COMMENT ON TABLE exit_shadow_log IS
    'Side-effect-free shadow log of two candidate exit rules (Q1 arm@+3%, '
    'Q2 5% give-back trail) vs the live Prove-It rule, one row per open '
    'position per monitor cycle. Never drives an order. See exit_shadow.py.';

-- RLS: operational detail about a live trading system; service-role only,
-- matching the policy pattern in 20260906_fix_rls_missing_policies.sql.
ALTER TABLE exit_shadow_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'exit_shadow_log'
          AND policyname = 'exit_shadow_log_service_role_all'
    ) THEN
        CREATE POLICY exit_shadow_log_service_role_all ON exit_shadow_log
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- End-of-file verification: confirm the object exists.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_name = 'exit_shadow_log') THEN
        RAISE NOTICE 'OK: exit_shadow_log present.';
    ELSE
        RAISE NOTICE 'FAIL: exit_shadow_log missing.';
    END IF;
END $$;
