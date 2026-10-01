-- 20261001_daily_notifications_rls.sql
--
-- Let the agent actually write daily_notifications.
--
-- THE BUG. migrations/20260928_add_daily_notifications.sql created the dedup
-- ledger that keeps the once-a-day "unfilled slots" summary to one message per
-- ET day. The table has row-level security ENABLED but NO policy that applies to
-- the publishable/anon role the execution agent authenticates with
-- (SUPABASE_KEY is `sb_publishable_...` on the production host). The result,
-- verified live on 2026-10-01 with the agent's own key:
--
--     SELECT ... FROM daily_notifications  -> 200, zero rows (RLS hides them)
--     UPSERT ... INTO daily_notifications  -> 42501 "new row violates row-level
--                                             security policy"
--
-- buying.maybe_report_unfilled_slots reads the latch (empty, because the write
-- was denied), sends the summary, then tries to latch the day -- and that write
-- is refused. run_market_open_buys runs every 15 minutes, so the latch never
-- persists and the operator receives ~26 identical UNFILLED SLOTS alerts a day.
-- This is the exact failure agent_logs hit on 2026-09-18
-- (migrations/20260918_relax_agent_logs_rls.sql); the fix is the same shape.
--
-- WHY THE SELECT-BASED GUARD DID NOT CATCH IT. Under RLS a denied SELECT returns
-- 200 with zero rows, not an error, so _slot_report_already_sent() saw "nothing
-- sent today" rather than "access denied" and re-sent every cycle. "Empty
-- because it is a new day" and "empty because RLS hides the row" are indistin-
-- guishable from the read side. Only an explicit INSERT/UPSERT probe separates
-- them -- that is how this was confirmed.
--
-- THE FIX. Add a policy with NO `TO` clause, which applies to PUBLIC and there-
-- fore to the publishable key, matching every other table in this database.
-- daily_notifications holds only report_type/report_date/sent_at/detail -- it is
-- regenerable operational state, never trading data or a credential -- so making
-- it readable by the publishable key widens nothing that matters.
--
-- See decisions/2026-10-01_daily-notifications-rls-blocked-writes.md.

ALTER TABLE daily_notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS daily_notifications_service_role_all ON daily_notifications;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'daily_notifications'
          AND policyname = 'daily_notifications_full_access'
    ) THEN
        CREATE POLICY daily_notifications_full_access ON daily_notifications
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- End-of-file verification: report OK/FAIL for the operator applying by hand.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'daily_notifications'
          AND policyname = 'daily_notifications_full_access'
    ) THEN
        RAISE NOTICE 'daily_notifications RLS policy: OK';
    ELSE
        RAISE NOTICE 'daily_notifications RLS policy: FAIL (policy not created)';
    END IF;
END $$;
