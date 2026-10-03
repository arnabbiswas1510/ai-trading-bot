import copy
import datetime as dt
from unittest.mock import Mock, MagicMock

import pytest

from research import calibration_worker as worker
from research import auto_calibration
from test_intraday_replay import records  # noqa: F401
from test_shadow_calibration import training_and_holdout


class MemoryStore:
    def __init__(self):
        self.config = {"revision": 0, "value": copy.deepcopy(auto_calibration.DEFAULT_SETTINGS)}
        self.rows = {}
        self.log = []
        self.health = None
        self.owner = None

    def settings(self):
        return copy.deepcopy(self.config)

    def list_proposals(self, limit=200):
        return copy.deepcopy(list(self.rows.values())[:limit])

    def has_proposal(self, identifier):
        return identifier in self.rows

    def proposal(self, identifier):
        return copy.deepcopy(self.rows[identifier])

    def deferred_status(self, identifier):
        return self.rows[identifier].get("_deferred_from", "evaluating")

    def research_queue(self):
        return [{key: copy.deepcopy(value) for key, value in row.items() if key != "artifact"}
                for row in self.rows.values()
                if row["status"] in ("evaluating", "deferred", "investigation_approved")]

    def create_proposal(self, row):
        if row["id"] not in self.rows:
            self.rows[row["id"]] = {**copy.deepcopy(row), "revision": 0,
                                     "created_at": "2026-10-03T12:00:00+00:00"}
        return copy.deepcopy(self.rows[row["id"]])

    def update_proposal(self, identifier, revision, patch, event, note="", data=None, child=None):
        row = self.rows[identifier]
        if row["revision"] != revision:
            raise RuntimeError("Concurrent proposal edit")
        row.update(copy.deepcopy(patch), revision=revision + 1)
        if child:
            self.create_proposal(child)
        self.add_event(identifier, event, note, data)
        return copy.deepcopy(row)

    def add_event(self, proposal_id, event, note="", data=None, event_id=None):
        identifier = event_id or str(len(self.log))
        if not any(row["id"] == identifier for row in self.log):
            self.log.append({"id": identifier, "proposal_id": proposal_id, "event": event,
                             "note": note, "data": data or {}, "notified_at": None})

    def set_health(self, status, error=None, metrics=None):
        self.health = {"status": status, "last_error": error, "metrics": metrics or {}}

    def claim(self, owner, seconds=1800):
        if self.owner:
            return False
        self.owner = owner
        return True

    def release(self, owner):
        assert self.owner == owner
        self.owner = None

    def events(self, pending_notifications=False):
        return [row for row in self.log if not pending_notifications or not row["notified_at"]]

    def mark_notified(self, identifier):
        next(row for row in self.log if row["id"] == identifier)["notified_at"] = "delivered"


def stamp(day, time="20:30"):
    return dt.datetime.fromisoformat(f"{day}T{time}:00+00:00")


def frozen(settings):
    return {"selection": {"selected_name": "candidate",
                           "training": {"observed_end": "2026-10-02T20:00:00+00:00"}},
            "settings": copy.deepcopy(settings), "hypotheses": []}


