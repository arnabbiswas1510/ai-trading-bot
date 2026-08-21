# Weekly Supabase backup to Parquet on the production server

- **Date:** 2026-08-21
- **Status:** Accepted

## Problem

Supabase was the single point of failure for every piece of trading state this
system owns, with no backup of any kind.

That matters most for `trade_history`. It is the only record of what this
strategy actually did with real money, and every scheduled parameter review in
`docs/tech_debt_and_requirements_tracker.md` replays it — the 2026-09-20 review
of the Early Dollar Stop, and the 1/2/3/6-month reviews after it. Losing that
table would not merely lose data; it would make every future change to the exit
rules unjustifiable against anything except opinion, permanently. The replay
harness in `research/exit_rule_replay.py` has no other input.

Nothing else in the system persisted it. The local SQLite DB holds user settings
only (initial balance, stop-loss %, API keys) by deliberate design, and all three
containers are disposable.

## Decision

A GitHub Actions workflow (`weekly_supabase_backup.yml`, Sundays 14:00 UTC)
exports all 12 Supabase tables and rsyncs them to
`/home/dietpi/docker/ai-trading-bot/backups/` on the production server.

```
backups/
  parquet/table_name=<t>/snapshot_date=<YYYY-MM-DD>/data.parquet
  manifest/<YYYY-MM-DD>.json
  README.md
```

## Why

### Full snapshots, not row-level deltas

The request was for incremental backups. Measured, the entire database is **700
rows / ~180KB**. A delta saves nothing.

More importantly a delta would be **wrong**. `portfolio_positions` is mutated in
place every 15 minutes — HWM, trail %, armed state, plateau counters. An
append-only delta captures a position's birth and never its life, which silently
destroys exactly the data the exit-rule reviews depend on. Deltas would also
require watermark state that can itself drift, and would break single-week
point-in-time restore.

Each run writes a complete, self-contained snapshot into a **new** dated
partition and never overwrites an earlier one. The archive therefore still grows
incrementally — the property actually wanted — while every week remains
independently restorable. At this rate ten years is ~190MB.

### Parquet only

The stated requirement was to run SQL against the files. Parquet delivers that:
DuckDB — a single binary, no server, free, on macOS/Linux/Windows/ARM — reads
the whole archive through one glob, and Parquet carries real column types, so
`profit_loss` arrives as a number rather than text.

A parallel CSV copy was written first, and then removed before shipping. The
motivation was real — the archive is destined for Google Drive, which has no
Parquet preview, so a `.parquet` there is an opaque blob while a `.csv` previews
inline. But it meant two artifacts holding the same trading history, which can
disagree: a partial write, a manual edit, or a future change touching one path
and not the other leaves no way to tell which is authoritative. The cost of
removing it is one `duckdb -c "COPY (...) TO 'x.csv' (HEADER)"` at the moment a
CSV is actually wanted, and Colab reads Parquet from Drive directly. A
regression test asserts no `.csv` is produced, and the conversion recipes are in
the README generated into the archive itself — where someone browsing Drive will
find them.

### Hive partitioning, with `table_name` rather than `table`

The directory naming makes `snapshot_date` and `table_name` real queryable
columns without storing either in the files, which is what turns "the book as of
week N" into a `WHERE` clause instead of a file hunt.

The key is `table_name` because **`table` is a reserved word in DuckDB**. It was
tried first, and `SELECT table, ... FROM read_parquet(...)` fails with a parser
error; every ad-hoc query would have needed quoting forever. Renaming the
partition costs nothing and removes that permanently.

DuckDB types the partition value, so `snapshot_date` reads back as a `DATE`, not
a string. This is documented in the generated README because it changes how you
filter on it.

### Schema drift is expected, not exceptional

This schema has changed 30+ times (see `migrations/`). Older snapshots
legitimately lack columns that later ones have, so readers must pass
`union_by_name=true` or any query spanning a schema change fails. A regression
test asserts drift stays readable, and the generated README states the
requirement in bold rather than leaving it to be rediscovered.

### Failing loudly

A backup that silently skips a table is worse than no backup, because it is
trusted. Four guards:

1. Row counts are checked against an exact server-side count; a mismatch means
   the table changed mid-read, so the snapshot is not point-in-time and is
   rejected rather than written torn.
