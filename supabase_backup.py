"""
supabase_backup.py

Weekly point-in-time export of every Supabase table to flat files on the
production server.

WHY THIS EXISTS
---------------
Supabase is the only durable home for the bot's trading state: the watchlist,
the daily triggers, the open book, and — critically — `trade_history`, which is
the sole record of what this strategy actually did with real money. Every
scheduled parameter review in `docs/tech_debt_and_requirements_tracker.md`
replays that history. If it is lost, the reviews cannot be run and no future
change to the exit rules can be justified against anything but opinion.

Nothing else in this repo persists it. The local SQLite DB holds user settings
only, and the containers are disposable by design.

DESIGN DECISIONS
----------------
1. **Full snapshots, not row-level deltas.** The whole database is ~700 rows and
   ~200KB as Parquet, so a delta saves nothing measurable. It would also be
   wrong: `portfolio_positions` is mutated in place every 15 minutes (HWM,
   trail %, armed state), so an append-only delta would capture a position's
   birth and never its life. Each run writes a complete, self-contained
   snapshot into a new dated partition. Nothing is ever overwritten, so the
   archive still grows incrementally and every week remains independently
   restorable.

2. **Parquet only.** CSV is not written. It was considered — Google Drive has no
   Parquet preview, so a `.csv` there previews inline while a `.parquet` is an
   opaque blob — but shipping both meant two artifacts that could disagree, and
   a duplicated copy of the trading history for a convenience that a one-line
   DuckDB `COPY` reproduces on demand. See `docs/backups.md` for the conversion
   recipes.

3. **Hive partitioning.** Files land under `table_name=<name>/snapshot_date=<date>/`
   so DuckDB exposes both as real queryable columns without any of it being
   stored in the files. This is what makes "show me the book as of week N" a
   WHERE clause rather than a file hunt.

4. **Schema drift is expected, not an error.** This schema has changed 30+ times
   (see `migrations/`). Old snapshots legitimately lack columns that later ones
   have. Readers must use `union_by_name=true`; see the generated README.

5. **Loud on partial failure.** A backup that silently skips a table is worse
   than no backup, because it is trusted. Row counts are verified against an
   exact server-side count, written files are re-read and re-counted, and any
   table that fails aborts the run with a non-zero exit after the others are
   attempted.

Run: python supabase_backup.py --out-dir /path/to/backups [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# ── Tables ───────────────────────────────────────────────────────────────────
# Every table the bot reads or writes, mapped to the columns that uniquely and
# stably order it.
#
# The ordering is NOT cosmetic. PostgREST paginates with LIMIT/OFFSET, and
# Postgres gives no ordering guarantee without an ORDER BY, so an unordered
# multi-page fetch can silently duplicate one row and drop another. The order
# columns below are each table's natural key.
#
# This list is asserted complete by tests/test_supabase_backup.py, which scans
# migrations/ and the Supabase calls in the source. A new table that is not
# added here fails that test rather than being quietly omitted from backups.
TABLES: dict[str, tuple[str, ...]] = {
    "account_balances":    ("date",),
    "breakout_learnings":  ("id",),
    "cash_flows":          ("id",),
    "daily_triggers":      ("triggered_at", "ticker"),
    "exit_requests":       ("id",),
    "ibkr_fills":          ("exec_id",),
    "portfolio_positions": ("ticker",),
    "trade_history":       ("id",),
    "trigger_decisions":   ("decision_date", "ticker"),
    "trigger_history":     ("triggered_at", "ticker"),
    "watchlist":           ("ticker",),
    "watchlist_history":   ("snapshot_date", "ticker"),
}

# PostgREST caps a single response; page well under it and loop.
PAGE_SIZE = 1000

# zstd beats snappy on this data and is read natively by DuckDB, pandas and
# pyarrow. Nothing in the toolchain needs a plugin for it.
PARQUET_COMPRESSION = "zstd"

NY = ZoneInfo("America/New_York")


# ── Fetch ────────────────────────────────────────────────────────────────────
def fetch_table(client, table: str, order_by: tuple[str, ...]) -> list[dict]:
    """
    Return every row of `table`, paginated and deterministically ordered.

    Raises if the number of rows retrieved disagrees with the server's exact
    count. That mismatch means rows were added or removed mid-read, so the
    snapshot is not a consistent point in time and must not be written.
    """
    rows: list[dict] = []
    offset = 0
    expected: int | None = None

    while True:
        query = client.table(table).select("*", count="exact")
        for column in order_by:
            query = query.order(column)
        response = query.range(offset, offset + PAGE_SIZE - 1).execute()

        if expected is None:
            expected = getattr(response, "count", None)

        page = response.data or []
        rows.extend(page)

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if expected is not None and len(rows) != expected:
        raise RuntimeError(
            f"{table}: fetched {len(rows)} rows but server counted {expected}. "
            f"The table changed mid-read, so this snapshot is not point-in-time."
        )

    return rows


# ── Normalise ────────────────────────────────────────────────────────────────
def _scalar_kind(value: object) -> str:
    """Coarse type bucket used to detect columns Parquet cannot type."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "complex"


