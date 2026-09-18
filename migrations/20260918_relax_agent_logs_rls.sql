-- 20260918_relax_agent_logs_rls.sql
--
-- Let the agent actually write agent_logs.
--
-- THE BUG. migrations/20260918_add_agent_logs.sql created the table with a
-- policy scoped to ONE role:
--
--     CREATE POLICY agent_logs_service_role_all ON agent_logs
--         FOR ALL TO service_role USING (true) WITH CHECK (true);
--
-- Every other table in this database uses a policy with NO `TO` clause, which
-- applies to PUBLIC and therefore to the anon/publishable key:
--
--     CREATE POLICY "Service role full access" ON ibkr_fills
--         FOR ALL USING (true) WITH CHECK (true);
--
-- The execution agent authenticates with a single SUPABASE_KEY (execution_agent
-- L380). On a host where that is the publishable key, every log flush fails:
--
--     401  {"code":"42501","message":"new row violates row-level security
--           policy for table \"agent_logs\""}
--
-- and flush_logs_to_supabase() deliberately swallows that error to stderr, so
-- the failure is visible ONLY via `docker logs` on the production host -- the
-- one place the operator cannot reach from work, which is the entire reason
-- log shipping was built. The feature defeated its own purpose.
--
-- WHY THIS WAS NOT CAUGHT. schema_guard.py probes agent_logs with a SELECT.
-- Under RLS a denied SELECT returns 200 with zero rows, not an error, so the
-- guard confirmed the table EXISTS while it was silently unwritable. "Empty
-- because nothing shipped" and "empty because RLS hides it" are indistinguish-
-- able from outside. Verified 2026-09-18 by an explicit INSERT probe, which is
-- the only test that separates them.
--
-- THE TRADE-OFF, ACCEPTED DELIBERATELY. Dropping `TO service_role` makes these
-- logs readable by anyone holding the publishable key. That is a real widening:
-- agent_logs carries the bot's complete operational narrative. It is accepted
-- because (a) it matches every other table here, so there is one rule rather
-- than two, (b) the alternative requires provisioning and distributing a second
-- key to the host and to every research script, and (c) redaction already runs
-- on the host BEFORE transmission and is now the primary control rather than a
-- second layer -- which is why TeeLogger._REDACTIONS was hardened in the same
-- commit to cover Supabase's sb_publishable_/sb_secret_ key format, previously
-- unmatched by the JWT pattern.
--
-- Idempotent: drops the restrictive policy if present, creates the permissive
-- one only if absent. Safe to re-run.
--
-- See decisions/2026-09-18_agent-logs-rls-blocked-writes.md.

ALTER TABLE agent_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_logs_service_role_all ON agent_logs;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'agent_logs' AND policyname = 'agent_logs_full_access'
    ) THEN
        CREATE POLICY agent_logs_full_access ON agent_logs
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

COMMENT ON TABLE agent_logs IS
    'Full, redacted execution-agent log. READABLE BY THE PUBLISHABLE KEY -- '
    'redaction on the host (IBKR account numbers, Telegram bot tokens, JWTs, '
    'sb_publishable_/sb_secret_ keys, key=value secrets) is the PRIMARY control '
    'protecting it, not a backstop. Never log a raw credential on the '
    'assumption this table is private. Retention is tiered and enforced by the '
    'agent: INFO/TRADE expire early, WARN+ late, plus a hard row ceiling.';

-- Verification -- should return exactly one row, with roles = {public}:
--   SELECT policyname, roles, cmd FROM pg_policies WHERE tablename = 'agent_logs';
