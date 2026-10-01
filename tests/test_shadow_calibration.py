import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import uuid

import pytest

import intraday_replay as capture
import shadow_engine as shadow
from research import calibrate_intraday as calibration
from research import live_rule_replay as core
from test_intraday_calibration import shift_window
from test_intraday_replay import DAY, records  # noqa: F401
from test_shadow_engine import seed_and_frames, durable_records


def training_and_holdout(records):
    data = capture.build_dataset(records, DAY, DAY)
    for event in data["events"]:
        if event["timestamp"][11:16] >= "09:35":
            event["market_observations"]["CANDIDATE"]["price"] = 104
    seed, frames = seed_and_frames(data)
    later = shift_window(data)
    for event in later["events"]:
        context = event.get("cycle_context", {})
        for key in ("cycle_id", "preceding_buy_cycle_id"):
            if key in context:
                context[key] = "holdout-" + context[key]
        if event["timestamp"][11:16] >= "09:35":
            event["market_observations"]["CANDIDATE"]["price"] = 95
    _, more = seed_and_frames(later)
    _, rows = durable_records(seed, frames + more)
    training = shadow.export_shadow_dataset(seed, rows, DAY, DAY)
    holdout = shadow.export_shadow_dataset(seed, rows, "2026-09-29", "2026-09-29")
    return training, holdout


def test_export_select_frozen_later_holdout(records, monkeypatch):
    training, holdout = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
    assert plan["selected_name"] == "no_veto"
    calls, original = [], calibration._run
    def record(data, experiment):
        calls.append(experiment["name"])
        return original(data, experiment)
    monkeypatch.setattr(calibration, "_run", record)
    result = calibration.evaluate(plan, holdout)
    assert calls == ["baseline", "no_veto"]
    assert result["selected_name"] == "no_veto"
    summary = result["frozen_candidate"]["summary"]
    assert summary["equity_delta_vs_recorded_config_baseline"] < 0
    assert summary["conditional_experiment"]
    assert "starting_actual_account" not in summary
    assert result["baseline"]["summary"]["starting_hypothetical_account"] == summary["starting_hypothetical_account"]
    assert any("SAME baseline shadow checkpoint" in text for text in result["limitations"])


def test_checkpoint_requires_reproduced_prefix_not_hash(records):
    _, holdout = training_and_holdout(records)
    changed = copy.deepcopy(holdout)
    changed["window_checkpoint"]["cash"] += 100
    changed["window_checkpoint"] = shadow._seal(changed["window_checkpoint"])
    with pytest.raises(core.ReplayInputError, match="does not reproduce"):
        calibration.validate_dataset(changed)
    changed = copy.deepcopy(holdout)
    changed["prefix_frames"] = []
    with pytest.raises((core.ReplayInputError, capture.CaptureError)):
        calibration.validate_dataset(changed)


def test_holdout_cannot_rewrite_frozen_training_prefix(records):
    training, holdout = training_and_holdout(records)
    plan = calibration.select(training, {"experiments": [{"name": "same"}]})
    frames = copy.deepcopy(holdout["prefix_frames"] + holdout["window_frames"])
    # Still internally reproducible, but no longer the history used to freeze the plan.
    frames[1]["events"][0]["market_observations"]["HELD"]["price"] = 101.1
    rewritten = shadow.export_shadow_dataset(holdout["seed"], [{"frame": f} for f in frames],
                                               "2026-09-29", "2026-09-29")
    with pytest.raises(calibration.CalibrationError, match="frozen training history"):
        calibration.evaluate(plan, rewritten)


def test_observer_and_schema2_rejection_not_weakened(records):
    training, _ = training_and_holdout(records)
    with pytest.raises(calibration.CalibrationError, match="observer-only"):
        calibration.validate_dataset({"schema_version": 1, "capture_mode": "observer"})
    disguised = copy.deepcopy(training)
    disguised["schema_version"] = 2
    with pytest.raises(core.ReplayInputError):
        calibration.validate_dataset(disguised)


def test_multiple_partial_sales_group_under_one_position(records):
    data = capture.build_dataset(records, DAY, DAY)
    for event in data["events"]:
        event["market_observations"]["HELD"]["price"] = (
            104.5 if event["timestamp"][11:16] < "09:35" else 95)
    seed, frames = seed_and_frames(data)
    _, rows = durable_records(seed, frames)
    dataset = shadow.export_shadow_dataset(seed, rows, DAY, DAY)
    report = calibration.select(dataset, {"experiments": [{"name": "same"}]})
    attribution = report["training_trials"][0]["summary"]["position_attribution"]
    assert attribution["completed_positions"] == 1
    assert attribution["positions"][0]["sale_rows"] == 2


