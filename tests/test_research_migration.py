"""Offline guards; scratch artifacts stay inside the repository, not /tmp."""

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
import uuid

import pytest

pytest.importorskip("psycopg")

from scripts import research_bootstrap as bootstrap
from scripts import research_migration as migration
from scripts import research_pg_tools as tools


@pytest.fixture
def workspace():
    directory = Path.cwd() / (".research-migration-test-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


@pytest.fixture
def archive(workspace):
    root = workspace / "export"
    root.mkdir(mode=0o700)
    tables, files = {}, {}
    for table in migration.TABLES:
        data = b'{"id":"a","value":12345678901234567890.123456789,"payload":{"x":null}}\n'
        (root / f"{table}.jsonl").write_bytes(data)
        tables[table] = {"rows": 1, "sha256": hashlib.sha256(data).hexdigest(), "schema_sha256": "schema"}
    (root / "research.dump").write_bytes(b"native-archive-fixture")
    (root / "functions.sql").write_bytes(b"SELECT 1;")
    for path in root.iterdir():
        files[path.name] = {"bytes": path.stat().st_size, "sha256": migration.digest_file(path)}
    manifest = {
        "version": 1, "export_id": str(uuid.uuid4()), "source_identity": "source",
        "objects": {"functions": {}, "sequences": []}, "tables": tables, "files": files,
        "view_definition": "SELECT session FROM intraday_capture_events",
    }
    (root / "manifest.json").write_bytes(migration.canonical(manifest))
    (root / "COMPLETE").write_text(migration.digest_file(root / "manifest.json") + "\n")
    return root, manifest


def test_inventory_exactly_research_not_live():
    assert len(migration.TABLES) == 19
    assert len(set(migration.TABLES)) == 19
    assert not {"agent_logs", "portfolio_positions", "trade_history", migration.VIEW} & set(migration.TABLES)
    assert len(migration.FUNCTIONS) == 5


def test_function_script_terminates_pg_get_functiondef_statements():
    result = migration.function_script({"functions": {
        "one": "CREATE FUNCTION one() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$\n",
        "two": "CREATE FUNCTION two() RETURNS int LANGUAGE sql AS $$ SELECT 2 $$\n",
    }})
    assert result.count(b"$$;") == 2


@pytest.mark.parametrize("value", [
    "postgresql://admin:secret@host/source",
    "postgresql://admin:secret@host:6543/source?sslmode=require",
    "postgresql://admin:secret@host/source?sslmode=prefer",
])
def test_source_rejects_non_tls_and_transaction_pooler(monkeypatch, value):
    monkeypatch.setenv("SOURCE_RESEARCH_PG_DSN", value)
    with pytest.raises(migration.MigrationError, match="TLS"):
        migration.get_dsn(True)


def test_source_accepts_explicit_tls_session_pooler(monkeypatch):
    dsn = "postgresql://admin:secret@host:5432/source?sslmode=verify-full"
    monkeypatch.setenv("SOURCE_RESEARCH_PG_DSN", dsn)
    assert migration.get_dsn(True) == dsn


def test_rest_key_is_not_native_credentials(monkeypatch):
    monkeypatch.delenv("SOURCE_RESEARCH_PG_DSN", raising=False)
    monkeypatch.setenv("SUPABASE_KEY", "not-a-dsn")
    with pytest.raises(migration.MigrationError, match="REST key"):
        migration.get_dsn(True)


def test_complete_archive_readback(archive):
    root, manifest = archive
    assert migration.verify_archive(root) == manifest


@pytest.mark.parametrize("path", ["research.dump", "functions.sql", "intraday_capture_events.jsonl", "manifest.json"])
def test_modified_archive_rejected(archive, path):
    root, _ = archive
    with (root / path).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(migration.MigrationError):
        migration.verify_archive(root)


def test_incomplete_export_rejected(archive):
    root, _ = archive
    (root / "COMPLETE").unlink()
    assert migration.main(["check-archive", "--archive", str(root)]) == 1


def test_manifest_cannot_add_unchecked_sql(archive):
    root, manifest = archive
    manifest["files"]["../../evil.sql"] = {"bytes": 0, "sha256": ""}
    (root / "manifest.json").write_bytes(migration.canonical(manifest))
    (root / "COMPLETE").write_text(migration.digest_file(root / "manifest.json"))
    with pytest.raises(migration.MigrationError, match="inventory"):
        migration.verify_archive(root)


def test_same_filesystem_is_not_second_backup(archive, workspace):
    root, _ = archive
    with pytest.raises(migration.MigrationError, match="DIFFERENT"):
        migration.archive_copy(root, workspace / "second")
    assert not (workspace / "second").exists()


def test_nested_archive_is_not_independent(archive):
    root, _ = archive
    with pytest.raises(migration.MigrationError, match="independent"):
        migration.archive_copy(root, root / "second")


def test_symlink_archive_file_refused(archive, workspace):
    root, _ = archive
    data = (root / "research.dump").read_bytes()
    (workspace / "external.dump").write_bytes(data)
    (root / "research.dump").unlink()
    (root / "research.dump").symlink_to(workspace / "external.dump")
    with pytest.raises(migration.MigrationError, match="symlink"):
        migration.verify_archive(root)


def test_import_requires_explicit_trust_and_confirmation(archive, workspace):
    root, _ = archive
    with pytest.raises(migration.MigrationError, match="--trust-archive"):
        migration.import_archive(root, workspace / "work", migration.IMPORT_CONFIRMATION)
    assert not (workspace / "work").exists()


def test_same_database_refused_even_different_hostname(monkeypatch):
    monkeypatch.setattr(migration, "database_identity", lambda _: "same")
    with pytest.raises(migration.MigrationError, match="SAME"):
        migration.require_distinct("same", object())


def test_managed_database_identity_permission_failure_is_explicit():
    class Denied:
        def execute(self, *_):
            raise migration.psycopg.errors.InsufficientPrivilege("private native connection details")
    with pytest.raises(migration.MigrationError, match=r"EXECUTE on pg_catalog.pg_control_system") as error:
        migration.database_identity(Denied())
    assert "private native connection details" not in str(error.value)
    assert "No weaker identity fallback" in str(error.value)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, results=(), label=""):
        self.results = iter(results)
        self.statements = []
        self.label = label
        self.committed = False

    def execute(self, query, params=None):
        self.statements.append((query.as_string() if hasattr(query, "as_string") else query, params))
        return Result(next(self.results, []))

    def commit(self):
        self.committed = True


