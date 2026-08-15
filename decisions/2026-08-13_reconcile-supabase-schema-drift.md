# 2026-08-13 — Reconcile Supabase schema drift (7 unapplied migrations)

## Status
Accepted

## Context

While investigating the NBIX loss and the PRE_BREAKOUT-vs-BREAKOUT ranking
question, two separate queries against the live Supabase project came back
empty in ways that should have been impossible:

- `portfolio_positions.closed_above_entry` → `42703` (undefined column)
- `trigger_history`, `trigger_decisions`, `trigger_outcomes` → `PGRST205`
  (table not found)

The migrations were believed to have been applied. They had not been — at least
not to project `yhaynfrmsjzbybbehfjs`, which is the project the bot's
`SUPABASE_URL` points at.

Rather than guess, every `CREATE TABLE` and `ADD COLUMN` target in
`migrations/*.sql` was extracted and probed individually against the live
PostgREST API. That is the audit this ADR records.

## Findings

Applied and healthy (no action): `cash_flows`, `ibkr_fills`,
`breakout_learnings`, all 15 `daily_triggers` columns, 26 of 29
`portfolio_positions` columns, margin tracking, power-hold, plateau/rotation,
armed-exit, TWR, retention period, and RLS on the five original tables.

Missing — 4 tables:

| object | source migration |
|---|---|
| `trigger_history` | `add_trigger_history.sql` |
| `trigger_decisions` | `add_trigger_history.sql` |
| `trigger_history` outcome columns | `add_trigger_outcomes.sql` |
| `watchlist_history` | `add_watchlist_history.sql` |

Missing — 3 columns, all on `portfolio_positions`:

| column | source migration |
|---|---|
| `closed_above_entry` | `add_closed_above_entry.sql` |
| `highest_rs_score` | `add_highest_rs_score.sql` |
| `hwm_rs_score` | `add_hwm_rs_score.sql` |

## Why this mattered operationally

This was not cosmetic drift. Three shipped rules were silently inert in
production:

1. **Thesis Stop** reads `closed_above_entry` to confine itself to breakouts
   that never followed through. With the column absent the code takes its
   conservative fallback path. This is the column named in the NBIX
   post-mortem.
2. **Rule 1 (RS Decay)** compares `live_rs_score` against `hwm_rs_score`. With
   both anchor columns absent the rule has nothing to compare to and is
   skipped, so RS breakdown never produced an exit.
3. **The counterfactual archive did not exist.** `technical_screener.py` and
   `tv_api_screener.py` both truncate their tables on every run. Without
   `trigger_history` / `trigger_decisions` / `watchlist_history`, every
   rejected candidate — the control group — was destroyed daily. This is
   precisely what forced
   `2026-08-13_reject-confirmed-breakout-first-ranking.md` to fall back to a
   historical replay with a proxy score instead of using real archived
   `final_score` values.

The general lesson: a migration that exists in the repo is not evidence that it
is applied. Code that reads a column which silently does not exist degrades to
a fallback path rather than failing loudly, so the drift produced no alert.

## Decision

Ship a single consolidated, idempotent repair script:
`migrations/2026-08-13_apply_missing_migrations.sql`.

Design choices:

- **Only genuinely absent objects are included.** Already-applied migrations are
  not repeated, so the script is small enough to read before running against a
  live trading database.
- **Fully idempotent** — `IF NOT EXISTS` on every create, `ON CONFLICT DO
  NOTHING` on both seeds, guarded `UPDATE`, and the `pg_policies` existence
  check used by `enable_rls_all_tables.sql`. Safe to re-run.
- **Ordered by dependency** — `trigger_history` is created before the
  `add_trigger_outcomes.sql` columns are added to it.
- **Seeds run last** so a data problem cannot block the schema changes.
- **Backfills preserve existing semantics** — `closed_above_entry` is latched
  TRUE for any position with a positive peak, so currently-open positions are
  not retroactively exposed to the Thesis Stop.
- **RLS applied to the new tables** to match the existing convention.
- **Section 8 is a verification query** that must return `OK` on every row,
  because "I ran it and saw no error" is exactly the assumption that produced
  this drift.

Type compatibility was checked against live data before shipping, since
`INSERT ... SELECT` will not implicitly cast: `retention_period` is `TEXT` on
both `daily_triggers` and `watchlist`, `float_shares` is a big integer, and
`est_days_to_target` is an integer. The script parses cleanly as Postgres (39
statements).

## Consequences

- The Thesis Stop and RS Decay rules become live for the first time. Their
  behaviour should be watched over the next few sessions — they have never
  actually fired in production.
- `trigger_history` and `watchlist_history` are seeded from current state, so
  the archive starts today rather than at the next scheduled run.
- The forward-looking PRE_BREAKOUT-vs-BREAKOUT study can begin accumulating
  real data, and `backfill_trigger_outcomes.py` has a table to write into.

## Follow-up

Add a startup schema assertion to the execution agent so a missing column fails
loudly at boot rather than degrading a trading rule into a fallback path. Not
done here — it is a code change with its own testing burden, and this ADR is
scoped to the schema repair.

## How to apply

Paste `migrations/2026-08-13_apply_missing_migrations.sql` into the Supabase SQL
Editor for project `yhaynfrmsjzbybbehfjs` and run it. Confirm the final
verification query returns `OK` for all eight object rows.
