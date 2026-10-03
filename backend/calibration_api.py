"""Authenticated research decisions; never enables entries or writes live settings."""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

if __package__:
    from . import intraday_service
else:
    import intraday_service
from research.calibration_store import CalibrationStore, Conflict, StoreUnavailable, ValidationError

router = APIRouter()
log = logging.getLogger(__name__)


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    value: dict


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    action: StrictStr
    note: StrictStr = Field(default="", max_length=4000)
    artifact_sha256: StrictStr | None = None
    expected_policy_revision: StrictInt | None = Field(default=None, ge=0)
    experiment: dict | None = None


class DeploymentVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict
    overlay: StrictStr = Field(max_length=10000)
    candidate_config: dict | None = None
    candidate_probe: dict | None = None


def _configured_token():
    value = os.getenv("TRADING_CONTROL_TOKEN", "")
    return value if len(value) >= 32 else ""


def authenticate(authorization: str = Header(default="")):
    token = _configured_token()
    if not token:
        raise HTTPException(503, "Research decisions are locked; configure the private operator token.")
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
        raise HTTPException(403, "Invalid operator token.")
    return "operator"


def get_store():
    try:
        return CalibrationStore(intraday_service.get_client())
    except Exception as exc:
        _unavailable(exc)


def _unavailable(exc):
    try:
        intraday_service.report_failure("calibration_request_failed", exc)
    except Exception as diagnostic_error:
        log.error("Calibration diagnostic could not be recorded (%s)", type(diagnostic_error).__name__)
    raise HTTPException(503, "Research inbox unavailable. No change is confirmed; refresh before retrying.") from None


def _call(operation):
    try:
        return operation()
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except StoreUnavailable as exc:
        _unavailable(exc)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        # Key/type failures commonly indicate malformed stored artifacts. Do not
        # leak data or SDK exception text into a response.
        detail = str(exc) if isinstance(exc, ValueError) else "Invalid or incomplete research evidence."
        raise HTTPException(400, detail) from None
    except HTTPException:
        raise
    except Exception as exc:
        _unavailable(exc)


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"


@router.get("/api/calibration")
def inbox(response: Response, store=Depends(get_store)):
    _no_store(response)
    return _call(lambda: {
        "settings": store.settings(), "health": store.health(),
        "proposals": store.list_proposals(), "write_configured": bool(_configured_token()),
        "deployment_mode": "operator_artifact",
    })


@router.get("/api/calibration/proposals/{identifier}")
def proposal(identifier: str, response: Response, store=Depends(get_store)):
    _no_store(response)
    return _call(lambda: {"proposal": store.proposal(identifier),
                          "events": store.events(proposal_id=identifier)})


@router.put("/api/calibration/settings")
def update_settings(body: SettingsInput, response: Response,
                    actor=Depends(authenticate), store=Depends(get_store)):
    _no_store(response)
    return _call(lambda: {"settings": store.update_settings(body.expected_revision, body.value)})


def _experiment(body):
    from research.calibrate_intraday import ALLOWLIST, experiments
    if body.experiment is None:
        if not body.note.strip():
            raise ValidationError("Describe a rule investigation or provide a supported experiment.")
        return None, "rule", "investigation_requested"
    normalized = experiments({"experiments": [body.experiment]})[0]
    for key in ("min_trigger_score", "min_pre_breakout_score", "min_relaxed_trigger_score"):
        if key in normalized["decision_config"]:
            value = normalized["decision_config"][key]
            if value != int(value):
                raise ValidationError(f"{key} must be an integer score.")
            normalized["decision_config"][key] = int(value)
    rule = normalized["disable_ai_veto"] or any(
        ALLOWLIST[group][key] == "boolean"
        for group in ALLOWLIST for key in normalized[group]
    )
    if not rule and not any(normalized[group] for group in ALLOWLIST):
        raise ValidationError("The experiment must change at least one supported parameter.")
    return normalized, "rule" if rule else "parameter", (
        "investigation_requested" if rule else "investigation_approved")


