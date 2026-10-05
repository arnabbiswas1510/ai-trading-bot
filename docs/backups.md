# Backups

Supabase is the only durable home for the bot's trading state. `trade_history`
in particular is the sole record of what this strategy did with real money, and
every scheduled parameter review in
[`tech_debt_and_requirements_tracker.md`](tech_debt_and_requirements_tracker.md)
replays it. It is backed up weekly to flat files on the production server.

See `decisions/2026-08-21_weekly-supabase-backup.md` for why it is designed this
way.

## What runs, and when

The workflow uses `scripts/run_supabase_backup_bws.py`, which reuses the
watchdog's Bitwarden loader and no-cache `scripts/bws_ci.toml` profile. Configure
only the GitHub Actions application bootstrap secret `BWS_ACCESS_TOKEN`; its
machine account must read the `ai-trading-bot` Bitwarden project. That project
must contain one valid value each for `SUPABASE_URL`, `SUPABASE_KEY`,
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS`. Values are
loaded into the exporter subprocess environment only, masked in Actions output,
and never written to an environment file or CLI cache. Missing/ambiguous vault
values fail before export, without falling back to individual Actions secrets.

The exporter uses only `SUPABASE_KEY`. The backup neither requires nor forwards
`INTRADAY_SUPABASE_KEY`; the independent research watchdog still requires it.
Production SSH secrets remain Actions secrets.

The required inventory contains **13 trading-state tables**:

| Tables | Stable export ordering |
|---|---|
| `account_balances` | `date` |
| `breakout_learnings`, `cash_flows`, `exit_requests`, `trade_history` | `id` |
| `daily_notifications` | `report_type`, `report_date` |
| `daily_triggers`, `trigger_history` | `triggered_at`, `ticker` |
| `ibkr_fills` | `exec_id` |
| `portfolio_positions`, `watchlist` | `ticker` |
| `trigger_decisions` | `decision_date`, `ticker` |
| `watchlist_history` | `snapshot_date`, `ticker` |

A missing table or denied read for any required table still fails the backup.
Permissions failures are not silently ignored.

**All calibration and benchmarking research is excluded:** every
`intraday_calibration_*`, `intraday_research_*`, `intraday_shadow_*` and
`intraday_capture_*` table/view, plus `intraday_replay_runs` and `exit_shadow_log`.
`agent_logs` remains excluded under its existing retention policy.
Each known exclusion has an explicit reason in `supabase_backup.NOT_BACKED_UP`;
the exporter reports excluded names and records `excluded_tables` with reasons
in the manifest. `--tables` cannot override these exclusions.

This is a recovery limitation, not a claim that research history can be
recreated. Weekly backups cannot restore calibration settings/decisions,
benchmarks, simulated trades or their reports. Export any research evidence
that must survive independently, including raw inputs before
`INTRADAY_RETENTION_DAYS` (365 by default) expires. Existing server archives,
Supabase tables and research workers are not deleted or altered.
See `decisions/2026-10-04_trading-only-backup-scope.md` for why.

| | |
|---|---|
| Workflow | `.github/workflows/weekly_supabase_backup.yml` |
| Schedule | Sundays, 14:00 UTC |
| Script | `supabase_backup.py` |
| Destination | `/home/pom/docker/ai-trading-bot/backups/` on the prod server |
| Size | Depends on retained trading-state tables; measure each archive |

The export runs in the GitHub Actions runner and is rsynced to the server, so
the DietPi host needs no Python, no pyarrow and no Supabase credentials. It
reuses the existing `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_KEY` secrets on
port 2222; no new third-party action sees the production
key: the rsync is plain shell, accepting the host key on first connect
(`StrictHostKeyChecking=accept-new`, the same trust-on-use model
`deploy_to_server.yml` uses for this host), with the key deleted from the runner
in an `always()` step.

> **Port 2222 is the router's external NAT to the host's internal port 22.**
> GitHub Actions connect over the internet on 2222; on the LAN you connect on
> **22** (`ssh -p 22 … pom@192.168.1.2`) and 2222 is refused — that is expected.

### Why past runs failed

Two unrelated causes, for the record:

- **Export step (2026-09-27):** a table was added to `TABLES` before its
  migration was applied in Supabase, so PostgREST returned a 404 and the
  "loud on partial failure" export aborted. Fix: apply the migration. This is
  why a new table must exist in Supabase before it is added to `TABLES`.
- **Ship step (Aug–Sep 2026):** the step used to run a separate one-shot
  `ssh-keyscan` and then require a strict host-key match; if that ~5-second scan
  hit a momentarily slow or unreachable home network it returned nothing and the
  rsync failed even though the host was reachable moments later. Replaced with
  `accept-new` (same trust-on-first-use, no fragile separate scan). See
  `decisions/2026-09-28_backup-ship-host-key-trust.md`.

Run it by hand from the Actions tab. `dry_run` reports row counts without
writing or shipping anything; `snapshot_date` overrides the partition date and
must be a valid `YYYY-MM-DD` date. Inputs pass as quoted arguments, never as
shell source. The Bitwarden wrapper preserves the exporter's exit code.

### A failed export is not a retained partial backup

The exporter attempts every declared table, records failures in its manifest
and exits nonzero if any failed. Other successful table files remain in the
runner's staging directory, **not** on production: a failed export skips
shipping and verification, and this workflow does not upload a fallback
artifact. The previous successful server archive remains intact. A fresh
complete archive requires correcting the failure, rerunning the workflow and
verifying its dated manifest on the server.

### Private exit-shadow observations

`exit_shadow.py` remains a pure candidate-rule calculation. The monitor writes
its genuine observations through `exit_shadow_store.py`, with a separate cached
client using `INTRADAY_SUPABASE_KEY` and a 5-second PostgREST request timeout.
There is no fallback to the ordinary trading key and no replacement of the
global trading client. The service-role-only policy remains unchanged.

Write and calculation errors cannot prevent the monitor from continuing its
live exit decisions. They produce a credential-safe console warning and
`exit_shadow_write_failed` research diagnostics instead of silently suppressing
missing-table or permission errors. Neither raw exception messages nor position
payloads are included in those warnings.

After deployment, verify a real monitor-cycle observation rather than inserting
test rows or invoking the live monitor solely as a probe. A successful empty
REST read confirms read access only, not insert authorization or actual capture.

## Layout

```
backups/
  parquet/table_name=<table>/snapshot_date=<YYYY-MM-DD>/data.parquet
  manifest/<YYYY-MM-DD>.json                                          <- counts + checksums
  manifest/latest.json
  README.md
