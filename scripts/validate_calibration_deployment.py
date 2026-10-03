#!/usr/bin/env python3
"""Fail closed before touching running services; never source the secret .env."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

RUNTIME_ENGINE_FILES = (
    "config.py", "decision_core.py", "exit_core.py", "exit_rules.py", "cooling_off.py",
    "market_calendar.py", "trade_costs.py", "research_configuration.py",
    "market_direction.py", "indicators.py", "trigger_audit.py",
)
LIVE_ORCHESTRATOR_FILES = (
    "execution_agent.py", "agent_entrypoint.py", "research_entrypoint.py",
    "buying.py", "monitoring.py", "selling.py", "orders.py", "trading_control.py",
)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_token(root):
    token = os.environ.get("TRADING_CONTROL_TOKEN", "")
    if not token:
        path = Path(root) / ".env"
        if path.exists():
            matches = []
            for line in path.read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "TRADING_CONTROL_TOKEN":
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    matches.append(value)
            if len(matches) == 1:
                token = matches[0]
    if len(token) < 32 or any(c in token for c in "\r\n"):
        raise ValueError("A literal TRADING_CONTROL_TOKEN of at least 32 characters is required")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Approval verification redirects are forbidden")


def verify_remote(url, token, manifest, overlay, candidate_config=None, candidate_probe=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a plain dashboard base URL without credentials, query or fragment")
    if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Use HTTPS or a localhost SSH tunnel; do not send the token over plaintext LAN")
    payload = {"manifest": manifest, "overlay": overlay}
    if candidate_config is not None:
        payload["candidate_config"] = candidate_config
    if candidate_probe is not None:
        payload["candidate_probe"] = candidate_probe
    request = urllib.request.Request(
        url.rstrip("/") + "/api/calibration/verify-deployment",
        data=json.dumps(payload, allow_nan=False).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Dashboard refused approval verification (HTTP {exc.code})") from None
    except urllib.error.URLError:
        raise ValueError("Authenticated dashboard approval verification is unavailable") from None
    if result.get("verified") is not True or result.get("manifest_sha256") != digest(manifest):
        raise ValueError("Dashboard did not verify this exact manifest")
    if candidate_probe is not None and result.get("candidate_probe_sha256") != digest(candidate_probe):
        raise ValueError("Dashboard did not verify the complete candidate engine/account snapshots")
    return result


def probe_service(root, service, code):
    process = subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "-T", "--entrypoint", "python",
         service, "-c", code],
        cwd=root, check=True, capture_output=True, text=True,
    )
    try:
        result = json.loads(process.stdout)
    except ValueError:
        raise ValueError("Candidate image did not return a clean JSON snapshot") from None
    if not isinstance(result, dict):
        raise ValueError("Candidate image snapshot must be an object")
    return result


def probe_current_execution(root, code):
    inspected = subprocess.run(
        ["docker", "inspect", "execution-agent"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    try:
        containers = json.loads(inspected.stdout)
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError("Expected one incumbent execution container")
        image = containers[0]["Image"]
        environment = containers[0]["Config"]["Env"]
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Incumbent container lacks an immutable image identity")
        if not isinstance(environment, list):
            raise ValueError("Incumbent container lacks its exact environment")
        values = {}
        for item in environment:
            if not isinstance(item, str):
                raise ValueError("Malformed incumbent environment")
            key, separator, value = item.partition("=")
            if (not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                    or key in values or key.startswith(("DOCKER_", "LD_", "DYLD_"))):
                raise ValueError("Unsupported incumbent environment")
            values[key] = value
    except (ValueError, KeyError, TypeError):
        raise ValueError("Could not inspect the exact incumbent image/environment") from None
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("Docker client is unavailable")
    host_env = os.environ.copy()
    # Preserve the operator's Docker context even though the container's HOME and
    # PATH are reproduced. Values travel only in the child process environment,
    # never command arguments, files, logs or mounted production volumes.
    docker_config = host_env.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
    host_env.update(values)
    host_env["DOCKER_CONFIG"] = docker_config
    command = [docker, "run", "--rm", "--pull", "never", "--network", "none",
               "--read-only", "--entrypoint", "python"]
    for key in sorted(values):
        command.extend(("--env", key))
    command.extend((image, "-c", code))
    process = subprocess.run(
        command, cwd=root, check=True, capture_output=True, text=True, env=host_env,
    )
    try:
        result = json.loads(process.stdout)
    except ValueError:
        raise ValueError("Incumbent image did not return a clean JSON snapshot") from None
    if not isinstance(result, dict):
        raise ValueError("Incumbent execution image snapshot must be an object")
    return result


def probe_candidates(root):
    execution_code = (
        "import hashlib,importlib.metadata,json,os,sys\nfrom pathlib import Path\n"
        "import research_configuration\n"
        "root=Path(research_configuration.__file__).resolve().parent\n"
        "files=sorted(path.name for path in root.glob('*.py') if path.is_file())\n"
        "packages={dist.metadata['Name'].lower().replace('_','-'):dist.version "
        "for dist in importlib.metadata.distributions() if dist.metadata['Name']}\n"
        "print(json.dumps({'config':research_configuration.effective_config(),"
        "'account_id':os.environ.get('IBKR_ACCOUNT',''),"
        "'runtime_identity':{'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() "
        "for name in files},'python':list(sys.version_info[:3]),'packages':packages}},sort_keys=True))"
    )
    research_code = (
        "import json; from research import auto_calibration,calibrate_intraday; "
        "print(json.dumps({'automatic_engine':auto_calibration.automatic_fingerprint(),"
        "'engine':calibrate_intraday.engine_fingerprint()},sort_keys=True))"
    )
    result = {
        "current_execution": probe_current_execution(root, execution_code),
        "execution": probe_service(root, "execution-agent", execution_code),
        "trading_bot": probe_service(root, "trading-bot", research_code),
        "calibration_worker": probe_service(root, "calibration-worker", research_code),
    }
    for name in ("current_execution", "execution"):
        execution = result[name]
        if (set(execution) != {"config", "account_id", "runtime_identity"}
                or not isinstance(execution["config"], dict)
                or not isinstance(execution["account_id"], str) or not execution["account_id"].strip()
                or not isinstance(execution["runtime_identity"], dict)):
            raise ValueError("Execution snapshot lacks configuration, account or runtime identity")
        identity = execution["runtime_identity"]
        if (set(identity) != {"files", "python", "packages"}
                or not isinstance(identity["files"], dict)
                or not set((*RUNTIME_ENGINE_FILES, *LIVE_ORCHESTRATOR_FILES)) <= set(identity["files"])
                or not isinstance(identity["python"], list) or len(identity["python"]) != 3
                or not isinstance(identity["packages"], dict) or not identity["packages"]):
            raise ValueError("Execution runtime source/Python/dependency snapshot is incomplete")
    for name in ("trading_bot", "calibration_worker"):
        snapshot = result[name]
        if (set(snapshot) != {"automatic_engine", "engine"}
                or not isinstance(snapshot["automatic_engine"], dict) or not snapshot["automatic_engine"]
                or not isinstance(snapshot["engine"], dict) or not snapshot["engine"]):
            raise ValueError("Candidate research engine snapshot is incomplete")
    return result


def validate(root, url="http://127.0.0.1:8000"):
    root = Path(root)
    manifest_path = root / "approved_strategy.json"
    overlay_path = root / "approved_strategy.env"
    activation_marker = root / ".approved_strategy_activated.json"
    # Compatibility with pre-artifact installations: neither file means inactive.
    if not manifest_path.exists() and not overlay_path.exists():
        if activation_marker.exists():
            raise ValueError("Previously approved deployment cannot be removed without replacement approval")
        return "No approved strategy overlay; existing operator configuration is unchanged."
    manifest = json.loads(manifest_path.read_text())
    overlay = overlay_path.read_text()
    if manifest == {"active": False, "schema_version": 1}:
        if activation_marker.exists():
            raise ValueError("Rollback to inactive requires a newly reviewed replacement proposal")
        if any(line.strip() and not line.lstrip().startswith("#") for line in overlay.splitlines()):
            raise ValueError("Inactive manifest cannot contain strategy environment assignments")
        return "Strategy deployment inactive; existing operator configuration is unchanged."
    if manifest.get("active") is not True or manifest.get("schema_version") != 1:
        raise ValueError("Unknown strategy deployment manifest")
    token = read_token(root)
    # Verify approval BEFORE even starting the isolated configuration probe.
    verify_remote(url, token, manifest, overlay)
    candidate_probe = probe_candidates(root)
    verify_remote(url, token, manifest, overlay, candidate_probe=candidate_probe)
    # A host-local, nonsecret latch prevents an accidental git revert of the two
    # committed files from silently clearing the requirement for approval.
    activation_marker.write_text(json.dumps({
        "manifest_sha256": digest(manifest), "proposal_id": manifest["proposal_id"],
    }, sort_keys=True) + "\n")
    return "Exact approved strategy and candidate runtime verified; trading permission unchanged."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        print(validate(args.root, args.dashboard_url))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError):
        # Do not echo subprocess output, request headers or secret .env contents.
        print("ERROR: approved-strategy preflight failed; existing services were not recreated. "
              "Check approval, policy revision, dashboard availability and runtime configuration.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