2. Every Parquet file is re-read and re-counted immediately after writing.
3. The workflow SSHes back in and re-verifies the manifest against files on
   disk. A green rsync only proves bytes were sent, not that the backup exists.
4. Failures raise a Telegram alert and a non-zero exit. The remaining tables are
   still attempted, so one bad table cannot mask the state of the others.

Empty tables (`watchlist`, `ibkr_fills`, `breakout_learnings` are currently
empty) have no inferable column types and therefore no honest Parquet schema.
They are recorded in the manifest as `written: false, reason: "table is empty"`
so that *empty* stays distinguishable from *missed*.

### A mechanical guard against omission

`tests/test_supabase_backup.py::test_every_known_table_is_backed_up` derives the
expected table set by scanning `migrations/` and the Supabase calls in the
source, then asserts every one appears in `TABLES`. Adding a table without
adding it to the backup fails the suite instead of producing a quietly
incomplete archive years later.

This follows the precedent set by `schema_guard.py` and
`decisions/2026-08-14_schema-guard-fail-loud.md`, and by the mechanical doc-sync
check in `AGENTS.md`: a rule a human must remember is a rule that eventually
fails.

### Export in the runner, not on the box

The runner already has network access and Python. Exporting there means the
DietPi host needs no Python, no pyarrow and no Supabase credentials — it only
receives files over the existing deploy SSH key. rsync rather than scp because
the destination accumulates every past week and scp would re-upload the whole
archive weekly. There is deliberately no `--delete`: the remote history *is* the
backup.

The rsync is invoked as plain shell rather than through a marketplace rsync
action. `DEPLOY_KEY` reaches the host that places live orders, so the set of
third-party actions that can see it should not grow for a convenience wrapper
around one command. The workflow writes the key to `~/.ssh` at 0600, pins the
host key with `ssh-keyscan` instead of disabling host verification, and deletes
the key in an `always()` step. The only actions holding it remain the ones
already used by `deploy_to_server.yml`.

`pyarrow` is installed in the workflow rather than added to `requirements.txt`,
since a ~40MB wheel in both runtime images for a weekly export neither container
runs is pure cost. It is added to `requirements-dev.txt`, and to the test step in
`daily_screener.yml` — without that the Parquet tests would `importorskip` and
the backup would go unverified in CI.

## Alternatives rejected

- **`pg_dump` to `.sql`** — a faithful dump, but not queryable without restoring
  it into a running Postgres first, which fails the core requirement.
- **JSON / JSONL** — Drive-viewable and DuckDB-readable, but untyped and ~5x
  larger, with no compression or column pruning.
- **Parquet + a parallel CSV copy** — implemented, then removed before shipping;
  see above.
- **Supabase's own PITR** — a paid tier, still hosted with the thing being
  backed up, and does not produce files the operator can hold.
- **Committing backups to git** — trading history in a repo, growing unboundedly,
  with no way to prune. Rejected on both privacy and hygiene grounds.

## Known limitations

- **Not offsite by itself.** The archive lives on the same box the bot runs on.
  Google Drive sync is the operator's step and is not automated here; until it
  happens, a disk failure loses both the bot and its backups.
- **No retention/pruning.** Nothing is ever deleted. Correct at ~180KB/week, but
  it will need revisiting if table volumes grow by orders of magnitude.
- **Reads use the publishable Supabase key**, which today can read everything
  because the RLS policies in `migrations/enable_rls_all_tables.sql` are
  `FOR ALL USING (true)`. That works, but a backup job holding broad read access
  on a publishable key is worth tightening. Tracked as FU-009.
- **Restore is untested.** The path is documented in the generated README but no
  drill has been run. Tracked as FU-010.

## Files changed

- `supabase_backup.py` — new; the exporter, plus the README generated into the archive (incl. Parquet→CSV recipes)
- `.github/workflows/weekly_supabase_backup.yml` — new; Sunday schedule, rsync, post-ship verification
- `.github/workflows/daily_screener.yml` — installs pyarrow/duckdb so the new tests actually run
- `tests/test_supabase_backup.py` — new; 23 tests, incl. the completeness guard, the no-CSV assertion and a schema-drift regression
- `requirements-dev.txt` — pyarrow, duckdb
- `docs/backups.md` — new; operator guide
- `docs/tech_debt_and_requirements_tracker.md` — FU-009, FU-010
- `README.md` — Backups section, glossary, further reading