def test_inventory_rejects_unknown_table():
    rows = sorted([(table, "r") for table in migration.TABLES] + [(migration.VIEW, "v"), ("intraday_new", "r")])
    with pytest.raises(migration.MigrationError, match="inventory"):
        migration.inventory(FakeConnection([rows]))


def test_inventory_rejects_cross_boundary_fk():
    rows = sorted([(table, "r") for table in migration.TABLES] + [(migration.VIEW, "v")])
    with pytest.raises(migration.MigrationError, match="foreign key"):
        migration.inventory(FakeConnection([rows, [(1,)]]))


def test_inventory_rejects_truncate_trigger():
    rows = sorted([(table, "r") for table in migration.TABLES] + [(migration.VIEW, "v")])
    with pytest.raises(migration.MigrationError, match="triggers"):
        migration.inventory(FakeConnection([rows, [], [(1,)]]))


@pytest.mark.parametrize("boundary_drift", [False, True])
def test_inventory_checks_schema_expressions_and_sequences(boundary_drift):
    relations = sorted([(table, "r") for table in migration.TABLES] + [(migration.VIEW, "v")])
    functions = [(name, "definition") for name in migration.FUNCTIONS]
    responses = [
        relations, [], [], [("public", migration.VIEW)], [], [], functions,
        [("public", "exit_shadow_log_id_seq")], [], [(1,)] if boundary_drift else [],
        [("exit_shadow_log_id_seq", "bigint", 1, 1, 9223372036854775807, 1, 1, False)],
    ]
    connection = FakeConnection(responses)
    if boundary_drift:
        with pytest.raises(migration.MigrationError, match="schema expressions"):
            migration.inventory(connection)
    else:
        result = migration.inventory(connection)
        assert result["sequences"] == ["exit_shadow_log_id_seq"]
        assert result["sequence_definitions"][0][3] == 1