def test_repeated_partial_rows_are_not_independent_positions():
    result = calibration.position_attribution({"position_sales": [
        dict(ticker="X", buy_date=DAY, sell_date=DAY, partial=partial, net_profit_loss=net)
        for partial, net in [(True, 4), (True, 5), (False, -10)]]})
    assert result["completed_positions"] == 1
    assert result["positions"][0]["sale_rows"] == 3
    assert result["realised_loss_completed_positions"] == -1


def test_early_close_export_uses_exchange_close(records):
    data = shift_window(capture.build_dataset(records, DAY, DAY), days=60)
    day = "2026-11-27"
    # Shift fixture wall-clock observations to the actual winter UTC offset.
    def winter(value):
        if isinstance(value, str):
            return value.replace("-04:00", "-05:00")
        if isinstance(value, dict):
            return {k: winter(v) for k, v in value.items()}
        if isinstance(value, list):
            return [winter(v) for v in value]
        return value
    data = winter(data)
    latch = next(copy.deepcopy(e) for e in data["events"] if e["type"] == "eod_latch")
    closing = next(copy.deepcopy(e) for e in data["events"] if e["type"] == "end_mark")
    data["events"] = [e for e in data["events"] if e["timestamp"][11:16] < "13:00"]
    for event, clock in ((latch, "12:45"), (closing, "13:00")):
        stamp = f"{day}T{clock}:00-05:00"
        event["timestamp"] = stamp
        if "observed_at" in event:
            event["observed_at"] = stamp
        for quote in event["market_observations"].values():
            quote["observed_at"] = stamp
        if event is latch:
            index = next(i for i, e in enumerate(data["events"]) if
                         e["type"] == "monitor" and e["timestamp"][11:16] == clock)
            data["events"].insert(index + 1, event)
        else:
            data["events"].append(event)
    seed, frames = seed_and_frames(data)
    seed["source_evidence"]["trade_history"]["filter_gte"] = "2026-01-01T00:00:00-05:00"
    _, rows = durable_records(seed, frames)
    exported = shadow.export_shadow_dataset(seed, rows, day, day)
    assert exported["events"][-1]["timestamp"] == "2026-11-27T13:00:00-05:00"
    assert calibration.validate_dataset(exported)["sessions"] == [day]


