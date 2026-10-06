"""Startup retries are not experiments; only validated observations start a run."""
import copy
import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import shadow_engine
import shadow_worker
from market_calendar import trading_days_between
from research.calibration_worker import ShadowDatasets
from research_configuration import effective_config
from shadow_inputs import InputGap, InputProducer
from shadow_store import ShadowStore, StoreError, fingerprint
from test_shadow_worker import Cloud, directory, flat_evidence  # noqa: F401


class Sources:
    def __init__(self, clock):
        self.clock, self.cash = clock, 100000
        self.candidates = [{
            "ticker": "CAND", "triggered_at": "2026-09-30", "trigger_type": "BREAKOUT",
            "final_score": 99, "quality_score": 99, "adjusted_score": None, "ai_rating": 99,
            "ai_grade": "A", "next_earnings_date": None, "volume_surge": 2,
            "pivot_distance_pct": None, "close_price": 100, "atr_pct": None,
        }]

    def read(self, table, *args, **kwargs):
        stamp = self.clock[0].isoformat()
        return {"rows": copy.deepcopy(self.candidates) if table == "daily_triggers" else [],
                "complete": True, "requested_at": stamp, "received_at": stamp}

    def observer_pair(self, account):
        assert account == "U_TEST"
        pair, _, _, _ = flat_evidence()
        for index, record in enumerate(pair):
            stamp = self.clock[0] - dt.timedelta(minutes=5 if index == 0 else 0)
            raw = record["payload"]
            raw.update(started_at=stamp.isoformat(), snapshot_at=stamp.isoformat())
            raw["account_values"][0]["value"] = str(self.cash)
            for times in raw["component_times"].values():
                times.update(requested_at=stamp.isoformat(), completed_at=stamp.isoformat())
        return pair


class Market:
    def __init__(self, clock):
        self.clock, self.stale = clock, False
        self.quote_calls, self.history_calls = 0, 0
        bars, day = [], clock[0].date() - dt.timedelta(days=500)
        while day < clock[0].date():
            if trading_days_between(day, day + dt.timedelta(days=1)):
                price = 100 + len(bars)
                bars.append(dict(date=day.isoformat(), open=price, high=price + 2,
                                 low=price - 1, close=price + 1, volume=10000))
            day += dt.timedelta(days=1)
        # Reuse byte-identical provider history, as the real market cache does.
        self.history = {"bars": bars, "available_at": clock[0].isoformat()}

    def histories(self, symbols, as_of):
        self.history_calls += 1
        return {symbol: copy.deepcopy(self.history) for symbol in symbols}

    def quotes(self, symbols):
        self.quote_calls += 1
        now = self.clock[0]
        provider_at = now - dt.timedelta(minutes=11 if self.stale else 0)
        return {symbol: {"price": 100, "provider_timestamp": provider_at.isoformat(),
                         "received_at": now.isoformat(), "source": "FMP"}
                for symbol in symbols}, []


@pytest.fixture
def rig(directory, monkeypatch):
    monkeypatch.setattr(shadow_worker.diagnostics, "emit", Mock())
    clock = [dt.datetime.fromisoformat("2026-09-30T09:30:30-04:00")]
    sources, market = Sources(clock), Market(clock)
    producer = InputProducer(sources, market, "U_TEST", effective_config(), clock=lambda: clock[0])
    path = directory / "startup.sqlite3"
    store, cloud = ShadowStore(path), Cloud()
    seeds, frames, order = [], [], []
    original_seed, original_frame, original_committed = producer.seed, producer.frame, producer.committed

    def seed():
        result = original_seed()
        seeds.append(copy.deepcopy(result))
        order.append("seed")
        return result

    def frame(*args, **kwargs):
        result = original_frame(*args, **kwargs)
        frames.append(copy.deepcopy(result))
        order.append("frame")
        return result

    def committed(value):
        active_store = result.store
        current = active_store.active()
        assert current["sequence"] >= 1
        assert active_store.pending(current["id"]) is None
        saved = active_store.db.execute(
            "SELECT frame,committed FROM inputs WHERE run_id=? AND cycle_key=?",
            (current["id"], value["cycle_key"])).fetchone()
        assert saved["committed"] == 1 and json.loads(saved["frame"]) == value
        order.append("committed")
        original_committed(value)

    monkeypatch.setattr(producer, "seed", Mock(side_effect=seed))
    monkeypatch.setattr(producer, "frame", Mock(side_effect=frame))
    monkeypatch.setattr(producer, "committed", Mock(side_effect=committed))
    worker = shadow_worker.Worker(store, producer, cloud, clock=lambda: clock[0])
    result = SimpleNamespace(clock=clock, sources=sources, market=market, producer=producer,
                             store=store, cloud=cloud, worker=worker, seeds=seeds, frames=frames,
                             order=order, path=path)
    yield result
    result.store.close()


