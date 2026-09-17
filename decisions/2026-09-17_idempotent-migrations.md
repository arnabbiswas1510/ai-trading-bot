# Migrations must be re-runnable

**Date:** 2026-09-17
**Status:** Accepted

## Context

`migrations/20260917_add_rs_percentile.sql` was pending application. While
applying it, the operator instead ran `20260624_migration_retention_period.sql`
— a migration applied back in June — and Postgres aborted with:

```
ERROR: column "retention_period" of relation "watchlist" already exists
```

Nothing was damaged. The Supabase SQL Editor runs a script inside a transaction,
so the failure rolled the whole thing back; `retention_period` was verified still
present on both `watchlist` and `daily_triggers`, the three `DROP COLUMN` targets
were confirmed already absent, and all 104 `watchlist` rows were intact.

But the incident cost real time, and the error message actively misled: it named
a table and column that had nothing to do with the migration the operator meant
to apply, so the first reasonable hypothesis was that the *pending* migration was
broken.

Two things made this likely:

1. **Nothing in this repo runs migrations.** They are applied by hand in the
   Supabase SQL Editor. There is no ledger of what has been applied, no
   `schema_migrations` table, and no tool that refuses to run something twice.
   The only protection against a double-run is the file being safe to re-run.

2. **The date-prefix convention concentrated the risk.** Normalising all 39
   migrations to `YYYYMMDD_slug.sql` on 2026-09-17
   (`decisions/2026-09-17_migration-naming-convention.md`) was the right call for
   sequence legibility, but it puts every migration — applied and pending — into
   one uniform sorted list. Picking the wrong line out of that list is easy, and
   the convention offers no visual distinction between "applied in June" and
   "pending today".

An audit of all 40 migrations found exactly **two** that were not re-runnable,
both from 2026-06-24, predating the `IF NOT EXISTS` convention that every later
migration already follows:

| File | Defect |
|---|---|
| `20260624_migration_retention_period.sql` | two bare `ADD COLUMN` |
| `20260624_twr_schema.sql` | bare `ADD COLUMN`, plus `ADD PRIMARY KEY (date, key)` |

The second was worse than it looked. `account_balances.key` **no longer exists** —
the live table (verified 2026-09-17) is `date, ibkr_cash_balance,
ibkr_positions_value, ibkr_total_value, ibkr_own_cash, ibkr_margin_loan`. So that
final statement cannot succeed on any current schema, and `IF NOT EXISTS` cannot
save it: the failure is in the *referenced* column, not the created object.

## Decision

**Every migration must succeed, and change nothing, when run a second time.**

1. Both files were repaired. Guarded DDL (`IF NOT EXISTS` / `IF EXISTS`) where
   that suffices; a `DO $$ ... $$` precondition block for the primary-key step,
   which runs only if `key` still exists *and* no primary key is present, and
   otherwise `RAISE NOTICE`s that it skipped.

2. The historical step was made **inert, not modernised.** The PK block is dead
   code on today's schema by design. An old migration's job is to record what was
   done at the time, not to reshape a table it no longer describes — rewriting it
   to impose a current-schema PK would silently alter production on the next
   accidental run, which is the failure mode this ADR exists to close.

3. Both files gained the `*** ALREADY APPLIED TO PRODUCTION` header already used
   by `20260910_backfill_ntra_round_trips.sql` and
   `20260915_fix_nbix_reconstructed_sell.sql`, and a closing verification
   `SELECT` that prints `OK`/`FAIL` per object so a re-run produces visible
   confirmation instead of silence.

4. The rule is enforced by `tests/test_migrations_idempotent.py`, which scans
   every file in `migrations/` for unguarded `ADD COLUMN`, `DROP COLUMN`,
   `CREATE TABLE` and `CREATE INDEX`. It runs in the normal suite, so a
   non-idempotent migration cannot be committed. Comments and `DO $$` bodies are
   stripped before scanning — these files quote the failing SQL in their own
   headers, and the DO block is the sanctioned escape hatch.

5. AGENTS.md gained a mandatory section, sited directly after the naming rule
   that concentrated the risk.

## Verification

Checked against a real PostgreSQL 15 instance rather than by inspection, because
the interesting cases are precisely the ones that are hard to predict by reading.

Two fixtures: **A**, the original 2026-06-24 pre-migration schema (with `key`);
**B**, today's live schema (no `key`, already migrated).

| Scenario | Run 1 | Run 2 | Outcome |
|---|---|---|---|
| A — original schema | ✅ | ✅ | `retention_period` added; PK `(date, key)` created |
| B — today's schema | ✅ | ✅ | no-op; PK step skipped; **no PK invented** |

Scenario B's `account_balances` was byte-identical afterwards — six columns, no
primary key — confirming the guard does not mutate the evolved schema.

The repair is **not vacuous**: the pre-change files were replayed against
fixture B and reproduced the production error exactly.

```
OLD retention_period : ERROR: column "retention_period" of relation "watchlist" already exists
OLD twr_schema       : ERROR: column "date" of relation "account_balances" already exists
```

The test was likewise checked against the old files — it fails on both, and on
both pinning assertions, then passes once repaired (4 failed → 50 passed).

## Consequences

- An accidental re-run is now a silent no-op with an `OK` report, not an abort.
- The two 2026-06-24 files remain accurate records of what was done in June.
- `account_balances` still has **no primary key** on the live schema. This ADR
  does not change that, and deliberately does not use an accidental re-run as the
  mechanism to fix it. If a PK is wanted there it needs its own dated migration
  and its own decision.
- The scanner is textual, so it cannot catch a semantic non-idempotency such as
  an unguarded `UPDATE` that double-applies. Data-repair migrations still need
  hand-written guards — see the `WHERE ... = 152.74` idempotency guard in
  `20260915_fix_nbix_reconstructed_sell.sql`.

## Related

- `decisions/2026-09-17_migration-naming-convention.md` — the rename that made
  mis-selection easy, and which this rule complements.
- `schema_guard.py` — the runtime counterpart: detects *missing* columns and
  degrades rather than dying. This ADR covers the authoring side of the same
  problem.
