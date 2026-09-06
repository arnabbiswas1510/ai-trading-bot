-- Migration: add missing RLS policies (fixes two silent data-loss bugs)
--
-- SYMPTOM
--   ibkr_fills and breakout_learnings have been empty since creation. Every
--   write failed with:
--
--     42501: new row violates row-level security policy for table "ibkr_fills"
--
--   The failures were real and continuous -- 16 on 2026-09-03 alone -- but the
--   handler in execution_agent.py only print()ed them, so nothing surfaced in
--   Telegram and the table simply looked unused.
--
-- CAUSE
--   Row Level Security is ENABLED on ibkr_fills but no policy was ever created
--   for it. migrations/add_ibkr_fills.sql left the ENABLE statement commented
--   out, and migrations/enable_rls_all_tables.sql -- which pairs every
--   ENABLE with a permissive policy -- does not list ibkr_fills. So the table
--   ended up with RLS on and zero policies, which denies everything.
--
--   The header of enable_rls_all_tables.sql assumes "service role key bypasses
--   RLS, so the bot is unaffected". That assumption does not hold here: this
--   deployment authenticates with a PUBLISHABLE (anon-class) key, which RLS
--   applies to in full. Every other table works only because its policy is
--   FOR ALL USING (true), which admits anon as well.
--
-- IMPACT
--   ibkr_fills is Tier 1 of the sell-price resolution ladder in
--   reconcile_with_ibkr(). It exists specifically to survive agent restarts,
--   container restarts and IB Gateway session resets -- the durability that the
--   ephemeral reqExecutions() cache cannot provide, and whose absence caused
--   RSI's sell price to be recorded incorrectly on 2026-07-17.
--
--   Because Tier 1 always returned nothing, every exit has silently fallen
--   through to Tier 2 -- the exact session cache Tier 1 was built to replace.
--   The guarantee was inert, and nothing reported that.
--
-- Run once in Supabase SQL Editor.

-- A log audit across every retained execution log found exactly two tables in
-- this state. Both are write-only sinks the operator never reads directly, which
-- is why an empty table looked plausible rather than alarming:
--
--     62 violations  ibkr_fills          (fill + commission capture, Tier 1)
--      5 violations  breakout_learnings  (screener outcome feedback loop)
--
-- Both are confirmed at 0 rows.

ALTER TABLE ibkr_fills ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'ibkr_fills' AND policyname = 'Service role full access'
    ) THEN
        CREATE POLICY "Service role full access" ON ibkr_fills
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- breakout_learnings: written by _write_breakout_learning_row() on every exit so
-- the screener can learn which setups worked. Silently discarded on every trade
-- to date, so that feedback loop has never had any data to learn from.
ALTER TABLE breakout_learnings ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'breakout_learnings' AND policyname = 'Service role full access'
    ) THEN
        CREATE POLICY "Service role full access" ON breakout_learnings
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Verification -- should return one row:
-- SELECT tablename, policyname, cmd FROM pg_policies WHERE tablename = 'ibkr_fills';
--
-- After the next fill, this should be non-zero:
-- SELECT count(*) FROM ibkr_fills;
--
-- Audit for any OTHER table with RLS enabled but no policy (same silent failure
-- mode, and the reason this went unnoticed for so long):
--   SELECT c.relname AS table_with_rls_but_no_policy
--   FROM pg_class c
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--   WHERE n.nspname = 'public'
--     AND c.relrowsecurity
--     AND NOT EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname)
--   ORDER BY 1;
