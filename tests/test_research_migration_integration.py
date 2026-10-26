"""Opt-in native PostgreSQL test; NEVER uses the normal operator DSN variables.

Requires two empty disposable TLS-enabled databases whose names begin with
research_migration_test_, with PostgreSQL admin access and native v17 clients
(or RESEARCH_MIGRATION_TEST_DOCKER_TOOLS=1). Set:
  RESEARCH_MIGRATION_INTEGRATION=ALLOW_DISPOSABLE_TEST_CLUSTER
  RESEARCH_MIGRATION_TEST_SOURCE_PG_DSN
  RESEARCH_MIGRATION_TEST_LOCAL_PG_DSN
Set RESEARCH_MIGRATION_TEST_POSTGREST=1 to additionally run a disposable
PostgREST container and verify signed-JWT reads, permissions and successful RPCs.
The roles in this cluster must be disposable too. Test-created tables are removed.
"""

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

from scripts import research_bootstrap as bootstrap
from scripts import research_migration as migration
from scripts import research_pg_tools as pg_tools


pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_MIGRATION_INTEGRATION") != "ALLOW_DISPOSABLE_TEST_CLUSTER",
    reason="Opt-in disposable PostgreSQL integration environment not supplied.",
)


def assert_postgrest_roundtrip(dsn, workspace, docker_tools):
    """Optional real HTTP/role/RPC exercise against the disposable imported DB."""
    from postgrest import SyncPostgrestClient

    info = conninfo_to_dict(dsn)
    password = "disposable-test-password-" + "x" * 32
    host = info["host"]
    if sys.platform == "darwin" and host in ("localhost", "127.0.0.1", "::1"):
        host = "host.docker.internal"
    env = os.environ.copy()
    env.update({
        "PGRST_DB_URI": f"postgresql://authenticator:{quote(password)}@{host}:{info.get('port','5432')}/{info['dbname']}?sslmode=require",
        "PGRST_JWT_SECRET": secrets.token_hex(32),
        "PGRST_DB_ANON_ROLE": "anon", "PGRST_DB_SCHEMAS": "public",
    })
    name = "research-migration-api-test-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "-d", "--name", name]
    if sys.platform == "darwin":
        command += ["--publish", "127.0.0.1::3000"]
        port = None
    else:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env["PGRST_SERVER_PORT"] = str(port)
        env["PGRST_SERVER_HOST"] = "127.0.0.1"
        command += ["--network", "host"]
    for key in env:
        if key.startswith("PGRST_"):
            command += ["--env", key]
    command += ["postgrest/postgrest:v12.2.12"]
    started = False
    try:
        subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL)
        started = True
        if port is None:
            port = subprocess.check_output([
                "docker", "inspect", "--format",
                '{{(index (index .NetworkSettings.Ports "3000/tcp") 0).HostPort}}', name,
            ], text=True).strip()
        base = f"http://127.0.0.1:{port}"
        token = bootstrap.service_jwt(env["PGRST_JWT_SECRET"])
        def request(path, payload=None, authenticated=True, extra_headers=None):
            headers = {"Content-Type": "application/json"}
            if authenticated:
                headers["Authorization"] = "Bearer " + token
            headers.update(extra_headers or {})
            data = None if payload is None else json.dumps(payload).encode()
            try:
                with urlopen(Request(base + path, data=data, headers=headers), timeout=5) as response:
                    content = response.read()
                    return json.loads(content) if content else None
            except HTTPError as error:
                if authenticated:
                    print("Disposable PostgREST response:", error.read().decode())
                raise
        for attempt in range(60):
            try:
                settings = request("/intraday_calibration_settings?id=eq.default&select=id,value")
                assert settings[0]["id"] == "default"
                break
            except (URLError, HTTPError):
                if attempt == 59:
                    raise
                time.sleep(0.5)
        with pytest.raises(HTTPError) as denied:
            request("/intraday_calibration_settings?select=*", authenticated=False,
                    extra_headers={"role": "service_role"})
        assert denied.value.code in (401, 403)
        with SyncPostgrestClient(base, headers={"Authorization": "Bearer " + token}) as client:
            settings = client.table("intraday_calibration_settings").select("id").eq("id", "default").execute()
            assert settings.data == [{"id": "default"}]
            claimed = client.rpc("intraday_calibration_mutate", {
                "p_operation": "claim", "p_payload": {"owner": "typed-client", "seconds": 60},
            }).execute()
            assert claimed.data is True
            released = client.rpc("intraday_calibration_mutate", {
                "p_operation": "release", "p_payload": {"owner": "typed-client"},
            }).execute()
            assert released.data is True
        assert request("/rpc/claim_intraday_reporting", {"worker": "migration-test"}) is True
        request("/rpc/release_intraday_reporting", {"worker": "migration-test"})
        assert request("/rpc/intraday_calibration_mutate", {
            "p_operation": "claim", "p_payload": {"owner": "migration-test", "seconds": 60},
        }) is True
        assert request("/rpc/intraday_calibration_mutate", {
            "p_operation": "release", "p_payload": {"owner": "migration-test"},
        }) is True
        report = {
            "version": 1, "phase": "training", "status": "partial",
            "analytics_fingerprint": {}, "reference_window": ["2026-10-25", "2026-10-26"],
            "scope": "disposable-test", "selected_name": "baseline",
            "calculation_inputs": {"data": {"test": True}, "baseline": {}, "candidate": None},
            "reference_snapshot": {"status": "unavailable"},
            "baseline": {"metrics": {}, "unavailable": {}, "reference": {},
                         "daily_equity": [], "daily_returns": []},
            "candidate": {},
            **{key: "a" * 64 for key in ("selection_sha256", "benchmark_sha256", "input_sha256", "sha256")},
        }
        artifact = {"risk_analytics": {"training": report, "retry_pending": True}}
        old_hash = hashlib.sha256(migration.canonical(artifact)).hexdigest()
        proposal = request("/rpc/intraday_calibration_mutate", {
            "p_operation": "create", "p_payload": {"row": {
                "id": "http-risk-test", "kind": "parameter", "title": "Disposable HTTP test",
                "status": "ready", "artifact": artifact, "artifact_sha256": old_hash, "request": {},
            }},
        })
        assert proposal["revision"] == 0
        updated = json.loads(json.dumps(artifact))
        updated["risk_analytics"]["retry_pending"] = False
        updated["risk_analytics"]["training"].update({
            "status": "available", "sha256": "b" * 64,
            "reference_snapshot": {"status": "available"},
        })
        new_hash = hashlib.sha256(migration.canonical(updated)).hexdigest()
        refreshed = request("/rpc/intraday_calibration_refresh_risk", {"p_payload": {
            "id": "http-risk-test", "expected_revision": 0,
            "expected_artifact_sha256": old_hash, "artifact_sha256": new_hash,
            "risk_analytics": updated["risk_analytics"], "event_id": "http-risk-refresh",
        }})
        assert refreshed["revision"] == 1
        assert refreshed["artifact"]["risk_analytics"]["retry_pending"] is False
        with pytest.raises(HTTPError) as denied:
            request("/rpc/intraday_calibration_refresh_risk", {"p_payload": {}}, authenticated=False)
        assert denied.value.code in (401, 403, 404)
        proof = pg_tools.run_client("psql", [
            "-X", "-v", "ON_ERROR_STOP=1", "-Atc",
            "SELECT verified AND source_identity<>target_identity "
            "FROM research_migration_admin.import_proof WHERE singleton",
        ], dsn, workspace, docker_tools)
        assert proof.strip() == b"t"
    finally:
        if started:
            subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)


