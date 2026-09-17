"""Every migration must be safe to run twice.

WHY THIS EXISTS
---------------
Nothing in this repo runs migrations. They are applied by hand in the Supabase
SQL Editor, so there is no ledger of what has already been applied and no tool
that refuses to apply something twice. The only thing standing between an
operator and a failed script is the file itself being re-runnable.

On 2026-09-17 `20260624_migration_retention_period.sql` was re-run by mistake and
aborted with:

    ERROR: column "retention_period" of relation "watchlist" already exists

Nothing was damaged, because the SQL Editor wraps a script in a transaction and
the whole thing rolled back. But the error named a migration the operator had not
meant to run, which is alarming and takes real time to diagnose. The date-prefix
convention (AGENTS.md) makes this MORE likely rather than less: every migration
now sorts into one list, so picking a long-applied file out of it is easy.

An audit that day found exactly two unguarded files out of 40 -- both from
2026-06-24, before the IF NOT EXISTS convention settled. This test exists so that
number can never grow back.

WHAT IT CHECKS
--------------
Bare DDL that Postgres rejects when the object already exists. The guarded forms
(`IF NOT EXISTS` / `IF EXISTS`) are accepted, and so is DDL inside a `DO $$ ... $$`
block, which is the escape hatch for steps whose guard cannot be expressed as
`IF NOT EXISTS` -- see `20260624_twr_schema.sql`, where the referenced column no
longer exists at all.

See decisions/2026-09-17_idempotent-migrations.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# DDL that fails on a second run unless guarded.
UNGUARDED_PATTERNS = {
    "ADD COLUMN": re.compile(
        r"\bALTER\s+TABLE\b.*?\bADD\s+COLUMN\b(?!\s+IF\s+NOT\s+EXISTS)",
        re.IGNORECASE | re.DOTALL,
    ),
    "DROP COLUMN": re.compile(
        r"\bALTER\s+TABLE\b.*?\bDROP\s+COLUMN\b(?!\s+IF\s+EXISTS)",
        re.IGNORECASE | re.DOTALL,
    ),
    "CREATE TABLE": re.compile(
        r"\bCREATE\s+TABLE\b(?!\s+IF\s+NOT\s+EXISTS)", re.IGNORECASE
    ),
    "CREATE INDEX": re.compile(
        r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b(?!\s+CONCURRENTLY\s+IF\s+NOT\s+EXISTS)"
        r"(?!\s+IF\s+NOT\s+EXISTS)",
        re.IGNORECASE,
    ),
}


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def _strip_noise(sql: str) -> str:
    """Remove comments and DO-block bodies before scanning.

    Comments are stripped because these files document the very errors this test
    prevents, and that prose quotes the unguarded SQL verbatim. DO blocks are
    stripped because they are the sanctioned guard for steps that cannot use
    IF NOT EXISTS -- the precondition is tested in PL/pgSQL instead.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"\$\$.*?\$\$", " ", sql, flags=re.DOTALL)
    return sql


def _statements(sql: str) -> list[str]:
    return [s for s in _strip_noise(sql).split(";") if s.strip()]


def test_migrations_directory_is_not_empty():
    assert _migration_files(), "no migrations found — is the path wrong?"


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_is_rerunnable(path: Path):
    """No migration may contain DDL that fails on a second run."""
    offenders: list[str] = []
    for stmt in _statements(path.read_text(encoding="utf-8")):
        for label, pattern in UNGUARDED_PATTERNS.items():
            if pattern.search(stmt):
                offenders.append(f"{label}: {' '.join(stmt.split())[:110]}")

    assert not offenders, (
        f"{path.name} contains DDL that fails when the migration is re-run.\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n\nUse IF NOT EXISTS / IF EXISTS, or wrap the step in a DO $$ ... $$ "
          "block that tests its own preconditions. See AGENTS.md "
          "'Every Migration Must Be Re-Runnable'."
    )


class TestTheDetectorActuallyDetects:
    """Guard against the scanner silently passing everything.

    A test that only ever sees clean input cannot prove it would catch dirty
    input. These pin the detector against the exact SQL that broke on 2026-09-17.
    """

    def test_flags_the_statement_that_failed_in_production(self):
        sql = "ALTER TABLE public.watchlist ADD COLUMN retention_period TEXT DEFAULT '1d';"
        assert UNGUARDED_PATTERNS["ADD COLUMN"].search(sql)

    def test_accepts_the_guarded_form(self):
        sql = "ALTER TABLE public.watchlist ADD COLUMN IF NOT EXISTS retention_period TEXT;"
        assert not UNGUARDED_PATTERNS["ADD COLUMN"].search(sql)

    def test_flags_bare_drop_column(self):
        assert UNGUARDED_PATTERNS["DROP COLUMN"].search("ALTER TABLE t DROP COLUMN c;")

    def test_accepts_guarded_drop_column(self):
        assert not UNGUARDED_PATTERNS["DROP COLUMN"].search(
            "ALTER TABLE t DROP COLUMN IF EXISTS c;"
        )

    def test_flags_bare_create_table(self):
        assert UNGUARDED_PATTERNS["CREATE TABLE"].search("CREATE TABLE cash_flows (id INT);")

    def test_accepts_guarded_create_table(self):
        assert not UNGUARDED_PATTERNS["CREATE TABLE"].search(
            "CREATE TABLE IF NOT EXISTS cash_flows (id INT);"
        )

    def test_do_block_contents_are_exempt(self):
        """The twr PK step is guarded in PL/pgSQL, not by IF NOT EXISTS."""
        sql = (
            "DO $$ BEGIN IF EXISTS (SELECT 1) THEN "
            "EXECUTE 'ALTER TABLE account_balances ADD PRIMARY KEY (date, key)'; "
            "END IF; END $$;"
        )
        stripped = _strip_noise(sql)
        assert "ALTER TABLE" not in stripped.upper()

    def test_comments_are_exempt(self):
        """These files quote the failing SQL in their own explanatory headers."""
        sql = "-- ALTER TABLE t ADD COLUMN c TEXT;  <- this is what used to fail\n"
        assert not UNGUARDED_PATTERNS["ADD COLUMN"].search(_strip_noise(sql))


class TestTheTwoRepairedFiles:
    """Pin the two files repaired on 2026-09-17 so they cannot regress."""

    def test_retention_period_migration_is_guarded(self):
        sql = (MIGRATIONS / "20260624_migration_retention_period.sql").read_text()
        assert sql.upper().count("ADD COLUMN IF NOT EXISTS") == 2
        assert sql.upper().count("DROP COLUMN IF EXISTS") == 3

    def test_twr_migration_guards_the_primary_key_step(self):
        sql = (MIGRATIONS / "20260624_twr_schema.sql").read_text()
        assert "DO $$" in sql, "the PK step must be precondition-guarded"
        assert "ADD PRIMARY KEY" in sql, "the historical intent must be preserved"
        # The bare form must not survive outside the DO block.
        assert "ADD PRIMARY KEY" not in _strip_noise(sql).upper()
