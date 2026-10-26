#!/usr/bin/env python3
"""Explicit research-only native export/import/archive/purge. No default writes.

Requires PostgreSQL admin DSNs in SOURCE_RESEARCH_PG_DSN and
LOCAL_RESEARCH_PG_DSN. A Supabase REST service key is NOT sufficient.
Both native users must be allowed to execute pg_catalog.pg_control_system();
a managed Supabase postgres password alone does not guarantee that permission.
Source TLS and a direct or session-pooler connection are mandatory; transaction
poolers cannot preserve exported snapshots. Never run against production without
the documented maintenance/cutover procedure.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

if __package__:
    from .research_pg_tools import MigrationError, run_client
else:
    from research_pg_tools import MigrationError, run_client


TABLES = tuple(sorted((
    "intraday_capture_events", "intraday_capture_symbols", "intraday_capture_health",
    "intraday_shadow_runs", "intraday_shadow_events", "intraday_shadow_checkpoints",
    "intraday_shadow_health", "intraday_calibration_settings",
    "intraday_calibration_proposals", "intraday_calibration_events",
    "intraday_calibration_health", "intraday_calibration_lease",
    "intraday_research_reports", "intraday_research_calibration_artifacts",
    "intraday_research_incidents", "intraday_research_delivery_receipts",
    "intraday_research_reporting_state", "intraday_replay_runs", "exit_shadow_log",
)))
VIEW = "intraday_capture_sessions"
FUNCTIONS = (
    "claim_intraday_reporting(text)", "release_intraday_reporting(text)",
    "purge_intraday_capture(integer)", "intraday_calibration_mutate(text,jsonb)",
    "intraday_calibration_refresh_risk(jsonb)",
)
PURGE_CONFIRMATION = "PURGE EXPORTED CLOUD RESEARCH ONLY"
IMPORT_CONFIRMATION = "IMPORT INTO EMPTY LOCAL RESEARCH"
CUTOVER_CONFIRMATION = "RESEARCH WRITERS PAUSED AND ROUTED LOCAL"
FORMAT_VERSION = 1


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def private_write(path, content):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def fresh_directory(path):
    path = Path(path).resolve()
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    return path


def get_dsn(source):
    name = "SOURCE_RESEARCH_PG_DSN" if source else "LOCAL_RESEARCH_PG_DSN"
    value = os.environ.get(name)
    if not value:
        raise MigrationError(f"{name} is required; a Supabase REST key cannot export or purge a native snapshot.")
    info = conninfo_to_dict(value)
    if not info.get("host") or not info.get("dbname") or not info.get("user"):
        raise MigrationError(f"{name} must explicitly specify host, database and admin user.")
    if source and (info.get("port") == "6543" or info.get("sslmode") not in ("require", "verify-ca", "verify-full")):
        raise MigrationError("Source requires TLS (prefer sslmode=verify-full) and a direct/session connection, never transaction port 6543.")
    return value


@contextmanager
def connect(source, readonly=False):
    with psycopg.connect(get_dsn(source), connect_timeout=15) as connection:
        connection.execute("SET statement_timeout = '30min'")
        connection.execute("SET lock_timeout = '15s'")
        connection.execute("SET idle_in_transaction_session_timeout = '30min'")
        connection.execute("SET timezone = 'UTC'")
        connection.execute("SET datestyle = 'ISO, YMD'")
        connection.execute("SET extra_float_digits = 3")
        connection.execute("SET search_path = public, pg_catalog")
        connection.execute("SET row_security = off")
        connection.commit()
        if source and not connection.pgconn.ssl_in_use:
            raise MigrationError("Source connection is not using TLS.")
        if readonly:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield connection


def database_identity(connection):
    # Unlike hostname comparisons this catches two aliases/poolers for one DB.
    try:
        row = connection.execute("""
            SELECT system_identifier::text, d.oid::text, current_database()
            FROM pg_catalog.pg_control_system(), pg_catalog.pg_database d
            WHERE d.datname=current_database()
        """).fetchone()
    except psycopg.errors.InsufficientPrivilege:
        raise MigrationError(
            "Database identity requires EXECUTE on pg_catalog.pg_control_system(). "
            "Managed Supabase postgres access may not include this permission. "
            "Obtain an authorized grant from the database/platform administrator or support; "
            "a password alone is insufficient. No weaker identity fallback is allowed."
        ) from None
    return hashlib.sha256(canonical(list(row))).hexdigest()


def require_distinct(source_identity, target):
    target_identity = database_identity(target)
    if source_identity == target_identity:
        raise MigrationError("Source and target are the SAME database; operation refused.")
    if any(target.execute(
        "SELECT to_regclass('public.portfolio_positions'), to_regclass('public.trade_history')"
    ).fetchone()):
        raise MigrationError("Local target contains live trading tables; operation refused.")
    return target_identity


def relation_list():
    return sql.SQL(", ").join(sql.Identifier("public", name) for name in TABLES)


def lock_tables(connection):
    connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(relation_list()))


def inventory(connection):
    """Fail closed on drift or relation dependencies outside the fixed inventory."""
    relations = connection.execute("""
        SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public'
          AND (c.relname LIKE 'intraday\\_%%' ESCAPE '\\' OR c.relname='exit_shadow_log')
          AND c.relkind IN ('r','p','v','m','f')
        ORDER BY c.relname
    """).fetchall()
    expected = sorted([(name, "r") for name in TABLES] + [(VIEW, "v")])
    if relations != expected:
        raise MigrationError("Research inventory differs from the exact 19 tables + view; inspect schema before proceeding.")
    names = list(TABLES)
    cross_fk = connection.execute("""
        SELECT 1 FROM pg_constraint k
        JOIN pg_class a ON a.oid=k.conrelid JOIN pg_namespace an ON an.oid=a.relnamespace
        JOIN pg_class b ON b.oid=k.confrelid JOIN pg_namespace bn ON bn.oid=b.relnamespace
        WHERE k.contype='f'
          AND ((an.nspname='public' AND a.relname=ANY(%s))
            <> (bn.nspname='public' AND b.relname=ANY(%s))) LIMIT 1
    """, (names, names)).fetchone()
    if cross_fk:
        raise MigrationError("A foreign key crosses the research boundary; live-table cascade risk.")
    unsafe = connection.execute("""
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE NOT t.tgisinternal AND n.nspname='public' AND c.relname=ANY(%s)
        UNION ALL
        SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid OR c.oid=i.inhparent
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s)
        LIMIT 1
    """, (names, names)).fetchone()
    if unsafe:
        raise MigrationError("Research triggers/inheritance require an explicit reviewed migration; refusing.")
    views = connection.execute("""
        SELECT DISTINCT vn.nspname, v.relname FROM pg_depend d
        JOIN pg_rewrite r ON r.oid=d.objid AND d.classid='pg_rewrite'::regclass
        JOIN pg_class v ON v.oid=r.ev_class JOIN pg_namespace vn ON vn.oid=v.relnamespace
        JOIN pg_class t ON t.oid=d.refobjid AND d.refclassid='pg_class'::regclass
        JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE tn.nspname='public' AND t.relname=ANY(%s) AND v.oid<>t.oid
    """, (names,)).fetchall()
    if set(views) != {("public", VIEW)}:
        raise MigrationError("Unexpected dependent research view; review schema dependencies.")
    outside_view = connection.execute("""
        SELECT 1 FROM pg_depend d
        JOIN pg_rewrite r ON r.oid=d.objid AND d.classid='pg_rewrite'::regclass
        JOIN pg_class v ON v.oid=r.ev_class JOIN pg_namespace vn ON vn.oid=v.relnamespace
        JOIN pg_class t ON t.oid=d.refobjid AND d.refclassid='pg_class'::regclass
        JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE vn.nspname='public' AND v.relname=%s AND v.oid<>t.oid
          AND NOT (tn.nspname='public' AND t.relname=ANY(%s)) LIMIT 1
    """, (VIEW, names)).fetchone()
    if outside_view:
        raise MigrationError("Research view reads nonresearch relations; refusing cross-boundary migration.")
    external_types = connection.execute("""
        SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace tn ON tn.oid=t.typnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped
          AND (tn.nspname<>'pg_catalog'
               OR t.typelem IN ('json'::regtype,'jsonb'::regtype)) LIMIT 1
    """, (names,)).fetchone()
    if external_types:
        raise MigrationError("Research uses non-built-in types or JSON arrays; explicit dependency/serialization support is required.")
    rows = connection.execute("""
        SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND
          (p.proname LIKE 'intraday\\_%%' ESCAPE '\\'
           OR p.proname LIKE '%%intraday%%')
        ORDER BY p.oid::regprocedure::text
    """).fetchall()
    normalize = lambda value: value.replace("public.", "").replace(", ", ",")
    if {normalize(row[0]) for row in rows} != set(FUNCTIONS):
        raise MigrationError("Research RPC inventory changed; all functions must be explicitly reviewed.")
    sequences = connection.execute("""
        SELECT sn.nspname, s.relname FROM pg_class s
        JOIN pg_namespace sn ON sn.oid=s.relnamespace
        JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass
          AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
        JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE s.relkind='S' AND tn.nspname='public' AND t.relname=ANY(%s)
        ORDER BY sn.nspname,s.relname
    """, (names,)).fetchall()
    if any(schema != "public" for schema, _ in sequences):
        raise MigrationError("Research sequence outside public schema is unsupported.")
    shared_sequence = connection.execute("""
        SELECT 1 FROM pg_depend d
        JOIN pg_attrdef a ON a.oid=d.objid AND d.classid='pg_attrdef'::regclass
        JOIN pg_class t ON t.oid=a.adrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        JOIN pg_class s ON s.oid=d.refobjid AND d.refclassid='pg_class'::regclass
        JOIN pg_namespace sn ON sn.oid=s.relnamespace
        WHERE sn.nspname='public' AND s.relname=ANY(%s)
          AND NOT (tn.nspname='public' AND t.relname=ANY(%s)) LIMIT 1
    """, ([name for _, name in sequences], names)).fetchone()
    if shared_sequence:
        raise MigrationError("Research-owned sequence is shared with a nonresearch table.")
    # Follow expressions (defaults, constraints, policies and view rules), not
    # just foreign keys. In particular a policy must not read a live table.
    boundary_dependency = connection.execute("""
        WITH owned AS (
            SELECT 'pg_class'::regclass AS classid,c.oid AS objid
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relname=ANY(%s)
            UNION ALL
            SELECT 'pg_attrdef'::regclass,a.oid FROM pg_attrdef a
              WHERE a.adrelid IN (SELECT objid FROM (
                SELECT c.oid objid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname=ANY(%s)) tables)
            UNION ALL
            SELECT 'pg_constraint'::regclass,k.oid FROM pg_constraint k
              JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relname=ANY(%s)
            UNION ALL
            SELECT 'pg_policy'::regclass,p.oid FROM pg_policy p
              JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relname=ANY(%s)
            UNION ALL
            SELECT 'pg_rewrite'::regclass,r.oid FROM pg_rewrite r
              JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relname=%s
        )
        SELECT 1 FROM owned o JOIN pg_depend d ON d.classid=o.classid AND d.objid=o.objid
        LEFT JOIN pg_class c ON d.refclassid='pg_class'::regclass AND c.oid=d.refobjid
        LEFT JOIN pg_namespace cn ON cn.oid=c.relnamespace
        LEFT JOIN pg_index ci ON ci.indexrelid=c.oid
        LEFT JOIN pg_class ct ON ct.oid=ci.indrelid
        LEFT JOIN pg_namespace ctn ON ctn.oid=ct.relnamespace
        LEFT JOIN pg_proc p ON d.refclassid='pg_proc'::regclass AND p.oid=d.refobjid
        LEFT JOIN pg_namespace pn ON pn.oid=p.pronamespace
        WHERE (c.oid IS NOT NULL AND cn.nspname<>'pg_catalog'
               AND NOT (cn.nspname='public' AND c.relname=ANY(%s))
               AND NOT coalesce(ctn.nspname='public' AND ct.relname=ANY(%s),false))
           OR (p.oid IS NOT NULL AND pn.nspname<>'pg_catalog'
               AND NOT (pn.nspname='public' AND p.proname=ANY(%s)))
        LIMIT 1
    """, (names + [VIEW], names, names, names, VIEW,
          names + [VIEW] + [name for _, name in sequences],
          names,
          [function.split("(")[0] for function in FUNCTIONS])).fetchone()
    if boundary_dependency:
        raise MigrationError("Research schema expressions depend on nonresearch relations/routines.")
    sequence_definitions = connection.execute("""
        SELECT c.relname, s.seqtypid::regtype::text, s.seqstart, s.seqincrement,
               s.seqmax, s.seqmin, s.seqcache, s.seqcycle
        FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname
    """, ([name for _, name in sequences],)).fetchall()
    return {"functions": dict(rows), "sequences": [name for _, name in sequences],
            "sequence_definitions": [list(row) for row in sequence_definitions]}


def schema_definition(connection, table):
    relation = f"public.{table}"
    columns = connection.execute("""
        SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated,
               co.collname
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation
        WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
    """, (relation,)).fetchall()
    constraints = connection.execute("""
        SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid=%s::regclass ORDER BY conname
    """, (relation,)).fetchall()
    indexes = connection.execute("""
        SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname
    """, (table,)).fetchall()
    settings = connection.execute("""
        SELECT relrowsecurity,relforcerowsecurity,reloptions FROM pg_class WHERE oid=%s::regclass
    """, (relation,)).fetchone()
    policies = connection.execute("""
        SELECT policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' AND tablename=%s ORDER BY policyname
    """, (table,)).fetchall()
    return {"columns": columns, "constraints": constraints, "indexes": indexes,
            "settings": settings, "policies": policies}


def scan_table(connection, table, output=None):
    """Server cursor + PG's own JSON text: no float/Decimal/JSON round trip."""
    schema = schema_definition(connection, table)
    keys = connection.execute("""
        SELECT a.attname, a.attcollation<>0 FROM pg_index i
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(num,ord)
        JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num
        WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.ord
    """, (f"public.{table}",)).fetchall()
    if not keys:
        raise MigrationError(f"{table} has no primary key; deterministic full-data verification unavailable.")
    # Preserve native numeric/UUID ordering; text keys use a fixed collation so
    # source and target locale differences cannot change stream hashes.
    order = sql.SQL(", ").join(
        sql.SQL('t.{} COLLATE "C"' if key[1] else "t.{}").format(sql.Identifier(key[0]))
        for key in keys
    )
    json_columns = [column[0] for column in schema.get("columns", [])
                    if column[1] in ("json", "jsonb")]
    null_flags = sql.SQL(", ").join(
        sql.SQL("CASE WHEN t.{} IS NULL THEN {} END").format(sql.Identifier(column), sql.Literal(column))
        for column in json_columns
    )
    # Native row JSON alone conflates SQL NULL with JSON null. Record which
    # JSON-typed columns were SQL NULL, without parsing any data in Python.
    query = sql.SQL("""
        SELECT json_build_object('row', row_to_json(t),
            'sql_null_json_columns', array_remove(ARRAY[{}]::text[],NULL))::text
        FROM {} t ORDER BY {}
    """).format(
        null_flags, sql.Identifier("public", table), order,
    )
    digest, count = hashlib.sha256(), 0
    with connection.cursor(name="research_scan_" + uuid.uuid4().hex) as cursor:
        cursor.itersize = 1000
        cursor.execute(query)
        for row in cursor:
            data = row[0].encode("utf-8") + b"\n"
            digest.update(data)
            count += 1
            if output is not None:
                output.write(data)
    return {
        "rows": count, "sha256": digest.hexdigest(),
        "schema_sha256": hashlib.sha256(canonical(schema)).hexdigest(),
    }