def _coerce_column(values: list) -> list:
    """
    Make one column safely writable to Parquet.

    JSONB columns (`failed_params`, and others) arrive as dicts or lists, which
    have no Parquet scalar type. A column can also hold genuinely mixed scalars
    after a loose migration. Both are serialised to JSON text: the value is
    preserved and readable, and the column gets one unambiguous type instead of
    failing the write or silently landing as an unusable struct.
    """
    kinds = {_scalar_kind(v) for v in values} - {"null"}

    if not kinds or kinds in ({"bool"}, {"number"}, {"string"}):
        return values

    return [
        None if v is None else (v if isinstance(v, str) else json.dumps(v, default=str))
        for v in values
    ]


def to_dataframe(rows: list[dict], order_by: tuple[str, ...]) -> pd.DataFrame:
    """
    Build a DataFrame with a deterministic column order.

    Key columns lead, the rest follow alphabetically. Postgres does not
    guarantee a stable column order across responses, and an unstable order
    would make every CSV appear to change each week even when the data did not.
    """
    if not rows:
        return pd.DataFrame()

    seen: list[str] = []
    for row in rows:
        for column in row:
            if column not in seen:
                seen.append(column)

    leading = [c for c in order_by if c in seen]
    columns = leading + sorted(c for c in seen if c not in leading)

    data = {c: _coerce_column([row.get(c) for row in rows]) for c in columns}
    return pd.DataFrame(data, columns=columns)


# ── Write ────────────────────────────────────────────────────────────────────
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_table(
    df: pd.DataFrame,
    table: str,
    snapshot_date: str,
    out_dir: Path,
) -> dict:
    """
    Write one table as Parquet, then verify it by reading it back.
    Returns the manifest entry for this table.
    """
    entry: dict = {"rows": int(len(df)), "columns": int(len(df.columns))}

    if df.empty:
        # An empty table has no column types to infer from REST, so there is no
        # honest Parquet schema to write. Record it explicitly instead: this
        # distinguishes "the table was empty" from "the table was missed",
        # which a reader could not otherwise tell apart.
        entry["written"] = False
        entry["reason"] = "table is empty"
        return entry

    parquet_path = out_dir / "parquet" / f"table_name={table}" / f"snapshot_date={snapshot_date}" / "data.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    df.to_parquet(parquet_path, engine="pyarrow", compression=PARQUET_COMPRESSION, index=False)

    verified = pd.read_parquet(parquet_path, engine="pyarrow")
    if len(verified) != len(df):
        raise RuntimeError(
            f"{table}: wrote {len(df)} rows but read back {len(verified)}. "
            f"The Parquet file is corrupt; refusing to record it as a good backup."
        )

    entry.update({
        "written": True,
        "parquet_bytes": parquet_path.stat().st_size,
        "parquet_sha256": _sha256(parquet_path),
    })
    return entry