@router.post("/api/calibration/experiments")
def request_root_experiment(body: ActionInput, response: Response,
                            actor=Depends(authenticate), store=Depends(get_store)):
    _no_store(response)

    def create():
        if body.action != "request_experiment" or body.expected_revision != 0:
            raise ValidationError("New experiment requests require request_experiment and revision 0.")
        if body.artifact_sha256 is not None or body.expected_policy_revision is not None:
            raise ValidationError("An investigation request cannot include deployment approval bindings.")
        normalized, kind, status = _experiment(body)
        row = {"id": uuid.uuid4().hex, "kind": kind, "title": (
            normalized["name"] if normalized else "Requested rule investigation"),
            "parent_id": None, "status": status, "artifact": {},
            "request": {"experiment": normalized, "note": body.note, "requested_by": actor}}
        return {"proposal": store.create_proposal(row)}

    return _call(create)


def _deployment_validation(row, settings, allow_candidate_runtime=False):
    from research import auto_calibration, calibrate_intraday as calibration
    from research.calibration_deployment import (
        CONFIG_KEYS, candidate_configuration, config_snapshot, validate_frozen_policy,
    )
    import research_configuration

    artifact = row.get("artifact")
    calibration.require(isinstance(artifact, dict)
                        and row.get("artifact_sha256") == calibration.digest(artifact),
                        "Proposal artifact fingerprint mismatch.")
    calibration.require(artifact["frozen"].get("automatic_engine") == auto_calibration.automatic_fingerprint(),
                        "Automatic research engine changed or was not recorded; freeze a new campaign.")
    policy = auto_calibration.validate_settings(settings["value"])["risk_policy"]
    frozen_settings = artifact["frozen"].get("settings")
    calibration.require(isinstance(frozen_settings, dict) and "risk_policy" in frozen_settings,
                        "The research risk policy was not frozen before evaluation; start a new campaign.")
    frozen_policy = auto_calibration.validate_settings(frozen_settings)["risk_policy"]
    calibration.require(frozen_policy is not None and policy is not None,
                        "Research selected without a numeric risk policy is exploratory only; "
                        "freeze a new campaign before collecting future evaluation evidence.")
    calibration.require(calibration.digest(frozen_policy) == calibration.digest(policy),
                        "Risk policy changed after selection; a new frozen campaign and future "
                        "evaluation evidence are required.")
    validate_frozen_policy(artifact, settings)
    plan = artifact["frozen"]["selection"]
    evaluation = artifact["evaluation"]["evaluation"]
    account_id = os.getenv("IBKR_ACCOUNT", "")
    calibration.require(bool(account_id) and account_id == account_id.strip(),
                        "Configure an explicit IBKR_ACCOUNT before approving or deploying research.")
    calibration.require(plan["training"].get("account_id") == account_id,
                        "Research evidence belongs to a different brokerage account.")
    calibration.verify_plan(plan)
    calibration.require(evaluation.get("artifact_sha256") == calibration.digest({
        key: value for key, value in evaluation.items() if key != "artifact_sha256"
    }), "Evaluation fingerprint mismatch.")
    calibration.require(evaluation.get("artifact_type") == "intraday_calibration_holdout"
                        and evaluation.get("version") == calibration.VERSION
                        and evaluation.get("selection_plan_sha256") == plan["artifact_sha256"]
                        and evaluation.get("engine") == plan["engine"]
                        and evaluation.get("selected_name") == plan["selected_name"]
                        and evaluation.get("candidate_trial_count") == plan["candidate_trial_count"]
                        and evaluation.get("holdout_candidates_tested") == 1,
                        "Evaluation does not match the frozen selection.")
    selected = next(trial for trial in plan["training_trials"]
                    if trial["name"] == plan["selected_name"])
    calibration.require(evaluation["training"] == plan["training"]
                        and evaluation["training_baseline"] == plan["training_trials"][0]
                        and evaluation["training_selected"] == selected
                        and evaluation["original_settings"] == plan["original_settings"],
                        "Evaluation training evidence or original configuration changed.")
    baseline, baseline_diff = calibration.effective_settings(plan["original_settings"], calibration.BASELINE)
    candidate, candidate_diff = calibration.effective_settings(plan["original_settings"], plan["frozen_experiment"])
    calibration.require(evaluation["baseline"]["effective_settings"] == baseline
                        and evaluation["baseline"]["settings_diff"] == baseline_diff
                        and evaluation["frozen_candidate"]["effective_settings"] == candidate
                        and evaluation["frozen_candidate"]["settings_diff"] == candidate_diff
                        and evaluation["frozen_candidate"]["name"] == plan["selected_name"],
                        "Evaluated settings differ from the exact frozen experiment.")
    training, holdout = plan["training"], evaluation["holdout"]
    sessions = artifact.get("evaluation_sessions")
    calibration.require(isinstance(sessions, list) and bool(sessions)
                        and sessions == sorted(set(sessions))
                        and holdout.get("sessions") == sessions
                        and artifact.get("evaluation_start") == sessions[0]
                        and artifact.get("evaluation_end") == sessions[-1],
                        "Evaluation evidence must cover the entire predeclared session window.")
    frozen_at = dt.datetime.fromisoformat(artifact["frozen_at"].replace("Z", "+00:00"))
    calibration.require(holdout["account_id"] == training["account_id"]
                        and holdout.get("origin") == training.get("origin")
                        and holdout["input_sha256"] != training["input_sha256"]
                        and dt.datetime.fromisoformat(holdout["observed_start"].replace("Z", "+00:00"))
                        > frozen_at
                        >= dt.datetime.fromisoformat(training["observed_end"].replace("Z", "+00:00")),
                        "Evaluation must use later, nonoverlapping evidence from the same account.")
    if holdout.get("origin"):
        calibration.require(holdout["actual_seed_sha256"] == training["actual_seed_sha256"],
                            "Evaluation shadow seed changed.")
    eligibility = auto_calibration.assess_evaluation(evaluation, policy)
    calibration.require(eligibility["eligible"],
                        "Deployment fails the current risk policy: " + "; ".join(eligibility["reasons"]))
    current = config_snapshot(research_configuration.effective_config())
    if row["status"] == "approved":
        approval = row.get("approval") or {}
        calibration.require(approval.get("artifact_sha256") == row["artifact_sha256"]
                            and approval.get("settings_revision") == settings["revision"]
                            and approval.get("account_id") == account_id
                            and approval.get("actor") == "operator",
                            "Approval is revoked, edited or stale, or the brokerage account changed.")
        reviewed = config_snapshot(approval.get("runtime_config"))
        calibration.require(approval.get("runtime_config_sha256") == calibration.digest(reviewed),
                            "Reviewed runtime fingerprint mismatch.")
    else:
        reviewed = current
    calibration.require(all(plan["original_settings"].get(key) == reviewed[key] for key in CONFIG_KEYS),
                        "Recorded baseline differs from the current reviewed runtime configuration.")
    deployable = candidate_configuration(reviewed, plan["frozen_experiment"])
    calibration.require(current == reviewed or (allow_candidate_runtime and current == deployable),
                        "Runtime configuration changed since the proposal was reviewed.")
    return reviewed


