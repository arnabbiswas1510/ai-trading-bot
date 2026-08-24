# Backups

Supabase is the only durable home for the bot's trading state. `trade_history`
in particular is the sole record of what this strategy did with real money, and
every scheduled parameter review in
[`tech_debt_and_requirements_tracker.md`](tech_debt_and_requirements_tracker.md)
replays it. It is backed up weekly to flat files on the production server.

See `decisions/2026-08-21_weekly-supabase-backup.md` for why it is designed this
way.

## What runs, and when

| | |
|---|---|
| Workflow | `.github/workflows/weekly_supabase_backup.yml` |
| Schedule | Sundays, 14:00 UTC |
| Script | `supabase_backup.py` |
| Destination | `/home/pom/docker/ai-trading-bot/backups/` on the prod server |
| Size | ~180KB per week (700 rows across 12 tables) |

The export runs in the GitHub Actions runner and is rsynced to the server, so
the DietPi host needs no Python, no pyarrow and no Supabase credentials. It
reuses the existing `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_KEY` secrets on
port 2222 — no new secrets, and no new third-party action sees the production
key: the rsync is plain shell, with the host key pinned via `ssh-keyscan` and
the key deleted from the runner in an `always()` step.

Run it by hand from the Actions tab. `dry_run` reports row counts without
writing or shipping anything; `snapshot_date` overrides the partition date.

## Layout

```
backups/
  parquet/table_name=<table>/snapshot_date=<YYYY-MM-DD>/data.parquet
  manifest/<YYYY-MM-DD>.json                                          <- counts + checksums
  manifest/latest.json
  README.md
```

Each run writes a **complete snapshot** into a new dated partition. Nothing is
ever overwritten, so every week is independently restorable and the archive
grows incrementally. There is no row-level delta: the whole database is ~700
rows, so a delta would save nothing, and `portfolio_positions` is mutated in
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
uses is missing from `TABLES`.

## Restoring

The files are plain tables — read the Parquet and upsert back into Supabase.
Check `parquet_sha256` in the manifest first if a file's integrity is in doubt.

⚠️ **This has never been rehearsed.** Tracked as FU-010.

## Offsite

The archive currently lives on the same box the bot runs on, so a disk failure
loses both. Syncing `backups/` to Google Drive is a manual operator step and is
not automated by this workflow.
