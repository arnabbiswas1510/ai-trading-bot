-- Migration: watchlist / daily_triggers retention_period
--
-- *** ALREADY APPLIED TO PRODUCTION (2026-06-24). Kept as the record of what was
-- *** done. Re-running is a safe no-op: every statement is guarded.
--
-- WHY THE GUARDS EXIST
-- --------------------
-- This file originally used bare `ADD COLUMN`. On 2026-09-17 it was run a second
-- time by mistake -- the date-prefix rename sorts every migration together, so
-- picking a long-applied file out of the list is easy. Postgres aborted with:
--
--     ERROR: column "retention_period" of relation "watchlist" already exists
--
-- No damage resulted (the Supabase SQL Editor runs a script in one transaction, so
-- the whole thing rolled back) but the failure was alarming, and the error named a
-- migration the operator had not intended to run. An applied migration should be
-- re-runnable and silent, not a trap. Every migration written after 2026-06-24
-- already uses IF NOT EXISTS; this was one of the last two unguarded stragglers.
-- See decisions/2026-09-17_idempotent-migrations.md.

-- Superseded by retention_period: the pre-2026-06-24 rolling-retention columns.
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS weeks_retained;
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS first_seen_at;
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS last_seen_at;

ALTER TABLE public.watchlist      ADD COLUMN IF NOT EXISTS retention_period TEXT DEFAULT '1d';
ALTER TABLE public.daily_triggers ADD COLUMN IF NOT EXISTS retention_period TEXT DEFAULT '1d';

-- Verification
SELECT 'watchlist.retention_period' AS column,
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='watchlist' AND column_name='retention_period')
            THEN 'OK' ELSE 'FAIL' END AS status
UNION ALL
SELECT 'daily_triggers.retention_period',
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='daily_triggers' AND column_name='retention_period')
            THEN 'OK' ELSE 'FAIL' END;