def scan_all(connection, directory=None):
    results = {}
    for table in TABLES:
        if directory is None:
            results[table] = scan_table(connection, table)
        else:
            path = directory / f"{table}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                results[table] = scan_table(connection, table, stream)
                stream.flush()
                os.fsync(stream.fileno())
    return results


def assert_equal(actual, expected, label):
    if actual != expected:
        raise MigrationError(f"{label} differs in full row content/count/schema. Pause research writers and re-export; counts alone are not proof.")


def view_definition(connection):
    return list(connection.execute("""
        SELECT pg_get_viewdef(oid,true), reloptions FROM pg_class WHERE oid=%s::regclass
    """, (f"public.{VIEW}",)).fetchone())


def function_script(objects):
    # pg_get_functiondef() returns a definition without a statement terminator.
    return "\n".join(value.rstrip().rstrip(";") + ";\n"
                     for value in objects["functions"].values()).encode()


def export_snapshot(directory, docker=False, database="source"):
    if database not in ("source", "local"):
        raise MigrationError("Export database must be source or local.")
    source_flag = database == "source"
    directory = fresh_directory(directory)
    with connect(source_flag, readonly=True) as source:
        objects = inventory(source)
        identity = database_identity(source)
        # Hold schema stable while allowing ongoing INSERT/UPDATE/DELETE.
        source.execute(sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(relation_list()))
        snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
        manifest = {
            "version": FORMAT_VERSION, "export_id": str(uuid.uuid4()), "origin": database,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_identity": identity,
            "server_version": source.info.server_version,
            "objects": objects,
            "view_definition": view_definition(source),
            "tables": scan_all(source, directory),
        }
        args = ["--format=custom", "--no-owner", "--no-privileges",
                "--strict-names", "--lock-wait-timeout=15000",
                f"--snapshot={snapshot}", "--file=research.dump"]
        for name in (*TABLES, VIEW, *objects["sequences"]):
            args += ["--table", f"public.{name}"]
        run_client("pg_dump", args, get_dsn(source_flag), directory, docker)
        os.chmod(directory / "research.dump", 0o600)
        functions = function_script(objects)
        private_write(directory / "functions.sql", functions)
        names = [f"{table}.jsonl" for table in TABLES] + ["research.dump", "functions.sql"]
        manifest["files"] = {
            name: {"sha256": digest_file(directory / name), "bytes": (directory / name).stat().st_size}
            for name in names
        }
        private_write(directory / "manifest.json", canonical(manifest))
    # Never advertise completeness before the snapshot transaction succeeds.
    private_write(directory / "COMPLETE", (digest_file(directory / "manifest.json") + "\n").encode())
    return manifest


