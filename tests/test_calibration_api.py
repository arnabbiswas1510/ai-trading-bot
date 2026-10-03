"""Isolated research API authorization, state and exact-evidence approval contracts."""
import copy
import importlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FastAPI = pytest.importorskip("fastapi").FastAPI
pytest.importorskip("httpx")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
api = importlib.import_module("backend.calibration_api")
from research.auto_calibration import DEFAULT_SETTINGS, validate_settings
from research.calibration_store import Conflict, StoreUnavailable, artifact_digest

TOKEN = "test-only-calibration-operator-token-123456"
HEADERS = {"Authorization": "Bearer " + TOKEN}
POLICY = {"min_completed_positions": 30, "min_distinct_sessions": 5,
          "min_improvement_usd": 500, "max_drawdown_increase_pp": 0,
          "max_worst_loss_increase_usd": 0, "min_positive_tickers": 3,
          "max_largest_contributor_fraction": 0.4}


class MemoryStore:
    def __init__(self):
        self.config = {"revision": 0, "value": copy.deepcopy(DEFAULT_SETTINGS)}
        self.rows = {"p": {"id": "p", "kind": "parameter", "title": "Research",
                          "status": "ready", "revision": 0, "artifact": {},
                          "artifact_sha256": artifact_digest({}), "request": {}}}
        self.audit = []

    def settings(self):
        return copy.deepcopy(self.config)

    def health(self):
        return None

    def list_proposals(self):
        return list(copy.deepcopy(self.rows).values())

    def proposal(self, identifier):
        return copy.deepcopy(self.rows[identifier])

    def create_proposal(self, row):
        self.rows[row["id"]] = {**copy.deepcopy(row), "revision": 0,
                               "artifact_sha256": artifact_digest(row["artifact"])}
        return self.proposal(row["id"])

    def events(self, proposal_id=None):
        return [event for event in reversed(self.audit) if event["proposal_id"] == proposal_id]

    def deferred_status(self, identifier):
        return next((e["data"]["previous_status"] for e in self.events(identifier)
                     if e["event"] == "defer"), None)

    def update_settings(self, revision, value):
        value = validate_settings(value)
        if revision != self.config["revision"]:
            raise Conflict("Revision changed.")
        self.config = {"revision": revision + 1, "value": value}
        return self.settings()

    def update_proposal(self, identifier, revision, patch, event, note="", data=None, child=None):
        if revision != self.rows[identifier]["revision"]:
            raise Conflict("Revision changed.")
        self.rows[identifier].update(copy.deepcopy(patch), revision=revision + 1)
        self.audit.append({"proposal_id": identifier, "event": event, "note": note, "data": data})
        if child:
            self.rows[child["id"]] = {**copy.deepcopy(child), "revision": 0}
        return self.proposal(identifier)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", TOKEN)
    monkeypatch.setenv("IBKR_ACCOUNT", "test-account")
    monkeypatch.setattr(api.intraday_service, "report_failure", lambda *args: None)
    store = MemoryStore()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_store] = lambda: store
    with TestClient(app) as client:
        yield client, store


def post(client, action, revision=0, **kwargs):
    return client.post("/api/calibration/proposals/p/actions",
                       json={"expected_revision": revision, "action": action, **kwargs}, headers=HEADERS)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                   {"Authorization": "Basic " + TOKEN}])
def test_unauthorized_actions_and_export(setup, headers):
    client, store = setup
    assert client.post("/api/calibration/proposals/p/actions", json={
        "expected_revision": 0, "action": "reject"}, headers=headers).status_code == 403
    assert client.get("/api/calibration/proposals/p/deployment", headers=headers).status_code == 403
    assert store.rows["p"]["revision"] == 0


