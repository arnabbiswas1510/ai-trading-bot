"""Offline deployment contract; Docker is a recording executable, never a daemon."""
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy_runtime.sh"
WORKFLOW = ROOT / ".github/workflows/deploy_to_server.yml"


@pytest.fixture
def deployment():
    work = ROOT / f".deploy-test-{uuid.uuid4().hex}"
    work.mkdir()
    log = work / "docker.jsonl"
    docker = work / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_LOG'], 'a') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "if os.environ.get('FAIL_DOCKER_ACTION') in args:\n"
        "    sys.exit(42)\n"
        "if args[:2] == ['container', 'ls'] and os.environ['EXISTING_AGENT'] != 'missing':\n"
        "    print('container-id')\n"
        "if args[:2] == ['compose', 'config']:\n"
        "    assert os.environ.get('COMPOSE_PROFILES') == 'local-research'\n"
        "    print(os.environ.get('TEST_COMPOSE_CONFIG', '{}'))\n"
        "if args[:3] == ['compose', 'exec', '-T']:\n"
        "    print(os.environ.get('TEST_IMPORT_PROOF', '{}'))\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    print(os.environ['IMAGE_GATE'])\n"
        "elif args[:2] == ['inspect', 'execution-agent'] and 'live-entry-gate' in args[-1]:\n"
        "    print('1' if os.environ['EXISTING_AGENT'] == 'guarded' else '<no value>')\n"
    )
    docker.chmod(0o755)
    # Neither this file nor arbitrary shell embedded in it may be executed.
    (work / ".env").write_text("touch secret-env-was-executed\nTRADING_RUNTIME_MODE=live\n")

    (work / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (work / "sleep").chmod(0o755)
    (work / "scripts").mkdir()
    shutil.copy(HELPER, work / "scripts/deploy_runtime.sh")

    def run(mode=None, fail=None, args=(), existing="guarded", image_gate="1", morning=False,
            research=None, receipt=False, local_config=None, dotenv_backend=None):
        env = os.environ.copy()
        env.pop("TRADING_RUNTIME_MODE", None)
        env.pop("FAIL_DOCKER_ACTION", None)
        env.pop("RESEARCH_STORAGE_BACKEND", None)
        env.update(PATH=f"{work}{os.pathsep}{env['PATH']}", DOCKER_LOG=str(log),
                   EXISTING_AGENT=existing, IMAGE_GATE=image_gate, PROJECT_DIR=str(work))
        if mode is not None:
            env["TRADING_RUNTIME_MODE"] = mode
        if fail:
            env["FAIL_DOCKER_ACTION"] = fail
        if research is not None:
            env["RESEARCH_STORAGE_BACKEND"] = research
        if receipt:
            identity = ["123456", "16384", "research"]
            target = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            env["TEST_IMPORT_PROOF"] = json.dumps({
                "identity": identity,
                "proof": {"verified": True, "target_identity": target,
                          "source_identity": "source", "manifest_sha256": "f" * 64},
            })
        env["TEST_COMPOSE_CONFIG"] = json.dumps(local_config if local_config is not None else {
            "services": {name: {"environment": {}} for name in [*SERVICES, "research-reporting"]},
        })
        if dotenv_backend is not None:
            with (work / ".env").open("a") as out:
                out.write(f"RESEARCH_STORAGE_BACKEND={dotenv_backend}\n")
        result = subprocess.run(
            ["sh", str(ROOT / "scripts/restart_6am.sh" if morning else HELPER), *args], cwd=work, env=env,
            text=True, capture_output=True, check=False,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        assert not (work / "secret-env-was-executed").exists()
        return result, calls

    try:
        yield run
    finally:
        shutil.rmtree(work)


SERVICES = ["execution-agent", "intraday-observer", "shadow-worker", "calibration-worker", "trading-bot"]
GATEWAY = ["compose", "up", "-d", "--no-deps", "--no-recreate", "ib-gateway"]
CONFIG = ["compose", "config", "--format", "json"]


@pytest.mark.parametrize("mode", [None, "", "observe", "live"])
def test_all_modes_keep_protection_and_research_running(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call[0] == "compose"] == [
        CONFIG,
        ["compose", "pull", *SERVICES],
        GATEWAY,
        ["compose", "up", "-d", "--no-deps", "--pull", "never", *SERVICES],
    ]
    assert "dashboard controls new live entries" in result.stdout


@pytest.mark.parametrize("mode", ["paper", "LIVE", " observe", "live; touch injected"])
def test_invalid_mode_does_not_touch_docker(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 2
    assert "no containers changed" in result.stderr
    assert calls == []


def test_unexpected_argument_does_not_silently_select_a_mode(deployment):
    result, calls = deployment(args=("live",))
    assert result.returncode == 2
    assert calls == []


def test_failed_image_pull_preserves_existing_protective_agent(deployment):
    result, calls = deployment(fail="pull")
    assert result.returncode == 42
    assert calls[-1] == ["compose", "pull", *SERVICES]
    assert not any("stop" in call or "up" in call for call in calls)


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_first_transition_stops_ungated_agent_before_pull(deployment, mode):
    result, calls = deployment(mode, existing="legacy", fail="pull")
    assert result.returncode == 42
    assert calls[-2:] == [["compose", "stop", "execution-agent"], ["compose", "pull", *SERVICES]]


def test_legacy_stop_failure_prevents_pulling_or_starting_anything(deployment):
    result, calls = deployment(fail="stop", existing="legacy")
    assert result.returncode == 42
    assert calls[-1] == ["compose", "stop", "execution-agent"]
    assert not any("pull" in call or "up" in call for call in calls)


def test_gateway_failure_does_not_start_execution_agent(deployment):
    result, calls = deployment(fail="up")
    assert result.returncode == 42
    assert calls[-1] == GATEWAY
    assert not any("up" in call and "execution-agent" in call for call in calls)


def test_fresh_install_does_not_need_existing_agent(deployment):
    result, calls = deployment(existing="missing")
    assert result.returncode == 0
    assert not any("stop" in call for call in calls)
    assert ["compose", "up", "-d", "--no-deps", "--pull", "never", *SERVICES] in calls


@pytest.mark.parametrize("args", [(), ("--restart",)])
def test_legacy_image_cannot_be_started_even_on_restart(deployment, args):
    result, calls = deployment(args=args, image_gate="")
    assert result.returncode == 1
    assert "lacks the live-entry gate" in result.stderr
    assert not any("up" in call or "restart" in call for call in calls)


def test_daemon_inspection_failure_never_assumes_agent_absent(deployment):
    result, calls = deployment(fail="ls")
    assert result.returncode == 42
    assert len(calls) == 2 and calls[0] == CONFIG


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_morning_restart_uses_shared_gate_and_local_images_without_control_writes(deployment, mode):
    result, calls = deployment(mode, morning=True)
    assert result.returncode == 0
    assert ["compose", "restart", "ib-gateway"] in calls
    assert ["compose", "up", "-d", "--no-deps", "--pull", "never", "--force-recreate", *SERVICES] in calls
    assert not any(call[:2] == ["compose", "pull"] or call[0] == "cp" for call in calls)
    assert calls[-1] == ["exec", "execution-agent", "python3", "/app/restart_and_health_check.py"]
    assert not any("trading_control" in " ".join(call) for call in calls)


@pytest.mark.parametrize("failure", ["up", "exec"])
def test_morning_restart_propagates_lifecycle_and_health_failures(deployment, failure):
    result, calls = deployment(morning=True, fail=failure)
    assert result.returncode == 42
    if failure == "up":
        assert not any(call[0] == "exec" for call in calls)


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_runtime_deploy_cannot_execute_order_operations_or_restart_gateway(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 0
    for call in calls:
        if call[0] in {"inspect", "container", "image"}:
            continue
        assert call[0] == "compose"
        operation = call[1]
        assert operation in {"config", "stop", "pull", "up"}
        if operation == "up":
            assert "--no-deps" in call
        if "ib-gateway" in call:
            assert call == GATEWAY


def test_workflow_ships_helper_and_passes_operator_mode_as_environment():
    text = WORKFLOW.read_text()
    source = re.search(r'^\s+source: "([^"]+)"', text, re.MULTILINE).group(1).split(",")
    assert "scripts/deploy_runtime.sh" in source
    assert "scripts/restart_6am.sh" in source
    assert "docker-compose.yml" in source
    assert "TRADING_RUNTIME_MODE: ${{ vars.TRADING_RUNTIME_MODE || 'observe' }}" in text
    assert "envs: TRADING_RUNTIME_MODE,EXPECTED" in text
    script = text.split("script: |", 1)[1]
    assert script.lstrip().startswith("set -eu\n")
    assert "sh scripts/deploy_runtime.sh" in script
    assert "${{" not in script
    assert "exit 0" not in script
    assert "/api/version" in script
    assert '[ "$SERVED" != "$EXPECTED" ]' in script
    commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"^\s*(?:sh\s+)?scripts/render_env", commands, re.MULTILINE)
    assert not re.search(r"^\s*(?:source|\.)\s+.*\.env", commands, re.MULTILINE)
    assert "docker compose" not in script


def service_block(name):
    text = (ROOT / "docker-compose.yml").read_text()
    return re.search(
        rf"^  {name}:\n(.*?)(?=^  [a-z][\w-]*:|^networks:|\Z)", text, re.MULTILINE | re.DOTALL,
    ).group(1)


def test_compose_runs_protection_and_research_independent_of_runtime_label():
    for name in SERVICES:
        assert "profiles:" not in service_block(name)
    dashboard = service_block("trading-bot")
    assert "depends_on:" not in dashboard


def test_gateway_browser_port_targets_pinned_image_listener():
    gateway = service_block("ib-gateway")
    assert '"15800:5800"' in gateway
    assert '"15800:15800"' not in gateway
    assert '"4002:4000"' in gateway


def test_only_dashboard_and_execution_share_persistent_fixed_control_path():
    for name in ("execution-agent", "trading-bot"):
        block = service_block(name)
        assert "trading-control:/app/control" in block
        assert "TRADING_CONTROL_PATH=/app/control/trading-control.sqlite3" in block
    for name in ("ib-gateway", "intraday-observer", "shadow-worker"):
        block = service_block(name)
        assert "trading-control:" not in block
        assert "TRADING_CONTROL_PATH" not in block
    assert "  trading-control:\n    driver: local" in (ROOT / "docker-compose.yml").read_text()


def test_capture_uses_host_settings_with_distinct_default_spools_and_client_ids():
    assert "INTRADAY_CAPTURE_ENABLED" not in service_block("execution-agent")
    observer = service_block("intraday-observer")
    assert "INTRADAY_CAPTURE_ENABLED" not in observer
    assert 'command: ["python", "research_entrypoint.py", "intraday-observer"]' in observer
    source = (ROOT / "intraday_observer.py").read_text()
    assert 'result.add_argument("--client-id", type=int, default=71)' in source
    assert 'DEFAULT_SPOOL = "/app/logs/intraday_observer.sqlite3"' in source
    config = (ROOT / "config.py").read_text()
    assert '"INTRADAY_CAPTURE_SPOOL", "/app/logs/intraday_capture.sqlite3"' in config
    assert "Path(args.spool).resolve() == Path(INTRADAY_CAPTURE_SPOOL).resolve()" in source


def test_execution_image_contains_control_and_healthcheck_dependencies():
    image = (ROOT / "Dockerfile.agent").read_text()
    assert 'LABEL io.ai-trading-bot.live-entry-gate="1"' in image
    assert "COPY trading_control.py restart_and_health_check.py ./" in image
    assert "ib-insync==" in image and "requests==" in image
    assert "python:3.12" in image


def test_shadow_worker_has_no_gateway_network_or_dependency():
    text = (ROOT / "docker-compose.yml").read_text()
    block = text.split("  shadow-worker:", 1)[1].split("  trading-bot:", 1)[0]
    assert "research_bridge" in block
    assert "trading_bridge" not in block
    assert "depends_on:" not in block
    assert "IB_GATEWAY" not in block
    assert "shadow-data:/app/shadow" in block


def local_configuration():
    env = {
        "RESEARCH_STORAGE_BACKEND": "local",
        "RESEARCH_DATABASE_URL": "http://research-api:3000",
        "RESEARCH_DATABASE_KEY": "test-jwt",
        "GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo",
        "TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_IDS": "123",
    }
    services = {name: {"environment": dict(env)} for name in [*SERVICES, "research-reporting"]}
    services["research-db"] = {"environment": {"POSTGRES_PASSWORD": "p" * 32}}
    services["research-api"] = {"environment": {
        "PGRST_JWT_SECRET": "s" * 32,
        "PGRST_DB_URI": "postgres://authenticator:" + "a" * 32 + "@research-db:5432/research",
    }}
    return {"services": services}


def test_local_backend_requires_verified_import_before_replacing_consumers(deployment):
    result, calls = deployment(research="local", local_config=local_configuration())
    assert result.returncode == 1
    assert "verified import proof missing" in result.stderr
    assert not any("up" in call and "execution-agent" in call for call in calls)
    assert not any("up" in call and "research-reporting" in call for call in calls)


def test_local_backend_validates_credentials_before_changing_runtime(deployment):
    config = local_configuration()
    config["services"]["trading-bot"]["environment"]["RESEARCH_DATABASE_KEY"] = ""
    result, calls = deployment(research="local", receipt=True, local_config=config)
    assert result.returncode != 0
    assert "credentials are incomplete" in result.stderr
    assert calls == [["compose", "config", "--format", "json"]]


def test_local_backend_starts_database_checks_restore_then_starts_consumers(deployment):
    result, calls = deployment(research="local", receipt=True, local_config=local_configuration())
    assert result.returncode == 0, result.stderr
    start = ["compose", "up", "-d", "--no-deps", "--pull", "never", *SERVICES, "research-reporting"]
    db = ["compose", "up", "-d", "--no-deps", "--pull", "never", "--wait",
          "--wait-timeout", "120", "research-db"]
    assert calls.index(db) < calls.index(start)
    probe = next(call for call in calls if call[:2] == ["compose", "run"])
    assert calls.index(probe) < calls.index(start)
    assert "intraday_calibration_settings" in probe[-1]
    assert not any("stop" in call for call in calls)


def test_local_restore_probe_failure_does_not_replace_consumers(deployment):
    result, calls = deployment(research="local", receipt=True,
                               local_config=local_configuration(), fail="run")
    assert result.returncode == 42
    assert not any("up" in call and "execution-agent" in call for call in calls)


@pytest.mark.parametrize("kind", ["transport", "api", "configuration", "unexpected"])
def test_readiness_retries_only_expected_errors(monkeypatch, kind):
    import httpx
    import research_storage
    import time
    from postgrest.exceptions import APIError
    from unittest.mock import Mock

    errors = {
        "transport": httpx.ConnectError("private transport detail"),
        "api": APIError({"code": "PGRST205", "message": "private response",
                         "details": "", "hint": ""}),
        "configuration": ValueError("private configuration"),
        "unexpected": RuntimeError("unexpected programming failure"),
    }
    factory = Mock(side_effect=errors[kind])
    sleep = Mock()
    monkeypatch.setattr(research_storage, "research_client", factory)
    monkeypatch.setattr(time, "sleep", sleep)
    code = re.search(
        r"docker compose run --rm --no-deps --entrypoint python research-reporting -c '\n(.*?)\n'",
        HELPER.read_text(), re.DOTALL,
    ).group(1)
    if kind == "unexpected":
        with pytest.raises(RuntimeError, match="unexpected programming failure"):
            exec(code, {})
        assert factory.call_count == 1
        sleep.assert_not_called()
    else:
        with pytest.raises(SystemExit, match="restored research settings are unavailable"):
            exec(code, {})
        assert factory.call_count == 6
        assert sleep.call_count == 5


def test_local_storage_and_reporter_are_opt_in_without_exposed_database_ports():
    for name in ("research-db", "research-api", "research-reporting"):
        block = service_block(name)
        assert 'profiles: ["local-research"]' in block
        assert "ports:" not in block
    assert "research_database" in service_block("research-db")
    assert "research_bridge" not in service_block("research-db")
    assert "postgres://authenticator:" in service_block("research-api")
    assert "PGRST_DB_ANON_ROLE: anon" in service_block("research-api")
    assert "RESEARCH_API_PASSWORD" in service_block("research-api")
    assert "${RESEARCH_DATA_DIR:-/home/pom/docker/ai-trading-bot/research-data}/postgres" in service_block("research-db")
    for name in ("execution-agent", "intraday-observer", "trading-bot"):
        assert "research_bridge" in service_block(name)


@pytest.mark.parametrize(("shell", "effective"), [("supabase", "local"), ("local", "supabase")])
def test_shell_cannot_bypass_effective_backend_readiness(deployment, shell, effective):
    config = local_configuration()
    for name in [*SERVICES, "research-reporting"]:
        config["services"][name]["environment"]["RESEARCH_STORAGE_BACKEND"] = effective
    result, calls = deployment(research=shell, local_config=config)
    assert result.returncode != 0
    assert "shell and effective Compose" in result.stderr
    assert calls == [CONFIG]


@pytest.mark.parametrize("invalid", ["", "LOCAL", "other"])
def test_invalid_effective_backend_never_changes_containers(deployment, invalid):
    config = local_configuration()
    for name in [*SERVICES, "research-reporting"]:
        config["services"][name]["environment"]["RESEARCH_STORAGE_BACKEND"] = invalid
    result, calls = deployment(local_config=config)
    assert result.returncode != 0
    assert "effective research consumer backends disagree or are invalid" in result.stderr
    assert calls == [CONFIG]


@pytest.mark.parametrize("service", [*SERVICES, "research-reporting"])
def test_one_consumer_cannot_choose_a_different_backend(deployment, service):
    config = local_configuration()
    config["services"][service]["environment"]["RESEARCH_STORAGE_BACKEND"] = "supabase"
    result, calls = deployment(local_config=config)
    assert result.returncode != 0
    assert "effective research consumer backends disagree" in result.stderr
    assert calls == [CONFIG]


@pytest.mark.parametrize(("declared", "effective"), [("supabase", "local"), ("local", "supabase")])
def test_strategy_overlay_cannot_silently_override_operator_backend(deployment, declared, effective):
    config = local_configuration()
    for name in [*SERVICES, "research-reporting"]:
        config["services"][name]["environment"]["RESEARCH_STORAGE_BACKEND"] = effective
    result, calls = deployment(local_config=config, dotenv_backend=declared)
    assert result.returncode != 0
    assert ".env and effective Compose" in result.stderr
    assert calls == [CONFIG]


@pytest.mark.parametrize(("dotenv", "shell", "overlay"), [
    ("local", "supabase", None),
    ("supabase", "local", None),
    ("local", None, "supabase"),
    ("supabase", None, "local"),
    ("local", "local", "supabase"),
    ("supabase", "supabase", "local"),
])
def test_real_compose_precedence_is_checked_before_lifecycle_commands(
        tmp_path, dotenv, shell, overlay):
    real_docker = shutil.which("docker")
    if not real_docker:
        pytest.skip("Docker Compose CLI is not installed")
    version = subprocess.run([real_docker, "compose", "version"], capture_output=True)
    if version.returncode:
        pytest.skip("Docker Compose plugin is not installed")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text(f"RESEARCH_STORAGE_BACKEND={dotenv}\n")
    (tmp_path / "approved_strategy.env").write_text(
        "" if overlay is None else f"RESEARCH_STORAGE_BACKEND={overlay}\n")
    shim = tmp_path / "docker"
    log = tmp_path / "calls.jsonl"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(log)!r}, 'a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] != ['compose', 'config']: raise SystemExit(73)\n"
        f"os.execv({real_docker!r}, [{real_docker!r}, *sys.argv[1:]])\n")
    shim.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    for key in ("RESEARCH_STORAGE_BACKEND", "COMPOSE_FILE", "COMPOSE_ENV_FILES", "COMPOSE_PROFILES"):
        env.pop(key, None)
    if shell is not None:
        env["RESEARCH_STORAGE_BACKEND"] = shell
    result = subprocess.run(["sh", str(HELPER)], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode not in (0, 73), result.stderr
    assert "research backend disagree" in result.stderr
    assert [json.loads(line) for line in log.read_text().splitlines()] == [CONFIG]
