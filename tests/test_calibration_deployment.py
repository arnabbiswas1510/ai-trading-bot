"""Offline approval deployment tests: no broker, production, or Git remote calls."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from research import calibration_deployment as deploy
from scripts import apply_calibration_artifact as operator
from scripts import validate_calibration_deployment as preflight

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def work():
    path = ROOT / f".calibration-deploy-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def signed(document):
    return {**document, "artifact_sha256": deploy.digest(document)}


@pytest.fixture
def reviewed(work, monkeypatch):
    config = {
        "decision_config": {
            "min_trigger_score": 60, "min_pre_breakout_score": 65,
            "min_relaxed_trigger_score": 58, "min_vol_surge_gate": .75,
            "max_pivot_extension": .05, "max_pivot_breakdown": .02,
            "max_pre_breakout_pivot_dist": .05, "max_positions": 5,
            "min_position_size": 5000, "price_safety_reserve": 1000,
        },
        "exit_config": {
            "armed_exit_deadline_hours": 3.25, "scale_out_enabled": True,
            "scale_out_trigger_pct": .04, "scale_out_fraction": .33, "stop_loss_pct": .07,
        },
        "replay_config": {"cooling_off_days": 7},
        "shared_exit_rules": {"PROVE_IT_ENABLED": True},
        "costs": {"commission": .001},
        "runtime": {"MARKET_DIRECTION_BUFFER_PCT": .005},
        "costs_semantics": "simulation_assumptions_not_measured_live_costs",
    }
    selection = signed({
        "original_settings": {k: config[k] for k in deploy.CONFIG_KEYS},
        "training": {"account_id": "U_TEST_APPROVED"},
        "engine": {
            "files": {name: "a" * 64 for name in deploy.RUNTIME_ENGINE_FILES},
            "python": [3, 12, 12],
            "packages": {"exchange-calendars": "4.0", "pandas": "2.0", "numpy": "1.0"},
        },
        "frozen_experiment": {"name": "score70", "disable_ai_veto": False,
                              "decision_config": {"min_trigger_score": 70}, "exit_config": {}},
    })
    evaluation = signed({"selection_plan_sha256": selection["artifact_sha256"]})
    policy = {"max_drawdown_increase": 0}
    artifact = {
        "frozen": {"selection": selection, "settings": {"risk_policy": policy},
                   "automatic_engine": {"sha256": "b" * 64},
                   "risk_policy_sha256": deploy.digest(policy)},
        "evaluation": {"evaluation": evaluation, "eligibility": {"eligible": True}},
        "settings_revision": 2, "evaluation_complete": True,
        "evaluation_end": "2026-10-02", "last_evaluated_session": "2026-10-02",
    }
    proposal = {
        "id": "proposal-123", "revision": 4, "status": "approved", "artifact": artifact,
        "artifact_sha256": deploy.digest(artifact),
        "approval": {
            "artifact_sha256": deploy.digest(artifact), "policy_revision": 2,
            "approved_at": "2026-10-03T15:00:00Z", "proposal_revision": 4,
            "runtime_config": config, "runtime_config_sha256": deploy.config_digest(config),
            "account_id": "U_TEST_APPROVED",
        },
    }
    (work / deploy.ENV_FILE).write_text("# Inactive\n")
    (work / deploy.MANIFEST_FILE).write_text('{"active": false, "schema_version": 1}\n')
    monkeypatch.setattr(deploy, "ROOT", work)
    return proposal, {"revision": 2, "value": {"risk_policy": copy.deepcopy(policy)}}, config


def refresh(proposal):
    selection = proposal["artifact"]["frozen"]["selection"]
    selection["artifact_sha256"] = deploy.digest({k: v for k, v in selection.items()
                                                  if k != "artifact_sha256"})
    evaluation = proposal["artifact"]["evaluation"]["evaluation"]
    evaluation["selection_plan_sha256"] = selection["artifact_sha256"]
    evaluation["artifact_sha256"] = deploy.digest({k: v for k, v in evaluation.items()
                                                   if k != "artifact_sha256"})
    proposal["artifact_sha256"] = deploy.digest(proposal["artifact"])
    proposal["approval"]["artifact_sha256"] = proposal["artifact_sha256"]


def test_artifact_is_exact_two_file_patch_and_no_permissions(reviewed):
    proposal, settings, config = reviewed
    result = deploy.build_deployment_artifact(proposal, settings)
    assert result["sha256"] == deploy.text_digest(result["patch"])
    assert result["patch"].count("diff --git ") == 2
    assert "MIN_TRIGGER_SCORE=70\n" in result["overlay"]
    assert result["manifest"]["baseline_config_sha256"] == deploy.config_digest(config)
    assert result["manifest"]["account_sha256"] == deploy.digest("U_TEST_APPROVED")
    assert "U_TEST_APPROVED" not in result["patch"]
    for forbidden in ("TRADING_RUNTIME_MODE=", "live_entries_enabled=", "MAX_POSITIONS=",
                      "STOP_LOSS_PCT=", "TRADING_CONTROL_TOKEN="):
        assert forbidden not in result["overlay"]
    verified = deploy.verify_deployment(
        proposal, settings, result["manifest"], result["overlay"], config)
    assert verified == {"verified": True, "manifest_sha256": deploy.digest(result["manifest"])}


def test_generated_patch_actually_applies_to_exact_prior_files(reviewed, work):
    result = deploy.build_deployment_artifact(*reviewed[:2])
    subprocess.run(["git", "init", "--quiet", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "apply", "--check", "-"], cwd=work, input=result["patch"],
                   text=True, check=True, capture_output=True)
    subprocess.run(["git", "apply", "-"], cwd=work, input=result["patch"],
                   text=True, check=True, capture_output=True)
    assert (work / deploy.ENV_FILE).read_text() == result["overlay"]
    assert json.loads((work / deploy.MANIFEST_FILE).read_text()) == result["manifest"]


@pytest.mark.parametrize("mutation", [
    lambda p, s: p.update(status="evaluated"),
    lambda p, s: p["artifact"]["evaluation"]["eligibility"].update(eligible=False),
    lambda p, s: p["approval"].update(artifact_sha256="edited"),
    lambda p, s: s.update(revision=3),
    lambda p, s: p.update(revision=3),
    lambda p, s: p["approval"].pop("runtime_config"),
    lambda p, s: p["approval"].pop("account_id"),
    lambda p, s: p["approval"].update(account_id="U_OTHER"),
    lambda p, s: p["approval"]["runtime_config"]["runtime"].update(MARKET_DIRECTION_BUFFER_PCT=.02),
])
def test_unapproved_edited_stale_missing_baseline_rejected(reviewed, mutation):
    proposal, settings, _ = reviewed
    mutation(proposal, settings)
    with pytest.raises(deploy.DeploymentError):
        deploy.build_deployment_artifact(proposal, settings)


@pytest.mark.parametrize("group,key,value", [
    ("decision_config", "min_trigger_score", 70.1),
    ("decision_config", "min_trigger_score", True),
    ("decision_config", "min_vol_surge_gate", float("inf")),
    ("decision_config", "max_positions", 10),
    ("decision_config", "live_entries_enabled", True),
    ("exit_config", "stop_loss_pct", .9),
    ("exit_config", "scale_out_enabled", "true"),
    ("exit_config", "scale_out_fraction", .999),
])
def test_restricted_and_nonrepresentable_overrides_reject(reviewed, group, key, value):
    original = reviewed[2]
    experiment = {"name": "bad", group: {key: value}}
    with pytest.raises(deploy.DeploymentError):
        deploy.candidate_configuration(original, experiment)


def test_ai_veto_has_no_live_switch_and_noop_rejects(reviewed):
    with pytest.raises(deploy.DeploymentError, match="no verified"):
        deploy.candidate_configuration(reviewed[2], {"disable_ai_veto": True})
    with pytest.raises(deploy.DeploymentError, match="no deployable"):
        deploy.candidate_configuration(reviewed[2], {"name": "baseline"})


def test_scale_out_change_requires_exact_bound_investigation(reviewed):
    proposal, settings, _ = reviewed
    experiment = proposal["artifact"]["frozen"]["selection"]["frozen_experiment"]
    experiment["exit_config"]["scale_out_enabled"] = False
    refresh(proposal)
    with pytest.raises(deploy.DeploymentError, match="investigation provenance"):
        deploy.build_deployment_artifact(proposal, settings)
    proposal["parent_id"] = "reviewed-investigation"
    proposal["approval"]["investigation_approval"] = {
        "proposal_id": proposal["parent_id"], "campaign_id": proposal["id"],
        "experiment_sha256": deploy.digest(experiment),
    }
    assert "SCALE_OUT_ENABLED=false\n" in deploy.build_deployment_artifact(proposal, settings)["overlay"]
    proposal["approval"]["investigation_approval"]["experiment_sha256"] = "other-experiment"
    with pytest.raises(deploy.DeploymentError, match="investigation provenance"):
        deploy.build_deployment_artifact(proposal, settings)


def test_candidate_normalizes_live_loader_numeric_types(reviewed):
    candidate = deploy.candidate_configuration(reviewed[2], {
        "decision_config": {"min_trigger_score": 70.0},
        "exit_config": {"armed_exit_deadline_hours": 2}})
    assert type(candidate["decision_config"]["min_trigger_score"]) is int
    assert type(candidate["exit_config"]["armed_exit_deadline_hours"]) is float


def test_append_only_comments_and_audits_preserve_immutable_approval_binding(reviewed):
    proposal, settings, config = reviewed
    before = deploy.build_deployment_artifact(proposal, settings)
    proposal["revision"] += 3
    after = deploy.build_deployment_artifact(proposal, settings)
    assert before == after
    assert deploy.verify_deployment(proposal, settings, before["manifest"],
                                    before["overlay"], config)["verified"]


def test_changed_risk_baseline_and_overlay_edits_block_activation(reviewed):
    proposal, settings, config = reviewed
    result = deploy.build_deployment_artifact(proposal, settings)
    current = copy.deepcopy(config)
    current["decision_config"]["max_positions"] = 6
    with pytest.raises(deploy.DeploymentError, match="drifted"):
        deploy.verify_deployment(proposal, settings, result["manifest"], result["overlay"], current)
    with pytest.raises(deploy.DeploymentError, match="Overlay"):
        deploy.verify_deployment(proposal, settings, result["manifest"],
                                 result["overlay"] + "TRADING_RUNTIME_MODE=live\n", config)


def test_candidate_image_and_idempotent_restart_verified(reviewed):
    proposal, settings, baseline = reviewed
    result = deploy.build_deployment_artifact(proposal, settings)
    candidate = deploy.candidate_configuration(
        baseline, proposal["artifact"]["frozen"]["selection"]["frozen_experiment"])
    assert deploy.verify_deployment(proposal, settings, result["manifest"],
                                    result["overlay"], candidate, candidate)["verified"]
    with pytest.raises(deploy.DeploymentError, match="Candidate image"):
        deploy.verify_deployment(proposal, settings, result["manifest"], result["overlay"],
                                 baseline, baseline)
    candidate["runtime"]["MARKET_DIRECTION_BUFFER_PCT"] = .02
    with pytest.raises(deploy.DeploymentError, match="Candidate image"):
        deploy.verify_deployment(proposal, settings, result["manifest"], result["overlay"],
                                 baseline, candidate)


def candidate_probe(proposal, baseline):
    frozen = proposal["artifact"]["frozen"]
    candidate = deploy.candidate_configuration(baseline, frozen["selection"]["frozen_experiment"])
    research = {"automatic_engine": frozen["automatic_engine"], "engine": frozen["selection"]["engine"]}
    live_identity = deploy.runtime_engine_identity(research["engine"])
    live_identity["files"].update({name: "d" * 64 for name in deploy.LIVE_ORCHESTRATOR_FILES})
    live_identity["packages"] = {"pandas": "2.2.2", "ib-insync": "0.9.86"}
    return copy.deepcopy({
        "current_execution": {"config": baseline, "account_id": proposal["approval"]["account_id"],
                              "runtime_identity": live_identity},
        "execution": {"config": candidate, "account_id": proposal["approval"]["account_id"],
                      "runtime_identity": live_identity},
        "trading_bot": research, "calibration_worker": research,
    })


def test_complete_candidate_probe_matches_approval_and_receives_bound_acknowledgement(reviewed):
    proposal, settings, baseline = reviewed
    artifact = deploy.build_deployment_artifact(proposal, settings)
    probe = candidate_probe(proposal, baseline)
    verified = deploy.verify_deployment(
        proposal, settings, artifact["manifest"], artifact["overlay"], baseline, candidate_probe=probe)
    assert verified["candidate_probe_sha256"] == deploy.digest(probe)
    assert probe["execution"]["runtime_identity"]["packages"] != probe["trading_bot"]["engine"]["packages"]


def test_matching_live_sources_still_must_match_frozen_shared_research_code(reviewed):
    proposal, settings, baseline = reviewed
    artifact = deploy.build_deployment_artifact(proposal, settings)
    probe = candidate_probe(proposal, baseline)
    for name in ("execution", "current_execution"):
        probe[name]["runtime_identity"]["files"]["exit_core.py"] = "e" * 64
    with pytest.raises(deploy.DeploymentError, match="frozen research"):
        deploy.verify_deployment(proposal, settings, artifact["manifest"], artifact["overlay"],
                                 baseline, candidate_probe=probe)


def test_host_and_verifier_require_same_runtime_sources():
    assert preflight.RUNTIME_ENGINE_FILES == deploy.RUNTIME_ENGINE_FILES
    assert preflight.LIVE_ORCHESTRATOR_FILES == deploy.LIVE_ORCHESTRATOR_FILES


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop("execution"),
    lambda p: p.pop("current_execution"),
    lambda p: p.pop("trading_bot"),
    lambda p: p["execution"].pop("account_id"),
    lambda p: p["execution"].update(account_id=""),
    lambda p: p["execution"].update(account_id="U_OTHER"),
    lambda p: p["current_execution"].update(account_id="U_OTHER"),
    lambda p: p["current_execution"]["config"]["decision_config"].update(max_positions=6),
    lambda p: p["execution"]["runtime_identity"]["python"].__setitem__(2, 99),
    lambda p: p["execution"]["runtime_identity"]["packages"].update(numpy="other"),
    lambda p: p["execution"]["runtime_identity"]["files"].update({"exit_core.py": "c" * 64}),
    lambda p: p["execution"]["runtime_identity"]["files"].update({"execution_agent.py": "c" * 64}),
    lambda p: p["trading_bot"]["engine"]["python"].__setitem__(2, 99),
    lambda p: p["trading_bot"]["engine"]["packages"].update(pandas="other"),
    lambda p: p["trading_bot"]["automatic_engine"].update(sha256="other"),
    lambda p: p["calibration_worker"].pop("engine"),
    lambda p: p["calibration_worker"]["engine"]["files"].update({"exit_core.py": "other"}),
    lambda p: p["calibration_worker"]["automatic_engine"].update(sha256="other"),
])
def test_engine_dependency_python_and_account_drift_fail_before_recreate(reviewed, mutation):
    proposal, settings, baseline = reviewed
    artifact = deploy.build_deployment_artifact(proposal, settings)
    probe = candidate_probe(proposal, baseline)
    # Break intentional same-state aliases before simulating one changed image.
    probe = json.loads(json.dumps(probe))
    mutation(probe)
    with pytest.raises(deploy.DeploymentError):
        deploy.verify_deployment(proposal, settings, artifact["manifest"], artifact["overlay"],
                                 baseline, candidate_probe=probe)


def test_recorded_baseline_must_match_reviewed_runtime(reviewed):
    proposal, settings, _ = reviewed
    proposal["artifact"]["frozen"]["selection"]["original_settings"] = copy.deepcopy(
        proposal["artifact"]["frozen"]["selection"]["original_settings"])
    proposal["artifact"]["frozen"]["selection"]["original_settings"]["decision_config"]["max_positions"] = 6
    refresh(proposal)
    with pytest.raises(deploy.DeploymentError, match="recorded evaluation baseline"):
        deploy.build_deployment_artifact(proposal, settings)


@pytest.mark.parametrize("mutation", [
    lambda a, s: a["frozen"]["settings"].update(risk_policy=None),
    lambda a, s: a["frozen"].update(risk_policy_sha256="edited"),
    lambda a, s: s["value"]["risk_policy"].update(max_drawdown_increase=100),
    lambda a, s: a.update(settings_revision=1),
    lambda a, s: a.update(evaluation_complete=False),
    lambda a, s: a.update(last_evaluated_session="2026-10-01"),
])
def test_posthoc_policy_and_early_stopping_cannot_qualify(reviewed, mutation):
    proposal, settings, _ = reviewed
    mutation(proposal["artifact"], settings)
    refresh(proposal)
    with pytest.raises(deploy.DeploymentError):
        deploy.build_deployment_artifact(proposal, settings)


def test_host_inactive_preserves_env_but_cannot_silently_rollback(work):
    (work / deploy.ENV_FILE).write_text("# inactive\n")
    (work / deploy.MANIFEST_FILE).write_text('{"active": false, "schema_version": 1}')
    assert "inactive" in preflight.validate(work)
    (work / deploy.ENV_FILE).write_text("MIN_TRIGGER_SCORE=99\n")
    with pytest.raises(ValueError, match="Inactive"):
        preflight.validate(work)
    (work / deploy.ENV_FILE).write_text("# inactive\n")
    (work / ".approved_strategy_activated.json").write_text("{}")
    with pytest.raises(ValueError, match="Rollback"):
        preflight.validate(work)
    (work / deploy.ENV_FILE).unlink()
    (work / deploy.MANIFEST_FILE).unlink()
    with pytest.raises(ValueError, match="cannot be removed"):
        preflight.validate(work)


def test_secret_file_is_parsed_not_executed(work, monkeypatch):
    monkeypatch.delenv("TRADING_CONTROL_TOKEN", raising=False)
    (work / ".env").write_text('touch should-not-exist\nTRADING_CONTROL_TOKEN="' + "x" * 40 + '"\n')
    assert preflight.read_token(work) == "x" * 40
    assert not (work / "should-not-exist").exists()


def test_plaintext_remote_and_redirect_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        preflight.verify_remote("http://192.168.1.2:8000", "x" * 40, {}, "")
    with pytest.raises(ValueError, match="redirects"):
        preflight.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")


def test_failed_approval_never_runs_candidate_container(reviewed, work, monkeypatch):
    result = deploy.build_deployment_artifact(*reviewed[:2])
    (work / deploy.ENV_FILE).write_text(result["overlay"])
    (work / deploy.MANIFEST_FILE).write_text(deploy.manifest_text(result["manifest"]))
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", "x" * 40)
    monkeypatch.setattr(preflight, "verify_remote", lambda *a: (_ for _ in ()).throw(ValueError("stale")))
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="stale"):
        preflight.validate(work)
    assert not (work / ".approved_strategy_activated.json").exists()


def test_successful_preflight_checks_candidate_and_records_nonsecret_latch(reviewed, work, monkeypatch):
    proposal, settings, baseline = reviewed
    result = deploy.build_deployment_artifact(proposal, settings)
    snapshots = candidate_probe(proposal, baseline)
    (work / deploy.ENV_FILE).write_text(result["overlay"])
    (work / deploy.MANIFEST_FILE).write_text(deploy.manifest_text(result["manifest"]))
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", "x" * 40)
    calls = []
    monkeypatch.setattr(preflight, "verify_remote", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(preflight.shutil, "which", lambda command: "/recording/docker")
    services = []
    def probe(args, **kwargs):
        if args == ["docker", "inspect", "execution-agent"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps([
                {"Image": "sha256:" + "a" * 64, "Config": {"Env": []}, "State": {"Running": False}}
            ]))
        if args[:2] == ["/recording/docker", "run"]:
            compile(args[-1], "<probe>", "exec")
            services.append("current_execution")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snapshots["current_execution"]))
        assert args[:7] == ["docker", "compose", "run", "--rm", "--no-deps", "-T", "--entrypoint"]
        compile(args[-1], "<probe>", "exec")
        service = args[8]
        services.append(service)
        key = {"execution-agent": "execution", "trading-bot": "trading_bot",
               "calibration-worker": "calibration_worker"}[service]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snapshots[key]))
    monkeypatch.setattr(preflight.subprocess, "run", probe)
    assert "verified" in preflight.validate(work)
    assert services == ["current_execution", "execution-agent", "trading-bot", "calibration-worker"]
    assert len(calls) == 2 and calls[-1][1] == {"candidate_probe": snapshots}
    assert "x" * 40 not in (work / ".approved_strategy_activated.json").read_text()


@pytest.mark.parametrize("missing", ["account_id", "runtime_identity", "engine", "automatic_engine"])
def test_host_cli_rejects_missing_account_or_engine_snapshots(reviewed, work, monkeypatch, missing):
    snapshots = candidate_probe(reviewed[0], reviewed[2])
    snapshots["execution" if missing in {"account_id", "runtime_identity"} else "trading_bot"].pop(missing)
    monkeypatch.setattr(preflight, "probe_current_execution",
                        lambda root, code: snapshots["current_execution"])
    monkeypatch.setattr(preflight, "probe_service", lambda root, service, code: snapshots[
        {"execution-agent": "execution", "trading-bot": "trading_bot",
         "calibration-worker": "calibration_worker"}[service]])
    with pytest.raises(ValueError, match="snapshot"):
        preflight.probe_candidates(work)


def test_missing_current_executor_prevents_any_candidate_start(work, monkeypatch):
    def missing(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["docker", "inspect", "execution-agent"])
    monkeypatch.setattr(preflight, "probe_current_execution", missing)
    monkeypatch.setattr(preflight, "probe_service", lambda *args: pytest.fail("No candidate may start"))
    with pytest.raises(subprocess.CalledProcessError):
        preflight.probe_candidates(work)


@pytest.mark.parametrize("running", [False, True])
def test_incumbent_identity_probe_works_when_protective_container_is_stopped(work, monkeypatch, running):
    image = "sha256:" + "e" * 64
    secret = "never-print-this-secret"
    calls = []
    monkeypatch.setattr(preflight.shutil, "which", lambda command: "/host/docker")
    def run(args, **kwargs):
        calls.append(args)
        if args == ["docker", "inspect", "execution-agent"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps([{
                "Image": image, "State": {"Running": running},
                "Config": {"Env": ["IBKR_ACCOUNT=U_TEST", f"SUPABASE_KEY={secret}",
                                   "HOME=/root", "PATH=/usr/local/bin"]},
            }]))
        assert args[:2] == ["/host/docker", "run"]
        assert "--read-only" in args and args[args.index("--network") + 1] == "none"
        assert args[args.index("--pull") + 1] == "never"
        assert image in args and not any(flag in args for flag in ("-v", "--volume", "--mount"))
        assert kwargs["env"]["SUPABASE_KEY"] == secret
        assert kwargs["env"]["IBKR_ACCOUNT"] == "U_TEST"
        assert "--env" in args and "SUPABASE_KEY" in args
        assert secret not in repr(args) and "U_TEST" not in repr(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"snapshot": "exact-incumbent"}')
    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.probe_current_execution(work, "print('probe')") == {"snapshot": "exact-incumbent"}
    assert len(calls) == 2 and not any("exec" in call for call in calls)


@pytest.mark.parametrize("inspection", [
    [], [{"Image": "latest", "Config": {"Env": []}}],
    [{"Image": "sha256:" + "e" * 64, "Config": {}}],
    [{"Image": "sha256:" + "e" * 64, "Config": {"Env": ["IBKR_ACCOUNT=U", "IBKR_ACCOUNT=V"]}}],
    [{"Image": "sha256:" + "e" * 64, "Config": {"Env": ["DOCKER_HOST=tcp://other"]}}],
])
def test_incomplete_or_unsafe_incumbent_inspection_cannot_start_probe(work, monkeypatch, inspection):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert args == ["docker", "inspect", "execution-agent"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(inspection))
    monkeypatch.setattr(preflight.subprocess, "run", run)
    with pytest.raises(ValueError, match="exact incumbent"):
        preflight.probe_current_execution(work, "print('probe')")
    assert len(calls) == 1


def test_host_requires_probe_specific_server_acknowledgement(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"verified": True, "manifest_sha256": preflight.digest({})})
    class Opener:
        def open(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(preflight.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="complete candidate"):
        preflight.verify_remote("http://localhost:8000", "x" * 40, {}, "", candidate_probe={})


def test_operator_rejects_extra_code_patch_and_wrong_target(reviewed, work, monkeypatch):
    artifact = deploy.build_deployment_artifact(*reviewed[:2])
    monkeypatch.setattr(operator, "ROOT", work)
    monkeypatch.setattr(operator, "git", lambda *a, **k: pytest.fail("Git must not run"))
    artifact["patch"] += "diff --git a/config.py b/config.py\n"
    artifact["sha256"] = deploy.text_digest(artifact["patch"])
    with pytest.raises(deploy.DeploymentError, match="edited"):
        operator.apply_artifact(artifact, "http://localhost:8000")
    artifact = deploy.build_deployment_artifact(*reviewed[:2])
    (work / deploy.ENV_FILE).write_text("# different prior state\n")
    with pytest.raises(deploy.DeploymentError, match="different prior"):
        operator.apply_artifact(artifact, "http://localhost:8000")


def test_operator_apply_only_and_explicit_push_have_fixed_git_scope(reviewed, work, monkeypatch):
    artifact = deploy.build_deployment_artifact(*reviewed[:2])
    monkeypatch.setattr(operator, "ROOT", work)
    monkeypatch.setattr(operator, "read_token", lambda root: "x" * 40)
    monkeypatch.setattr(operator, "verify_remote", lambda *args: {"verified": True})
    calls = []
    def git(*args, **kwargs):
        calls.append(args)
        return {"symbolic-ref": "main", "rev-parse": "samehash"}.get(args[0], "")
    monkeypatch.setattr(operator, "git", git)
    assert "Applied only" in operator.apply_artifact(artifact, "http://localhost:8000")
    assert not any(c[0] in {"commit", "push", "add"} for c in calls)
    calls.clear()
    assert "pushed" in operator.apply_artifact(artifact, "http://localhost:8000", push=True)
    assert ("add", "--", deploy.ENV_FILE, deploy.MANIFEST_FILE) in calls
    assert calls[-1] == ("push", "origin", "main")


@pytest.mark.parametrize("restart", [False, True])
def test_preflight_failure_precedes_any_service_lifecycle(work, restart):
    (work / "scripts").mkdir()
    (work / deploy.MANIFEST_FILE).write_text("{}")
    (work / "scripts/validate_calibration_deployment.py").write_text("raise SystemExit(42)\n")
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/deploy_runtime.sh"), *(["--restart"] if restart else [])],
        cwd=work, capture_output=True, text=True)
    assert result.returncode == 42
    assert "docker" not in result.stderr