def test_short_token_disables_writes_not_reads(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", "short")
    assert post(client, "reject").status_code == 503
    response = client.get("/api/calibration")
    assert response.json()["write_configured"] is False
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text


@pytest.mark.parametrize("body", [
    {"expected_revision": True, "value": {}},
    {"expected_revision": 0, "value": {}, "actor": "admin"},
])
def test_strict_settings_request_schema(setup, body):
    client, _ = setup
    assert client.put("/api/calibration/settings", json=body, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("value", [{"enabled": "true"}, {"training_sessions": True},
                                  {"evaluation_sessions": 1.5}, {"eligible": True},
                                  {"risk_policy": {"min_completed_positions": True}}])
def test_invalid_settings_rejected(setup, value):
    client, store = setup
    response = client.put("/api/calibration/settings", json={
        "expected_revision": 0, "value": value}, headers=HEADERS)
    assert response.status_code == 400
    assert store.config["revision"] == 0


@pytest.mark.parametrize("field,value", [("actor", "alice"), ("eligible", True),
                                        ("expected_revision", True), ("bogus", 1)])
def test_action_unknown_fields_and_boolean_revision_rejected(setup, field, value):
    client, _ = setup
    body = {"expected_revision": 0, "action": "comment", field: value}
    assert client.post("/api/calibration/proposals/p/actions", json=body,
                       headers=HEADERS).status_code == 422


def test_comment_is_not_an_instruction_and_records_shared_identity(setup):
    client, store = setup
    assert post(client, "comment", note="disable all stops").status_code == 200
    assert len(store.rows) == 1
    assert store.rows["p"]["status"] == "ready"
    assert store.audit[0]["data"]["actor"] == "operator"


def test_stale_revision_conflicts(setup):
    client, _ = setup
    assert post(client, "comment").status_code == 200
    assert post(client, "reject").status_code == 409


def test_defer_resume_and_reject(setup):
    client, store = setup
    assert post(client, "defer").status_code == 200
    assert store.rows["p"]["status"] == "deferred"
    assert post(client, "resume", 1).status_code == 200
    assert store.rows["p"]["status"] == "ready"
    assert post(client, "reject", 2).status_code == 200
    assert post(client, "resume", 3).status_code == 409


@pytest.mark.parametrize("experiment,kind,status", [
    ({"name": "numeric", "decision_config": {"min_trigger_score": 70}}, "parameter", "investigation_approved"),
    ({"name": "veto", "disable_ai_veto": True}, "rule", "investigation_requested"),
    ({"name": "scaleout", "exit_config": {"scale_out_enabled": False}}, "rule", "investigation_requested"),
])
def test_strategy_changes_require_prior_investigation(setup, experiment, kind, status):
    client, store = setup
    response = post(client, "request_experiment", experiment=experiment)
    assert response.status_code == 200, response.text
    child = store.rows[response.json()["child_id"]]
    assert child["kind"] == kind and child["status"] == status
    assert child["parent_id"] == "p"
    assert "approval" not in child


def test_text_rule_needs_engine_work_after_approval(setup):
    client, store = setup
    response = post(client, "request_experiment", note="Investigate a new price-pattern rule")
    child_id = response.json()["child_id"]
    response = client.post(f"/api/calibration/proposals/{child_id}/actions",
                           json={"expected_revision": 0, "action": "approve_investigation"}, headers=HEADERS)
    assert response.status_code == 200
    assert store.rows[child_id]["status"] == "needs_engine_support"


def test_approved_artifact_requires_explicit_revoke(setup):
    client, store = setup
    store.rows["p"]["status"] = "approved"
    for action in ("reject", "defer", "approve_deployment", "approve_investigation"):
        assert post(client, action).status_code == 409
    assert post(client, "revoke").status_code == 200
    assert store.rows["p"]["status"] == "rejected"


def test_policy_and_artifact_bindings_required(setup):
    client, store = setup
    assert post(client, "approve_deployment").status_code == 409
    assert post(client, "approve_deployment", expected_policy_revision=1,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 409
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256="a" * 64).status_code == 409


def test_failed_evidence_validation_never_approves(setup, monkeypatch):
    client, store = setup
    store.config["value"]["risk_policy"] = POLICY
    def fail(*args):
        raise ValueError("Evaluation fingerprint mismatch.")
    monkeypatch.setattr(api, "_deployment_validation", fail)
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert store.rows["p"]["status"] == "ready"
    assert not store.audit


def test_approval_identity_and_exact_binding(setup, monkeypatch):
    client, store = setup
    store.config["value"]["risk_policy"] = POLICY
    monkeypatch.setattr(api, "_deployment_validation", lambda *args: None)
    monkeypatch.setattr(api, "_investigation_provenance", lambda *args: None)
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 200
    approval = store.rows["p"]["approval"]
    assert approval["actor"] == "operator"
    assert approval["settings_revision"] == 0
    assert approval["artifact_sha256"] == store.rows["p"]["artifact_sha256"]
    assert approval["account_id"] == "test-account"


def test_absent_risk_policy_never_approves(setup):
    client, store = setup
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "risk policy" in response.text
    assert store.rows["p"]["status"] == "ready"


def test_partial_failure_not_success_and_does_not_leak_details(setup, monkeypatch):
    client, store = setup
    def fail(*args, **kwargs):
        raise StoreUnavailable("https://private-database/key?token=secret")
    monkeypatch.setattr(store, "update_proposal", fail)
    response = post(client, "comment")
    assert response.status_code == 503
    assert "private-database" not in response.text
    assert not store.audit


def test_failed_diagnostic_is_logged_without_private_error_text(setup, monkeypatch, caplog):
    client, store = setup
    def fail_write(*args, **kwargs):
        raise StoreUnavailable("private database")
    def fail_diagnostic(*args, **kwargs):
        raise RuntimeError("secret token https://private-database")
    monkeypatch.setattr(store, "update_proposal", fail_write)
    monkeypatch.setattr(api.intraday_service, "report_failure", fail_diagnostic)
    response = post(client, "comment")
    assert response.status_code == 503
    assert "Calibration diagnostic could not be recorded (RuntimeError)" in caplog.text
    assert "secret token" not in caplog.text
    assert "private-database" not in caplog.text


@pytest.fixture
def evidence(setup, monkeypatch):
    from research import calibrate_intraday as calibration
    from research import auto_calibration
    from research.calibration_deployment import RUNTIME_ENGINE_FILES
    import research_configuration
    from test_auto_calibration import modeled_result

    client, store = setup
    engine = {"sha256": "test-engine", "files": {name: "a" * 64 for name in RUNTIME_ENGINE_FILES},
              "python": [3, 12, 0], "packages": {"numpy": "test-version", "pandas": "3.0.5"}}
    monkeypatch.setattr(calibration, "engine_fingerprint", lambda: copy.deepcopy(engine))
    runtime = research_configuration.effective_config()
    original = {key: copy.deepcopy(runtime[key]) for key in calibration.capture.CONFIG_KEYS}
    original["scope"] = {"source": "recorded"}
    experiment = calibration.experiments({"experiments": [{
        "name": "better", "decision_config": {"min_trigger_score": 70},
    }]})[0]
    result = modeled_result()
    for key, selected in (("baseline", calibration.BASELINE), ("frozen_candidate", experiment)):
        effective, changes = calibration.effective_settings(original, selected)
        result[key]["effective_settings"] = effective
        result[key]["settings_diff"] = changes
    training = {"account_id": "test-account", "input_sha256": "training",
                "observed_start": "2026-09-24T13:30:00+00:00",
                "observed_end": "2026-09-25T20:00:00+00:00",
                "sessions": ["2026-09-24", "2026-09-25"]}
    candidates = {"experiments": [experiment]}
    plan = calibration.seal({
        "artifact_type": "intraday_calibration_selection", "version": calibration.VERSION,
        "recommendation": "research_only_manual_approval", "engine": calibration.engine_fingerprint(),
        "candidate_config": candidates, "candidate_config_sha256": calibration.digest(candidates),
        "candidate_trial_count": 1, "total_trials_including_baseline": 2,
        "original_settings": original, "training": training, "original_capture_evidence": {},
        "training_trials": [copy.deepcopy(result["baseline"]), copy.deepcopy(result["frozen_candidate"])],
        "selected_name": "better", "frozen_experiment": experiment, "limitations": [],
    })
    result["holdout"].update(account_id="test-account", input_sha256="holdout",
                              observed_start="2026-09-28T13:30:00+00:00",
                              observed_end="2026-09-29T20:00:00+00:00")
    result.update(
        artifact_type="intraday_calibration_holdout", version=calibration.VERSION,
        selection_plan_sha256=plan["artifact_sha256"], engine=calibration.engine_fingerprint(),
        selected_name="better", candidate_trial_count=1, holdout_candidates_tested=1,
        training=copy.deepcopy(training), training_baseline=copy.deepcopy(result["baseline"]),
        training_selected=copy.deepcopy(result["frozen_candidate"]), original_settings=original,
    )
    store.config["value"]["risk_policy"] = {
        "min_completed_positions": 2, "min_distinct_sessions": 2,
        "min_improvement_usd": 100, "max_drawdown_increase_pp": .2,
        "max_worst_loss_increase_usd": 10, "min_positive_tickers": 2,
        "max_largest_contributor_fraction": .7,
    }
    artifact = {"frozen": {"selection": plan, "settings": copy.deepcopy(store.config["value"]),
                          "automatic_engine": auto_calibration.automatic_fingerprint(),
                          "risk_policy_sha256": artifact_digest(store.config["value"]["risk_policy"])},
                "settings_revision": 0, "evaluation_complete": True,
                "evaluation_start": "2026-09-28", "evaluation_end": "2026-09-29",
                "evaluation_sessions": ["2026-09-28", "2026-09-29"],
                "frozen_at": "2026-09-25T21:00:00+00:00",
                "last_evaluated_session": "2026-09-29",
                "evaluation": {"evaluation": calibration.seal(result), "eligibility": {"eligible": True}}}
    store.rows["p"].update(artifact=artifact, artifact_sha256=artifact_digest(artifact))
    return client, store


def test_full_evidence_can_be_approved(evidence):
    client, store = evidence
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 200, response.text
    assert store.rows["p"]["approval"]["runtime_config_sha256"]


@pytest.mark.parametrize("mutation", [
    lambda a: a["evaluation"]["evaluation"]["frozen_candidate"]["summary"].update(net_profit=1e9),
    lambda a: a["evaluation"]["evaluation"].update(selection_plan_sha256="wrong"),
    lambda a: a["frozen"]["selection"]["frozen_experiment"]["decision_config"].update(min_trigger_score=99),
])
def test_edited_inner_evidence_rejected_even_with_refreshed_outer_hash(evidence, mutation):
    client, store = evidence
    mutation(store.rows["p"]["artifact"])
    store.rows["p"]["artifact_sha256"] = artifact_digest(store.rows["p"]["artifact"])
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert store.rows["p"]["status"] == "ready"


def test_changed_automatic_engine_invalidates_campaign(evidence):
    client, store = evidence
    store.rows["p"]["artifact"]["frozen"]["automatic_engine"] = {"sha256": "obsolete"}
    store.rows["p"]["artifact_sha256"] = artifact_digest(store.rows["p"]["artifact"])
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "Automatic research engine changed" in response.text


def test_forged_cached_eligible_cannot_override_current_policy(evidence):
    client, store = evidence
    store.config["value"]["risk_policy"]["min_completed_positions"] = 30
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "Risk policy changed" in response.text


@pytest.mark.parametrize("frozen_policy", [None, "missing"])
def test_retroactive_policy_cannot_promote_exploratory_research(evidence, frozen_policy):
    client, store = evidence
    frozen = store.rows["p"]["artifact"]["frozen"]
    if frozen_policy == "missing":
        frozen.pop("settings")
    else:
        frozen["settings"]["risk_policy"] = None
    store.rows["p"]["artifact_sha256"] = artifact_digest(store.rows["p"]["artifact"])
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "campaign" in response.text
    assert store.rows["p"]["status"] == "ready"


def test_relaxed_risk_policy_requires_new_campaign(evidence):
    client, store = evidence
    store.config["value"]["risk_policy"]["min_improvement_usd"] = 1
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "future evaluation evidence" in response.text


@pytest.mark.parametrize("field,value", [
    ("evaluation_complete", False), ("last_evaluated_session", "2026-09-28"),
    ("settings_revision", 1),
    ("evaluation_sessions", ["2026-09-29"]),
    ("frozen_at", "2026-09-29T21:00:00+00:00"),
])
def test_incomplete_or_stale_campaign_cannot_be_approved(evidence, field, value):
    client, store = evidence
    store.rows["p"]["artifact"][field] = value
    store.rows["p"]["artifact_sha256"] = artifact_digest(store.rows["p"]["artifact"])
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert store.rows["p"]["status"] == "ready"


def test_current_runtime_drift_invalidates_approval(evidence, monkeypatch):
    import research_configuration
    client, store = evidence
    changed = research_configuration.effective_config()
    changed["decision_config"]["min_trigger_score"] = 99
    monkeypatch.setattr(research_configuration, "effective_config", lambda: changed)
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert "baseline" in response.text


@pytest.mark.parametrize("configured", [None, "", " ", "other-account"])
def test_unset_or_different_brokerage_account_cannot_approve(evidence, monkeypatch, configured):
    client, store = evidence
    if configured is None:
        monkeypatch.delenv("IBKR_ACCOUNT", raising=False)
    else:
        monkeypatch.setenv("IBKR_ACCOUNT", configured)
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 400
    assert store.rows["p"]["status"] == "ready"
    assert "other-account" not in response.text


def test_account_binding_rejects_post_approval_drift(evidence, monkeypatch, tmp_path):
    from research import calibration_deployment as deploy
    client, store = evidence
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / deploy.ENV_FILE).write_text("# inactive\n")
    (tmp_path / deploy.MANIFEST_FILE).write_text('{"active":false}\n')
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    artifact = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert artifact.status_code == 200, artifact.text
    assert artifact.json()["manifest"]["account_sha256"] == artifact_digest("test-account")
    assert "test-account" not in artifact.text
    monkeypatch.setenv("IBKR_ACCOUNT", "other-account")
    assert client.get("/api/calibration/proposals/p/deployment", headers=HEADERS).status_code == 400
    assert client.post("/api/calibration/verify-deployment", headers=HEADERS, json={
        "manifest": artifact.json()["manifest"], "overlay": artifact.json()["overlay"],
    }).status_code == 400


def test_private_approval_account_binding_cannot_be_edited(evidence):
    client, store = evidence
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    store.rows["p"]["approval"]["account_id"] = "other-account"
    response = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert response.status_code == 400
    assert "brokerage account changed" in response.text


@pytest.fixture
def candidate_verification(evidence, monkeypatch, tmp_path):
    from research import calibration_deployment as deploy
    client, store = evidence
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / deploy.ENV_FILE).write_text("# inactive\n")
    (tmp_path / deploy.MANIFEST_FILE).write_text('{"active":false}\n')
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    exported = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert exported.status_code == 200, exported.text
    row = store.rows["p"]
    frozen = row["artifact"]["frozen"]
    engine = frozen["selection"]["engine"]
    snapshot = {"engine": engine, "automatic_engine": frozen["automatic_engine"]}
    identity = deploy.runtime_engine_identity(engine)
    identity["files"].update({name: "b" * 64 for name in deploy.LIVE_ORCHESTRATOR_FILES})
    identity["packages"] = {"pandas": "2.2.2", "numpy": "live-version", "ib-insync": "live-version"}
    probe = {"execution": {
        "config": deploy.candidate_configuration(row["approval"]["runtime_config"],
                                                 frozen["selection"]["frozen_experiment"]),
        "account_id": "test-account", "runtime_identity": copy.deepcopy(identity),
    }, "current_execution": {
        "config": copy.deepcopy(row["approval"]["runtime_config"]),
        "account_id": "test-account", "runtime_identity": copy.deepcopy(identity),
    }, "trading_bot": copy.deepcopy(snapshot), "calibration_worker": copy.deepcopy(snapshot)}
    body = {"manifest": exported.json()["manifest"], "overlay": exported.json()["overlay"],
            "candidate_probe": probe}
    return client, store, body


