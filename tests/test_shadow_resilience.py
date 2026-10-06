"""Recovery starts new research experiments; it never repairs a missing observation."""
import copy
import datetime as dt
import json
import pytest
import httpx
from postgrest.exceptions import APIError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout, SSLError

import shadow_engine
import shadow_worker
from research.calibration_worker import ShadowDatasets
from shadow_store import fingerprint
from test_shadow_startup import rig, health, domain_rows, reopen  # noqa: F401
from test_shadow_worker import directory  # noqa: F401


def events(store, run_id):
    return [json.loads(row[0]) for row in store.db.execute(
        "SELECT row_json FROM events WHERE run_id=? ORDER BY sequence", (run_id,))]


def expire(rig):
    assert rig.worker.tick()
    previous = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
    rig.market.stale = True
    assert not rig.worker.tick()
    rig.clock[0] += dt.timedelta(seconds=121)
    assert not rig.worker.tick()
    return previous


@pytest.mark.parametrize("retry_seconds", [30, 120])
def test_transient_stale_quote_retries_same_slot_without_durable_failed_frame(rig, retry_seconds):
    assert rig.worker.tick()
    previous = domain_rows(rig.store)
    run = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
    rig.market.stale = True
    assert not rig.worker.tick()
    assert domain_rows(rig.store) == previous
    assert rig.store.active()["status"] == "running"
    assert rig.store.pending(run["id"]) is None
    assert "Retrying scheduled observation" in health(rig.store)["last_error"]
    rig.clock[0] += dt.timedelta(seconds=retry_seconds)
    rig.market.stale = False
    assert rig.worker.tick()
    current = rig.store.active()
    assert current["id"] == run["id"] and current["sequence"] == 2
    assert current["state"]["last_frame_id"] == "2026-09-30T09:35:00-04:00"
    assert rig.producer.seed.call_count == 1
    assert rig.producer.committed.call_count == 2
    assert rig.store.new_run_request(current) is None
    assert health(rig.store)["last_error"] is None


def test_exhausted_deadline_preserves_state_and_queues_only_one_recovery(rig):
    previous = expire(rig)
    blocked = rig.store.active()
    assert blocked["id"] == previous["id"] and blocked["status"] == "blocked"
    assert blocked["state"] == previous["state"]
    journal = events(rig.store, previous["id"])
    assert [event["kind"] for event in journal] == ["cycle", "gap", "recovery_queued"]
    gap = journal[1]["payload"]
    assert gap["requires_explicit_new_run"] is False
    assert gap["evidence"]["reason_code"] == "observation_deadline"
    assert "stale/future provider quote" in gap["evidence"]["last_attempt"]["last_error"]
    request = rig.store.new_run_request(blocked)
    assert request["mode"] == "automatic" and request["run_id"] == previous["id"]
    saved = domain_rows(rig.store)
    for _ in range(12):
        rig.clock[0] += dt.timedelta(seconds=30)
        assert not rig.worker.tick()
        assert domain_rows(rig.store) == saved
        assert rig.store.new_run_request(rig.store.active()) == request
    assert health(rig.store)["last_error"]
    assert health(rig.store)["metrics"]["recovery"]["old_run_id"] == previous["id"]


