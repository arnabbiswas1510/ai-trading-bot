from pathlib import Path
import json
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_local_research.sh"


def test_backup_script_has_valid_shell_and_no_destructive_command():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    text = SCRIPT.read_text()
    assert "export --database local" in text
    assert "check-archive" in text
    assert "--destination" in text
    assert "migration.py purge" not in text
    assert "rm " not in text


def test_backup_usage_fails_before_any_database_work():
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "PRIVATE_ENV_FILE SECOND_DEVICE_DIRECTORY" in result.stderr


def test_backup_runs_only_local_export_verification_and_copy(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / SCRIPT.name
    wrapper.write_text(SCRIPT.read_text())
    (scripts / "research_migration.py").write_text(
        "import json, os, sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text("#!/bin/sh\nprintf '172.20.0.2\\n'\n")
    docker.chmod(0o700)
    config = tmp_path / "backup.env"
    config.write_text("RESEARCH_POSTGRES_PASSWORD=synthetic-test-password\n")
    config.chmod(0o600)
    second = tmp_path / "second"
    second.mkdir()
    log = tmp_path / "calls.jsonl"
    env = dict(os.environ, RESEARCH_MIGRATION_PYTHON=sys.executable,
               CALL_LOG=str(log), PATH=f"{tools}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(wrapper), str(config), str(second)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0][:4] == ["--docker-tools", "export", "--database", "local"]
    assert calls[1][0] == "check-archive"
    assert calls[2][0] == "archive"
    assert len(calls) == 3
    assert "synthetic-test-password" not in result.stdout + result.stderr

    log.unlink()
    config.chmod(0o644)
    result = subprocess.run(
        ["bash", str(wrapper), str(config), str(second)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not log.exists()