def test_final_verification_binds_all_candidate_image_evidence(candidate_verification):
    client, store, body = candidate_verification
    response = client.post("/api/calibration/verify-deployment", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    expected = artifact_digest(body["candidate_probe"])
    assert response.json()["candidate_probe_sha256"] == expected
    assert store.audit[-1]["data"]["candidate_probe_sha256"] == expected
    assert body["candidate_probe"]["execution"]["runtime_identity"]["packages"]["pandas"] == "2.2.2"
    assert body["candidate_probe"]["trading_bot"]["engine"]["packages"]["pandas"] == "3.0.5"


@pytest.mark.parametrize("mutation", [
    lambda probe: probe.pop("calibration_worker"),
    lambda probe: probe.pop("current_execution"),
    lambda probe: probe["current_execution"].update(account_id="other-account"),
    lambda probe: probe["current_execution"]["runtime_identity"]["packages"].update(pandas="unapproved-upgrade"),
    lambda probe: probe["execution"].update(account_id="other-account"),
    lambda probe: probe["execution"].pop("account_id"),
    lambda probe: probe["execution"]["runtime_identity"].update(python=[3, 13, 0]),
    lambda probe: probe["execution"]["config"]["decision_config"].update(min_trigger_score=99),
    lambda probe: probe["trading_bot"]["engine"].update(python=[3, 13, 0]),
    lambda probe: probe["calibration_worker"]["automatic_engine"].update(sha256="wrong-engine"),
])
def test_final_verification_rejects_new_engine_account_or_incomplete_proof(candidate_verification, mutation):
    client, store, body = candidate_verification
    previous_events = len(store.audit)
    mutation(body["candidate_probe"])
    response = client.post("/api/calibration/verify-deployment", headers=HEADERS, json=body)
    assert response.status_code == 400, response.text
    assert len(store.audit) == previous_events


def test_verify_endpoint_requires_authentication(setup):
    client, _ = setup
    response = client.post("/api/calibration/verify-deployment", json={"manifest": {}, "overlay": ""})
    assert response.status_code == 403


def test_ready_artifact_is_not_downloadable(setup):
    client, _ = setup
    assert client.get("/api/calibration/proposals/p/deployment", headers=HEADERS).status_code == 409


def test_download_stays_bound_to_approval_across_comments_and_exports(evidence, monkeypatch, tmp_path):
    from research import calibration_deployment as deploy
    client, store = evidence
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / deploy.ENV_FILE).write_text("# inactive\n")
    (tmp_path / deploy.MANIFEST_FILE).write_text('{"active":false}\n')
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    first = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert first.status_code == 200, first.text
    assert post(client, "comment", store.rows["p"]["revision"], note="Reviewed again").status_code == 200
    second = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    payload = second.json()
    verification = client.post("/api/calibration/verify-deployment", headers=HEADERS, json={
        "manifest": payload["manifest"], "overlay": payload["overlay"],
    })
    assert verification.status_code == 200, verification.text
    assert verification.json()["verified"] is True
    payload["overlay"] += "TRADING_RUNTIME_MODE=live\n"
    assert client.post("/api/calibration/verify-deployment", headers=HEADERS, json={
        "manifest": payload["manifest"], "overlay": payload["overlay"],
    }).status_code == 400