def test_recovery_request_survives_restart_and_distinct_replacement_consumes_once(rig):
    previous = expire(rig)
    request = rig.store.new_run_request(rig.store.active())
    reopen(rig)
    assert rig.store.new_run_request(rig.store.active()) == request
    rig.sources.cash = 125000
    rig.market.stale = False
    assert rig.worker.tick()
    replacement = rig.store.active()
    assert replacement["id"] != previous["id"]
    assert replacement["seed"]["account"]["cash"] == 125000
    assert replacement["state"]["frame_count"] == replacement["sequence"] == 1
    assert replacement["state"]["seed_fingerprint"] != previous["state"]["seed_fingerprint"]
    marker = {"mode": "automatic", "previous_run_id": previous["id"],
              "requested_at": request["requested_at"], "reason_code": request["reason_code"]}
    assert replacement["seed"]["source_evidence"]["recovery"] == marker
    assert replacement["state"]["seed"]["source_evidence"]["recovery"] == marker
    assert dt.datetime.fromisoformat(marker["requested_at"]).tzinfo is not None
    assert shadow_engine.restore(replacement["state"]) == replacement["state"]
    assert rig.store.new_run_request(replacement) is None
    old = rig.store.db.execute("SELECT * FROM runs WHERE id=?", (previous["id"],)).fetchone()
    assert old["status"] == "blocked"
    assert json.loads(old["state"]) == previous["state"]
    journal = events(rig.store, previous["id"])
    assert journal[-1]["kind"] == "run_recovered"
    assert journal[-1]["payload"]["new_run_id"] == replacement["id"]
    assert all("data" in item for item in rig.frames[-1]["source_evidence"]["daily_history"].values())
    assert len(events(rig.store, replacement["id"])) == 1
    diagnostics = shadow_worker.diagnostics.emit.call_args_list
    for name in ("shadow_recovery_queued", "shadow_run_recovered"):
        matching = [call for call in diagnostics if call.args[1] == name]
        assert len(matching) == 1
        assert matching[0].kwargs["context"]["old_run_id"] == previous["id"]
    reopen(rig)
    assert not rig.worker.tick()
    assert rig.store.active()["id"] == replacement["id"]
    rig.store.upload(rig.cloud)
    assert any(row.get("kind") == "run_recovered" for row in rig.cloud.rows.values())
    cloud_run = rig.cloud.rows[("intraday_shadow_runs", replacement["id"])]
    assert cloud_run["initial_state"]["source_evidence"]["recovery"] == marker
    assert all(table.startswith("intraday_shadow_") for table, _ in rig.cloud.rows)


def test_continuously_bad_initial_inputs_do_not_create_empty_runs(rig):
    rig.market.stale = True
    for _ in range(20):
        assert not rig.worker.tick()
        rig.clock[0] += dt.timedelta(seconds=30)
    assert rig.store.active() is None
    assert domain_rows(rig.store) == {"runs": [], "events": [], "inputs": []}
    assert not rig.producer._committed_history


def test_opening_quote_retry_preserves_still_fresh_preopen_seed(rig):
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:29:30-04:00")
    assert not rig.worker.tick()
    assert rig.store.active() is None
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:30:00-04:00")
    rig.market.stale = True
    assert not rig.worker.tick()
    assert rig.store.active() is None
    rig.clock[0] += dt.timedelta(seconds=30)
    rig.market.stale = False
    assert rig.worker.tick()
    run = rig.store.active()
    assert run["seed"]["timestamp"] == "2026-09-30T09:29:30-04:00"
    assert rig.producer.seed.call_count == 1
    assert run["state"]["frame_count"] == 1


def test_explicit_flag_does_not_erase_existing_automatic_request_provenance(rig):
    previous = expire(rig)
    request = rig.store.new_run_request(rig.store.active())
    rig.market.stale = False
    assert rig.worker.tick(new_run=True)
    marker = rig.store.active()["seed"]["source_evidence"]["recovery"]
    assert marker == {"mode": "automatic", "previous_run_id": previous["id"],
                      "requested_at": request["requested_at"], "reason_code": request["reason_code"]}


@pytest.mark.parametrize("reason", [
    "Missed scheduled observation; cannot backfill using current quotes.",
    "Cycle acquisition missed its 120-second scheduled observation window.",
    "CAND: stale/future provider quote.",
    "CAND: stale/future provider quote (provider=2026-10-05T14:00:00+00:00, "
    "received=2026-10-05T14:12:00+00:00, captured=2026-10-05T14:12:00+00:00, age_seconds=720.000).",
])
def test_legacy_known_gap_recovers_without_operator_request(rig, reason):
    assert rig.worker.tick()
    previous = rig.store.active()
    rig.store.block(reason, rig.clock[0].isoformat())
    rig.clock[0] += dt.timedelta(seconds=30)
    reopen(rig)
    assert rig.worker.tick()
    assert rig.store.active()["id"] != previous["id"]
    assert events(rig.store, previous["id"])[-1]["kind"] == "run_recovered"


