"""The research image boots from its explicit copy list without brokerage modules."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def image_sources():
    dockerfile = (ROOT / "Dockerfile.shadow").read_text()
    for line in re.sub(r"\\\s*\n", " ", dockerfile).splitlines():
        if line.startswith("COPY "):
            tokens = shlex.split(line)
            for source in tokens[1:-1]:
                yield source, tokens[-1]


def test_shadow_image_has_no_broker_sdk_or_execution_orchestration(tmp_path):
    copied = []
    for source, destination in image_sources():
        if not source.endswith(".py"):
            continue
        target = tmp_path / destination / Path(source).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
        copied.append(source)
    assert not {"execution_agent.py", "orders.py", "buying.py", "selling.py",
                "broker_positions.py", "ibkr_data.py"} & set(copied)
    requirements = (ROOT / "requirements-shadow.txt").read_text().lower()
    assert "ib-insync" not in requirements and "ib_insync" not in requirements
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import shadow_worker, shadow_inputs, shadow_store, shadow_engine
from research import calibrate_intraday
assert 'ib_insync' not in sys.modules
assert 'execution_agent' not in sys.modules
assert 'orders' not in sys.modules
assert len(shadow_engine.engine_fingerprint()) == 64
"""
    result = subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60,
                            env={key: value for key, value in os.environ.items()
                                 if key in ("PATH", "HOME", "SYSTEMROOT")})
    assert result.returncode == 0, result.stderr


def test_worker_and_web_export_share_fingerprinted_dependency_constraints():
    for dockerfile in ("Dockerfile", "Dockerfile.shadow"):
        text = (ROOT / dockerfile).read_text()
        assert "FROM python:3.12-slim" in text
        assert "-r requirements-shadow.txt" in text
    workflow = (ROOT / ".github/workflows/docker_build_push.yml").read_text()
    assert "ai-trading-bot-shadow:${{ github.sha }}" in workflow