def test_export_audit_failure_does_not_return_artifact(evidence, monkeypatch, tmp_path):
    from research import calibration_deployment as deploy
    client, store = evidence
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / deploy.ENV_FILE).write_text("# inactive\n")
    (tmp_path / deploy.MANIFEST_FILE).write_text('{"active":false}\n')
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    def fail(*args, **kwargs):
        raise Conflict("Approval revoked concurrently.")
    monkeypatch.setattr(store, "update_proposal", fail)
    response = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS)
    assert response.status_code == 409
    assert "patch" not in response.json()


def test_changed_policy_blocks_export_and_revocation_blocks_verification(evidence, monkeypatch, tmp_path):
    from research import calibration_deployment as deploy
    client, store = evidence
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / deploy.ENV_FILE).write_text("# inactive\n")
    (tmp_path / deploy.MANIFEST_FILE).write_text('{"active":false}\n')
    assert post(client, "approve_deployment", expected_policy_revision=0,
                artifact_sha256=store.rows["p"]["artifact_sha256"]).status_code == 200
    payload = client.get("/api/calibration/proposals/p/deployment", headers=HEADERS).json()
    store.config["revision"] += 1
    assert client.get("/api/calibration/proposals/p/deployment", headers=HEADERS).status_code == 400
    assert post(client, "revoke", store.rows["p"]["revision"]).status_code == 200
    assert client.post("/api/calibration/verify-deployment", headers=HEADERS, json={
        "manifest": payload["manifest"], "overlay": payload["overlay"],
    }).status_code == 409