@pytest.mark.parametrize("reason", [
    "Unclassified old failure", "Effective strategy configuration changed; explicit new run is required.",
    "Checkpoint checksum mismatch; refusing to resume.", "Invalid shadow engine value",
])
def test_unclassified_legacy_or_semantic_failure_requires_manual_replacement(rig, reason):
    assert rig.worker.tick()
    run = rig.store.active()
    rig.store.block(reason, rig.clock[0].isoformat())
    reopen(rig)
    before = domain_rows(rig.store)
    assert not rig.worker.tick()
    assert domain_rows(rig.store) == before
    assert rig.store.new_run_request(rig.store.active()) is None
    assert rig.store.active()["id"] == run["id"]
    assert rig.producer.seed.call_count == 1


@pytest.mark.parametrize("change", ["config", "engine"])
def test_automatic_request_cannot_opt_into_changed_strategy(rig, monkeypatch, change):
    previous = expire(rig)
    if change == "config":
        rig.producer.config = copy.deepcopy(rig.producer.config)
        rig.producer.config["decision_config"]["min_trigger_score"] += 1
    else:
        monkeypatch.setattr(shadow_engine, "engine_fingerprint", lambda: "changed-engine")
    rig.market.stale = False
    assert not rig.worker.tick()
    assert rig.store.active()["id"] == previous["id"]
    assert rig.store.new_run_request(rig.store.active()) is None
    assert events(rig.store, previous["id"])[-1]["kind"] == "recovery_blocked"
    assert health(rig.store)["status"] == "blocked"


def test_pending_invalid_frame_is_not_refetched_or_recovered(rig):
    assert rig.worker.tick()
    run = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
    frame = rig.producer.frame(run["state"], rig.clock[0].isoformat(), rig.clock[0])
    frame["engine_frame"]["events"] = []
    rig.store.stage(run["id"], frame)
    calls = rig.producer.frame.call_count
    assert not rig.worker.tick()
    assert rig.store.active()["status"] == "blocked"
    assert rig.store.pending(run["id"]) == frame
    assert rig.producer.frame.call_count == calls
    assert rig.store.new_run_request(rig.store.active()) is None


@pytest.mark.parametrize("corruption", ["checkpoint", "pending"])
def test_corrupt_durable_data_never_recovers_automatically(rig, corruption):
    assert rig.worker.tick()
    run = rig.store.active()
    if corruption == "checkpoint":
        with rig.store.db:
            rig.store.db.execute("UPDATE runs SET state_hash='broken'")
    else:
        rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
        frame = rig.producer.frame(run["state"], rig.clock[0].isoformat(), rig.clock[0])
        rig.store.stage(run["id"], frame)
        with rig.store.db:
            rig.store.db.execute("UPDATE inputs SET input_hash='broken' WHERE committed=0")
    assert not rig.worker.tick()
    assert health(rig.store)["status"] == "blocked"
    assert "checksum" in health(rig.store)["last_error"]
    assert rig.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert not rig.store.db.execute("SELECT 1 FROM metadata WHERE key='new_run_request'").fetchone()


def test_invalid_engine_value_during_replacement_requires_manual_action(rig, monkeypatch):
    previous = expire(rig)
    rig.market.stale = False
    original = rig.producer.frame

    def malformed(*args, **kwargs):
        frame = original(*args, **kwargs)
        frame["engine_frame"]["events"] = []
        return frame

    monkeypatch.setattr(rig.producer, "frame", malformed)
    assert not rig.worker.tick()
    assert rig.store.active()["id"] == previous["id"]
    assert rig.store.new_run_request(rig.store.active()) is None
    monkeypatch.setattr(rig.producer, "frame", original)
    assert not rig.worker.tick()
    assert rig.store.active()["id"] == previous["id"]
    assert rig.worker.tick(new_run=True)
    assert rig.store.active()["id"] != previous["id"]


