# Isolate trading-state backups from calibration and benchmarking

- **Date:** 2026-10-04
- **Status:** Accepted
- **Scope:** Backup inventory and credentials only; no trading or research rules

## Context

The operator reports recurring backup grant failures and explicitly requests
exclusion of the entire calibration/benchmarking subsystem, including replay,
research reporting, simulated shadow portfolios and candidate exit observations.
The exporter currently attempts every required table and exits nonzero if any
read fails; the workflow therefore never ships the otherwise successful files.
Broadening grants for research tables is not the requested recovery policy.

A read-only local probe using the ordinary `SUPABASE_KEY` could read all 13
retained trading-state tables and `exit_shadow_log`; requests to the other 13
formerly required research tables returned authorization errors. This is not a
reproduction with the workflow's private credential, nor confirmation of the
latest Actions failure log. The code-level coupling is independently clear.

## Decision

Reduce required exports from 27 to 13 tables by moving 14 research entries
from `supabase_backup.TABLES` into its explicit `NOT_BACKED_UP` inventory.
All known `intraday_*` tables/views and `exit_shadow_log` are excluded.
Keep account balances, cash flows, live positions, fills, trades, exit requests,
watchlists, triggers, their histories/decisions, breakout learnings and daily
notification deduplication. Keep the existing raw-capture/log exclusions.

Do not query excluded tables, even when explicitly requested through `--tables`.
Print exclusions and record their reasons as `excluded_tables` in each manifest.
Keep permission, missing-table, count and write failures fatal for retained
tables; this is a deliberate scope reduction, not permission-error suppression.

The backup uses only `SUPABASE_KEY`. Its Bitwarden wrapper requests
`SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS`;
it neither requires nor forwards `INTRADAY_SUPABASE_KEY`. The shared loader
accepts a job-specific required-secret tuple, retaining the watchdog's original
five-secret default and strict validation. No grants, migrations, research
writers, live trading rules or production data are changed.

## Consequences

Calibration configuration, decisions, saved benchmarks, simulated trades,
reports and exit-shadow evidence cannot be restored from future weekly
snapshots. They are not all reproducible; the operator explicitly accepts this
recovery limitation. Preserve any needed research evidence independently.
Existing server archives remain untouched; this change is not retroactive
deletion or a research-data purge.

Deployment and a successful workflow rerun are still required for a new archive.
The local probe does not prove Bitwarden bootstrap, SSH shipping or production
restore. See `docs/backups.md` for inventory and operation,
`docs/configuration.md` for credentials and `docs/retired_code.md` for the
removed paths and restoration context.
