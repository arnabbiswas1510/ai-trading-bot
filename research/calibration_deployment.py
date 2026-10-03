"""Reviewed strategy artifacts; hashes are integrity checks, not signatures.

The authenticated dashboard verifies approval at deployment time. This module
never changes the trading-control database or submits orders.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = "approved_strategy.env"
MANIFEST_FILE = "approved_strategy.json"
CONFIG_KEYS = ("decision_config", "exit_config", "replay_config", "costs", "shared_exit_rules")
RUNTIME_KEYS = (*CONFIG_KEYS, "runtime", "costs_semantics")
RUNTIME_ENGINE_FILES = (
    "config.py", "decision_core.py", "exit_core.py", "exit_rules.py", "cooling_off.py",
    "market_calendar.py", "trade_costs.py", "research_configuration.py",
    "market_direction.py", "indicators.py", "trigger_audit.py",
)
LIVE_ORCHESTRATOR_FILES = (
    "execution_agent.py", "agent_entrypoint.py", "research_entrypoint.py",
    "buying.py", "monitoring.py", "selling.py", "orders.py", "trading_control.py",
)
FIELDS = {
    "decision_config": {
        "min_trigger_score": (0, 100, "integer"),
        "min_pre_breakout_score": (0, 100, "integer"),
        "min_relaxed_trigger_score": (0, 100, "integer"),
        "min_vol_surge_gate": (0, 10, "number"),
        "max_pivot_extension": (0, .2, "number"),
        "max_pivot_breakdown": (0, .2, "number"),
        "max_pre_breakout_pivot_dist": (0, .2, "number"),
    },
    "exit_config": {
        "armed_exit_deadline_hours": (.25, 24, "number"),
        "scale_out_enabled": (0, 1, "boolean"),
        "scale_out_trigger_pct": (.005, .5, "number"),
        "scale_out_fraction": (.01, .99, "number"),
    },
}


class DeploymentError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DeploymentError(message)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError("Expected finite JSON") from exc


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def config_snapshot(config):
    """Exclude build identity, never strategy/risk/runtime configuration."""
    require(isinstance(config, dict) and all(k in config for k in RUNTIME_KEYS),
            "A complete runtime configuration snapshot is required")
    return copy.deepcopy({k: config[k] for k in RUNTIME_KEYS})


def config_digest(config):
    return digest(config_snapshot(config))


def _number(key, value, limits):
    low, high, kind = limits
    if kind == "boolean":
        require(type(value) is bool, f"{key} must be boolean")
    else:
        require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
                f"{key} is outside the deployable range")
        if kind == "integer":
            require(value == int(value), f"{key} must be an integer (the live loader uses int)")


def candidate_configuration(original, experiment):
    require(isinstance(experiment, dict), "Missing frozen experiment")
    require(not set(experiment) - {"name", "disable_ai_veto", *FIELDS},
            "Unsupported experiment keys")
    require(experiment.get("disable_ai_veto", False) is False,
            "disable_ai_veto has no verified live deployment toggle")
    result = copy.deepcopy(original)
    changed = False
    for group, fields in FIELDS.items():
        overrides = experiment.get(group, {})
        require(isinstance(overrides, dict) and not set(overrides) - set(fields),
                f"Unsupported {group} overrides; risk and trading controls cannot change")
        for key, value in overrides.items():
            _number(key, value, fields[key])
            require(key in result[group], f"Original configuration lacks {group}.{key}")
            if fields[key][2] == "integer":
                value = int(value)
            elif fields[key][2] == "number":
                value = float(value)
            changed |= result[group][key] != value
            result[group][key] = value
    require(changed, "The selected experiment makes no deployable parameter change")
    return result


def environment(config):
    """Freeze every supported field, so older overlays cannot leak into a proposal."""
    values = {}
    for group, fields in FIELDS.items():
        for key, limits in fields.items():
            value = config[group][key]
            _number(key, value, limits)
            if limits[2] == "boolean":
                value = "true" if value else "false"
            elif limits[2] == "integer":
                value = str(int(value))
            else:
                value = str(value)
            values[key.upper()] = value
    return "# Approved strategy parameters only; this file never grants live-entry permission.\n" + "".join(
        f"{key}={value}\n" for key, value in sorted(values.items()))


def manifest_text(manifest):
    canonical(manifest)
    return json.dumps(manifest, sort_keys=True, indent=2) + "\n"


def make_patch(old_env, old_manifest, new_env, manifest):
    return "".join(
        f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{name}", tofile=f"b/{name}"))
        for name, old, new in (
            (ENV_FILE, old_env, new_env),
            (MANIFEST_FILE, old_manifest, manifest_text(manifest)))
        if old != new
    )


def validate_frozen_policy(artifact, settings_row):
    """Require the predeclared policy and complete fixed future evaluation.

    This check also applies before the ready-to-approved transition. A policy
    chosen after seeing outcomes cannot retroactively qualify the campaign.
    """
    frozen = artifact.get("frozen") or {}
    policy = (frozen.get("settings") or {}).get("risk_policy")
    current_policy = (settings_row.get("value") or {}).get("risk_policy")
    require(isinstance(policy, dict) and bool(policy),
            "No risk policy was frozen before evaluation; start a new prospective campaign")
    require(frozen.get("risk_policy_sha256") == digest(policy),
            "Frozen risk policy fingerprint mismatch")
    require(digest(policy) == digest(current_policy)
            and artifact.get("settings_revision") == settings_row.get("revision"),
            "Policy/settings changed since freezing; a new prospective campaign is required")
    require(artifact.get("evaluation_complete") is True
            and isinstance(artifact.get("evaluation_end"), str)
            and artifact.get("last_evaluated_session") == artifact["evaluation_end"],
            "The predeclared evaluation window has not completed; early approval is forbidden")
    return copy.deepcopy(policy)


def _approved(proposal, settings_row):
    require(proposal.get("status") == "approved", "Proposal is not approved")
    artifact = proposal.get("artifact")
    require(isinstance(artifact, dict), "Missing frozen/evaluated artifact")
    artifact_hash = digest(artifact)
    require(proposal.get("artifact_sha256") == artifact_hash, "Proposal artifact was edited")
    validate_frozen_policy(artifact, settings_row)
    approval = proposal.get("approval") or {}
    require(approval.get("artifact_sha256") == artifact_hash, "Approval does not bind this artifact")
    require(approval.get("policy_revision") == settings_row.get("revision"),
            "Approval is stale: risk-policy/settings revision changed")
    approved_revision = approval.get("proposal_revision")
    require(type(approved_revision) is int and type(proposal.get("revision")) is int
            and 1 <= approved_revision <= proposal["revision"],
            "Invalid immutable approval revision")
    baseline = config_snapshot(approval.get("runtime_config"))
    require(approval.get("runtime_config_sha256") == digest(baseline),
            "Approval does not bind the exact reviewed runtime configuration")
    selection = artifact["frozen"]["selection"]
    account_id = approval.get("account_id")
    require(isinstance(account_id, str) and bool(account_id.strip())
            and account_id == selection.get("training", {}).get("account_id"),
            "Approval must bind the exact nonempty training account")
    original = selection["original_settings"]
    require(all(original.get(k) == baseline[k] for k in CONFIG_KEYS),
            "Reviewed runtime differs from the recorded evaluation baseline")
    evaluation = artifact["evaluation"]["evaluation"]
    require(evaluation.get("selection_plan_sha256") == selection.get("artifact_sha256"),
            "Evaluation does not refer to the frozen selection")
    for name, document in (("selection", selection), ("evaluation", evaluation)):
        require(document.get("artifact_sha256") == digest(
            {k: v for k, v in document.items() if k != "artifact_sha256"}),
            f"Edited {name} artifact")
    candidate = candidate_configuration(baseline, selection["frozen_experiment"])
    if candidate["exit_config"]["scale_out_enabled"] != baseline["exit_config"]["scale_out_enabled"]:
        provenance = approval.get("investigation_approval") or {}
        require(isinstance(proposal.get("parent_id"), str) and bool(proposal["parent_id"])
                and provenance.get("proposal_id") == proposal["parent_id"]
                and provenance.get("campaign_id") == proposal["id"]
                and provenance.get("experiment_sha256") == digest(selection["frozen_experiment"]),
                "Scale-out rule change requires exact approved investigation provenance")
    return artifact_hash, approval, baseline, candidate


def build_deployment_artifact(proposal, settings_row):
    """API must recalculate evidence eligibility before calling this function.

    Approval.runtime_config is captured by the server at approval, not supplied
    by the browser.     Approval.proposal_revision is the immutable resulting approved revision.
    Later append-only discussion/export audits may advance the row revision;
    exact artifact, approved status and policy bindings remain mandatory.
    """
    artifact_hash, approval, baseline, candidate = _approved(proposal, settings_row)
    old_env = (ROOT / ENV_FILE).read_text()
    old_manifest = (ROOT / MANIFEST_FILE).read_text()
    overlay = environment(candidate)
    manifest = {
        "schema_version": 1, "active": True,
        "proposal_id": proposal["id"], "proposal_revision": approval["proposal_revision"],
        "artifact_sha256": artifact_hash, "policy_revision": settings_row["revision"],
        "account_sha256": digest(approval["account_id"]),
        "risk_policy_sha256": digest(proposal["artifact"]["frozen"]["settings"]["risk_policy"]),
        "approved_at": approval["approved_at"],
        "baseline_config_sha256": digest(baseline),
        "candidate_config_sha256": digest(candidate),
        "overlay_sha256": text_digest(overlay),
        "previous_overlay_sha256": text_digest(old_env),
        "previous_manifest_sha256": text_digest(old_manifest),
        "previous_values": {g: {k: baseline[g][k] for k in fields} for g, fields in FIELDS.items()},
        "approved_values": {g: {k: candidate[g][k] for k in fields} for g, fields in FIELDS.items()},
        "rollback": "A replacement approval must target previous_values. Do not reverse an active "
                    "patch: the previous approval may be stale. Preserve the exact prior files in git.",
    }
    patch = make_patch(old_env, old_manifest, overlay, manifest)
    return {
        "filename": f"approved-strategy-{proposal['id']}-r{approval['proposal_revision']}.patch",
        "patch": patch, "manifest": manifest, "overlay": overlay,
        "sha256": text_digest(patch),
        "notes": [
            "No order or trading-control write. Existing live-entry permission is unchanged.",
            "Save this JSON and run scripts/apply_calibration_artifact.py on the authorized machine.",
            "The authenticated running dashboard must verify approval before apply and deployment.",
            "Hashes detect edits; they are not digital signatures or independent authentication.",
            "Numeric-parameter approval only: candidate live code/dependencies must match the running "
            "executor. Research and live dependency versions may differ; simulation is not exact-environment parity.",
            manifest["rollback"],
        ],
    }


def verify_deployment(proposal, settings_row, manifest, overlay, current_config,
                      candidate_config=None, candidate_probe=None):
    """Called only behind authenticated API after recomputing risk eligibility.

    current_config comes from already-loaded server settings; candidate_config,
    when supplied by production preflight, comes from a no-order candidate-image
    process using the actual Compose environment.
    """
    artifact_hash, approval, baseline, candidate = _approved(proposal, settings_row)
    expected = {
        "schema_version": 1, "active": True, "proposal_id": proposal["id"],
        "proposal_revision": approval["proposal_revision"], "artifact_sha256": artifact_hash,
        "account_sha256": digest(approval["account_id"]),
        "policy_revision": settings_row["revision"], "approved_at": approval["approved_at"],
        "risk_policy_sha256": digest(proposal["artifact"]["frozen"]["settings"]["risk_policy"]),
        "baseline_config_sha256": digest(baseline),
        "candidate_config_sha256": digest(candidate), "overlay_sha256": text_digest(environment(candidate)),
        "approved_values": {g: {k: candidate[g][k] for k in f} for g, f in FIELDS.items()},
        "previous_values": {g: {k: baseline[g][k] for k in f} for g, f in FIELDS.items()},
    }
    require(isinstance(manifest, dict) and all(manifest.get(k) == v for k, v in expected.items()),
            "Manifest does not match this exact approved strategy")
    require(overlay == environment(candidate), "Overlay contains unapproved or modified environment values")
    require(config_digest(current_config) in {digest(baseline), digest(candidate)},
            "Running configuration drifted from the reviewed baseline/approved candidate")
    if candidate_config is not None:
        require(config_digest(candidate_config) == digest(candidate),
                "Candidate image/environment differs from the complete approved configuration")
    result = {"verified": True, "manifest_sha256": digest(manifest)}
    if candidate_probe is not None:
        verify_candidate_probe(proposal, candidate_probe, candidate)
        result["candidate_probe_sha256"] = digest(candidate_probe)
    return result


def runtime_engine_identity(engine):
    require(isinstance(engine, dict) and isinstance(engine.get("files"), dict)
            and isinstance(engine.get("packages"), dict)
            and isinstance(engine.get("python"), list) and len(engine["python"]) == 3,
            "Frozen engine lacks runtime source/Python/dependency identity")
    require(all(isinstance(engine["files"].get(name), str) and len(engine["files"][name]) == 64
                for name in RUNTIME_ENGINE_FILES),
            "Frozen engine lacks shared runtime source hashes")
    return {
        "files": {name: engine["files"][name] for name in RUNTIME_ENGINE_FILES},
        "python": engine["python"], "packages": engine["packages"],
    }


def verify_candidate_probe(proposal, probe, candidate):
    """Validate actual to-be-launched images, not only the still-running API."""
    require(isinstance(probe, dict)
            and set(probe) == {"current_execution", "execution", "trading_bot", "calibration_worker"},
            "Candidate probe must contain current/candidate execution, dashboard and calibration-worker snapshots")
    frozen = proposal["artifact"]["frozen"]
    engine, automatic = frozen["selection"].get("engine"), frozen.get("automatic_engine")
    require(isinstance(engine, dict) and isinstance(automatic, dict),
            "Frozen research engine identities are required")
    for name in ("trading_bot", "calibration_worker"):
        snapshot = probe[name]
        require(isinstance(snapshot, dict) and set(snapshot) == {"automatic_engine", "engine"}
                and snapshot["automatic_engine"] == automatic and snapshot["engine"] == engine,
                f"Candidate {name} engine/dependencies/Python differ from frozen approval")
    for name in ("current_execution", "execution"):
        execution = probe[name]
        require(isinstance(execution, dict)
                and set(execution) == {"config", "account_id", "runtime_identity"},
                "Current/candidate execution configuration/account/runtime snapshot is incomplete")
        require(isinstance(execution["account_id"], str) and bool(execution["account_id"].strip())
                and execution["account_id"] == proposal["approval"]["account_id"],
                "Current/candidate execution account differs from approved account")
        identity = execution["runtime_identity"]
        require(isinstance(identity, dict) and set(identity) == {"files", "python", "packages"}
                and isinstance(identity["files"], dict)
                and all(name in identity["files"] for name in (*RUNTIME_ENGINE_FILES, *LIVE_ORCHESTRATOR_FILES))
                and all(isinstance(value, str) and len(value) == 64 for value in identity["files"].values())
                and isinstance(identity["python"], list) and len(identity["python"]) == 3
                and all(type(value) is int for value in identity["python"])
                and isinstance(identity["packages"], dict) and bool(identity["packages"])
                and all(isinstance(value, str) and bool(value) for value in identity["packages"].values()),
                "Execution source/Python/dependency identity is incomplete")
    execution, current = probe["execution"], probe["current_execution"]
    require(config_digest(current["config"]) in {
        config_digest(proposal["approval"]["runtime_config"]), digest(candidate)},
        "Current execution configuration drifted from the approved baseline/candidate")
    require(config_digest(execution["config"]) == digest(candidate),
            "Candidate execution configuration differs from approved configuration")
    require(execution["runtime_identity"] == current["runtime_identity"],
            "Candidate execution sources/dependencies/Python differ from the running executor")
    expected_files = runtime_engine_identity(engine)["files"]
    require(all(execution["runtime_identity"]["files"][name] == sha
                for name, sha in expected_files.items()),
            "Execution shared runtime sources differ from frozen research sources")
