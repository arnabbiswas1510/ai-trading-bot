#!/usr/bin/env python3
"""Apply an exact reviewed artifact and optionally push from an authorized checkout.

Save the dashboard artifact JSON, set TRADING_CONTROL_TOKEN in the environment,
and use a localhost SSH tunnel or HTTPS dashboard URL. No GitHub token is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.calibration_deployment import (
    ENV_FILE, MANIFEST_FILE, DeploymentError, make_patch, text_digest,
)
from scripts.validate_calibration_deployment import read_token, verify_remote


def git(*args, input=None):
    return subprocess.run(["git", *args], cwd=ROOT, input=input, text=True,
                          check=True, capture_output=True).stdout.strip()


def apply_artifact(artifact, dashboard_url, push=False):
    manifest, overlay, patch = artifact["manifest"], artifact["overlay"], artifact["patch"]
    old_env = (ROOT / ENV_FILE).read_text()
    old_manifest = (ROOT / MANIFEST_FILE).read_text()
    if (artifact["sha256"] != text_digest(patch)
            or manifest["previous_overlay_sha256"] != text_digest(old_env)
            or manifest["previous_manifest_sha256"] != text_digest(old_manifest)
            or patch != make_patch(old_env, old_manifest, overlay, manifest)):
        raise DeploymentError("Artifact was edited or targets different prior overlay files")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise DeploymentError("Tracked worktree/index must be clean; preserve other work first")
    branch = git("symbolic-ref", "--short", "HEAD")
    if push:
        git("fetch", "origin")
        if git("rev-parse", "HEAD") != git("rev-parse", "@{upstream}"):
            raise DeploymentError("Checkout must exactly match its fetched upstream before apply-and-push")
    verify_remote(dashboard_url, read_token(ROOT), manifest, overlay)
    git("apply", "--check", "-", input=patch)
    git("apply", "-", input=patch)
    if not push:
        return "Applied only; review git diff, then commit and push on your authorized machine."
    git("add", "--", ENV_FILE, MANIFEST_FILE)
    git("commit", "-m",
        f"Deploy approved strategy proposal {manifest['proposal_id']} revision {manifest['proposal_revision']}\n\n"
        f"Apply exact dashboard-reviewed parameters and evidence {manifest['artifact_sha256']}; "
        "approved_strategy.env and approved_strategy.json preserve the approval and prior overlay hashes. "
        "No live-entry permission or risk-control setting is changed.\n\n"
        "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>")
    git("push", "origin", branch)
    return "Approved strategy committed and pushed; production preflight still must verify before activation."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", help="Downloaded artifact JSON (not an arbitrary patch)")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8000")
    parser.add_argument("--apply-and-push", action="store_true",
                        help="Explicitly authorize commit and push using existing operator Git access")
    args = parser.parse_args()
    try:
        artifact = json.loads(Path(args.artifact).read_text())
        print(apply_artifact(artifact, args.dashboard_url, args.apply_and_push))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print("ERROR: approval/apply/push failed. No trading permission was changed. "
              "Inspect git status/log before retrying; a push failure can leave the approved commit local.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
