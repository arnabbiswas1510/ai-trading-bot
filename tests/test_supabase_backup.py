"""
Tests for supabase_backup.py.

The most important test here is test_every_known_table_is_backed_up. A backup
that silently omits a table is worse than no backup, because it is trusted. That
test derives the expected table set from migrations/ and from the Supabase calls
in the source, so adding a table without adding it to TABLES fails the suite
rather than producing a quietly incomplete archive.
"""

import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

import supabase_backup
from supabase_backup import (
    TABLES,
    fetch_table,
    run_backup,
    to_dataframe,
    write_table,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# NOTE: pyarrow/duckdb are imported per-test, NOT skipped at module level.
# A module-level importorskip would also skip test_every_known_table_is_backed_up,
# which needs neither — and that is the one test whose silent absence would let a
# table go unbacked-up indefinitely.


# ── Fake Supabase client ─────────────────────────────────────────────────────
def make_client(data: dict[str, list[dict]], *, count_override: dict[str, int] | None = None):
    """
    Minimal fake supporting the exact chain the backup uses:
        .table(t).select("*", count="exact").order(c)...range(a, b).execute()
    """
    count_override = count_override or {}
    ordered_by: dict[str, list[str]] = {}

    def _table(name: str):
        rows = data.get(name, [])

        def _select(*_args, **_kwargs):
            node = MagicMock()

            def _order(column, **_kw):
                ordered_by.setdefault(name, []).append(column)
                return node

            def _range(start, end):
                page = MagicMock()
                response = MagicMock()
                response.data = rows[start:end + 1]
                response.count = count_override.get(name, len(rows))
                page.execute.return_value = response
                return page

            node.order.side_effect = _order
            node.range.side_effect = _range
            return node

        table_mock = MagicMock()
        table_mock.select.side_effect = _select
        return table_mock

    client = MagicMock()
    client.table.side_effect = _table
    # Exposed so tests can assert on ORDER BY without re-entering the chain,
    # which would build a fresh mock and lose the recorded calls.
    client.ordered_by = ordered_by
    return client


# ── Completeness guard ───────────────────────────────────────────────────────
def _tables_from_migrations() -> set[str]:
    found = set()
    for sql in (REPO_ROOT / "migrations").glob("*.sql"):
        for match in re.finditer(
            r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_]+)",
            sql.read_text(), re.IGNORECASE,
        ):
            found.add(match.group(1).lower())
    # Transient rescue copies made by a migration, not live tables.
    return {t for t in found if not t.startswith("portfolio_positions_backup")}


def _tables_from_source() -> set[str]:
    found = set()
    for py in REPO_ROOT.glob("*.py"):
        if py.name == "supabase_backup.py":
            continue
        text = py.read_text()
        found |= set(re.findall(r'\.(?:table|from_)\(\s*"([a-z_]+)"', text))
        found |= set(re.findall(r'_upsert\(\s*\w+\s*,\s*"([a-z_]+)"', text))
    return found


def test_every_known_table_is_backed_up():
    """A table the bot uses but never backs up is data loss waiting to happen."""
    expected = _tables_from_migrations() | _tables_from_source()
    missing = sorted(expected - set(TABLES))
    assert not missing, (
        f"These tables are used by the bot but absent from supabase_backup.TABLES: "
        f"{missing}. Add them (with their natural key as the order column) or they "
        f"will never be backed up."
    )


def test_migrations_and_source_actually_found_tables():
    """Guards the guard: a broken regex would make the test above vacuously pass."""
    assert len(_tables_from_migrations()) >= 5
    assert len(_tables_from_source()) >= 5


def test_every_table_has_order_columns():
    for table, order_by in TABLES.items():
        assert order_by, f"{table} has no ordering key; multi-page fetches would be unsafe"


# ── Fetching ─────────────────────────────────────────────────────────────────
def test_fetch_returns_all_rows():
    rows = [{"id": i, "ticker": f"T{i}"} for i in range(5)]
    client = make_client({"trade_history": rows})
    assert fetch_table(client, "trade_history", ("id",)) == rows


def test_fetch_paginates_beyond_one_page(monkeypatch):
    monkeypatch.setattr(supabase_backup, "PAGE_SIZE", 10)
    rows = [{"id": i} for i in range(25)]
    client = make_client({"trade_history": rows})
    assert fetch_table(client, "trade_history", ("id",)) == rows