def health(store):
    return json.loads(store.db.execute("SELECT value FROM metadata WHERE key='health'").fetchone()[0])


def domain_rows(store):
    return {table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("runs", "inputs", "events")}


def assert_no_run(rig, *, status="waiting"):
    assert rig.store.active() is None
    assert domain_rows(rig.store) == {"runs": [], "inputs": [], "events": []}
    visible = health(rig.store)
    assert visible["status"] == status and visible["run_id"] is None
    targets = {row[0] for row in rig.store.db.execute("SELECT target FROM outbox")}
    assert targets == {"intraday_shadow_health"}
    assert rig.producer.committed.call_count == 0
    assert not rig.producer._committed_history


def test_stale_first_quote_retries_fresh_seed_and_commits_exact_first_cycle(rig, monkeypatch):
    rig.market.stale = True
    assert rig.worker.tick() is False
    assert_no_run(rig)
    assert "stale" in health(rig.store)["last_error"].lower()
    assert rig.producer.seed.call_count == rig.producer.frame.call_count == 1
    assert rig.worker.flush()
    assert {table for table, _ in rig.cloud.rows} == {"intraday_shadow_health"}

    rig.clock[0] += dt.timedelta(seconds=30)
    rig.sources.cash = 125000
    rig.market.stale = False
    original_advance = shadow_engine.advance
    original_create = rig.store.create_run
    original_stage, original_commit = rig.store.stage, rig.store.commit_cycle

    def advance(*args):
        value = original_advance(*args)
        rig.order.append("validated")
        return value

    def create(seed, config, state, *args, **kwargs):
        assert rig.order[-1] == "validated"
        assert state["frame_count"] == 0
        assert state["cash"] == 125000 and state["positions"] == {}
        rig.order.append("created")
        return original_create(seed, config, state, *args, **kwargs)

    def stage(*args):
        rig.order.append("staged")
        return original_stage(*args)

    def commit(*args):
        result = original_commit(*args)
        rig.order.append("cycle_committed")
        return result

    monkeypatch.setattr(shadow_engine, "advance", advance)
    monkeypatch.setattr(rig.store, "create_run", create)
    monkeypatch.setattr(rig.store, "stage", stage)
    monkeypatch.setattr(rig.store, "commit_cycle", commit)
    assert rig.worker.tick() is True
    assert rig.producer.seed.call_count == rig.producer.frame.call_count == 2
    assert rig.market.history_calls == rig.market.quote_calls == 2
    assert rig.seeds[0]["account"]["cash"] == 100000
    assert rig.seeds[1]["account"]["cash"] == 125000
    run, envelope = rig.store.active(), rig.frames[-1]
    assert run["seed"] == rig.seeds[1]
    assert run["seed"]["timestamp"] != rig.seeds[0]["timestamp"]
    initial = shadow_engine.initialize(run["seed"], run["config"])
    expected_state, expected_output = original_advance(initial, envelope["engine_frame"])
    assert run["state"] == shadow_engine.checkpoint(expected_state)
    assert run["state"]["positions"]["CAND"]["shares"] > 0
    assert run["status"] == health(rig.store)["status"] == "running"
    assert run["sequence"] == run["state"]["frame_count"] == 1
    row = rig.store.db.execute("SELECT * FROM inputs").fetchone()
    assert json.loads(row["frame"]) == envelope and row["committed"] == 1
    assert row["input_hash"] == fingerprint(envelope)
    event = json.loads(rig.store.db.execute("SELECT row_json FROM events").fetchone()[0])
    assert event["kind"] == "cycle" and event["payload"]["output"] == expected_output
    checkpoints = [json.loads(row[0]) for row in rig.store.db.execute(
        "SELECT body FROM outbox WHERE target='intraday_shadow_checkpoints'")]
    assert [row["sequence"] for row in checkpoints] == [0, 1]
    assert checkpoints[0]["state"] == shadow_engine.checkpoint(initial)
    assert all("data" in item and "reference" not in item
               for item in envelope["source_evidence"]["daily_history"].values())
    assert rig.producer.committed.call_count == 1
    assert rig.order.index("validated") < rig.order.index("created") < rig.order.index("staged")
    assert rig.order.index("cycle_committed") < rig.order.index("committed")