def verify_archive(directory):
    directory = Path(directory).resolve()
    if (not directory.is_dir() or (directory / "COMPLETE").is_symlink()
            or (directory / "manifest.json").is_symlink()):
        raise MigrationError("Archive directory/COMPLETE marker missing or unsafe.")
    marker = (directory / "COMPLETE").read_text().strip()
    if marker != digest_file(directory / "manifest.json"):
        raise MigrationError("Archive manifest is incomplete or its checksum changed.")
    manifest = json.loads((directory / "manifest.json").read_text())
    expected_files = {f"{table}.jsonl" for table in TABLES} | {"research.dump", "functions.sql"}
    if (manifest.get("version") != FORMAT_VERSION
            or manifest.get("origin", "source") not in ("source", "local")
            or set(manifest.get("tables", {})) != set(TABLES)
            or set(manifest.get("files", {})) != expected_files):
        raise MigrationError("Archive format/inventory is not supported.")
    for name, detail in manifest["files"].items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise MigrationError("Archive file missing or symlink encountered.")
        if path.stat().st_size != detail["bytes"] or digest_file(path) != detail["sha256"]:
            raise MigrationError(f"Archive checksum failed: {name}")
    for table in TABLES:
        detail = manifest["tables"][table]
        if manifest["files"][f"{table}.jsonl"]["sha256"] != detail["sha256"]:
            raise MigrationError("Row-stream checksum and manifest disagree.")
        with (directory / f"{table}.jsonl").open("rb") as stream:
            if sum(1 for _ in stream) != detail["rows"]:
                raise MigrationError("Row-stream count and manifest disagree.")
    return manifest


