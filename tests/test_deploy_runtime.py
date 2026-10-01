"""Offline deployment contract; Docker is a recording executable, never a daemon."""
import json
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

    def run(mode=None, fail=None, args=(), existing="guarded", image_gate="1", morning=False):
        env = os.environ.copy()
        env.pop("TRADING_RUNTIME_MODE", None)
        env.pop("FAIL_DOCKER_ACTION", None)
        env.update(PATH=f"{work}{os.pathsep}{env['PATH']}", DOCKER_LOG=str(log),
                   EXISTING_AGENT=existing, IMAGE_GATE=image_gate, PROJECT_DIR=str(work))
        if mode is not None:
            env["TRADING_RUNTIME_MODE"] = mode
        if fail:
            env["FAIL_DOCKER_ACTION"] = fail
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


SERVICES = ["execution-agent", "intraday-observer", "shadow-worker", "trading-bot"]
GATEWAY = ["compose", "up", "-d", "--no-deps", "--no-recreate", "ib-gateway"]


@pytest.mark.parametrize("mode", [None, "", "observe", "live"])
def test_all_modes_keep_protection_and_research_running(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call[0] == "compose"] == [
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
    assert len(calls) == 1


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
        assert operation in {"stop", "pull", "up"}
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
    assert 'command: ["python", "intraday_observer.py"]' in observer
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