def test_malformed_first_engine_frame_leaves_no_experiment_or_history_references(rig, monkeypatch):
    original_frame = rig.producer.frame

    def malformed(*args, **kwargs):
        envelope = original_frame(*args, **kwargs)
        envelope["engine_frame"]["events"] = []
        return envelope

    monkeypatch.setattr(rig.producer, "frame", malformed)
    assert rig.worker.tick() is False
    assert health(rig.store)["status"] in {"waiting", "blocked"}
    assert_no_run(rig, status=health(rig.store)["status"])
    assert health(rig.store)["last_error"]
    monkeypatch.setattr(rig.producer, "frame", original_frame)
    rig.clock[0] += dt.timedelta(seconds=30)
    assert rig.worker.tick() is True
    assert rig.producer.seed.call_count == 2
    assert all("data" in item and "reference" not in item
               for item in rig.frames[-1]["source_evidence"]["daily_history"].values())


@pytest.mark.parametrize("previous_status", ["running", "blocked"])
@pytest.mark.parametrize("failure", ["seed", "quote", "engine"])
def test_failed_explicit_replacement_does_not_mutate_previous_run(rig, monkeypatch, previous_status, failure):
    assert rig.worker.tick()
    if previous_status == "blocked":
        rig.store.block("Established observation gap", rig.clock[0].isoformat())
    previous = domain_rows(rig.store)
    old_id = rig.store.active()["id"]
    rig.store.upload(rig.cloud)
    rig.clock[0] += dt.timedelta(seconds=30)
    if failure == "seed":
        monkeypatch.setattr(rig.producer, "seed", Mock(side_effect=InputGap("Seed unavailable")))
    elif failure == "quote":
        rig.market.stale = True
    else:
        original_frame = rig.producer.frame

        def malformed(*args, **kwargs):
            envelope = original_frame(*args, **kwargs)
            envelope["engine_frame"]["events"] = []
            return envelope

        monkeypatch.setattr(rig.producer, "frame", malformed)
    assert rig.worker.tick(new_run=True) is False
    assert domain_rows(rig.store) == previous
    assert rig.store.active()["id"] == old_id
    assert rig.store.active()["status"] == previous_status
    assert health(rig.store)["status"] in ({"waiting", "blocked"} if failure == "engine" else {"waiting"})
    assert health(rig.store)["last_error"]
    assert rig.producer.committed.call_count == 1
    assert {row[0] for row in rig.store.db.execute("SELECT target FROM outbox")} == {
        "intraday_shadow_health"}


@pytest.mark.parametrize("stamp", [
    "2026-09-30T09:29:59-04:00", "2026-09-30T16:00:00-04:00",
    "2026-09-30T20:00:00-04:00", "2026-10-03T10:00:00-04:00",
])
@pytest.mark.parametrize("replacing", [False, True])
def test_off_hours_do_not_acquire_seed_or_frame(rig, stamp, replacing):
    if replacing:
        assert rig.worker.tick()
        rig.store.block("Existing gap", rig.clock[0].isoformat())
    before = domain_rows(rig.store)
    seed_calls, frame_calls = rig.producer.seed.call_count, rig.producer.frame.call_count
    rig.clock[0] = dt.datetime.fromisoformat(stamp)
    assert rig.worker.tick(new_run=replacing) is False
    assert domain_rows(rig.store) == before
    assert rig.producer.seed.call_count == seed_calls
    assert rig.producer.frame.call_count == frame_calls
    assert health(rig.store)["status"] == "waiting"


@pytest.mark.parametrize("stage", ["seed", "frame"])
def test_startup_crossing_market_close_does_not_create_run(rig, monkeypatch, stage):
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T15:59:30-04:00")
    original = getattr(rig.producer, stage)

    def crosses_close(*args, **kwargs):
        value = original(*args, **kwargs)
        rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T16:00:00-04:00")
        return value

    monkeypatch.setattr(rig.producer, stage, crosses_close)
    assert rig.worker.tick() is False
    assert_no_run(rig)