def archive_copy(directory, destination):
    manifest = verify_archive(directory)
    directory, destination = Path(directory).resolve(), Path(destination).resolve()
    manifest_hash = digest_file(directory / "manifest.json")
    if directory == destination or directory in destination.parents:
        raise MigrationError("Second archive must be independent, not inside the first archive.")
    if destination.parent.stat().st_dev == directory.stat().st_dev:
        raise MigrationError("Second archive must be on a DIFFERENT filesystem/device; a second directory is not independent backup.")
    destination = fresh_directory(destination)
    for name in ("manifest.json", *manifest["files"]):
        with (directory / name).open("rb") as source:
            fd = os.open(destination / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
        expected_hash = manifest_hash if name == "manifest.json" else manifest["files"][name]["sha256"]
        if digest_file(destination / name) != expected_hash:
            raise MigrationError("Second-copy readback failed; no COMPLETE marker written.")
    private_write(destination / "COMPLETE", (manifest_hash + "\n").encode())
    assert_equal(verify_archive(destination), manifest, "Second archive")
    return manifest


def verify_second_copy(directory, second):
    directory, second = Path(directory).resolve(), Path(second).resolve()
    if directory == second or directory in second.parents or second in directory.parents:
        raise MigrationError("Two independent archive copies are required.")
    if directory.stat().st_dev == second.stat().st_dev:
        raise MigrationError("Second archive must be on a different filesystem/device.")
    one, two = verify_archive(directory), verify_archive(second)
    assert_equal(two, one, "Second archive")
    for name in ("manifest.json", "COMPLETE", *one["files"]):
        if (directory / name).samefile(second / name):
            raise MigrationError("Independent archive files must not be hard-linked.")
    return one


def permission_sql(objects, purge=False):
    statements = []
    roles = "PUBLIC, anon, authenticated, service_role"
    for table in (*TABLES, VIEW):
        statements.append(f'REVOKE ALL ON TABLE public."{table}" FROM {roles};')
    for name in objects["sequences"]:
        if not name.replace("_", "").isalnum():
            raise MigrationError("Unsupported sequence identifier.")
        statements.append(f'REVOKE ALL ON SEQUENCE public."{name}" FROM {roles};')
    for function in FUNCTIONS:
        statements.append(f"REVOKE ALL ON FUNCTION public.{function} FROM {roles};")
    if not purge:
        restricted = {"intraday_calibration_settings", "intraday_calibration_proposals",
                      "intraday_calibration_health", "intraday_calibration_lease"}
        for table in TABLES:
            privileges = ("SELECT, INSERT" if table == "intraday_calibration_events"
                          else "SELECT, INSERT, UPDATE" if table in restricted
                          else "SELECT, INSERT, UPDATE, DELETE")
            statements.append(f'GRANT {privileges} ON public."{table}" TO service_role;')
        statements.append("GRANT UPDATE (notified_at) ON public.intraday_calibration_events TO service_role;")
        statements.append(f"GRANT SELECT ON public.{VIEW} TO service_role;")
        for name in objects["sequences"]:
            statements.append(f'GRANT USAGE, SELECT ON SEQUENCE public."{name}" TO service_role;')
        for function in FUNCTIONS:
            statements.append(f"GRANT EXECUTE ON FUNCTION public.{function} TO service_role;")
    return "\n".join(statements)


def import_archive(directory, workdir, confirmation, trust_archive=False, docker=False):
    if confirmation != IMPORT_CONFIRMATION or not trust_archive:
        raise MigrationError("Import requires exact confirmation and --trust-archive (native SQL from a trusted source administrator).")
    manifest = verify_archive(directory)
    directory, workdir = Path(directory).resolve(), fresh_directory(workdir)
    # Recovery must work even when the old server is gone. The hash-verified
    # archive records its source identity; never restore back into that database.
    source_identity = manifest["source_identity"]
    with connect(False) as target:
        target_identity = require_distinct(source_identity, target)
        existing = target.execute("""
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S') LIMIT 1
        """).fetchone()
        if existing:
            raise MigrationError("Import requires a fresh local public schema; existing tables (even empty) are not overwritten.")
        if target.execute("SELECT to_regclass('research_migration_admin.import_proof')").fetchone()[0]:
            raise MigrationError("Local database has an earlier import marker; use a fresh database.")
        roles = target.execute("SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", (["anon", "authenticated", "service_role", "authenticator"],)).fetchall()
        if len(roles) != 4:
            raise MigrationError("Run research_bootstrap.py roles first.")
    # A dedicated workdir is retained on failure for operator diagnosis.
    for name in ("research.dump", "functions.sql"):
        shutil.copyfile(directory / name, workdir / name)
        os.chmod(workdir / name, 0o600)
        if digest_file(workdir / name) != manifest["files"][name]["sha256"]:
            raise MigrationError("Archive changed during import staging.")
    run_client("pg_restore", ["--no-owner", "--no-privileges", "--file=restore.sql", "research.dump"],
               get_dsn(False), workdir, docker)
    os.chmod(workdir / "restore.sql", 0o600)
    # Import proof is database-local and cannot be mistaken for a filesystem receipt.
    marker = f"""
CREATE SCHEMA research_migration_admin;
REVOKE ALL ON SCHEMA research_migration_admin FROM PUBLIC, anon, authenticated, service_role, authenticator;
CREATE TABLE research_migration_admin.import_proof (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    export_id text NOT NULL, manifest_sha256 text NOT NULL,
    source_identity text NOT NULL, target_identity text NOT NULL, verified boolean NOT NULL DEFAULT false
);
INSERT INTO research_migration_admin.import_proof
    (export_id,manifest_sha256,source_identity,target_identity)
VALUES ({sql.Literal(manifest['export_id']).as_string()},
        {sql.Literal(digest_file(directory / 'manifest.json')).as_string()},
        {sql.Literal(source_identity).as_string()}, {sql.Literal(target_identity).as_string()});
"""
    # No API privileges until full readback passes.
    private_write(workdir / "finish.sql", (permission_sql(manifest["objects"], purge=True) + marker).encode())
    run_client("psql", ["-X", "--single-transaction", "--set=ON_ERROR_STOP=1",
                       "--file=restore.sql", "--file=functions.sql", "--file=finish.sql"],
               get_dsn(False), workdir, docker)
    with connect(False) as target:
        require_distinct(source_identity, target)
        lock_tables(target)
        assert_equal(inventory(target), manifest["objects"], "Imported functions/sequences")
        assert_equal(scan_all(target), manifest["tables"], "Imported research")
        verify_view(target, manifest)
        target.execute(permission_sql(manifest["objects"]))
        target.execute("UPDATE research_migration_admin.import_proof SET verified=true WHERE singleton")
    for path in workdir.iterdir():
        path.unlink()
    workdir.rmdir()
    return manifest


def verify_view(connection, manifest):
    definition = view_definition(connection)
    assert_equal(definition, manifest["view_definition"], "Research view definition")


def verify_import_proof(target, manifest, manifest_hash):
    if not target.execute("SELECT to_regclass('research_migration_admin.import_proof')").fetchone()[0]:
        raise MigrationError("Verified local import proof is missing.")
    row = target.execute("""
        SELECT export_id,manifest_sha256,source_identity,target_identity,verified
        FROM research_migration_admin.import_proof WHERE singleton
    """).fetchone()
    expected = (manifest["export_id"], manifest_hash, manifest["source_identity"], database_identity(target), True)
    if row != expected:
        raise MigrationError("Local import proof is not verified or belongs to another archive/database.")


def verify_databases(directory):
    manifest = verify_archive(directory)
    with connect(True, readonly=True) as source, connect(False, readonly=True) as target:
        if database_identity(source) != manifest["source_identity"]:
            raise MigrationError("Source database identity changed.")
        require_distinct(manifest["source_identity"], target)
        verify_import_proof(target, manifest, digest_file(Path(directory) / "manifest.json"))
        for label, connection in (("Source", source), ("Target", target)):
            assert_equal(inventory(connection), manifest["objects"], f"{label} functions/sequences")
            assert_equal(scan_all(connection), manifest["tables"], label)
            verify_view(connection, manifest)
    return manifest


def verify_target(directory):
    manifest = verify_archive(directory)
    with connect(False, readonly=True) as target:
        require_distinct(manifest["source_identity"], target)
        verify_import_proof(target, manifest, digest_file(Path(directory) / "manifest.json"))
        assert_equal(inventory(target), manifest["objects"], "Target functions/sequences")
        assert_equal(scan_all(target), manifest["tables"], "Target")
        verify_view(target, manifest)
    return manifest


def write_import_receipt(directory, receipt, manifest):
    """Called only after full verification; never replace an existing receipt."""
    manifest_hash = digest_file(Path(directory) / "manifest.json")
    with connect(False, readonly=True) as target:
        verify_import_proof(target, manifest, manifest_hash)
        target_identity = require_distinct(manifest["source_identity"], target)
    private_write(Path(receipt), canonical({
        "version": FORMAT_VERSION,
        "status": "verified",
        "export_id": manifest["export_id"],
        "manifest_sha256": manifest_hash,
        "target_identity": target_identity,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "tables": len(TABLES),
        "rows": sum(table["rows"] for table in manifest["tables"].values()),
    }))


def revoke_source_access(source, objects):
    source.execute(permission_sql(objects, purge=True))
    # Table-level REVOKE does not revoke column grants (calibration audit updates).
    for table in (*TABLES, VIEW):
        columns = source.execute("""
            SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass
            AND attnum>0 AND NOT attisdropped ORDER BY attnum
        """, (f"public.{table}",)).fetchall()
        identifiers = sql.SQL(", ").join(sql.Identifier(row[0]) for row in columns)
        source.execute(sql.SQL(
            "REVOKE ALL ({}) ON TABLE {} FROM PUBLIC, anon, authenticated, service_role"
        ).format(identifiers, sql.Identifier("public", table)))
        for role in ("anon", "authenticated", "service_role"):
            row = source.execute("""
                SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                       has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')
            """, (role, f"public.{table}", role, f"public.{table}")).fetchone()
            if any(row):
                raise MigrationError(f"{role} retains inherited/owner access to {table} after REVOKE; source purge rolled back.")
    for function in FUNCTIONS:
        for role in ("anon", "authenticated", "service_role"):
            if source.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                              (role, f"public.{function}")).fetchone()[0]:
                raise MigrationError("Inherited RPC privileges remain; source purge rolled back.")
    for role in ("anon", "authenticated", "service_role"):
        if source.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                          (role, f"public.{VIEW}")).fetchone()[0]:
            raise MigrationError("Inherited view privileges remain; source purge rolled back.")