def test_actual_producer_store_export_selection_and_later_holdout():
    from market_calendar import trading_days_between
    from research_configuration import effective_config
    from shadow_inputs import InputProducer, build_seed
    from shadow_store import ShadowStore
    from shadow_worker import Worker, session_ticks
    from test_shadow_worker import Cloud, flat_evidence

    pair, proof, now, _ = flat_evidence()
    now = now.replace(hour=9, minute=30)
    config = effective_config()

    def observed_account():
        result = copy.deepcopy(pair)
        for index, record in enumerate(result):
            stamp = now - dt.timedelta(minutes=5 if index == 0 else 0)
            payload = record["payload"]
            payload.update(started_at=stamp.isoformat(), snapshot_at=stamp.isoformat())
            payload["account_values"][0]["value"] = "100000"
            for times in payload["component_times"].values():
                times.update(requested_at=stamp.isoformat(), completed_at=stamp.isoformat())
        return result

    proof.update(requested_at=now.isoformat(), received_at=now.isoformat())
    seed = build_seed(observed_account(), proof, proof, proof, proof,
                      account="U_TEST", as_of=now, config=config)

    class Sources:
        def read(self, *args, **kwargs):
            trigger = dict(
                ticker="BASE", triggered_at=now.date().isoformat(),
                trigger_type="BREAKOUT", final_score=99, quality_score=99,
                adjusted_score=None, ai_rating=99, ai_grade="A", next_earnings_date=None,
                volume_surge=2, pivot_distance_pct=None, close_price=100, atr_pct=None)
            return dict(rows=[trigger, dict(trigger, ticker="VETOED", ai_grade="D")],
                        complete=True, requested_at=now.isoformat(), received_at=now.isoformat())

        def observer_pair(self, account):
            assert account == "U_TEST"
            return observed_account()  # Actual book stays flat, unlike the hypothetical book.

    class Market:
        def __init__(self):
            self._history_cache = {}

        def history(self, symbol, as_of):
            key = symbol, as_of.date()
            if key in self._history_cache:
                return copy.deepcopy(self._history_cache[key])
            bars, day = [], as_of.date() - dt.timedelta(days=500)
            while day < as_of.date():
                if trading_days_between(day, day + dt.timedelta(days=1)):
                    price = 100 + len(bars)
                    bars.append(dict(date=day.isoformat(), open=price, high=price + 2,
                                     low=price - 1, close=price + 1, volume=10000))
                day += dt.timedelta(days=1)
            self._history_cache[key] = dict(bars=bars, available_at=as_of.isoformat())
            return copy.deepcopy(self._history_cache[key])

        def quotes(self, symbols):
            price = (104 if now.date().isoformat() == "2026-09-30" else 95) if (
                now.hour, now.minute) > (9, 30) else 100
            return {symbol: dict(
                price=price if symbol == "VETOED" else 100,
                provider_timestamp=now.isoformat(), received_at=now.isoformat(), source="FMP")
                for symbol in symbols}, []

    path = Path.cwd() / "tests" / (".shadow-calibration-" + uuid.uuid4().hex)
    path.mkdir()
    store = None
    try:
        store = ShadowStore(path / "spool.sqlite3")
        state = shadow.initialize(seed, config)
        run_id = store.create_run(seed, config, state, shadow.engine_fingerprint())
        producer = InputProducer(Sources(), Market(), "U_TEST", config, clock=lambda: now)
        cloud = Cloud()
        worker = Worker(store, producer, cloud, clock=lambda: now)
        for day in ("2026-09-30", "2026-10-01"):
            ticks = session_ticks(dt.date.fromisoformat(day))
            for now in ticks:
                assert worker.tick()
                assert worker.flush()
                assert store.active()["status"] == "running"
            if day == "2026-09-30":
                saved = shadow.checkpoint(store.active()["state"])
                store.close()
                store = ShadowStore(path / "spool.sqlite3")
                assert shadow.restore(store.active()["state"]) == saved
                producer = InputProducer(Sources(), Market(), "U_TEST", config, clock=lambda: now)
                worker = Worker(store, producer, cloud, clock=lambda: now)
        persisted = [json.loads(row["row_json"]) for row in store.db.execute(
            "SELECT row_json FROM events WHERE run_id=? ORDER BY sequence", (run_id,))]
        uploaded = sorted([row for (table, _), row in cloud.rows.items()
                           if table == "intraday_shadow_events"], key=lambda row: row["sequence"])
        assert uploaded == persisted
        for row in uploaded:
            assert all(row["payload"]["frame"]["source_evidence"].get(key) == value
                       for key, value in row["payload"]["source_evidence"].items())
        rows = [{k: row["payload"][k] for k in ("frame", "output")} for row in uploaded]
        paper_buys = [fill for row in rows for fill in row["output"]["fills"]
                      if fill["side"] == "BUY"]
        for row in rows:
            for event in row["frame"]["events"]:
                for trigger in event.get("triggers", []):
                    assert trigger["triggered_at"] == event["session"]
                    assert len(trigger["triggered_at"]) == 10
                    assert trigger["observed_at"] == event["timestamp"]
                    assert trigger["atr_pct"] is None
        assert any(fill["ticker"] == "BASE" and fill["shares"] > 0 for fill in paper_buys)
        assert all(fill["execution"] == "counterfactual_sampled_full_fill" for fill in paper_buys)
        assert seed["positions"] == []
        assert store.active()["state"]["positions"]["BASE"]["shares"] > 0
        assert store.active()["state"]["cash"] < seed["account"]["cash"]
        training = capture.export_shadow_dataset(seed, rows, "2026-09-30", "2026-09-30")
        holdout = capture.export_shadow_dataset(seed, rows, "2026-10-01", "2026-10-01")
        history_proofs = [
            proof for frame in holdout["prefix_frames"] + holdout["window_frames"]
            for proof in frame["source_evidence"]["daily_history"].values()]
        assert any("data" in proof for proof in history_proofs)
        assert any("reference" in proof for proof in history_proofs)
        plan = calibration.select(training, {"experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
        result = calibration.evaluate(plan, holdout)
        assert plan["selected_name"] == "no_veto"
        assert result["holdout_candidates_tested"] == 1
        assert result["frozen_candidate"]["summary"]["equity_delta_vs_recorded_config_baseline"] < 0
        assert holdout["initial_positions"]  # Real account is flat; window starts from baseline shadow holdings.
    finally:
        if store:
            store.close()
        shutil.rmtree(path)