def test_seed_input_gap_before_any_run_waits_and_fetches_again(rig, monkeypatch):
    original_seed = rig.producer.seed
    attempts = []

    def seed():
        attempts.append(rig.clock[0])
        if len(attempts) == 1:
            raise InputGap("Adjacent account observations unavailable")
        return original_seed()

    monkeypatch.setattr(rig.producer, "seed", seed)
    assert rig.worker.tick() is False
    assert_no_run(rig)
    assert "observations unavailable" in health(rig.store)["last_error"]
    assert rig.producer.frame.call_count == 0
    rig.clock[0] += dt.timedelta(seconds=30)
    assert rig.worker.tick() is True
    assert len(attempts) == 2 and rig.producer.frame.call_count == 1


def test_failure_after_run_creation_blocks_without_marking_history_committed(rig, monkeypatch):
    monkeypatch.setattr(rig.store, "commit_cycle", Mock(side_effect=StoreError("Commit rejected")))
    assert rig.worker.tick() is False
    run = rig.store.active()
    assert run["status"] == "blocked" and run["state"]["frame_count"] == 0
    assert rig.store.pending(run["id"]) is not None
    assert rig.producer.committed.call_count == 0
    assert not rig.producer._committed_history
    events = [json.loads(row[0]) for row in rig.store.db.execute("SELECT row_json FROM events")]
    assert len(events) == 1 and events[0]["kind"] == "gap"
    assert events[0]["payload"]["reason"] == "Commit rejected"
    assert rig.worker.tick() is False
    assert rig.producer.seed.call_count == rig.producer.frame.call_count == 1


def test_gap_after_first_commit_is_durable_and_does_not_automatically_reseed(rig):
    assert rig.worker.tick()
    old = rig.store.active()
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T09:35:00-04:00")
    rig.market.stale = True
    assert rig.worker.tick() is False
    blocked = rig.store.active()
    assert blocked["id"] == old["id"] and blocked["status"] == "blocked"
    assert blocked["sequence"] == old["sequence"] + 1
    assert blocked["state"] == old["state"]
    events = [json.loads(row[0]) for row in rig.store.db.execute(
        "SELECT row_json FROM events ORDER BY sequence")]
    assert [event["kind"] for event in events] == ["cycle", "gap"]
    assert events[-1]["payload"]["requires_explicit_new_run"] is True
    assert "stale" in events[-1]["payload"]["reason"].lower()
    rig.market.stale = False
    before = domain_rows(rig.store)
    assert rig.worker.tick() is False
    assert domain_rows(rig.store) == before
    assert rig.producer.seed.call_count == 1 and rig.producer.frame.call_count == 2
    assert rig.producer.committed.call_count == 1
    assert health(rig.store)["status"] == "blocked"
    assert rig.worker.tick(new_run=True) is True
    assert rig.store.active()["id"] != old["id"]
    assert rig.store.db.execute("SELECT status FROM runs WHERE id=?", (old["id"],)).fetchone()[0] == "superseded"
    assert rig.producer.seed.call_count == 2
    assert all("data" in item and "reference" not in item
               for item in rig.frames[-1]["source_evidence"]["daily_history"].values())


def test_partial_startup_session_is_not_a_complete_calibration_session(rig):
    rig.sources.candidates = []
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T15:59:10-04:00")
    assert rig.worker.tick()
    assert any(event["type"] == "end_mark" for event in rig.frames[-1]["engine_frame"]["events"])
    for tick in shadow_worker.session_ticks(dt.date(2026, 10, 1)):
        rig.clock[0] = tick
        assert rig.worker.tick()
    source = {
        "run": {"initial_state": rig.store.active()["seed"]},
        "records": [{"frame": json.loads(row[0])["payload"]["frame"]}
                    for row in rig.store.db.execute("SELECT row_json FROM events ORDER BY sequence")],
    }
    at_close = dt.datetime.fromisoformat("2026-10-01T16:00:00-04:00")
    assert ShadowDatasets.completed_sessions(source, at_close) == []
    assert ShadowDatasets.completed_sessions(source, at_close + dt.timedelta(seconds=1)) == ["2026-10-01"]