def test_actual_preopen_seed_and_full_opening_day_count_without_weakening_predicate(rig):
    rig.sources.candidates = []
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:29:30-04:00")
    rig.market.history["available_at"] = rig.clock[0].isoformat()
    assert not rig.worker.tick()
    assert rig.store.active() is None
    assert rig.producer.frame.call_count == 0
    assert rig.producer.seed.call_count == 1
    for tick in shadow_worker.session_ticks(dt.date(2026, 9, 30)):
        rig.clock[0] = tick
        assert rig.worker.tick()
    run = rig.store.active()
    assert run["seed"]["timestamp"] == "2026-09-30T09:29:30-04:00"
    assert rig.producer.seed.call_count == 1
    first = events(rig.store, run["id"])[0]["payload"]["frame"]
    assert first["frame_id"] == "2026-09-30T09:30:00-04:00"
    assert {event["type"] for event in first["events"]} == {"buy_cycle", "monitor"}
    source = {"run": {"initial_state": run["seed"]},
              "records": [{"frame": event["payload"]["frame"]} for event in events(rig.store, run["id"])]}
    close = dt.datetime.fromisoformat("2026-09-30T16:00:01-04:00")
    assert ShadowDatasets.completed_sessions(source, close) == ["2026-09-30"]


def test_lost_preopen_seed_after_restart_is_not_fabricated(rig):
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:29:30-04:00")
    assert not rig.worker.tick()
    reopen(rig)
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:30:30-04:00")
    assert rig.worker.tick()
    run = rig.store.active()
    assert run["seed"]["timestamp"] == rig.clock[0].isoformat()
    source = {"run": {"initial_state": run["seed"]},
              "records": [{"frame": event["payload"]["frame"]} for event in events(rig.store, run["id"])]}
    assert ShadowDatasets.completed_sessions(
        source, dt.datetime.fromisoformat("2026-09-30T16:00:01-04:00")) == []


def test_late_acquisition_success_is_rejected_before_staging(rig, monkeypatch):
    assert rig.worker.tick()
    run = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
    original = rig.producer.frame

    def slow(*args, **kwargs):
        frame = original(*args, **kwargs)
        rig.clock[0] += dt.timedelta(seconds=121)
        return frame

    monkeypatch.setattr(rig.producer, "frame", slow)
    assert not rig.worker.tick()
    assert rig.store.active()["state"] == run["state"]
    assert rig.store.pending(run["id"]) is None
    assert rig.store.db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] == 1
    assert rig.store.new_run_request(rig.store.active())["mode"] == "automatic"


def test_final_slot_cannot_retry_or_record_after_regular_close(rig):
    rig.sources.candidates = []
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T15:55:00-04:00")
    assert rig.worker.tick()
    previous = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T16:00:00-04:00")
    assert not rig.worker.tick()
    assert rig.store.active()["state"] == previous["state"]
    assert rig.store.active()["status"] == "blocked"
    assert rig.store.pending(previous["id"]) is None
    assert rig.store.new_run_request(rig.store.active())["reason_code"] == "observation_deadline"


def test_automatic_create_rollback_keeps_request_and_failed_run(rig, monkeypatch):
    previous = expire(rig)
    saved = domain_rows(rig.store)
    request = rig.store.new_run_request(rig.store.active())
    rig.market.stale = False
    original = rig.store._enqueue

    def fail_checkpoint(table, body):
        if table == "intraday_shadow_checkpoints":
            raise OSError("Synthetic local storage failure")
        original(table, body)

    monkeypatch.setattr(rig.store, "_enqueue", fail_checkpoint)
    seed = rig.producer.seed()
    state = shadow_engine.initialize(seed, rig.producer.config)
    with pytest.raises(OSError):
        rig.store.create_run(seed, rig.producer.config, state,
                             shadow_engine.engine_fingerprint(), explicit=True)
    assert domain_rows(rig.store) == saved
    assert rig.store.active()["id"] == previous["id"]
    assert rig.store.new_run_request(rig.store.active()) == request