def purge_source(directory, second, confirmation, cutover, manifest_sha256):
    if confirmation != PURGE_CONFIRMATION or cutover != CUTOVER_CONFIRMATION:
        raise MigrationError("Purge requires both exact confirmation phrases. Pause writers and route research local FIRST.")
    manifest = verify_second_copy(directory, second)
    if manifest_sha256 != digest_file(Path(directory) / "manifest.json"):
        raise MigrationError("Explicit --manifest-sha256 does not match this archive; cloud cleanup refused.")
    if manifest.get("origin", "source") != "source":
        raise MigrationError("Local recurring archives cannot authorize cloud cleanup.")
    with connect(False) as target, connect(True) as source:
        source_identity = database_identity(source)
        if source_identity != manifest["source_identity"]:
            raise MigrationError("Source does not match exported database.")
        require_distinct(source_identity, target)
        # READ COMMITTED is intentional: statements following a lock wait must see
        # writes which committed while waiting, not a pre-lock repeatable snapshot.
        lock_tables(target)
        lock_tables(source)
        verify_import_proof(target, manifest, digest_file(Path(directory) / "manifest.json"))
        for label, connection in (("Source under lock", source), ("Target under lock", target)):
            assert_equal(inventory(connection), manifest["objects"], f"{label} functions/sequences")
            assert_equal(scan_all(connection), manifest["tables"], label)
            verify_view(connection, manifest)
        revoke_source_access(source, manifest["objects"])
        source.execute(sql.SQL("TRUNCATE TABLE {} CONTINUE IDENTITY RESTRICT").format(relation_list()))
        for table in TABLES:
            if source.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {})").format(sql.Identifier("public", table))).fetchone()[0]:
                raise MigrationError("Post-purge research verification failed; transaction rolled back.")
        # Source commits while target remains locked and unchanged.
        source.commit()
    return manifest


