-- 20260918_expand_agent_logs.sql
--
-- Widen agent_logs from "noteworthy lines only" to the FULL agent log.
--
-- The original design (migrations/20260918_add_agent_logs.sql) shipped only
-- lines matching TeeLogger.SHIP_MARKERS. That answered "is the alert channel
-- dead?" but not the question actually being asked at work: "what was the bot
-- doing when it decided that?" A stack trace without the twenty lines that
-- preceded it explains nothing.
--
-- So every line ships now, and volume is controlled here and in the agent
-- rather than by discarding context at capture time:
--
--   * level now includes INFO and TRADE, and the two expire on DIFFERENT
--     schedules -- routine chatter after AGENT_LOG_INFO_RETENTION_DAYS (3),
--     WARN and above after AGENT_LOG_RETENTION_DAYS (14). Tiered retention is
--     what makes the firehose affordable.
--   * repeat_count collapses consecutive identical lines into one row, so a
--     stuck retry loop cannot fill the retention window.
--   * session_id + seq restore exact ordering and separate one container run
--     from the next -- without them, interleaved lines from a restart loop are
--     indistinguishable from one long session, which is the very shape a
--     crash-restart bug takes.
--
-- Idempotent and safe in either order: it creates the table if the first
-- migration was never applied, and only adds what is missing if it was.
--
-- See decisions/2026-09-18_comprehensive-log-shipping.md.

CREATE TABLE IF NOT EXISTS agent_logs (
    id         BIGSERIAL PRIMARY KEY,
    logged_at  TIMESTAMPTZ NOT NULL,
    level      TEXT        NOT NULL,
    message    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS session_id   TEXT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS seq          BIGINT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS repeat_count INTEGER NOT NULL DEFAULT 1;

COMMENT ON COLUMN agent_logs.session_id IS
    'Identifies one container run (start time, America/New_York). Distinguishes '
    'a restart loop from one long session.';
COMMENT ON COLUMN agent_logs.seq IS
    'Monotonic line number within session_id. Restores ordering when several '
    'lines share a logged_at timestamp.';
COMMENT ON COLUMN agent_logs.repeat_count IS
    'Consecutive identical lines collapsed into this row. 1 = not repeated.';
COMMENT ON COLUMN agent_logs.level IS
    'ALERT_CHANNEL | CRITICAL | ERROR | WARN | TRADE | INFO. TRADE and INFO are '
    'purged on the SHORT retention window; the rest on the long one.';

-- Replay one container run in exact emission order -- the single most common
-- query when reconstructing what the agent did.
CREATE INDEX IF NOT EXISTS idx_agent_logs_session_seq
    ON agent_logs (session_id, seq);

-- The retention sweep filters on (level, logged_at); the existing
-- idx_agent_logs_level covers it. Recreated here for the case where the first
-- migration was never applied.
CREATE INDEX IF NOT EXISTS idx_agent_logs_logged_at ON agent_logs (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_level     ON agent_logs (level, logged_at DESC);

COMMENT ON TABLE agent_logs IS
    'Full, redacted execution-agent log. Redaction (IBKR account numbers, '
    'Telegram bot tokens, JWTs, key=value secrets) runs on the host before '
    'transmission. Retention is tiered and enforced by the agent, not the '
    'database: INFO/TRADE expire early, WARN+ late, plus a hard row ceiling.';

-- RLS: this table now carries the agent's complete operational narrative and
-- must never be readable by the anon role.
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