def _approved_snapshot(row):
    """Audit-only comments advance the inbox revision, not the approved artifact."""
    revision = (row.get("approval") or {}).get("proposal_revision")
    if type(revision) is not int or revision < 1 or revision > row["revision"]:
        raise ValidationError("The immutable approval revision is missing or invalid.")
    return {**row, "revision": revision}


def _investigation_provenance(store, row):
    """A deployable boolean rule change still requires prior permission to test."""
    from research import calibrate_intraday as calibration
    plan = row["artifact"]["frozen"]["selection"]
    experiment = plan["frozen_experiment"]
    changed_rules = [
        (group, key) for group, fields in calibration.ALLOWLIST.items()
        for key, value in experiment.get(group, {}).items()
        if fields[key] == "boolean" and value != plan["original_settings"][group][key]
    ]
    if not changed_rules:
        return
    parent_id = row.get("parent_id")
    calibration.require(isinstance(parent_id, str) and bool(parent_id),
                        "Rule changes require a linked, previously approved investigation.")
    parent = store.proposal(parent_id)
    request = parent.get("request") or {}
    calibration.require(parent.get("kind") == "rule"
                        and parent.get("status") in ("investigation_approved", "no_change")
                        and request.get("campaign_id") == row["id"],
                        "Investigation linkage is missing, deferred or revoked.")
    normalized = calibration.experiments({"experiments": [request.get("experiment")]})[0]
    calibration.require(normalized == experiment,
                        "The frozen rule experiment differs from the approved investigation request.")
    frozen_at = dt.datetime.fromisoformat(row["artifact"]["frozen_at"].replace("Z", "+00:00"))
    approvals = [event for event in store.events(proposal_id=parent_id)
                 if event["event"] == "approve_investigation"
                 and (event.get("data") or {}).get("actor") == "operator"]
    calibration.require(any(
        dt.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) < frozen_at
        for event in approvals
    ), "Rule investigation must be approved before the campaign is frozen.")
    return {"proposal_id": parent_id, "campaign_id": row["id"],
            "experiment_sha256": calibration.digest(experiment)}


