-- Migration: Enable RLS on all trading bot tables
--
-- CORRECTED 2026-09-06. This header previously read:
--   "Safe to run: service role key bypasses RLS, so the bot is unaffected."
-- That is FALSE for this deployment. The bot authenticates with an anon-class
-- publishable key (`sb_publishable_...`), which RLS applies to in full. Every
-- table below is writable by the bot only because its policy is
-- `FOR ALL USING (true) WITH CHECK (true)`, which admits anon.
--
-- Consequence: enabling RLS on a table WITHOUT also creating a permissive
-- policy silently denies every write from the bot. That is exactly what
-- happened to `ibkr_fills` and `breakout_learnings` — see
-- migrations/fix_rls_missing_policies.sql and
-- decisions/2026-09-06_commission-accounting.md.
--
-- RULE: never `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` without pairing it
-- with a policy in the same migration.
--
-- Benefit: anon key cannot access live trading data even if accidentally exposed.

-- account_balances
ALTER TABLE account_balances ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'account_balances' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON account_balances FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- daily_triggers
ALTER TABLE daily_triggers ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'daily_triggers' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON daily_triggers FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- portfolio_positions
ALTER TABLE portfolio_positions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'portfolio_positions' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON portfolio_positions FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- trade_history
ALTER TABLE trade_history ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'trade_history' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON trade_history FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- watchlist
ALTER TABLE watchlist ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'watchlist' AND policyname = 'Service role full access') THEN
        CREATE POLICY "Service role full access" ON watchlist FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
