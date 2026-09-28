-- 20260928_add_next_earnings_date_to_daily_triggers.sql
--
-- Store each trigger's next scheduled earnings date so the buy loop can defer a
-- purchase that would otherwise open right before a report.
--
-- WHY THIS EXISTS
--
-- A freshly opened position sits under the Prove-It stop's tight floor (−1% on
-- day 0, −3% thereafter). Buying a name that reports earnings in the next couple
-- of sessions is therefore close to a coin-flip that the stop cashes it out at a
-- loss on the post-report gap -- and that loss then trips the reason-aware
-- cooling-off lockout, compounding an avoidable cost that was fully knowable
-- BEFORE the buy.
--
-- ai_evaluator.py now fetches the next earnings date per trigger from FMP
-- (/stable/earnings?symbol=) and writes it here; buying.py reads it and DEFERS
-- any buy inside EARNINGS_BLACKOUT_TRADING_DAYS (default 3) NYSE trading days.
-- It is a deferral, not a veto: the breakout can re-trigger and be bought once
-- the report clears. A NULL date means "not known / not fetched" and the buy
-- guard fails OPEN, so a per-name data gap never blocks every buy.
--
-- Until 2026-09-28 the AI's news feed was silently dead (the legacy
-- /api/v3/stock_news endpoint returns 403 on the current FMP plan), so imminent
-- earnings were invisible to the model; this column makes the protection
-- deterministic and independent of the model.
--
-- Re-runnable: guarded with IF NOT EXISTS per the idempotent-migrations rule.
--
-- See decisions/2026-09-28_earnings-blackout-and-news-veto.md

ALTER TABLE daily_triggers
    ADD COLUMN IF NOT EXISTS next_earnings_date DATE DEFAULT NULL;

COMMENT ON COLUMN daily_triggers.next_earnings_date IS
  'Next scheduled earnings date for this ticker, fetched by ai_evaluator.py from '
  'FMP /stable/earnings. Consumed by the buying.py earnings blackout, which defers '
  'any buy within EARNINGS_BLACKOUT_TRADING_DAYS trading days. NULL = unknown/not '
  'fetched; the buy guard fails OPEN on NULL.';

-- Verification:
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='daily_triggers' AND column_name='next_earnings_date') THEN
        RAISE NOTICE 'OK: daily_triggers.next_earnings_date present.';
    ELSE
        RAISE WARNING 'FAIL: daily_triggers.next_earnings_date missing.';
    END IF;
END $$;