def _action(store, identifier, body, actor):
    row = store.proposal(identifier)
    if row["revision"] != body.expected_revision:
        raise Conflict("Proposal revision changed; refresh before retrying.")
    action, status = body.action, row["status"]
    allowed = {"comment", "reject", "defer", "resume", "request_experiment",
               "approve_investigation", "approve_deployment", "revoke"}
    if action not in allowed:
        raise ValidationError("Unknown research action.")
    if body.experiment is not None and action != "request_experiment":
        raise ValidationError("experiment is valid only for request_experiment.")
    if action != "approve_deployment" and (
        body.artifact_sha256 is not None or body.expected_policy_revision is not None
    ):
        raise ValidationError("Approval bindings are valid only for approve_deployment.")
    if status == "approved" and action not in ("comment", "revoke", "request_experiment"):
        raise Conflict("Revoke the approved artifact before changing this decision.")
    if status == "rejected" and action not in ("comment", "request_experiment"):
        raise Conflict("Rejected proposals are terminal; request a linked experiment instead.")
    patch, child = {}, None
    data = {"actor": actor}
    if action == "reject":
        patch = {"status": "rejected"}
    elif action == "defer":
        if status == "deferred":
            raise Conflict("Proposal is already deferred.")
        patch = {"status": "deferred"}
        data["previous_status"] = status
    elif action == "resume":
        if status != "deferred":
            raise Conflict("Only deferred proposals can be resumed.")
        previous = store.deferred_status(identifier)
        if previous not in {"evaluating", "ready", "blocked", "no_change",
                            "investigation_requested", "investigation_approved", "needs_engine_support"}:
            raise ValidationError("The durable prior state could not be recovered.")
        patch = {"status": previous}
    elif action == "request_experiment":
        normalized, kind, initial = _experiment(body)
        child_id = uuid.uuid4().hex
        child = {"id": child_id, "kind": kind, "title": (
            normalized["name"] if normalized else "Requested rule investigation"),
            "parent_id": identifier, "status": initial, "artifact": {},
            "request": {"experiment": normalized, "note": body.note, "requested_by": actor}}
        data["child_id"] = child_id
    elif action == "approve_investigation":
        if status != "investigation_requested" or row["kind"] != "rule":
            raise Conflict("Only an awaiting rule investigation can be approved.")
        supported = row.get("request", {}).get("experiment")
        if supported:
            from research.calibrate_intraday import experiments
            experiments({"experiments": [supported]})
        patch = {"status": "investigation_approved" if supported else "needs_engine_support"}
        data["deployment_approved"] = False
    elif action == "approve_deployment":
        if status != "ready":
            raise Conflict("Only ready proposals can be approved for deployment.")
        settings = store.settings()
        if (body.expected_policy_revision is None
                or body.expected_policy_revision != settings["revision"]):
            raise Conflict("The exact current policy revision is required.")
        if not body.artifact_sha256 or body.artifact_sha256 != row["artifact_sha256"]:
            raise Conflict("The exact current artifact fingerprint is required.")
        if not settings["value"].get("risk_policy"):
            raise ValidationError("A reviewed numeric risk policy is required before deployment approval.")
        runtime = _deployment_validation(row, settings)
        investigation = _investigation_provenance(store, row)
        from research.calibration_store import artifact_digest
        patch = {"status": "approved", "approval": {
            "actor": actor, "artifact_sha256": body.artifact_sha256,
            "account_id": os.getenv("IBKR_ACCOUNT", ""),
            "settings_revision": settings["revision"],
            "policy_revision": settings["revision"],
            "proposal_revision": body.expected_revision + 1,
            "runtime_config": runtime, "runtime_config_sha256": artifact_digest(runtime),
            "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            **({"investigation_approval": investigation} if investigation else {}),
        }}
    elif action == "revoke":
        if status != "approved":
            raise Conflict("Only an approved artifact can be revoked.")
        patch = {"status": "rejected"}
        data["warning"] = "Revocation prevents future export; it cannot undo an already applied deployment."
    result = store.update_proposal(identifier, body.expected_revision, patch, action,
                                   note=body.note, data=data, **({"child": child} if child else {}))
    return {"proposal": result, **({"child_id": child["id"]} if child else {})}