def wrapped_transport():
    wrapper = RuntimeError("Supabase request wrapper")
    wrapper.__cause__ = httpx.ReadTimeout("secret-bearing transport URL")
    return wrapper


@pytest.mark.parametrize("factory", [
    lambda: RequestsConnectionError("https://provider.invalid?apikey=SECRET"),
    lambda: Timeout("https://provider.invalid?apikey=SECRET"),
    lambda: httpx.ReadTimeout("https://provider.invalid?apikey=SECRET"),
    wrapped_transport,
    lambda: APIError({"code": "503", "message": "upstream unavailable"}),
])
def test_typed_transport_failure_retries_without_persisting_secret_text(rig, monkeypatch, factory):
    assert rig.worker.tick()
    previous = domain_rows(rig.store)
    original = rig.producer.frame
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")

    def unavailable(*args, **kwargs):
        raise factory()

    monkeypatch.setattr(rig.producer, "frame", unavailable)
    assert not rig.worker.tick()
    assert domain_rows(rig.store) == previous
    message = health(rig.store)["last_error"]
    assert "Source transport temporarily unavailable" in message
    assert "SECRET" not in message and "https://" not in message
    monkeypatch.setattr(rig.producer, "frame", original)
    rig.clock[0] += dt.timedelta(seconds=30)
    assert rig.worker.tick()
    assert rig.store.active()["state"]["last_frame_id"] == "2026-09-30T09:35:00-04:00"


@pytest.mark.parametrize("factory", [
    lambda: RuntimeError("timeout text without typed transport evidence"),
    lambda: APIError({"code": "401", "message": "bad credentials"}),
    lambda: APIError({"code": "42703", "message": "unknown column"}),
    lambda: httpx.UnsupportedProtocol("invalid URL scheme"),
    lambda: httpx.LocalProtocolError("invalid request headers"),
    lambda: SSLError("certificate verification failed"),
])
def test_unclassified_auth_schema_and_protocol_errors_are_not_network_retries(rig, monkeypatch, factory):
    assert rig.worker.tick()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")

    def invalid(*args, **kwargs):
        raise factory()

    monkeypatch.setattr(rig.producer, "frame", invalid)
    assert not rig.worker.tick()
    assert rig.store.active()["status"] == "blocked"
    assert rig.store.new_run_request(rig.store.active()) is None
    assert health(rig.store)["status"] == "blocked"


def test_implicit_exception_context_does_not_authorize_transport_recovery():
    error = RuntimeError("unrelated failure")
    error.__context__ = Timeout("handled timeout")
    assert shadow_worker.transport_failure_type(error) is None


def test_transport_failure_during_engine_processing_never_authorizes_recovery(rig, monkeypatch):
    assert rig.worker.tick()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")

    def invalid_engine(*args, **kwargs):
        raise Timeout("unexpected network operation inside engine")

    monkeypatch.setattr(shadow_engine, "advance", invalid_engine)
    assert not rig.worker.tick()
    run = rig.store.active()
    assert run["status"] == "blocked"
    assert rig.store.pending(run["id"]) is not None
    assert rig.store.new_run_request(run) is None


def test_seed_transport_outage_waits_without_creating_a_run(rig, monkeypatch):
    def unavailable():
        raise RequestsConnectionError("https://provider.invalid?apikey=SECRET")

    monkeypatch.setattr(rig.producer, "seed", unavailable)
    for _ in range(5):
        assert not rig.worker.tick()
        rig.clock[0] += dt.timedelta(seconds=30)
    assert rig.store.active() is None
    assert domain_rows(rig.store) == {"runs": [], "events": [], "inputs": []}
    assert "SECRET" not in health(rig.store)["last_error"]