@pytest.fixture
def rule_provenance(setup):
    from research import calibrate_intraday as calibration
    _, store = setup
    experiment = calibration.experiments({"experiments": [{
        "name": "scaleout", "exit_config": {"scale_out_enabled": False},
    }]})[0]
    row = {"id": "p", "parent_id": "investigation", "artifact": {
        "frozen_at": "2026-09-25T21:00:00+00:00",
        "frozen": {"selection": {"frozen_experiment": experiment,
                                "original_settings": {"exit_config": {"scale_out_enabled": True}}}},
    }}
    store.rows["investigation"] = {
        "kind": "rule", "status": "no_change", "request": {
            "experiment": experiment, "campaign_id": "p",
        },
    }
    store.audit.append({"proposal_id": "investigation", "event": "approve_investigation",
                        "created_at": "2026-09-25T20:00:00+00:00", "data": {"actor": "operator"}})
    return store, row


def test_rule_provenance_accepts_exact_prior_approved_investigation(rule_provenance):
    _, row = rule_provenance
    assert api._investigation_provenance(*rule_provenance) == {
        "proposal_id": "investigation", "campaign_id": "p",
        "experiment_sha256": artifact_digest(row["artifact"]["frozen"]["selection"]["frozen_experiment"]),
    }


def test_rule_deployment_approval_binds_verified_investigation(setup, rule_provenance, monkeypatch):
    client, store = setup
    _, row = rule_provenance
    store.rows["p"] = {**row, "revision": 0, "status": "ready",
                       "artifact_sha256": artifact_digest(row["artifact"])}
    store.config["value"]["risk_policy"] = POLICY
    monkeypatch.setattr(api, "_deployment_validation", lambda *args: {"runtime": {}})
    response = post(client, "approve_deployment", expected_policy_revision=0,
                    artifact_sha256=store.rows["p"]["artifact_sha256"])
    assert response.status_code == 200, response.text
    assert store.rows["p"]["approval"]["investigation_approval"] == {
        "proposal_id": "investigation", "campaign_id": "p",
        "experiment_sha256": artifact_digest(row["artifact"]["frozen"]["selection"]["frozen_experiment"]),
    }


