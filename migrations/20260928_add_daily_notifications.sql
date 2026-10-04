-- Migration: daily_notifications dedup ledger
-- Run once in Supabase SQL Editor. Idempotent and re-runnable.
--
-- WHY THIS EXISTS
-- ---------------
-- The execution agent's buy check (run_market_open_buys) runs every 15 minutes
-- while the market is open — roughly 26 times a day. The "unfilled slots"
-- operator summary must fire AT MOST ONCE PER DAY, and must keep that promise
-- across container restarts (an in-memory flag would resend after every deploy
-- or crash-loop). This table is the persistent, restart-safe dedup ledger:
-- one row per (report_type, report_date) means "already sent today".
--
-- It is deliberately generic — keyed by report_type — so any future once-a-day
-- notification can reuse it without a new table.
--
-- Included in the required weekly backup inventory as of 2026-10-04, alongside
-- research delivery state, to retain the recorded notification history.
-- Missing schema or permissions fail the backup rather than silently omitting
-- this ledger. See decisions/2026-10-04_backup-vault-and-private-exit-shadow.md.
--
-- See decisions/2026-09-28_unfilled-slot-daily-alert.md.

CREATE TABLE IF NOT EXISTS daily_notifications (
    report_type TEXT        NOT NULL,
    report_date DATE        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail      TEXT,
    PRIMARY KEY (report_type, report_date)
);

-- End-of-file verification: report OK/FAIL for the operator applying by hand.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_name = 'daily_notifications') THEN
        RAISE NOTICE 'daily_notifications: OK';
    ELSE
        RAISE NOTICE 'daily_notifications: FAIL (table not created)';
    END IF;
END $$;