README_TEXT = """\
# Supabase backups — CAN SLIM trading bot

Weekly full snapshots written by `supabase_backup.py` (GitHub Actions, Sundays).
Each run adds a new dated partition. Nothing here is ever overwritten.

    parquet/table_name=<name>/snapshot_date=<YYYY-MM-DD>/data.parquet
    manifest/<YYYY-MM-DD>.json                                    <- row counts + checksums

Parquet only. It is typed, compressed and verified by read-back on write. See
"Getting a CSV" below if you need one to eyeball.

## Querying

Install DuckDB (a single binary, no server: https://duckdb.org/docs/installation).

    duckdb

    -- one table, full history, every week stacked
    SELECT * FROM read_parquet(
        'parquet/table_name=trade_history/**/*.parquet',
        hive_partitioning = true,
        union_by_name     = true
    );

`union_by_name = true` is REQUIRED. The schema has changed 30+ times, so older
snapshots legitimately have fewer columns than newer ones. Without it, any query
spanning a schema change fails.

`snapshot_date` and `table_name` are real queryable columns despite not being
stored in the files — that is what the directory naming buys you. The key is
`table_name`, not `table`, because `table` is a reserved word that would need
quoting in every ad-hoc query. DuckDB types the partition value, so
`snapshot_date` comes back as a DATE: compare it with `DATE '2026-08-21'`, not
with a string.

    -- how the open book changed week to week
    SELECT snapshot_date, ticker, shares, buy_price, hwm_price
    FROM read_parquet('parquet/table_name=portfolio_positions/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    ORDER BY snapshot_date, ticker;

    -- everything as of the most recent snapshot
    WITH all_rows AS (
        SELECT * FROM read_parquet('parquet/**/*.parquet',
                                   hive_partitioning = true, union_by_name = true)
    )
    SELECT * FROM all_rows WHERE snapshot_date = (SELECT max(snapshot_date) FROM all_rows);

For a GUI, run `duckdb -ui`, or point DBeaver Community at the same files.

## Getting a CSV

Nothing here ships as CSV, because a second copy of the same data can drift from
the first. Convert on demand instead — all of these are one line and none of
them upload anything anywhere:

    # one table, one week
    duckdb -c "COPY (SELECT * FROM 'parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet') TO 'trade_history.csv' (HEADER)"

    # one table, all weeks stacked
    duckdb -c "COPY (SELECT * FROM read_parquet('parquet/table_name=trade_history/**/*.parquet', hive_partitioning=true, union_by_name=true)) TO 'trade_history.csv' (HEADER)"

    # every table of one snapshot, one CSV each
    duckdb -c "COPY (SELECT * FROM read_parquet('parquet/**/*.parquet', hive_partitioning=true, union_by_name=true) WHERE snapshot_date = DATE '2026-08-21') TO 'csv-out' (FORMAT CSV, PARTITION_BY (table_name), HEADER, OVERWRITE_OR_IGNORE)"

Or in Python: `pd.read_parquet(path).to_csv(out, index=False)`.

### Viewing straight from Google Drive

Drive has no Parquet preview and no add-on that converts in place, so a
`.parquet` there will always show as a binary blob. Two ways around it:

- **Google Colab** — free, opens from Drive, and reads the files directly:
  mount Drive, then `pd.read_parquet(...)`. Nothing leaves Google.
- **A local viewer** — the `duckdb -ui` browser UI, DBeaver Community, or the
  Parquet Viewer extension for VS Code.

Avoid pasting these files into public online Parquet viewers. This is live
trading history.

## Restoring

These are plain tables. Read the Parquet, then upsert back into Supabase.
Check the manifest's `parquet_sha256` first if a file's integrity is in doubt.
"""