def empty_legacy_run(rig, *, reason="RS: stale/future provider quote."):
    seed = rig.producer.seed()
    state = shadow_engine.initialize(seed, rig.producer.config)
    old_revision = "pre-patch-114-engine-revision"
    state["engine_fingerprint"] = old_revision
    state["state_sha256"] = fingerprint({key: value for key, value in state.items() if key != "state_sha256"})
    run_id = rig.store.create_run(seed, rig.producer.config, state, old_revision)
    rig.store.block(reason, rig.clock[0].isoformat())
    assert rig.store.active()["id"] == run_id
    assert rig.store.active()["sequence"] == 1
    return rig.store.active()


def test_exact_production_empty_gap_replaced_without_restoring_old_engine(rig, monkeypatch):
    old = empty_legacy_run(rig)
    restore = shadow_engine.restore
    restored_revisions = []

    def current_only(state):
        restored_revisions.append(state["engine_fingerprint"])
        assert state["engine_fingerprint"] != old["revision"]
        return restore(state)

    monkeypatch.setattr(shadow_engine, "restore", current_only)
    rig.clock[0] += dt.timedelta(seconds=30)
    rig.sources.cash = 125000
    assert rig.worker.tick()
    replacement = rig.store.active()
    assert replacement["id"] != old["id"] and replacement["revision"] != old["revision"]
    assert replacement["sequence"] == replacement["state"]["frame_count"] == 1
    assert replacement["seed"]["account"]["cash"] == 125000
    assert replacement["seed"]["source_evidence"]["recovery"]["previous_run_id"] == old["id"]
    assert replacement["seed"]["source_evidence"]["recovery"]["reason_code"] == "stale_quote"
    saved = rig.store.db.execute("SELECT * FROM runs WHERE id=?", (old["id"],)).fetchone()
    assert json.loads(saved["state"]) == old["state"] and saved["status"] == "blocked"
    assert restored_revisions


@pytest.mark.parametrize("invalid", ["config", "inner_checksum", "seed", "stored_input", "cycle_event", "unknown_reason"])
def test_empty_legacy_exception_never_bypasses_semantics_or_integrity(rig, invalid):
    old = empty_legacy_run(rig, reason="unknown failure" if invalid == "unknown_reason"
                           else "RS: stale/future provider quote.")
    if invalid == "config":
        rig.producer.config = copy.deepcopy(rig.producer.config)
        rig.producer.config["decision_config"]["min_trigger_score"] += 1
    elif invalid in {"inner_checksum", "seed"}:
        state = copy.deepcopy(old["state"])
        if invalid == "inner_checksum":
            state["cash"] += 1
        else:
            state["seed"]["account"]["cash"] += 1
            state["state_sha256"] = fingerprint({
                key: value for key, value in state.items() if key != "state_sha256"})
        with rig.store.db:
            rig.store.db.execute("UPDATE runs SET state=?,state_hash=? WHERE id=?",
                                 (json.dumps(state), fingerprint(state), old["id"]))
    elif invalid == "stored_input":
        rig.store.stage(old["id"], {"cycle_key": "uncommitted", "engine_frame": {"events": []}})
    elif invalid == "cycle_event":
        row = events(rig.store, old["id"])[0]
        row["kind"] = "cycle"
        with rig.store.db:
            rig.store.db.execute("UPDATE events SET row_json=? WHERE run_id=?", (json.dumps(row), old["id"]))
    rig.clock[0] += dt.timedelta(seconds=30)
    assert not rig.worker.tick()
    assert rig.store.active()["id"] == old["id"]
    assert rig.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert rig.store.new_run_request(rig.store.active()) is None
    assert rig.producer.seed.call_count == 1


def test_empty_legacy_recovery_request_survives_overnight_restart(rig):
    old = empty_legacy_run(rig)
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T18:00:00-04:00")
    assert not rig.worker.tick()
    request = rig.store.new_run_request(rig.store.active())
    assert request["run_id"] == old["id"]
    reopen(rig)
    assert not rig.worker.tick()
    assert rig.store.new_run_request(rig.store.active()) == request
    rig.clock[0] = dt.datetime.fromisoformat("2026-10-01T09:30:30-04:00")
    assert rig.worker.tick()
    assert rig.store.active()["id"] != old["id"]