def test_fetch_raises_when_count_disagrees():
    """
    A row-count mismatch means the table changed mid-read, so the result is not
    a consistent point in time. Failing loudly beats writing a torn snapshot
    that looks valid forever after.
    """
    rows = [{"id": i} for i in range(5)]
    client = make_client({"trade_history": rows}, count_override={"trade_history": 9})
    with pytest.raises(RuntimeError, match="point-in-time"):
        fetch_table(client, "trade_history", ("id",))


def test_fetch_orders_by_every_key_column():
    client = make_client({"watchlist_history": [{"snapshot_date": "2026-01-01", "ticker": "A"}]})
    fetch_table(client, "watchlist_history", ("snapshot_date", "ticker"))
    assert client.ordered_by["watchlist_history"] == ["snapshot_date", "ticker"]


# ── Normalising ──────────────────────────────────────────────────────────────
def test_key_columns_lead_and_rest_are_alphabetical():
    df = to_dataframe([{"zebra": 1, "ticker": "A", "alpha": 2, "snapshot_date": "d"}],
                      ("snapshot_date", "ticker"))
    assert list(df.columns) == ["snapshot_date", "ticker", "alpha", "zebra"]


def test_column_order_is_stable_when_postgres_reorders():
    a = to_dataframe([{"id": 1, "x": 1, "y": 2}], ("id",))
    b = to_dataframe([{"y": 2, "id": 1, "x": 1}], ("id",))
    assert list(a.columns) == list(b.columns)


def test_jsonb_columns_are_serialised_to_text():
    """dicts and lists have no Parquet scalar type; keep the value, drop the ambiguity."""
    df = to_dataframe([{"id": 1, "failed_params": {"rs": {"drift": -3}}}], ("id",))
    assert json.loads(df["failed_params"][0]) == {"rs": {"drift": -3}}


def test_mixed_scalar_types_are_coerced_to_text():
    df = to_dataframe([{"id": 1, "v": 5}, {"id": 2, "v": "n/a"}], ("id",))
    assert list(df["v"]) == ["5", "n/a"]


def test_uniform_columns_keep_their_native_type():
    df = to_dataframe([{"id": 1, "px": 1.5}, {"id": 2, "px": None}], ("id",))
    assert pd.api.types.is_float_dtype(df["px"])


def test_nulls_survive_coercion_into_parquet(tmp_path):
    """A NULL jsonb must land as a Parquet null, not the string "None"."""
    pytest.importorskip("pyarrow")
    df = to_dataframe([{"id": 1, "v": {"a": 1}}, {"id": 2, "v": None}], ("id",))
    write_table(df, "breakout_learnings", "2026-08-21", tmp_path)
    back = pd.read_parquet(
        tmp_path / "parquet/table_name=breakout_learnings/snapshot_date=2026-08-21/data.parquet"
    )
    assert json.loads(back["v"][0]) == {"a": 1}
    assert back["v"][1] is None or pd.isna(back["v"][1])


# ── Writing ──────────────────────────────────────────────────────────────────
def test_writes_hive_partitioned_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    df = to_dataframe([{"id": 1, "ticker": "AAPL"}], ("id",))
    entry = write_table(df, "trade_history", "2026-08-21", tmp_path)

    assert (tmp_path / "parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet").exists()
    assert entry["rows"] == 1 and entry["written"] is True
    assert len(entry["parquet_sha256"]) == 64


def test_no_csv_is_produced(tmp_path):
    """
    Parquet is the only artifact. A second copy of the same data is a second
    thing that can drift; CSV is generated on demand instead (see the archive
    README).
    """
    pytest.importorskip("pyarrow")
    write_table(to_dataframe([{"id": 1}], ("id",)), "trade_history", "2026-08-21", tmp_path)
    assert list(tmp_path.rglob("*.csv")) == []


def test_parquet_round_trips_values(tmp_path):
    pytest.importorskip("pyarrow")
    rows = [{"id": 1, "ticker": "AAPL", "pnl": -12.5}, {"id": 2, "ticker": "MSFT", "pnl": 8.0}]
    write_table(to_dataframe(rows, ("id",)), "trade_history", "2026-08-21", tmp_path)
    back = pd.read_parquet(
        tmp_path / "parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet"
    )
    assert list(back["ticker"]) == ["AAPL", "MSFT"]
    assert list(back["pnl"]) == [-12.5, 8.0]