# ── Orchestration ────────────────────────────────────────────────────────────
def run_backup(client, out_dir: Path, snapshot_date: str, tables: dict[str, tuple[str, ...]]) -> dict:
    """Export every table. Continues past failures so one bad table does not hide the rest."""
    manifest: dict = {
        "snapshot_date": snapshot_date,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": os.getenv("GITHUB_SHA", "unknown"),
        "tables": {},
    }

    for table, order_by in sorted(tables.items()):
        try:
            rows = fetch_table(client, table, order_by)
            df = to_dataframe(rows, order_by)
            entry = write_table(df, table, snapshot_date, out_dir)
            status = "ok"
        except Exception as exc:  # noqa: BLE001 — recorded per table, re-raised by caller
            entry = {"error": f"{type(exc).__name__}: {exc}"}
            status = "failed"

        entry["status"] = status
        manifest["tables"][table] = entry

        detail = (
            f"{entry.get('rows', 0):>6} rows"
            if status == "ok"
            else entry["error"]
        )
        icon = "✅" if status == "ok" else "❌"
        print(f"   {icon} {table:22s} {detail}")

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["failed"] = sorted(t for t, e in manifest["tables"].items() if e["status"] == "failed")
    manifest["total_rows"] = sum(e.get("rows", 0) for e in manifest["tables"].values())
    return manifest


def notify_failure(message: str) -> None:
    """Best-effort Telegram alert. A backup that stops running must not do so quietly."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "")
    if not token or not chat_ids:
        return
    try:
        import requests

        for chat_id in [c.strip() for c in chat_ids.split(",") if c.strip()]:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
    except Exception as exc:  # noqa: BLE001 — alerting must never fail the job
        print(f"   ⚠️  Telegram notification failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export all Supabase tables to Parquet.")
    parser.add_argument("--out-dir", required=True, help="Backup root directory.")
    parser.add_argument("--snapshot-date", default=None,
                        help="Partition date (YYYY-MM-DD). Defaults to today in America/New_York.")
    parser.add_argument("--tables", default=None,
                        help="Comma-separated subset of tables. Defaults to all.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and report row counts without writing any file.")
    args = parser.parse_args(argv)

    snapshot_date = args.snapshot_date or datetime.now(NY).strftime("%Y-%m-%d")
    out_dir = Path(args.out_dir).expanduser().resolve()

    tables = dict(TABLES)
    if args.tables:
        requested = [t.strip() for t in args.tables.split(",") if t.strip()]
        unknown = [t for t in requested if t not in TABLES]
        if unknown:
            print(f"❌ Unknown table(s): {', '.join(unknown)}")
            return 2
        tables = {t: TABLES[t] for t in requested}

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        print("❌ SUPABASE_URL and SUPABASE_KEY must be set.")
        return 2

    from supabase import create_client

    client = create_client(supabase_url, supabase_key)

    print(f"📦 Supabase backup — snapshot_date={snapshot_date}")
    print(f"   Destination: {out_dir}{' (DRY RUN — nothing will be written)' if args.dry_run else ''}")

    if args.dry_run:
        failed = []
        for table, order_by in sorted(tables.items()):
            try:
                rows = fetch_table(client, table, order_by)
                df = to_dataframe(rows, order_by)
                print(f"   ✅ {table:22s} {len(df):>6} rows, {len(df.columns):>3} cols")
            except Exception as exc:  # noqa: BLE001
                print(f"   ❌ {table:22s} {type(exc).__name__}: {exc}")
                failed.append(table)
        return 1 if failed else 0

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_backup(client, out_dir, snapshot_date, tables)

    manifest_dir = out_dir / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_text = json.dumps(manifest, indent=2)
    (manifest_dir / f"{snapshot_date}.json").write_text(manifest_text)
    (manifest_dir / "latest.json").write_text(manifest_text)
    (out_dir / "README.md").write_text(README_TEXT)

    print(f"\n   Total: {manifest['total_rows']} rows across {len(tables)} tables")

    if manifest["failed"]:
        message = (
            f"❌ Supabase backup FAILED for {snapshot_date}\n"
            f"Tables: {', '.join(manifest['failed'])}\n"
            f"The remaining tables were written; re-run the workflow."
        )
        print(f"\n{message}")
        notify_failure(message)
        return 1

    print("✅ Backup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