@router.post("/api/calibration/proposals/{identifier}/actions")
def action(identifier: str, body: ActionInput, response: Response,
           actor=Depends(authenticate), store=Depends(get_store)):
    _no_store(response)
    return _call(lambda: _action(store, identifier, body, actor))


@router.get("/api/calibration/proposals/{identifier}/deployment")
def deployment(identifier: str, response: Response,
               actor=Depends(authenticate), store=Depends(get_store)):
    _no_store(response)

    def export():
        from research.calibration_deployment import build_deployment_artifact
        row, settings = store.proposal(identifier), store.settings()
        if row["status"] != "approved":
            raise Conflict("Only an explicitly approved artifact can be downloaded.")
        _deployment_validation(row, settings)
        _investigation_provenance(store, row)
        artifact = build_deployment_artifact(_approved_snapshot(row), settings)
        # Commit the export audit under the same revision/policy checks used for
        # approval; a concurrent revoke or policy edit must not return success.
        store.update_proposal(identifier, row["revision"], {}, "deployment_export",
                              data={"actor": actor, "artifact_sha256": row["artifact_sha256"],
                                    "expected_policy_revision": settings["revision"]})
        return artifact

    return _call(export)


@router.post("/api/calibration/verify-deployment")
def verify_deployment(body: DeploymentVerificationInput, response: Response,
                      actor=Depends(authenticate), store=Depends(get_store)):
    _no_store(response)

    def verify():
        from research.calibration_deployment import verify_deployment as verify_manifest
        import research_configuration
        identifier = body.manifest.get("proposal_id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
            raise ValidationError("Manifest requires a valid proposal id.")
        row, settings = store.proposal(identifier), store.settings()
        if row["status"] != "approved":
            raise Conflict("The proposal is not approved or was revoked.")
        _deployment_validation(row, settings, allow_candidate_runtime=True)
        _investigation_provenance(store, row)
        result = verify_manifest(_approved_snapshot(row), settings, body.manifest, body.overlay,
                                 research_configuration.effective_config(),
                                 candidate_config=body.candidate_config, candidate_probe=body.candidate_probe)
        store.update_proposal(identifier, row["revision"], {}, "deployment_export", data={
            "actor": actor, "artifact_sha256": row["artifact_sha256"],
            "expected_policy_revision": settings["revision"], "operation": "verify_deployment",
            **({"candidate_probe_sha256": result["candidate_probe_sha256"]}
               if body.candidate_probe is not None else {}),
        })
        return result

    return _call(verify)