def test_run_loop_keeps_explicit_replacement_pending_until_first_commit(rig, monkeypatch):
    assert rig.worker.tick()
    rig.store.block("Old run gap", rig.clock[0].isoformat())
    old_id = rig.store.active()["id"]
    rig.store.close()
    original_worker = shadow_worker.Worker
    real_seed = rig.producer.seed
    flags, seed_attempts = [], []
    rig.clock[0] = dt.datetime.fromisoformat("2026-10-01T09:29:30-04:00")
    rig.sources.candidates = []

    def seed():
        seed_attempts.append(rig.clock[0])
        if len(seed_attempts) == 1:
            raise InputGap("Fresh observer pair unavailable")
        return real_seed()

    def worker_factory(store, producer, cloud, **kwargs):
        rig.store = store
        worker = original_worker(store, producer, cloud, clock=lambda: rig.clock[0], **kwargs)
        real_tick = worker.tick

        def tick(*, new_run=False):
            flags.append(new_run)
            return real_tick(new_run=new_run)

        worker.tick = tick
        return worker

    class Stop:
        def is_set(self):
            return len(flags) == 4

        def wait(self, seconds):
            rig.clock[0] += dt.timedelta(seconds=seconds)

    # run() owns a different SQLite handle; this callback must check that handle.
    original_committed = InputProducer.committed

    def committed(frame):
        assert rig.store.pending(rig.store.active()["id"]) is None
        original_committed(rig.producer, frame)

    monkeypatch.setattr(rig.producer, "seed", seed)
    monkeypatch.setattr(rig.producer, "committed", committed)
    monkeypatch.setattr(shadow_worker, "Worker", worker_factory)
    args = SimpleNamespace(account="U_TEST", poll=30, spool=str(rig.path), new_run=True, once=False)
    try:
        shadow_worker.run(args, producer=rig.producer, cloud=rig.cloud, stop=Stop())
    finally:
        rig.store = ShadowStore(rig.path)
    assert flags == [True, True, True, False]
    assert len(seed_attempts) == 2
    assert rig.store.active()["id"] != old_id
    assert rig.store.active()["status"] == "running"
    assert rig.store.active()["sequence"] == 1
    assert rig.store.db.execute("SELECT status FROM runs WHERE id=?", (old_id,)).fetchone()[0] == "superseded"


def reopen(rig):
    rig.store.close()
    rig.store = ShadowStore(rig.path)
    rig.worker = shadow_worker.Worker(
        rig.store, rig.producer, rig.cloud, clock=lambda: rig.clock[0])


def blocked_run(rig):
    assert rig.worker.tick()
    rig.store.block("Missing established observation", rig.clock[0].isoformat())
    return rig.store.active()


@pytest.mark.parametrize("status", ["absent", "running", "superseded"])
def test_queue_new_run_requires_current_blocked_run(rig, status):
    if status != "absent":
        assert rig.worker.tick()
        if status == "superseded":
            with rig.store.db:
                rig.store.db.execute("UPDATE runs SET status='superseded'")
    before = domain_rows(rig.store)
    with pytest.raises(StoreError):
        rig.store.queue_new_run()
    assert domain_rows(rig.store) == before


def test_queued_request_is_idempotent_and_survives_reopening(rig, monkeypatch):
    import shadow_store

    run = blocked_run(rig)
    before = domain_rows(rig.store)
    assert rig.store.new_run_request(run) is None
    request = rig.store.queue_new_run()
    assert set(request) == {"run_id", "requested_at"}
    assert request["run_id"] == run["id"]
    assert dt.datetime.fromisoformat(request["requested_at"]).tzinfo is not None
    monkeypatch.setattr(shadow_store, "now", lambda: "2026-10-06T00:00:00+00:00")
    assert rig.store.queue_new_run() == request
    assert rig.store.new_run_request(run) == request
    assert domain_rows(rig.store) == before
    reopen(rig)
    assert rig.store.new_run_request(rig.store.active()) == request
    assert rig.store.queue_new_run() == request
    assert domain_rows(rig.store) == before