```

Each run writes a **complete snapshot of the required tables** into a new dated partition. Nothing is
ever overwritten, so every week is independently restorable and the archive
grows incrementally. There is no row-level delta. `portfolio_positions` is mutated in
place every 15 minutes, so an append-only delta would miss most of what changes.

There is currently **no retention or pruning** — nothing is deleted.

## Parquet only

Nothing is written as CSV. Parquet is typed, compressed and verified by
read-back on write; a parallel CSV copy would be a second artifact that can
drift from the first, for a convenience a one-line `COPY` reproduces whenever
it is actually needed. See [Getting a CSV](#getting-a-csv).

## Querying with SQL

Install [DuckDB](https://duckdb.org/docs/installation) — a single binary, no
server, free, on macOS/Linux/Windows/ARM. Then, from the backup root:

```sql
SELECT * FROM read_parquet(
    'parquet/table_name=trade_history/**/*.parquet',
    hive_partitioning = true,
    union_by_name     = true
);
```

Two flags matter, and both are required in practice:

- **`union_by_name = true`** — the schema has changed 30+ times (see
  `migrations/`), so older snapshots legitimately have fewer columns than newer
  ones. Without this, any query spanning a schema change fails.
- **`hive_partitioning = true`** — exposes `table_name` and `snapshot_date` as
  real queryable columns even though neither is stored in the files.

The partition key is `table_name`, **not** `table`, because `table` is a
reserved word in DuckDB and would need quoting in every query. DuckDB types the
partition value, so `snapshot_date` comes back as a `DATE` — compare it with
`DATE '2026-08-21'`, not with a string.

```sql
-- how the open book changed week to week
SELECT snapshot_date, ticker, shares, buy_price, hwm_price
FROM read_parquet('parquet/table_name=portfolio_positions/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY snapshot_date, ticker;

-- what is in the archive at all
SELECT table_name, snapshot_date, count(*)
FROM read_parquet('parquet/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
GROUP BY 1, 2 ORDER BY 3 DESC;
```

For a GUI, run `duckdb -ui` (opens a local browser UI) or point DBeaver
Community at the same files.

## Getting a CSV

Convert on demand. All of these are one line, and none upload anything:

```bash
# one table, one week
duckdb -c "COPY (SELECT * FROM 'parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet') TO 'trade_history.csv' (HEADER)"

# one table, every week stacked
duckdb -c "COPY (SELECT * FROM read_parquet('parquet/table_name=trade_history/**/*.parquet', hive_partitioning=true, union_by_name=true)) TO 'trade_history.csv' (HEADER)"

# every table of one snapshot, one CSV each
duckdb -c "COPY (SELECT * FROM read_parquet('parquet/**/*.parquet', hive_partitioning=true, union_by_name=true) WHERE snapshot_date = DATE '2026-08-21') TO 'csv-out' (FORMAT CSV, PARTITION_BY (table_name), HEADER, OVERWRITE_OR_IGNORE)"
```

Or in Python: `pd.read_parquet(path).to_csv(out, index=False)`.

### Viewing straight from Google Drive

Drive has **no** Parquet preview, and no add-on converts it in place — a
`.parquet` there will always render as a binary blob. Two workable routes:

- **Google Colab** — free, opens from Drive, reads the files directly. Mount
  Drive, then `pd.read_parquet('/content/drive/MyDrive/backups/...')`. Nothing
  leaves Google, and a saved notebook lives next to the backups.
- **A local viewer** — `duckdb -ui`, DBeaver Community, or the Parquet Viewer
  extension for VS Code.

Do not paste these files into public online Parquet viewers. This is live
trading history.

## Verifying a backup

`manifest/latest.json` records, per table, the row count, column count, byte
size and SHA-256 of each Parquet file, plus a `failed` list.

Four guards run automatically:

1. Row counts are checked against an exact server-side count. A mismatch means
   the table changed mid-read, so the snapshot is not point-in-time and is
   rejected rather than written torn.
2. Every Parquet file is re-read and re-counted right after writing.
3. After rsync, the workflow SSHes back in and re-verifies the manifest against
   the files actually on disk — a green rsync only proves bytes were sent.
4. Any failure sends a Telegram alert and fails the job. Remaining tables are
   still attempted, so one bad table cannot mask the state of the others.

Empty tables are recorded as `written: false, reason: "table is empty"` rather
than being skipped silently, so *empty* stays distinguishable from *missed*.

## Adding a table

Add it to `TABLES` in `supabase_backup.py`, mapped to the columns that uniquely
and stably order it. The ordering is not cosmetic: PostgREST paginates with
LIMIT/OFFSET and Postgres guarantees no order without an `ORDER BY`, so an
unordered multi-page fetch can duplicate one row and drop another.

You do not have to remember this.
`tests/test_supabase_backup.py::test_every_known_table_is_backed_up` scans
`migrations/` and the Supabase calls in the source and fails if a table the bot
uses is missing from both `TABLES` and `NOT_BACKED_UP`. New research tables must
be explicitly excluded with a reason rather than added to the required export.

## Restoring

The files are plain tables — read the Parquet and upsert back into Supabase.
Check `parquet_sha256` in the manifest first if a file's integrity is in doubt.

Restore while trading writers and notification workers are stopped. Reconcile
`daily_notifications` against actual delivery before resuming alerts.
The exporter reads tables sequentially, not in a single database transaction;
inclusion of all required tables does not prove cross-table consistency.
Consult the dated manifest: older archives may contain research tables, but
new snapshots deliberately cannot restore that subsystem. Do not interpret
historical worker leases or health timestamps as current.

⚠️ **This has never been rehearsed.** Tracked as FU-010.

## Offsite

The archive currently lives on the same box the bot runs on, so a disk failure
loses both. Syncing `backups/` to Google Drive is a manual operator step and is
not automated by this workflow.