def prepare():
    with connect(True, readonly=True) as source, connect(False, readonly=True) as target:
        objects = inventory(source)
        require_distinct(database_identity(source), target)
        for role in ("anon", "authenticated", "service_role"):
            if not source.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                raise MigrationError("Required source API roles missing.")
    return objects


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-tools", action="store_true", help="Use postgres:17 Docker clients instead of local pg_dump/pg_restore/psql.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Read-only connection, exact inventory and database-identity preflight.")
    export = commands.add_parser("export", help="Consistent native dump + exact JSONL + checksum manifest; never deletes.")
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--database", choices=("source", "local"), default="source",
                        help="source uses SOURCE_RESEARCH_PG_DSN; local supports recurring backups via LOCAL_RESEARCH_PG_DSN.")
    archive = commands.add_parser("archive", help="Copy and read back a complete archive onto a second filesystem.")
    archive.add_argument("--archive", required=True, type=Path)
    archive.add_argument("--destination", required=True, type=Path)
    check = commands.add_parser("check-archive", help="Offline full checksum/readback verification.")
    check.add_argument("--archive", required=True, type=Path)
    restore = commands.add_parser("import", help="Transactional schema/data restore into a fresh local database, then full readback.")
    restore.add_argument("--archive", required=True, type=Path)
    restore.add_argument("--workdir", required=True, type=Path, help="New private working directory, removed on success.")
    restore.add_argument("--confirm", required=True)
    restore.add_argument("--trust-archive", action="store_true")
    verify = commands.add_parser("verify", help="Read-only full source/target/schema comparison and local import proof.")
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--target-only", action="store_true", help="Verify a restored local database without requiring the original server (disaster recovery).")
    verify.add_argument("--receipt", type=Path,
                        help="After successful verification, create a NEW private deployment receipt (normally RESEARCH_DATA_DIR/import-verified.json).")
    purge = commands.add_parser("purge", help="DESTRUCTIVE: locked recheck, revoke source API access, then truncate ONLY research.")
    purge.add_argument("--archive", required=True, type=Path)
    purge.add_argument("--second-copy", required=True, type=Path)
    purge.add_argument("--confirm", required=True)
    purge.add_argument("--cutover-confirm", required=True)
    purge.add_argument("--manifest-sha256", required=True,
                       help="Explicit archive identity: copy the SHA-256 from this archive's COMPLETE file.")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare()
            print("Preflight passed: 19 research tables + view + five RPCs; distinct database identities. No data changed.")
            return 0
        if args.command == "export":
            manifest = export_snapshot(args.output, args.docker_tools, args.database)
        elif args.command == "archive":
            manifest = archive_copy(args.archive, args.destination)
        elif args.command == "check-archive":
            manifest = verify_archive(args.archive)
        elif args.command == "import":
            manifest = import_archive(args.archive, args.workdir, args.confirm, args.trust_archive, args.docker_tools)
        elif args.command == "verify":
            manifest = verify_target(args.archive) if args.target_only else verify_databases(args.archive)
            if args.receipt:
                write_import_receipt(args.archive, args.receipt, manifest)
        else:
            manifest = purge_source(args.archive, args.second_copy, args.confirm,
                                    args.cutover_confirm, args.manifest_sha256)
        print(f"{args.command} successful: 19 tables, {sum(t['rows'] for t in manifest['tables'].values())} archived rows, export {manifest['export_id']}.")
        return 0
    except MigrationError as error:
        print(f"STOP: {error}", file=sys.stderr)
    except (psycopg.Error, OSError, ValueError, KeyError, TypeError):
        print("STOP: operation failed; no success claimed. Check credentials, admin privileges, PostgreSQL versions and archive files. Database exception details suppressed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
