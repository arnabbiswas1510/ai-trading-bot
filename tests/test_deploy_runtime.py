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
    )
    docker.chmod(0o755)
    # Neither this file nor arbitrary shell embedded in it may be executed.
    (work / ".env").write_text("touch secret-env-was-executed\nTRADING_RUNTIME_MODE=live\n")

    def run(mode=None, fail=None, args=()):
        env = os.environ.copy()
        env.pop("TRADING_RUNTIME_MODE", None)
        env.pop("FAIL_DOCKER_ACTION", None)
        env.update(PATH=f"{work}{os.pathsep}{env['PATH']}", DOCKER_LOG=str(log))
        if mode is not None:
            env["TRADING_RUNTIME_MODE"] = mode
        if fail:
            env["FAIL_DOCKER_ACTION"] = fail
        result = subprocess.run(
            ["sh", str(HELPER), *args], cwd=work, env=env,
            text=True, capture_output=True, check=False,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        assert not (work / "secret-env-was-executed").exists()
        return result, calls

    try:
        yield run
    finally:
        shutil.rmtree(work)


@pytest.mark.parametrize("mode", [None, "", "observe"])
def test_observe_is_default_and_never_starts_execution_agent(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["compose", "--profile", "live", "stop", "execution-agent"],
        ["compose", "--profile", "observe", "pull", "intraday-observer", "trading-bot"],
        ["compose", "up", "-d", "--no-deps", "--no-recreate", "ib-gateway"],
        ["compose", "--profile", "observe", "up", "-d", "--no-deps", "intraday-observer", "trading-bot"],
        ["inspect", "intraday-observer", "--format", "{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})"],
        ["inspect", "ib-gateway", "--format", "{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})"],
        ["inspect", "can-slim-trading-bot", "--format", "{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})"],
    ]


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


def test_failed_image_pull_leaves_execution_agent_stopped(deployment):
    result, calls = deployment(fail="pull")
    assert result.returncode == 42
    assert calls == [
        ["compose", "--profile", "live", "stop", "execution-agent"],
        ["compose", "--profile", "observe", "pull", "intraday-observer", "trading-bot"],
    ]


def test_stop_failure_prevents_pulling_or_starting_anything(deployment):
    result, calls = deployment(fail="stop")
    assert result.returncode == 42
    assert calls == [["compose", "--profile", "live", "stop", "execution-agent"]]


def test_gateway_failure_does_not_start_execution_agent(deployment):
    result, calls = deployment(fail="up")
    assert result.returncode == 42
    assert calls[-1] == ["compose", "up", "-d", "--no-deps", "--no-recreate", "ib-gateway"]
    assert not any("up" in call and "execution-agent" in call for call in calls)


def test_live_requires_explicit_opt_in_and_stops_observer_first(deployment):
    result, calls = deployment("live")
    assert result.returncode == 0, result.stderr
    assert calls[0] == ["compose", "--profile", "observe", "stop", "intraday-observer"]
    assert calls[1] == ["compose", "--profile", "live", "pull", "execution-agent", "trading-bot"]
    assert calls[3] == ["compose", "--profile", "live", "up", "-d", "--no-deps", "execution-agent", "trading-bot"]
    assert calls[4][0:2] == ["inspect", "execution-agent"]
    assert not any("up" in call and "intraday-observer" in call for call in calls)


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_runtime_deploy_cannot_execute_order_operations_or_restart_gateway(deployment, mode):
    result, calls = deployment(mode)
    assert result.returncode == 0
    for call in calls:
        if call[0] == "inspect":
            continue
        assert call[0] == "compose"
        operation = call[3] if call[1] == "--profile" else call[1]
        assert operation in {"stop", "pull", "up"}
        if operation == "up":
            assert "--no-deps" in call
        if "ib-gateway" in call:
            assert call == ["compose", "up", "-d", "--no-deps", "--no-recreate", "ib-gateway"]


def test_workflow_ships_helper_and_passes_operator_mode_as_environment():
    text = WORKFLOW.read_text()
    source = re.search(r'^\s+source: "([^"]+)"', text, re.MULTILINE).group(1).split(",")
    assert "scripts/deploy_runtime.sh" in source
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


def test_compose_profiles_prevent_implicit_live_start():
    text = (ROOT / "docker-compose.yml").read_text()
    for name, profile in (("execution-agent", "live"), ("intraday-observer", "observe")):
        block = re.search(
            rf"^  {name}:\n(.*?)(?=^  [a-z][\w-]*:|\Z)", text, re.MULTILINE | re.DOTALL,
        ).group(1)
        assert re.search(rf'^\s+profiles: \["{profile}"\]', block, re.MULTILINE)
    dashboard = text.split("  trading-bot:", 1)[1].split("\nnetworks:", 1)[0]
    assert "depends_on:" not in dashboard