@pytest.fixture
def setup(monkeypatch):
    store = MemoryStore()
    source = {"run": {"id": "source-run"}}
    datasets = Mock()
    datasets.load.return_value = source
    datasets.completed_sessions.return_value = [
        "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    datasets.export.return_value = {"window": "training"}
    monkeypatch.setattr(auto_calibration, "freeze_selection",
                        lambda data, settings, **kwargs: frozen(settings))
    return store, datasets


def test_future_windows_start_after_freeze_not_after_training():
    assert worker.future_sessions(stamp("2026-10-03", "18:00"), 2) == ["2026-10-05", "2026-10-06"]
    assert worker.future_sessions(stamp("2026-10-05", "14:00"), 1) == ["2026-10-06"]
    assert worker.future_sessions(stamp("2026-10-05", "12:00"), 1) == ["2026-10-05"]
    assert worker.future_sessions(stamp("2026-11-25", "22:00"), 2) == ["2026-11-27", "2026-11-30"]


def test_freezes_one_campaign_and_deduplicates_week(setup):
    store, datasets = setup
    now = stamp("2026-10-03", "18:00")
    instance = worker.Worker(store, datasets, clock=lambda: now)
    instance.step()
    row = next(iter(store.rows.values()))
    assert row["status"] == "evaluating"
    assert row["artifact"]["evaluation_start"] == "2026-10-05"
    assert row["artifact"]["evaluation_end"] == "2026-10-09"
    store.rows[row["id"]]["status"] = "no_change"
    instance.step()
    assert len(store.rows) == 1
    assert len(store.log) == 1


def test_missing_source_is_visible_not_zero_performance(setup):
    store, datasets = setup
    datasets.load.side_effect = worker.WaitingForData("No shadow runs.")
    instance = worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03"))
    instance.step()
    instance.step()
    assert store.health["status"] == "waiting_for_data"
    assert store.rows == {}
    assert len(store.log) == 1
    assert "No calibrated recommendation" in store.log[0]["note"]


def test_disabled_and_deferred_do_not_start_replacement_campaign(setup):
    store, datasets = setup
    store.config["value"]["enabled"] = False
    worker.Worker(store, datasets).step()
    datasets.load.assert_not_called()
    store.config["value"]["enabled"] = True
    store.create_proposal({"id": "pending", "status": "deferred"})
    worker.Worker(store, datasets).step()
    assert store.health["status"] == "deferred"
    datasets.load.assert_not_called()


def test_deferring_unstarted_rule_does_not_stop_numeric_research(setup):
    store, datasets = setup
    store.create_proposal({"id": "later-rule", "kind": "rule", "status": "deferred",
                          "_deferred_from": "investigation_requested"})
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert any(row["status"] == "evaluating" for row in store.rows.values())


def test_partial_evaluation_never_becomes_ready_or_resets_window(setup, monkeypatch):
    store, datasets = setup
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    row = next(iter(store.rows.values()))
    datasets.completed_sessions.return_value = ["2026-10-05"]
    datasets.export.return_value = {"window": "evaluation"}
    monkeypatch.setattr(worker.calibrate_intraday, "validate_dataset",
                        lambda _: {"observed_start": "2026-10-05T13:30:00+00:00"})
    result = {"evaluation": {}, "eligibility": {"eligible": True, "reasons": []}}
    evaluate = Mock(return_value=result)
    monkeypatch.setattr(auto_calibration, "evaluate_selection", evaluate)
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-05")).step()
    assert store.rows[row["id"]]["status"] == "evaluating"
    assert not store.rows[row["id"]]["artifact"]["evaluation_complete"]
    datasets.completed_sessions.return_value = ["2026-10-05", "2026-10-06"]
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-06")).step()
    assert datasets.export.call_args.args[1:] == ("2026-10-05", "2026-10-06")
    assert evaluate.call_args.args[0] == row["artifact"]["frozen"]
    assert store.rows[row["id"]]["status"] == "evaluating"


def test_edited_risk_policy_cannot_qualify_observed_results(setup, monkeypatch):
    store, datasets = setup
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    row = next(iter(store.rows.values()))
    datasets.completed_sessions.return_value = ["2026-10-09"]
    monkeypatch.setattr(worker.calibrate_intraday, "validate_dataset",
                        lambda _: {"observed_start": "2026-10-05T13:30:00+00:00"})
    monkeypatch.setattr(auto_calibration, "evaluate_selection", lambda *_: {
        "evaluation": {}, "eligibility": {"eligible": True, "reasons": []}})
    store.config["value"]["risk_policy"] = {
        "min_completed_positions": 1, "min_distinct_sessions": 2,
        "min_improvement_usd": 100, "max_drawdown_increase_pp": 0,
        "max_worst_loss_increase_usd": 0, "min_positive_tickers": 2,
        "max_largest_contributor_fraction": 0.5,
    }
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-09")).step()
    row = store.rows[row["id"]]
    assert row["status"] == "no_change"
    assert "Risk policy changed" in row["artifact"]["evaluation"]["eligibility"]["reasons"][0]


def test_bad_evidence_blocks_entire_campaign_without_reset(setup, monkeypatch):
    store, datasets = setup
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    datasets.completed_sessions.return_value = ["2026-10-05"]
    datasets.export.side_effect = ValueError("Missing observation")
    with pytest.raises(ValueError, match="Missing observation"):
        worker.Worker(store, datasets, clock=lambda: stamp("2026-10-05")).step()
    assert next(iter(store.rows.values()))["status"] == "blocked"
    assert len(store.rows) == 1


def test_lease_and_notification_failure_do_not_undo_completed_research(setup, monkeypatch):
    store, datasets = setup
    store.owner = "other"
    assert worker.run_once(store, datasets, None, None) == "leased_elsewhere"
    datasets.load.assert_not_called()
    store.owner = None
    monkeypatch.setattr(worker, "deliver", Mock(side_effect=RuntimeError("delivery down")))
    with pytest.raises(RuntimeError, match="delivery down"):
        worker.run_once(store, datasets, None, None, clock=lambda: stamp("2026-10-03"))
    assert store.owner is None
    assert len(store.rows) == 1
    assert store.log[0]["notified_at"] is None


def test_approved_rule_request_is_linked_and_not_silently_deployed(setup, monkeypatch):
    store, datasets = setup
    request = store.create_proposal({
        "id": "rule-request", "kind": "rule", "status": "investigation_approved",
        "request": {"experiment": {"name": "no-veto", "disable_ai_veto": True}}})
    freeze = Mock(side_effect=lambda data, settings, **kwargs: frozen(settings))
    monkeypatch.setattr(auto_calibration, "freeze_selection", freeze)
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert freeze.call_args.kwargs["requested_experiments"] == [request["request"]["experiment"]]
    child = next(row for row in store.rows.values() if row.get("parent_id") == request["id"])
    assert child["status"] == "evaluating"
    assert store.rows[request["id"]]["request"]["campaign_id"] == child["id"]


def test_unapproved_rule_does_not_enter_parameter_sweep(setup, monkeypatch):
    store, datasets = setup
    store.create_proposal({"id": "new-rule", "kind": "rule", "status": "investigation_requested",
                          "request": {"experiment": {"name": "no-veto", "disable_ai_veto": True}}})
    freeze = Mock(side_effect=lambda data, settings, **kwargs: frozen(settings))
    monkeypatch.setattr(auto_calibration, "freeze_selection", freeze)
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert freeze.call_args.kwargs["requested_experiments"] == []


def test_source_sessions_exclude_partial_initial_day():
    source = {"run": {"initial_state": {"timestamp": "2026-10-01T15:00:00+00:00"}},
              "records": [{"frame": {"events": [
                  {"type": "end_mark", "session": "2026-10-01"},
                  {"type": "end_mark", "session": "2026-10-02"}]}}]}
    assert worker.ShadowDatasets.completed_sessions(source, stamp("2026-10-03")) == ["2026-10-02"]


def test_real_frozen_campaign_uses_unseen_shadow_window(records):
    training, holdout = training_and_holdout(records)
    source = {"run": {"id": "fixture-shadow"}}

    class Datasets:
        days = ["2026-09-28"]

        def load(self, run_id=None):
            assert run_id in (None, "fixture-shadow")
            return source

        def completed_sessions(self, source, now):
            return self.days

        def export(self, source, start, end):
            assert start == end
            return training if start == "2026-09-28" else holdout

    store, datasets = MemoryStore(), Datasets()
    store.config["value"].update(training_sessions=1, evaluation_sessions=1)
    store.create_proposal({
        "id": "approved-request", "kind": "rule", "status": "investigation_approved",
        "request": {"experiment": {"name": "no_veto", "disable_ai_veto": True}}})
    worker.Worker(store, datasets, clock=lambda: stamp("2026-09-28")).step()
    campaign = next(row for row in store.rows.values() if row.get("parent_id") == "approved-request")
    assert campaign["artifact"]["frozen"]["selection"]["selected_name"] == "no_veto"
    assert campaign["artifact"]["evaluation_sessions"] == ["2026-09-29"]
    datasets.days = ["2026-09-28", "2026-09-29"]
    worker.Worker(store, datasets, clock=lambda: stamp("2026-09-29")).step()
    result = store.proposal(campaign["id"])
    assert result["status"] == "no_change"
    assert result["artifact"]["evaluation_complete"]
    assert not result["artifact"]["evaluation"]["eligibility"]["eligible"]
    assert result["artifact"]["evaluation"]["evaluation"]["selected_name"] == "no_veto"


def test_revoked_investigation_does_not_create_child_campaign(setup, monkeypatch):
    store, datasets = setup
    store.create_proposal({
        "id": "request", "kind": "rule", "status": "investigation_approved",
        "request": {"experiment": {"name": "no_veto", "disable_ai_veto": True}}})

    def racing_selection(data, settings, **kwargs):
        store.rows["request"].update(status="rejected", revision=1)
        return frozen(settings)

    monkeypatch.setattr(auto_calibration, "freeze_selection", racing_selection)
    with pytest.raises(RuntimeError, match="Concurrent"):
        worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert list(store.rows) == ["request"]


def test_weekly_event_uses_real_store_contract():
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = {"id": "acknowledged"}
    store = worker.CalibrationStore(client)
    worker.Worker(store, None)._weekly_status("No usable sessions.", stamp("2026-10-03"))
    payload = client.rpc.call_args.args[1]["p_payload"]
    assert payload["event"] == "weekly_update"
    assert payload["proposal_id"] is None


def test_superseded_campaign_source_is_terminal_not_waiting(setup):
    store, datasets = setup
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    client = MagicMock()
    query = client.table.return_value.select.return_value
    query.eq.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.execute.return_value.data = [{"status": "superseded"}]
    actual = worker.ShadowDatasets(client)
    with pytest.raises(ValueError, match="superseded"):
        worker.Worker(store, actual, clock=lambda: stamp("2026-10-05")).step()
    assert next(iter(store.rows.values()))["status"] == "blocked"


def test_restart_recovers_notification_after_committed_selection(setup, monkeypatch):
    store, datasets = setup
    saved_add = store.add_event
    monkeypatch.setattr(store, "add_event", Mock(side_effect=RuntimeError("connection lost")))
    with pytest.raises(RuntimeError, match="connection lost"):
        worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert len(store.rows) == 1
    monkeypatch.setattr(store, "add_event", saved_add)
    worker.Worker(store, datasets, clock=lambda: stamp("2026-10-03")).step()
    assert len(store.rows) == 1
    assert store.log[0]["event"] == "selection_frozen"


def test_one_notification_failure_does_not_starve_later_notices(monkeypatch):
    store = MemoryStore()
    store.add_event(None, "first", data={"notification": True})
    store.add_event(None, "second", data={"notification": True})
    delivery = Mock(side_effect=[worker.ReportingError("recipient unavailable"), None])
    monkeypatch.setattr(worker, "deliver", delivery)
    with pytest.raises(worker.ReportingError, match="1 calibration notifications"):
        worker.notify(store, None, None, stamp("2026-10-03"))
    assert store.log[0]["notified_at"] is None
    assert store.log[1]["notified_at"] == "delivered"