def test_empty_table_is_recorded_not_written(tmp_path):
    """
    'Empty' and 'missed' must be distinguishable. An empty table has no inferable
    column types, so no honest Parquet schema exists — record the fact instead.
    """
    entry = write_table(pd.DataFrame(), "ibkr_fills", "2026-08-21", tmp_path)
    assert entry == {"rows": 0, "columns": 0, "written": False, "reason": "table is empty"}
    assert not (tmp_path / "parquet").exists()


# ── Orchestration ────────────────────────────────────────────────────────────
def test_run_backup_writes_all_tables_and_totals_rows(tmp_path):
    pytest.importorskip("pyarrow")
    client = make_client({
        "trade_history": [{"id": 1, "ticker": "AAPL"}, {"id": 2, "ticker": "MSFT"}],
        "portfolio_positions": [{"ticker": "NVDA", "shares": 10}],
    })
    manifest = run_backup(client, tmp_path, "2026-08-21", TABLES)

    assert manifest["failed"] == []
    assert manifest["total_rows"] == 3
    assert set(manifest["tables"]) == set(TABLES)
    assert manifest["tables"]["trade_history"]["rows"] == 2


def test_one_bad_table_does_not_abort_the_others(tmp_path):
    pytest.importorskip("pyarrow")
    client = make_client(
        {"trade_history": [{"id": 1}], "portfolio_positions": [{"ticker": "NVDA"}]},
        count_override={"trade_history": 99},
    )
    manifest = run_backup(client, tmp_path, "2026-08-21", TABLES)

    assert manifest["failed"] == ["trade_history"]
    assert manifest["tables"]["portfolio_positions"]["status"] == "ok"


def test_failed_table_is_never_reported_as_written(tmp_path):
    client = make_client({"trade_history": [{"id": 1}]}, count_override={"trade_history": 99})
    manifest = run_backup(client, tmp_path, "2026-08-21", {"trade_history": ("id",)})
    assert manifest["tables"]["trade_history"].get("written") is not True


def test_snapshots_accumulate_rather_than_overwrite(tmp_path):
    """The archive is the backup. A later run must never disturb an earlier week."""
    pytest.importorskip("pyarrow")
    run_backup(make_client({"trade_history": [{"id": 1}]}), tmp_path, "2026-08-14", {"trade_history": ("id",)})
    run_backup(make_client({"trade_history": [{"id": 1}, {"id": 2}]}), tmp_path, "2026-08-21", {"trade_history": ("id",)})

    first = pd.read_parquet(tmp_path / "parquet/table_name=trade_history/snapshot_date=2026-08-14/data.parquet")
    second = pd.read_parquet(tmp_path / "parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet")
    assert len(first) == 1 and len(second) == 2


def test_hive_partitions_are_readable_as_columns(tmp_path):
    """
    Guards the whole point of the layout: snapshot_date must query as a column,
    and the table key must not need quoting (hence table_name, not the reserved
    word `table`).
    """
    duckdb = pytest.importorskip("duckdb")
    run_backup(make_client({"trade_history": [{"id": 1}]}), tmp_path, "2026-08-14", {"trade_history": ("id",)})
    run_backup(make_client({"trade_history": [{"id": 2}]}), tmp_path, "2026-08-21", {"trade_history": ("id",)})

    rows = duckdb.sql(
        f"SELECT table_name, snapshot_date, id FROM read_parquet("
        f"'{tmp_path}/parquet/**/*.parquet', hive_partitioning=true, union_by_name=true) "
        f"ORDER BY snapshot_date"
    ).fetchall()
    # DuckDB types the partition value, so snapshot_date arrives as a DATE, not
    # a string. Documented in the generated README because it changes how you
    # filter on it.
    assert rows == [
        ("trade_history", date(2026, 8, 14), 1),
        ("trade_history", date(2026, 8, 21), 2),
    ]


def test_schema_drift_across_snapshots_is_readable(tmp_path):
    """
    Older snapshots legitimately lack columns added by later migrations. The
    documented union_by_name=true must make that a null, not an error.
    """
    duckdb = pytest.importorskip("duckdb")
    run_backup(make_client({"trade_history": [{"id": 1}]}), tmp_path, "2026-08-14", {"trade_history": ("id",)})
    run_backup(make_client({"trade_history": [{"id": 2, "new_col": "x"}]}), tmp_path, "2026-08-21", {"trade_history": ("id",)})

    rows = duckdb.sql(
        f"SELECT id, new_col FROM read_parquet("
        f"'{tmp_path}/parquet/**/*.parquet', hive_partitioning=true, union_by_name=true) "
        f"ORDER BY id"
    ).fetchall()
    assert rows == [(1, None), (2, "x")]