def test_scan_streams_exact_pg_text_no_numeric_roundtrip(monkeypatch):
    rows = [
        '{"id":1,"n":12345678901234567890.1234567890123456789,"j":{"x":null},"v":null}',
        '{"id":2,"n":-0.0000000000000000001,"j":{"a":[1,2]},"v":"東京"}',
    ]
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, query):
            self.query = query.as_string()
            assert 'COLLATE "C"' in self.query
        def __iter__(self):
            return iter((row,) for row in rows)
    connection = FakeConnection([[("id", True)]])
    connection.cursor = lambda **_: Cursor()
    monkeypatch.setattr(migration, "schema_definition", lambda *_: {"column": "numeric"})
    result = migration.scan_table(connection, "exit_shadow_log")
    assert result["rows"] == 2
    assert result["sha256"] == hashlib.sha256(("\n".join(rows) + "\n").encode()).hexdigest()


def test_primary_key_order_keeps_sequence_numeric_and_text_collation_fixed(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, query):
            assert 'ORDER BY t."run_id" COLLATE "C", t."sequence"' in query.as_string()
        def __iter__(self):
            return iter(())
    connection = FakeConnection([[("run_id", True), ("sequence", False)]])
    connection.cursor = lambda **_: Cursor()
    monkeypatch.setattr(migration, "schema_definition", lambda *_: {})
    assert migration.scan_table(connection, "intraday_shadow_checkpoints")["rows"] == 0


def test_permissions_preserve_calibration_audit_column_restriction():
    statement = migration.permission_sql({"sequences": ["exit_shadow_log_id_seq"]})
    assert "GRANT UPDATE (notified_at) ON public.intraday_calibration_events" in statement
    assert 'GRANT SELECT, INSERT ON public."intraday_calibration_events"' in statement
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON public."intraday_calibration_events"' not in statement
    assert "intraday_calibration_refresh_risk(jsonb) FROM PUBLIC, anon, authenticated, service_role" in statement
    assert 'REVOKE ALL ON TABLE public."intraday_capture_sessions"' in statement
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON public."intraday_capture_sessions"' not in statement


