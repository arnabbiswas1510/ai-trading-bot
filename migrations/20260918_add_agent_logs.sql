-- 20260918_add_agent_logs.sql
--
-- ⚠️ SUPERSEDED THE SAME DAY by migrations/20260918_expand_agent_logs.sql,
-- which is what you should apply. The description below of WHAT gets shipped
-- ("only TeeLogger.SHIP_MARKERS lines") is no longer true: the full log ships
-- now, and volume is controlled by tiered retention rather than by filtering
-- at capture time. The table comments written here are overwritten by the
-- expand migration. Kept as the historical record; it is still safe to run
-- first, and the expand migration works whether or not it was.
-- See decisions/2026-09-18_comprehensive-log-shipping.md.
--
-- Noteworthy execution-agent log lines, shipped to Supabase.
--
-- The production host runs on a home network that is unreachable from most
-- corporate networks, so `docker logs execution-agent` is frequently
-- unavailable at exactly the moment a problem needs diagnosing. That is how the
-- 2026-09-18 Telegram outage went unexplained: five buys and a broker-side
-- close went unannounced, and the logs that would have named the cause could
-- not be read from where the question was being asked.
--
-- Exposing the host to the internet was rejected -- it runs ib-gateway with
-- READ_ONLY_API=no, so it can submit live orders, and inbound exposure is a
-- poor trade for log visibility. Supabase is already reachable, already
-- credentialed and already in the architecture.
--
-- This table is deliberately NOT a copy of stdout. Only lines matching
-- TeeLogger.SHIP_MARKERS are shipped (alert-channel failures, criticals,
-- tracebacks, errors and warnings), and every line is passed through
-- TeeLogger.redact() first, which strips IBKR account numbers, Telegram bot
-- tokens, JWTs and key=value secrets. Position sizes, cash balances and
-- ordinary cycle output never leave the host.
--
-- Retention: rows older than AGENT_LOG_RETENTION_DAYS (default 14) are deleted
-- by the agent at most once per day. Without that this is the one table in the
-- system that would grow without bound.
--
-- See decisions/2026-09-18_supabase-log-shipping.md.

CREATE TABLE IF NOT EXISTS agent_logs (
    id         BIGSERIAL PRIMARY KEY,
    logged_at  TIMESTAMPTZ NOT NULL,
    level      TEXT        NOT NULL,
    message    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The two access patterns: "what happened recently" and the retention sweep,
-- both of which are ordered/filtered by logged_at.
CREATE INDEX IF NOT EXISTS idx_agent_logs_logged_at ON agent_logs (logged_at DESC);

-- Cheap filter for "show me only alert-channel failures" / "only criticals".
CREATE INDEX IF NOT EXISTS idx_agent_logs_level ON agent_logs (level, logged_at DESC);

COMMENT ON TABLE agent_logs IS
    'Filtered, redacted execution-agent log lines. NOT a full stdout mirror: '
    'only TeeLogger.SHIP_MARKERS lines are shipped, and redaction runs before '
    'transmission. Retention is enforced by the agent, not by the database.';

COMMENT ON COLUMN agent_logs.level IS
    'ALERT_CHANNEL (Telegram delivery failure) | CRITICAL | ERROR | WARN';

COMMENT ON COLUMN agent_logs.logged_at IS
    'America/New_York timestamp recorded on the host when the line was emitted, '
    'not when it was shipped — a restart can delay shipping by a cycle.';

-- RLS: this table carries operational detail about a live trading system and
-- must never be readable by the anon role. Service-role access only, matching
-- the policy pattern established in 20260906_fix_rls_missing_policies.sql.
ALTER TABLE agent_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'agent_logs' AND policyname = 'agent_logs_service_role_all'
    ) THEN
        CREATE POLICY agent_logs_service_role_all ON agent_logs
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