@pytest.mark.parametrize("corruption", [
    "invalid_json", "list", "missing_run_id", "missing_requested_at",
    "invalid_timestamp", "naive_timestamp", "wrong_run", "nonblocked_run",
])
def test_queued_request_rejects_malformed_or_wrong_target(rig, corruption):
    run = blocked_run(rig)
    request = rig.store.queue_new_run()
    matches = [row["key"] for row in rig.store.db.execute("SELECT key,value FROM metadata")
               if json.loads(row["value"]) == request]
    assert len(matches) == 1
    value = copy.deepcopy(request)
    supplied_run = copy.deepcopy(run)
    if corruption == "missing_run_id":
        value.pop("run_id")
    elif corruption == "missing_requested_at":
        value.pop("requested_at")
    elif corruption == "invalid_timestamp":
        value["requested_at"] = "not-a-timestamp"
    elif corruption == "naive_timestamp":
        value["requested_at"] = "2026-10-05T10:00:00"
    elif corruption == "wrong_run":
        value["run_id"] = "different-run"
    elif corruption == "nonblocked_run":
        supplied_run["status"] = "running"
    text = "not-json" if corruption == "invalid_json" else json.dumps(
        [] if corruption == "list" else value)
    with rig.store.db:
        rig.store.db.execute("UPDATE metadata SET value=? WHERE key=?", (text, matches[0]))
    before = domain_rows(rig.store)
    with pytest.raises(StoreError):
        rig.store.new_run_request(supplied_run)
    assert domain_rows(rig.store) == before
    assert rig.store.db.execute("SELECT value FROM metadata WHERE key=?", (matches[0],)).fetchone()[0] == text


def test_failed_create_rolls_back_queued_request_consumption_and_supersession(rig, monkeypatch):
    run = blocked_run(rig)
    request = rig.store.queue_new_run()
    before = domain_rows(rig.store)
    rig.clock[0] += dt.timedelta(seconds=30)
    seed = rig.producer.seed()
    state = shadow_engine.initialize(seed, rig.producer.config)
    enqueue = rig.store._enqueue

    def fail_checkpoint(target, body):
        if target == "intraday_shadow_checkpoints":
            raise StoreError("Synthetic checkpoint failure")
        return enqueue(target, body)

    monkeypatch.setattr(rig.store, "_enqueue", fail_checkpoint)
    with pytest.raises(StoreError, match="Synthetic checkpoint failure"):
        rig.store.create_run(seed, rig.producer.config, state,
                             shadow_engine.engine_fingerprint(), explicit=True)
    assert domain_rows(rig.store) == before
    assert rig.store.new_run_request(run) == request
    monkeypatch.setattr(rig.store, "_enqueue", enqueue)
    new_id = rig.store.create_run(seed, rig.producer.config, state,
                                  shadow_engine.engine_fingerprint(), explicit=True)
    assert new_id != run["id"]
    assert rig.store.new_run_request(rig.store.active()) is None
    assert rig.store.db.execute("SELECT status FROM runs WHERE id=?", (run["id"],)).fetchone()[0] == "superseded"
    reopen(rig)
    assert rig.store.new_run_request(rig.store.active()) is None


def test_queued_replacement_waits_retries_and_is_consumed_once_across_restarts(rig, monkeypatch):
    run = blocked_run(rig)
    request = rig.store.queue_new_run()
    before = domain_rows(rig.store)
    rig.clock[0] = dt.datetime.fromisoformat("2026-09-30T20:00:00-04:00")
    reopen(rig)
    assert rig.worker.tick() is False
    assert health(rig.store)["status"] == "waiting"
    assert rig.producer.seed.call_count == rig.producer.frame.call_count == 1
    assert rig.store.new_run_request(rig.store.active()) == request
    assert domain_rows(rig.store) == before

    real_seed = rig.producer.seed
    unavailable_seed = Mock(side_effect=InputGap("Seed not ready"))
    monkeypatch.setattr(rig.producer, "seed", unavailable_seed)
    rig.clock[0] = dt.datetime.fromisoformat("2026-10-01T09:30:00-04:00")
    reopen(rig)
    assert rig.worker.tick() is False
    unavailable_seed.assert_called_once()
    assert health(rig.store)["status"] == "waiting"
    assert rig.store.new_run_request(rig.store.active()) == request
    assert domain_rows(rig.store) == before

    monkeypatch.setattr(rig.producer, "seed", real_seed)
    rig.market.stale = True
    rig.clock[0] += dt.timedelta(seconds=30)
    reopen(rig)
    assert rig.worker.tick() is False
    assert health(rig.store)["status"] == "waiting"
    assert rig.store.new_run_request(rig.store.active()) == request
    assert domain_rows(rig.store) == before
    assert not rig.producer._committed_history

    rig.market.stale = False
    rig.clock[0] += dt.timedelta(seconds=30)
    reopen(rig)
    assert rig.worker.tick() is True
    replacement = rig.store.active()
    assert replacement["id"] != run["id"] and replacement["status"] == "running"
    assert replacement["sequence"] == 1
    assert rig.store.new_run_request(replacement) is None
    assert rig.store.db.execute("SELECT status FROM runs WHERE id=?", (run["id"],)).fetchone()[0] == "superseded"
    assert all("data" in item and "reference" not in item
               for item in rig.frames[-1]["source_evidence"]["daily_history"].values())
    seed_calls = rig.producer.seed.call_count
    reopen(rig)
    rig.clock[0] = dt.datetime.fromisoformat("2026-10-01T09:35:00-04:00")
    assert rig.worker.tick() is True
    assert rig.store.active()["id"] == replacement["id"]
    assert rig.store.active()["sequence"] == 2
    assert rig.producer.seed.call_count == seed_calls
    assert rig.store.new_run_request(rig.store.active()) is None
    assert rig.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