def test_purge_rejects_wrong_confirm_before_opening_any_database(monkeypatch):
    monkeypatch.setattr(migration, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(migration.MigrationError, match="both exact"):
        migration.purge_source("missing", "missing", "yes", migration.CUTOVER_CONFIRMATION, "wrong")


def test_local_backup_cannot_authorize_cloud_purge(monkeypatch, archive):
    root, manifest = archive
    manifest["origin"] = "local"
    monkeypatch.setattr(migration, "verify_second_copy", lambda *_: manifest)
    monkeypatch.setattr(migration, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(migration.MigrationError, match="Local recurring"):
        migration.purge_source(root, "copy", migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                               migration.digest_file(root / "manifest.json"))


def test_purge_requires_this_archives_explicit_manifest_sha(monkeypatch, archive):
    root, manifest = archive
    monkeypatch.setattr(migration, "verify_second_copy", lambda *_: manifest)
    monkeypatch.setattr(migration, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(migration.MigrationError, match="manifest-sha256"):
        migration.purge_source(root, "copy", migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                               "0" * 64)


def configure_purge(monkeypatch, archive, mutate_source=False):
    root, manifest = archive
    source, target = FakeConnection(label="source"), FakeConnection(label="target")
    @contextmanager
    def connect(source_flag, **_):
        yield source if source_flag else target
    monkeypatch.setattr(migration, "connect", connect)
    monkeypatch.setattr(migration, "verify_second_copy", lambda *_: manifest)
    monkeypatch.setattr(migration, "database_identity", lambda conn: conn.label)
    monkeypatch.setattr(migration, "require_distinct", lambda *_: "target")
    monkeypatch.setattr(migration, "verify_import_proof", lambda *_: None)
    monkeypatch.setattr(migration, "inventory", lambda *_: manifest["objects"])
    monkeypatch.setattr(migration, "verify_view", lambda *_: None)
    monkeypatch.setattr(migration, "revoke_source_access", lambda conn, _: conn.execute("REVOKE proof"))
    def scan(conn):
        values = json.loads(json.dumps(manifest["tables"]))
        if conn is source and mutate_source:
            values["exit_shadow_log"]["sha256"] = "changed with same count"
        return values
    monkeypatch.setattr(migration, "scan_all", scan)
    # The only fetches after patching other routines are empty-table checks.
    source.results = iter([[], [], [], *[[(False,)]] * 19])
    return source, target


def test_same_count_changed_source_blocks_purge(monkeypatch, archive):
    root, _ = archive
    source, target = configure_purge(monkeypatch, archive, mutate_source=True)
    with pytest.raises(migration.MigrationError, match="full row content"):
        migration.purge_source(root, "copy", migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                               migration.digest_file(root / "manifest.json"))
    assert not source.committed
    assert not any("TRUNCATE" in query for query, _ in source.statements)
    assert "ACCESS EXCLUSIVE" in source.statements[0][0]
    assert "ACCESS EXCLUSIVE" in target.statements[0][0]


def test_purge_lock_revoke_truncate_order_and_no_cascade(monkeypatch, archive):
    root, manifest = archive
    source, target = configure_purge(monkeypatch, archive)
    assert migration.purge_source(root, "copy", migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                                  migration.digest_file(root / "manifest.json")) == manifest
    queries = [query for query, _ in source.statements]
    assert "ACCESS EXCLUSIVE" in queries[0]
    assert queries[1] == "REVOKE proof"
    assert queries[2].startswith("TRUNCATE TABLE ")
    assert queries[2].endswith("CONTINUE IDENTITY RESTRICT")
    assert "CASCADE" not in queries[2]
    assert "agent_logs" not in queries[2] and "portfolio_positions" not in queries[2]
    assert source.committed


def test_unverified_local_import_marker_blocks_purge(archive, monkeypatch):
    root, manifest = archive
    monkeypatch.setattr(migration, "database_identity", lambda *_: "target")
    target = FakeConnection([
        [("research_migration_admin.import_proof",)],
        [(manifest["export_id"], migration.digest_file(root / "manifest.json"), "source", "target", False)],
    ])
    with pytest.raises(migration.MigrationError, match="not verified"):
        migration.verify_import_proof(target, manifest, migration.digest_file(root / "manifest.json"))


def test_verified_deployment_receipt_is_private_and_not_overwritten(monkeypatch, archive, workspace):
    root, manifest = archive
    @contextmanager
    def connection(*_, **__):
        yield object()
    monkeypatch.setattr(migration, "connect", connection)
    monkeypatch.setattr(migration, "verify_import_proof", lambda *_: None)
    monkeypatch.setattr(migration, "require_distinct", lambda *_: "target")
    receipt = workspace / "import-verified.json"
    migration.write_import_receipt(root, receipt, manifest)
    content = json.loads(receipt.read_text())
    assert content["status"] == "verified"
    assert content["export_id"] == manifest["export_id"]
    assert content["target_identity"] == "target"
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        migration.write_import_receipt(root, receipt, manifest)


def test_failed_verification_never_writes_deployment_receipt(monkeypatch, archive, workspace):
    root, _ = archive
    def fail(*_):
        raise migration.MigrationError("Full comparison failed")
    monkeypatch.setattr(migration, "verify_databases", fail)
    receipt = workspace / "import-verified.json"
    assert migration.main(["verify", "--archive", str(root), "--receipt", str(receipt)]) == 1
    assert not receipt.exists()


def test_revoke_covers_column_grants_and_security_definer():
    connection = FakeConnection()
    # Each table: columns, then three role checks. Finish RPC and view checks.
    rows = [[]]
    for _ in (*migration.TABLES, migration.VIEW):
        rows += [[("id",), ("notified_at",)], [], *[[(False, False)]] * 3]
    rows += [[(False,)]] * (len(migration.FUNCTIONS) * 3 + 3)
    connection.results = iter(rows)
    migration.revoke_source_access(connection, {"sequences": ["exit_shadow_log_id_seq"]})
    queries = "\n".join(query for query, _ in connection.statements)
    assert 'REVOKE ALL ("id", "notified_at")' in queries
    assert "intraday_calibration_refresh_risk(jsonb)" in queries


def test_secret_file_is_private_and_never_overwritten(workspace):
    path = workspace / "operator.env"
    bootstrap.generate_secrets(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert len(values["RESEARCH_POSTGRES_PASSWORD"]) == 64
    assert values["RESEARCH_POSTGRES_PASSWORD"] != values["RESEARCH_API_PASSWORD"]
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        bootstrap.generate_secrets(path)
    assert path.read_bytes() == original


def test_service_jwt_signature_role_and_expiry():
    token = bootstrap.service_jwt("a" * 64, now=1000)
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims == {"role": "service_role", "iss": "research-local", "iat": 1000, "exp": 1000 + 365 * 86400}
    expected = hmac.new(b"a" * 64, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected


def test_native_tools_never_put_credentials_in_argv(monkeypatch, workspace):
    seen = {}
    monkeypatch.setattr(tools.shutil, "which", lambda _: "/usr/bin/tool")
    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b"ok")
    monkeypatch.setattr(tools.subprocess, "run", run)
    tools.run_client("pg_dump", ["--file=archive.dump"], "postgresql://u:supersecret@host/db?sslmode=require", workspace)
    assert "supersecret" not in " ".join(seen["command"])
    assert seen["env"]["PGPASSWORD"] == "supersecret"
    assert "postgresql://" not in " ".join(seen["command"])


def test_native_failure_output_cannot_leak_credentials(monkeypatch, workspace):
    monkeypatch.setattr(tools.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"supersecret"))
    with pytest.raises(migration.MigrationError) as error:
        tools.run_client("pg_dump", [], "postgresql://u:supersecret@host/db", workspace)
    assert "supersecret" not in str(error.value)


def test_docker_clients_use_host_uid_and_mac_host_bridge(monkeypatch, workspace):
    seen = {}
    monkeypatch.setattr(tools.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(tools.sys, "platform", "darwin")
    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b"ok")
    monkeypatch.setattr(tools.subprocess, "run", run)
    tools.run_client("pg_restore", ["--file=restore.sql", "research.dump"],
                     "postgresql://admin:private@localhost/db", workspace, docker=True)
    assert "--user" in seen["command"]
    assert "--network" not in seen["command"]
    assert seen["env"]["PGHOST"] == "host.docker.internal"
    assert "private" not in " ".join(seen["command"])
    assert "PGPASSWORD" in seen["command"]


def test_bootstrap_refuses_existing_privileged_api_role():
    connection = FakeConnection([[], [(True, False, False, False)]])
    with pytest.raises(migration.MigrationError, match="administrative"):
        bootstrap.bootstrap_roles(connection, "x" * 32)


def test_export_failure_never_writes_complete_marker(monkeypatch, workspace):
    monkeypatch.setattr(migration, "inventory", lambda *_: {"functions": {}, "sequences": []})
    monkeypatch.setattr(migration, "database_identity", lambda *_: "source")
    # Use a minimal fake with the exported snapshot and view return values.
    fake = FakeConnection([[], [("snapshot-id",)], [("view-definition",)]])
    fake.info = SimpleNamespace(server_version=170000)
    @contextmanager
    def connection(*_, **__):
        yield fake
    monkeypatch.setattr(migration, "connect", connection)
    monkeypatch.setattr(migration, "scan_all", lambda *_: {})
    monkeypatch.setattr(migration, "get_dsn", lambda *_: "dsn")
    def fail(*_, **__):
        raise migration.MigrationError("pg_dump failed")
    monkeypatch.setattr(migration, "run_client", fail)
    with pytest.raises(migration.MigrationError, match="pg_dump"):
        migration.export_snapshot(workspace / "failed")
    assert not (workspace / "failed" / "COMPLETE").exists()


def test_import_refuses_existing_tables_without_running_native_tools(monkeypatch, archive, workspace):
    root, _ = archive
    @contextmanager
    def connection(source, **_):
        assert source is False, "Archive recovery must not require the original source server"
        yield FakeConnection([] if source else [[(1,)]])
    monkeypatch.setattr(migration, "connect", connection)
    monkeypatch.setattr(migration, "database_identity", lambda *_: "source")
    monkeypatch.setattr(migration, "require_distinct", lambda *_: "target")
    monkeypatch.setattr(migration, "run_client", lambda *_: pytest.fail("must not restore"))
    with pytest.raises(migration.MigrationError, match="fresh local public schema"):
        migration.import_archive(root, workspace / "restore", migration.IMPORT_CONFIRMATION, True)


def test_bootstrap_token_renews_without_rotating_database_secrets(monkeypatch, workspace):
    monkeypatch.setenv("RESEARCH_JWT_SECRET", "existing-signing-secret-" + "x" * 32)
    output = workspace / "renewal.env"
    assert bootstrap.main(["token", "--output", str(output)]) == 0
    assert output.read_text().startswith("RESEARCH_DATABASE_KEY=")
    assert "RESEARCH_POSTGRES_PASSWORD" not in output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