@pytest.mark.parametrize("mutation", [
    lambda s, r: r.update(parent_id=None),
    lambda s, r: s.rows["investigation"].update(status="rejected"),
    lambda s, r: s.rows["investigation"]["request"].update(campaign_id="other"),
    lambda s, r: s.rows["investigation"]["request"].update(experiment={"name": "other"}),
    lambda s, r: s.audit.clear(),
    lambda s, r: s.audit[0].update(created_at="2026-09-26T20:00:00+00:00"),
    lambda s, r: s.audit[0].update(data={"actor": "invented-person"}),
])
def test_rule_provenance_rejects_unapproved_replaced_or_late_investigation(rule_provenance, mutation):
    store, row = rule_provenance
    mutation(store, row)
    with pytest.raises(ValueError):
        api._investigation_provenance(store, row)


@pytest.mark.parametrize("experiment,kind,status", [
    ({"name": "numeric", "decision_config": {"min_trigger_score": 70}},
     "parameter", "investigation_approved"),
    ({"name": "rule", "exit_config": {"scale_out_enabled": False}},
     "rule", "investigation_requested"),
    (None, "rule", "investigation_requested"),
])
def test_empty_inbox_accepts_root_research_requests(setup, experiment, kind, status):
    client, store = setup
    store.rows.clear()
    response = client.post("/api/calibration/experiments", headers=HEADERS, json={
        "action": "request_experiment", "expected_revision": 0,
        "experiment": experiment, "note": "Investigate this hypothesis.",
    })
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["kind"] == kind
    assert proposal["status"] == status
    assert proposal["parent_id"] is None
    assert proposal["request"]["requested_by"] == "operator"
    assert "approval" not in proposal


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_root_experiment_requires_authentication(setup, headers):
    client, store = setup
    response = client.post("/api/calibration/experiments", headers=headers, json={
        "action": "request_experiment", "expected_revision": 0, "note": "New rule",
    })
    assert response.status_code == 403
    assert len(store.rows) == 1