def test_queue_cli_requires_neither_account_credentials_nor_online_clients(rig, monkeypatch):
    import builtins
    import socket

    blocked = blocked_run(rig)
    before = domain_rows(rig.store)
    rig.store.close()
    for name in ("IBKR_ACCOUNT", "SUPABASE_URL", "SUPABASE_KEY", "INTRADAY_SUPABASE_KEY",
                 "SHADOW_SOURCE_SUPABASE_KEY", "FMP_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    forbidden = Mock(side_effect=AssertionError("Queued recovery must remain offline"))
    for name in ("Worker", "InputProducer", "PublicMarketData", "ReadOnlySources"):
        monkeypatch.setattr(shadow_worker, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"requests", "supabase", "ib_insync"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline_import)
    args = shadow_worker.parser().parse_args(["--queue-new-run", "--spool", str(rig.path)])
    assert args.account is None
    try:
        assert shadow_worker.run(args) == 0
        assert shadow_worker.run(args) == 0
    finally:
        rig.store = ShadowStore(rig.path)
    forbidden.assert_not_called()
    assert domain_rows(rig.store) == before
    assert rig.store.new_run_request(rig.store.active())["run_id"] == blocked["id"]


def test_queue_cli_and_immediate_new_run_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        shadow_worker.parser().parse_args(["--queue-new-run", "--new-run"])
    assert error.value.code == 2


@pytest.mark.parametrize("failure", ["quote", "unexpected", None])
def test_once_new_run_reports_replacement_outcome_not_preserved_running_status(rig, monkeypatch, failure):
    assert rig.worker.tick()
    original_run = rig.store.active()
    before = domain_rows(rig.store)
    rig.clock[0] += dt.timedelta(seconds=30)
    rig.sources.cash = 125000
    if failure == "quote":
        rig.market.stale = True
    elif failure == "unexpected":
        monkeypatch.setattr(rig.producer, "frame", Mock(side_effect=ConnectionError(
            "https://provider.invalid?apikey=synthetic-sensitive-token")))
    original_worker = shadow_worker.Worker

    def worker_factory(store, producer, cloud, **kwargs):
        rig.store = store
        return original_worker(store, producer, cloud, clock=lambda: rig.clock[0], **kwargs)

    monkeypatch.setattr(shadow_worker, "Worker", worker_factory)
    args = shadow_worker.parser().parse_args([
        "--once", "--new-run", "--account", "U_TEST", "--spool", str(rig.path)])
    rig.store.close()
    try:
        result = shadow_worker.run(args, producer=rig.producer, cloud=rig.cloud)
    finally:
        rig.store = ShadowStore(rig.path)
    current = rig.store.active()
    assert current["status"] == "running"
    if failure is None:
        assert result == 0
        assert current["id"] != original_run["id"]
        assert current["sequence"] == 1
        assert current["seed"]["account"]["cash"] == 125000
        assert rig.store.db.execute(
            "SELECT status FROM runs WHERE id=?", (original_run["id"],)).fetchone()[0] == "superseded"
        assert rig.producer.committed.call_count == 2
    else:
        assert result == 1
        assert current == original_run
        assert domain_rows(rig.store) == before
        assert rig.producer.committed.call_count == 1
        if failure == "unexpected":
            message = health(rig.store)["last_error"]
            assert "no new run was created" in message
            assert "inspect diagnostics" in message
            assert "explicit new run required" not in message
            assert "synthetic-sensitive-token" not in message
            assert "provider.invalid" not in message
