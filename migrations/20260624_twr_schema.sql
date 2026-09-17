-- Migration: time-weighted-return support (cash_flows + dated account_balances)
--
-- *** ALREADY APPLIED TO PRODUCTION (2026-06-24). Kept as the record of what was
-- *** done. Re-running is a safe no-op: every statement is guarded, and the
-- *** primary-key step self-skips on the CURRENT schema (see below).
--
-- WHY THE GUARDS EXIST
-- --------------------
-- This file originally used bare `ADD COLUMN` and a bare `ADD PRIMARY KEY`. On
-- 2026-09-17 its sibling (20260624_migration_retention_period.sql) was re-run by
-- mistake and aborted; auditing for the same defect found only these two files
-- unguarded out of the whole migrations/ directory.
--
-- THIS ONE NEEDED MORE THAN `IF NOT EXISTS`
-- -----------------------------------------
-- `account_balances` has since dropped the `key` column, so the historical final
-- step -- ADD PRIMARY KEY (date, key) -- can no longer run at all: it now fails
-- with `column "key" named in key does not exist`, which IF NOT EXISTS cannot
-- prevent. The live table (verified 2026-09-17) is:
--
--     date, ibkr_cash_balance, ibkr_positions_value,
--     ibkr_total_value, ibkr_own_cash, ibkr_margin_loan
--
-- The PK step is therefore wrapped in a DO block that runs ONLY if `key` still
-- exists and the table has no primary key. On today's schema both conditions are
-- false, so it is skipped. That preserves the file as an accurate record of the
-- 2026-06-24 intent while making it inert against the schema that exists now --
-- deliberately NOT rewritten to impose a modern PK, because this file's job is to
-- document history, not to reshape a table it no longer describes.
-- See decisions/2026-09-17_idempotent-migrations.md.

CREATE TABLE IF NOT EXISTS cash_flows (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date DATE NOT NULL,
    amount NUMERIC NOT NULL,
    description TEXT
);

-- 1. Add the date column.
ALTER TABLE account_balances ADD COLUMN IF NOT EXISTS date DATE;

-- 2. Backfill pre-existing rows.
UPDATE account_balances SET date = CURRENT_DATE WHERE date IS NULL;

-- 3. Enforce NOT NULL (idempotent: SET NOT NULL on an already-NOT NULL column
--    is accepted by Postgres and does nothing).
ALTER TABLE account_balances ALTER COLUMN date SET NOT NULL;

-- 4/5. Historical re-key to (date, key). Skipped unless `key` exists and no
--      primary key is present -- i.e. skipped on every schema since the `key`
--      column was dropped.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'account_balances' AND column_name = 'key')
       AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                       WHERE table_name = 'account_balances'
                         AND constraint_type = 'PRIMARY KEY')
    THEN
        EXECUTE 'ALTER TABLE account_balances ADD PRIMARY KEY (date, key)';
        RAISE NOTICE 'account_balances: composite primary key (date, key) added.';
    ELSE
        RAISE NOTICE 'account_balances: primary-key step skipped (no "key" column, or a primary key already exists).';
    END IF;
END $$;

-- Verification
SELECT 'account_balances.date' AS object,
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='account_balances' AND column_name='date')
            THEN 'OK' ELSE 'FAIL' END AS status
UNION ALL
SELECT 'cash_flows table',
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables
                         WHERE table_name='cash_flows')
            THEN 'OK' ELSE 'FAIL' END;