def test_native_snapshot_roundtrip_and_locked_cleanup(monkeypatch):
    dsns = [
        os.environ["RESEARCH_MIGRATION_TEST_SOURCE_PG_DSN"],
        os.environ["RESEARCH_MIGRATION_TEST_LOCAL_PG_DSN"],
    ]
    for dsn in dsns:
        assert conninfo_to_dict(dsn)["dbname"].startswith("research_migration_test_")
    assert dsns[0] != dsns[1]
    for dsn in dsns:
        with psycopg.connect(dsn) as conn:
            assert not conn.execute("""
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%%' AND c.relkind IN ('r','p','v','m','S') LIMIT 1
            """).fetchone(), "Integration databases must initially be empty"
    monkeypatch.setenv("SOURCE_RESEARCH_PG_DSN", dsns[0])
    monkeypatch.setenv("LOCAL_RESEARCH_PG_DSN", dsns[1])
    original_run = pg_tools.subprocess.run
    def diagnostic_run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        if result.returncode and isinstance(result.stderr, bytes):
            detail = result.stderr.decode(errors="replace")
            for dsn in dsns:
                detail = detail.replace(dsn, "[test database]")
                password = conninfo_to_dict(dsn).get("password", "")
                if password:
                    detail = detail.replace(password, "[redacted]")
            print(detail)
        return result
    monkeypatch.setattr(pg_tools.subprocess, "run", diagnostic_run)
    docker = os.environ.get("RESEARCH_MIGRATION_TEST_DOCKER_TOOLS") == "1"
    workspace = Path.cwd() / (".research-migration-integration-" + uuid.uuid4().hex)
    workspace.mkdir(mode=0o700)
    source_initialized = False
    try:
        for dsn in dsns:
            with psycopg.connect(dsn) as conn:
                bootstrap.bootstrap_roles(conn, "disposable-test-password-" + "x" * 32)
        with psycopg.connect(dsns[0]) as conn:
            for name in (
                "20260927_add_exit_shadow_log.sql", "20260930_add_intraday_research.sql",
                "20260930_add_intraday_shadow.sql", "20260930_add_intraday_reporting.sql",
                "20261003_add_calibration_loop.sql", "20261003_refresh_calibration_risk_diagnostics.sql",
            ):
                conn.execute((Path("migrations") / name).read_text())
            conn.execute("CREATE TABLE portfolio_positions (id integer PRIMARY KEY, ticker text)")
            conn.execute("INSERT INTO portfolio_positions VALUES (1, 'LIVE_SENTINEL')")
            conn.execute("""
                INSERT INTO exit_shadow_log (cycle_ts,ticker,price)
                VALUES ('2026-10-26 14:00:00+00','PRECISE',12345678901234567890.1234567890123456789)
            """)
            conn.execute("""
                INSERT INTO intraday_capture_events(id,run_id,sequence,session,occurred_at,kind,payload)
                VALUES ('sample','run',1,'2026-10-26','2026-10-26 10:00:00.123456-04','quote_sample',
                        '{"complete":true,"nullable":null,"precise":12345678901234567890.123456789,"unicode":"東京"}')
            """)
            conn.execute("""
                INSERT INTO intraday_shadow_runs(id,status,initial_state,effective_config,engine_revision)
                VALUES ('run','completed','{}','{}','test')
            """)
            conn.execute("""
                INSERT INTO intraday_shadow_checkpoints(run_id,sequence,state) VALUES ('run',1,'{"x":null}')
            """)
            conn.execute("""
                INSERT INTO intraday_capture_health(id,last_seen_at,config)
                VALUES ('null-test',NULL,'null'::jsonb)
            """)
            conn.execute("""
                INSERT INTO intraday_replay_runs(id,start_date,end_date,status,request,summary,result)
                VALUES ('00000000-0000-0000-0000-000000000001','2026-10-26','2026-10-26',
                        'completed','{}',NULL,'null'::jsonb)
            """)
            source_initialized = True
        with psycopg.connect(dsns[0]) as conn:
            with pytest.raises(migration.MigrationError, match="SAME"):
                migration.require_distinct(migration.database_identity(conn), conn)
            conn.execute("CREATE TABLE intraday_unexpected (id integer PRIMARY KEY)")
        with pytest.raises(migration.MigrationError, match="inventory"):
            migration.prepare()
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("DROP TABLE intraday_unexpected")
            conn.execute("ALTER TABLE portfolio_positions ADD COLUMN research_ref text REFERENCES intraday_shadow_runs(id)")
        with pytest.raises(migration.MigrationError, match="foreign key"):
            migration.prepare()
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("ALTER TABLE portfolio_positions DROP COLUMN research_ref")
        migration.prepare()
        archive = workspace / "archive"
        native_client = migration.run_client
        def concurrent_writer(tool, *args, **kwargs):
            if tool == "pg_dump":
                with psycopg.connect(dsns[0]) as writer:
                    writer.execute("UPDATE exit_shadow_log SET ticker='DURING_SNAPSHOT'")
            return native_client(tool, *args, **kwargs)
        monkeypatch.setattr(migration, "run_client", concurrent_writer)
        manifest = migration.export_snapshot(archive, docker)
        assert manifest["tables"]["exit_shadow_log"]["rows"] == 1
        replay_record = json.loads((archive / "intraday_replay_runs.jsonl").read_text())
        assert "summary" in replay_record["sql_null_json_columns"]
        assert "result" not in replay_record["sql_null_json_columns"]
        assert migration.verify_archive(archive) == manifest
        migration.import_archive(archive, workspace / "restore", migration.IMPORT_CONFIRMATION, True, docker)
        # Native dump and review JSONL must share the pre-update snapshot even
        # though this writer committed between the JSONL scan and pg_dump.
        with psycopg.connect(dsns[1]) as conn:
            assert conn.execute("SELECT ticker FROM exit_shadow_log").fetchone()[0] == "PRECISE"
        with pytest.raises(migration.MigrationError, match="full row content"):
            migration.verify_databases(archive)
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("UPDATE exit_shadow_log SET ticker='PRECISE'")
        migration.verify_databases(archive)
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("UPDATE intraday_replay_runs SET summary='null'::jsonb")
        with pytest.raises(migration.MigrationError, match="full row content"):
            migration.verify_databases(archive)
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("UPDATE intraday_replay_runs SET summary=NULL")
        receipt = workspace / "import-verified.json"
        assert migration.main(["verify", "--archive", str(archive), "--receipt", str(receipt)]) == 0
        assert receipt.is_file()
        with monkeypatch.context() as offline:
            offline.delenv("SOURCE_RESEARCH_PG_DSN")
            migration.verify_target(archive)
            offline.setattr(migration, "run_client", native_client)
            local_manifest = migration.export_snapshot(workspace / "local-recurring", docker, database="local")
            assert local_manifest["origin"] == "local"
            assert migration.verify_archive(workspace / "local-recurring") == local_manifest
        with psycopg.connect(dsns[1]) as conn:
            assert conn.execute("SELECT price FROM exit_shadow_log").fetchone()[0] == Decimal("12345678901234567890.1234567890123456789")
            assert conn.execute("SELECT last_value FROM exit_shadow_log_id_seq").fetchone()[0] >= 1
            assert conn.execute("SELECT count(*) FROM intraday_shadow_checkpoints").fetchone()[0] == 1
            assert conn.execute("SELECT config IS NULL,jsonb_typeof(config),last_seen_at IS NULL FROM intraday_capture_health WHERE id='null-test'").fetchone() == (False, "null", True)
            assert conn.execute("SELECT summary IS NULL,result IS NULL,jsonb_typeof(result) FROM intraday_replay_runs").fetchone() == (True, False, "null")
            assert conn.execute("SELECT occurred_at FROM intraday_capture_events WHERE id='sample'").fetchone()[0].isoformat() == "2026-10-26T14:00:00.123456+00:00"
            assert conn.execute("SELECT has_function_privilege('anon','intraday_calibration_refresh_risk(jsonb)','EXECUTE')").fetchone()[0] is False
            assert conn.execute("SELECT has_function_privilege('service_role','intraday_calibration_refresh_risk(jsonb)','EXECUTE')").fetchone()[0] is True
        # Physical-device independence is tested separately; this native test is
        # deliberately runnable on one disposable filesystem.
        second = workspace / "second"
        shutil.copytree(archive, second)
        def verified_copy(first, other):
            first_manifest = migration.verify_archive(first)
            assert migration.verify_archive(other) == first_manifest
            return first_manifest
        monkeypatch.setattr(migration, "verify_second_copy", verified_copy)
        with psycopg.connect(dsns[0]) as conn:
            conn.execute("UPDATE exit_shadow_log SET ticker='CHANGED'")
        with pytest.raises(migration.MigrationError, match="full row content"):
            migration.purge_source(archive, second, migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                                   migration.digest_file(archive / "manifest.json"))
        with psycopg.connect(dsns[0]) as conn:
            assert conn.execute("SELECT count(*) FROM exit_shadow_log").fetchone()[0] == 1
            conn.execute("UPDATE exit_shadow_log SET ticker='PRECISE'")
        migration.purge_source(archive, second, migration.PURGE_CONFIRMATION, migration.CUTOVER_CONFIRMATION,
                               migration.digest_file(archive / "manifest.json"))
        with psycopg.connect(dsns[0]) as conn:
            for table in migration.TABLES:
                assert conn.execute(f'SELECT count(*) FROM public."{table}"').fetchone()[0] == 0
            assert conn.execute("SELECT ticker FROM portfolio_positions").fetchone()[0] == "LIVE_SENTINEL"
            for role in ("anon", "authenticated", "service_role"):
                assert conn.execute("SELECT has_function_privilege(%s,'intraday_calibration_refresh_risk(jsonb)','EXECUTE')", (role,)).fetchone()[0] is False
                assert conn.execute("SELECT has_any_column_privilege(%s,'intraday_calibration_events','UPDATE')", (role,)).fetchone()[0] is False
                assert conn.execute("SELECT has_table_privilege(%s,'intraday_capture_sessions','SELECT')", (role,)).fetchone()[0] is False
            assert conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_proc p,
                    LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                    WHERE p.oid='intraday_calibration_refresh_risk(jsonb)'::regprocedure
                    AND a.grantee=0 AND a.privilege_type='EXECUTE'
                )
            """).fetchone()[0] is False
        if os.environ.get("RESEARCH_MIGRATION_TEST_POSTGREST") == "1":
            assert_postgrest_roundtrip(dsns[1], workspace, docker)
    finally:
        # Only these explicitly empty/disposable test databases are eligible.
        if source_initialized:
            for dsn in dsns:
                with psycopg.connect(dsn) as conn:
                    conn.execute("DROP SCHEMA IF EXISTS research_migration_admin CASCADE")
                    conn.execute("DROP SCHEMA public CASCADE")
                    conn.execute("CREATE SCHEMA public")
        shutil.rmtree(workspace)