@pytest.mark.parametrize("extra", [
    {"action": "approve_deployment"}, {"expected_revision": 1},
    {"artifact_sha256": "a" * 64}, {"expected_policy_revision": 0},
])
def test_root_request_rejects_deployment_actions_and_bindings(setup, extra):
    client, store = setup
    response = client.post("/api/calibration/experiments", headers=HEADERS, json={
        "action": "request_experiment", "expected_revision": 0, "note": "New rule", **extra,
    })
    assert response.status_code == 400
    assert len(store.rows) == 1


def test_root_request_storage_failure_is_not_success(setup, monkeypatch):
    client, store = setup
    def fail(*args):
        raise StoreUnavailable("private DB")
    monkeypatch.setattr(store, "create_proposal", fail)
    response = client.post("/api/calibration/experiments", headers=HEADERS, json={
        "action": "request_experiment", "expected_revision": 0, "note": "New rule",
    })
    assert response.status_code == 503
    assert "private DB" not in response.text


@pytest.mark.parametrize("path", ["/api/calibration/experiments", "/api/calibration/proposals/p/actions"])
@pytest.mark.parametrize("key", ["min_trigger_score", "min_pre_breakout_score", "min_relaxed_trigger_score"])
def test_fractional_score_request_cannot_poison_worker_queue(setup, path, key):
    client, store = setup
    response = client.post(path, headers=HEADERS, json={
        "action": "request_experiment", "expected_revision": 0,
        "experiment": {"name": "fractional", "decision_config": {key: 65.5}},
    })
    assert response.status_code == 400
    assert "integer score" in response.text
    assert len(store.rows) == 1
    assert not store.audit
    assert store.rows["p"]["revision"] == 0


@pytest.mark.parametrize("key", ["min_trigger_score", "min_pre_breakout_score", "min_relaxed_trigger_score"])
def test_integral_float_score_is_normalized_before_queueing(setup, key):
    client, store = setup
    response = client.post("/api/calibration/experiments", headers=HEADERS, json={
        "action": "request_experiment", "expected_revision": 0,
        "experiment": {"name": "integral", "decision_config": {key: 65.0}},
    })
    assert response.status_code == 200, response.text
    row = store.rows[response.json()["proposal"]["id"]]
    value = row["request"]["experiment"]["decision_config"][key]
    assert type(value) is int
    assert value == 65


def test_top_level_web_image_layout_imports_without_backend_package(tmp_path):
    root = Path(__file__).parents[1]
    image = tmp_path / "web-image"
    image.mkdir()
    for source in (root / "backend").glob("*.py"):
        shutil.copyfile(source, image / source.name)
    for line in (root / "Dockerfile").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line)
        if not words or words[0] != "COPY" or not words[-1].startswith("./backend/"):
            continue
        destination = Path(words[-1].removeprefix("./backend/"))
        for filename in words[1:-1]:
            source = root / filename
            if not source.is_file() or source.suffix != ".py":
                continue
            target = image / destination
            if target.suffix != ".py":
                target /= source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"SUPABASE_URL", "SUPABASE_KEY", "INTRADAY_SUPABASE_KEY",
                                  "FMP_API_KEY", "PYTHONPATH"}}
    environment["DB_PATH"] = str(image / "isolated.sqlite3")
    script = (
        "import importlib.util, os, site, sys; sys.path.append(site.getusersitepackages()); "
        "sys.path.insert(0, os.getcwd()); "
        "assert importlib.util.find_spec('backend') is None; "
        "import calibration_api; "
        "assert calibration_api.__package__ == ''; "
        "assert calibration_api.intraday_service.__name__ == 'intraday_service'; "
        "assert any(r.path == '/api/calibration/experiments' for r in calibration_api.router.routes)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=image,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
