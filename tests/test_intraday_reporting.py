"""Offline contract tests: no HTTP, brokerage, production data or notifications."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_reporting as reporting
from research.intraday_reporting_delivery import operational_issue_body
from test_intraday_replay import records  # noqa: F401 — shared synthetic captured-input fixture


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=reporting.NY)


@pytest.fixture(autouse=True)
def offline_calendar(monkeypatch):
    def bounds(day):
        day = dt.date.fromisoformat(day) if isinstance(day, str) else day
        if day.weekday() >= 5 or day.isoformat() in ("2026-11-26", "2026-12-25"):
            return None
        close = 13 if day.isoformat() in ("2026-11-27", "2026-12-24") else 16
        return (dt.datetime.combine(day, dt.time(9, 30), reporting.NY),
                dt.datetime.combine(day, dt.time(close), reporting.NY))
    monkeypatch.setattr(reporting, "session_bounds", bounds)


class MemoryStore:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.writes = []
        self.claimed = False

    def select(self, table, params=None):
        rows = list(self.rows.get(table, {}).values())
        for key, query in (params or {}).items():
            if isinstance(query, str) and query.startswith("eq."):
                rows = [row for row in rows if str(row.get(key)) == query[3:]]
        return rows[:int((params or {}).get("limit", 100000))]

    def all(self, table, params=None):
        return self.select(table, params)

    def put(self, table, row, ignore=False):
        assert table in reporting.WRITE_TABLES
        self.writes.append((table, row))
        table_rows = self.rows.setdefault(table, {})
        if not ignore or row["id"] not in table_rows:
            table_rows[row["id"]] = dict(row)

    def rpc(self, name, worker):
        if name == "release_intraday_reporting":
            self.claimed = False
            return None
        if self.claimed:
            return False
        self.claimed = True
        return True


class FakeTelegram:
    def __init__(self):
        self.recipients = ["one", "two"]
        self.sent = []
        self.fail = set()

    def send(self, recipient, body):
        if recipient in self.fail:
            raise reporting.ReportingError("Telegram fake failure")
        self.sent.append((recipient, body))


class FakeIssues:
    def __init__(self):
        self.rows = {}
        self.created = 0

    def find(self, key):
        return self.rows.get(key)

    def ensure(self, key, body):
        if key not in self.rows:
            self.created += 1
            self.rows[key] = {"number": self.created, "body": operational_issue_body(key), "key": key}
        return self.rows[key]

    def update(self, issue, **fields):
        issue.update(fields)
        if fields.get("state") == "closed":
            self.rows.pop(issue["key"], None)

    def close(self, key):
        self.rows.pop(key, None)


def healthy(now):
    iso = now.isoformat()
    return dict(
        observer={"id": "intraday-observer", "last_seen_at": iso, "last_persisted_at": iso,
                  "config": {"run_id": "observer", "spool_available": True}},
        shadow={"id": "shadow-worker", "last_seen_at": iso, "last_cycle_at": iso,
                "last_persisted_at": iso, "status": "running", "run_id": "shadow"},
        snapshot={"payload": {"snapshot_at": iso, "complete": True}},
        quotes={"occurred_at": iso, "payload": {"complete": True,
                                               "quotes": [{"provider_timestamp": iso}]}},
        shadow_output={"occurred_at": iso, "kind": "cycle"},
        shadow_decision={"occurred_at": iso, "kind": "cycle", "payload": {"frame": {
            "events": [{"type": "buy_cycle"}, {"type": "monitor"}]}}},
    )


def test_missing_workers_fail_during_market_but_no_trader_required():
    now = at("2026-09-30T10:00")
    assert not reporting.health_failures(now, **healthy(now))
    faults = reporting.health_failures(now, None, None, None, None)
    assert {"observer-heartbeat", "observer-output", "broker-snapshot",
            "quote-coverage", "shadow-heartbeat", "shadow-progress"} <= faults.keys()
    assert not any("trader" in key for key in faults)


def test_calibration_is_supervised_even_outside_market_hours():
    now = at("2026-10-03T12:00")
    inputs = healthy(now)
    inputs["calibration_required"] = True
    assert "calibration-heartbeat" in reporting.health_failures(now, **inputs)
    inputs["calibration"] = {"last_seen_at": now.isoformat(), "status": "waiting_for_data"}
    assert not reporting.health_failures(now, **inputs)
    inputs["calibration"]["status"] = "error"
    assert "calibration-progress" in reporting.health_failures(now, **inputs)
    inputs["calibration"]["status"] = "disabled"
    assert not reporting.health_failures(now, **inputs)
    inputs["calibration"]["last_seen_at"] = (now - dt.timedelta(minutes=31)).isoformat()
    assert "calibration-heartbeat" in reporting.health_failures(now, **inputs)


def test_heartbeat_does_not_hide_missing_or_stale_shadow_output():
    now = at("2026-09-30T10:00")
    inputs = healthy(now)
    inputs["shadow_output"] = None
    assert "shadow-progress" in reporting.health_failures(now, **inputs)
    inputs = healthy(now)
    inputs["shadow"]["last_cycle_at"] = at("2026-09-30T09:40").isoformat()
    assert "shadow-progress" in reporting.health_failures(now, **inputs)


def test_worker_waiting_between_ticks_is_healthy_only_with_fresh_cycle_output():
    now = at("2026-09-30T10:00")
    inputs = healthy(now)
    inputs["shadow"]["status"] = "waiting"
    assert reporting.health_failures(now, **inputs) == {}
    inputs["shadow_output"] = None
    assert "shadow-progress" in reporting.health_failures(now, **inputs)
    inputs = healthy(now)
    inputs["shadow"]["status"] = "waiting"
    inputs["shadow"]["last_cycle_at"] = at("2026-09-30T09:40").isoformat()
    assert "shadow-progress" in reporting.health_failures(now, **inputs)


def test_fresh_quote_frames_cannot_hide_stalled_fifteen_minute_decisions():
    now = at("2026-09-30T10:00")
    inputs = healthy(now)
    inputs["shadow_decision"]["occurred_at"] = at("2026-09-30T09:41").isoformat()
    assert reporting.health_failures(now, **inputs) == {}
    inputs["shadow_decision"]["occurred_at"] = at("2026-09-30T09:39").isoformat()
    faults = reporting.health_failures(now, **inputs)
    assert "20 minutes" in faults["shadow-progress"]
    inputs = healthy(now)
    inputs["shadow_decision"]["payload"]["frame"]["events"] = [{"type": "quote"}]
    assert "shadow-progress" in reporting.health_failures(now, **inputs)


def test_health_query_requires_paired_buy_monitor_input_not_just_a_heartbeat():
    now = at("2026-09-30T10:00")
    data = healthy(now)
    queried = []
    class QueryStore:
        def select(self, table, params):
            queried.append((table, params))
            if table == "intraday_capture_health":
                return [data["observer"]]
            if table == "intraday_shadow_health":
                return [data["shadow"]]
            return []
    reporting.load_health(QueryStore())
    decision_queries = [params for table, params in queried
                        if table == "intraday_shadow_events" and "payload->frame->events" in params]
    assert len(decision_queries) == 1
    assert decision_queries[0]["payload->frame->events"] == 'cs.[{"type":"buy_cycle"},{"type":"monitor"}]'
    assert decision_queries[0]["run_id"] == "eq.shadow"


def test_fresh_receipt_does_not_hide_stale_provider_quote():
    now = at("2026-09-30T10:00")
    inputs = healthy(now)
    inputs["quotes"]["payload"]["quotes"][0]["provider_timestamp"] = at("2026-09-30T09:30").isoformat()
    assert "quote-coverage" in reporting.health_failures(now, **inputs)
    inputs["quotes"]["payload"] = {"quotes": [], "complete": True}
    assert "quote-coverage" in reporting.health_failures(now, **inputs)


@pytest.mark.parametrize("time", ["2026-09-30T09:35", "2026-10-03T10:00",
                                   "2026-11-26T11:00", "2026-11-27T13:01"])
def test_expected_market_excludes_startup_weekend_holiday_and_early_close(time):
    assert reporting.health_failures(at(time), None, None, None, None) == {}


def test_daily_waits_thirty_minutes_after_early_close():
    start = dt.date(2026, 11, 27)
    assert reporting.due_periods(at("2026-11-27T13:29"), start) == []
    rows = reporting.due_periods(at("2026-11-27T13:30"), start)
    assert rows == [{"id": "daily:2026-11-27", "report_kind": "daily",
                     "period_start": "2026-11-27", "period_end": "2026-11-27"}]


def test_dst_and_weekly_catchup_use_new_york_dates_not_fixed_utc_hours():
    start = dt.date(2026, 10, 26)
    before = dt.datetime(2026, 11, 2, 12, 59, tzinfo=reporting.UTC)
    after = dt.datetime(2026, 11, 2, 13, 0, tzinfo=reporting.UTC)
    assert not any(r["report_kind"] == "weekly" for r in reporting.due_periods(before, start))
    assert "weekly:2026-10-26" in {r["id"] for r in reporting.due_periods(after, start)}
    caught = reporting.due_periods(at("2026-11-04T09:00"), start)
    assert len({r["id"] for r in caught}) == len(caught)
    assert {"daily:2026-10-26", "weekly:2026-10-26", "daily:2026-11-03"} <= {r["id"] for r in caught}


def test_zero_data_report_is_persistable_missing_evidence_not_zero_pnl():
    period = {"id": "daily:2026-09-30", "report_kind": "daily",
              "period_start": "2026-09-30", "period_end": "2026-09-30"}
    report = reporting.build_report(period, [], [], None, at("2026-09-30T16:31"))
    assert report["payload"]["coverage"]["sessions_expected"] == 1
    assert report["payload"]["shadow"]["realized_pnl"] is None
    assert report["payload"]["shadow"]["completed_positions"] is None
    assert report["payload"]["shadow"]["contribution_concentration"] is None
    assert report["payload"]["evidence_warnings"]
    assert "HYPOTHETICAL" in report["body"] and "NOT real trades" in report["body"]
    assert report["payload"]["human_review_required"] is True
    assert not report["payload"]["calibration"]["available"]
    assert report["payload"]["calibration"]["recommendation_status"] == "none"
    assert "No calibrated recommendation" in report["body"]


def test_retry_only_failed_recipient_and_chunk():
    store, telegram = MemoryStore(), FakeTelegram()
    telegram.fail = {"two"}
    with pytest.raises(reporting.ReportingError):
        reporting.deliver(store, telegram, "daily:one", "test", at("2026-09-30T17:00"))
    telegram.fail.clear()
    reporting.deliver(store, telegram, "daily:one", "test", at("2026-09-30T17:15"))
    reporting.deliver(store, telegram, "daily:one", "test", at("2026-09-30T17:30"))
    assert [r for r, _ in telegram.sent] == ["one", "two"]
    assert len(store.rows[reporting.RECEIPTS]) == 2


def test_db_outage_fallback_dedups_retries_and_recovers_without_db():
    issues, telegram = FakeIssues(), FakeTelegram()
    telegram.fail = {"two"}
    with pytest.raises(reporting.ReportingError):
        reporting.fallback_alert(issues, telegram, "database", "safe alert")
    telegram.fail.clear()
    reporting.fallback_alert(issues, telegram, "database", "safe alert")
    reporting.fallback_alert(issues, telegram, "database", "safe alert")
    assert [r for r, _ in telegram.sent] == ["one", "two"]
    assert issues.created == 1
    reporting.fallback_alert(issues, telegram, "database", "recovered", recovery=True)
    assert not issues.rows
    assert len(telegram.sent) == 4
    reporting.fallback_alert(issues, telegram, "database", "recovered", recovery=True)
    assert len(telegram.sent) == 4


def test_issue_failure_still_attempts_telegram():
    class DownIssues(FakeIssues):
        def ensure(self, key, body):
            raise reporting.ReportingError("GitHub unavailable")
    telegram = FakeTelegram()
    with pytest.raises(reporting.ReportingError):
        reporting.fallback_alert(DownIssues(), telegram, "database", "safe alert")
    assert len(telegram.sent) == 2


def test_incident_dedup_and_recovery_require_fresh_market_evidence():
    now = at("2026-09-30T10:00")
    store, telegram, issues = MemoryStore(), FakeTelegram(), FakeIssues()
    data = healthy(now)
    data["shadow_output"] = None
    reporting.monitor(store, telegram, issues, now, data)
    reporting.monitor(store, telegram, issues, now, data)
    assert len(store.rows[reporting.INCIDENTS]) == 1
    assert len(telegram.sent) == 2
    reporting.monitor(store, telegram, issues, at("2026-09-30T20:00"), data)
    assert list(store.rows[reporting.INCIDENTS].values())[0]["status"] == "open"
    reporting.monitor(store, telegram, issues, now, healthy(now))
    assert list(store.rows[reporting.INCIDENTS].values())[0]["status"] == "resolved"
    assert len(telegram.sent) == 4


def test_daily_catchup_delivery_stays_frozen_and_no_duplicates(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-29"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    result = reporting.run(store, telegram, issues, now)
    assert result["reports_delivered"] == ["daily:2026-09-29", "daily:2026-09-30"]
    assert len(telegram.sent) == 4
    result = reporting.run(store, telegram, issues, now)
    assert result["reports_delivered"] == []
    assert len(telegram.sent) == 4
    assert not store.claimed


def test_db_missing_alert_does_not_require_database_write(monkeypatch, capsys):
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "Telegram", lambda *_: telegram)
    monkeypatch.setattr(reporting, "Issues", lambda *_: issues)
    assert reporting.main(env={}, now=at("2026-09-30T12:00")) == 1
    assert "database" in issues.rows
    assert len(telegram.sent) == 2
    assert "::error::" in capsys.readouterr().err


def test_network_errors_are_redacted():
    class HTTP:
        def request(self, *_args, **_kwargs):
            raise RuntimeError("https://private?apikey=TOP_SECRET")
        post = request
    store = reporting.Store("https://private", "TOP_SECRET", HTTP())
    with pytest.raises(reporting.StorageError, match="RuntimeError") as error:
        store.select(reporting.REPORTS)
    assert "TOP_SECRET" not in str(error.value)
    telegram = reporting.Telegram("TOP_SECRET", ["one"], HTTP())
    with pytest.raises(reporting.ReportingError) as error:
        telegram.send("one", "body")
    assert "TOP_SECRET" not in str(error.value)


def test_reporting_store_forbids_live_writes():
    store = reporting.Store("https://unused", "unused")
    for table in ("daily_notifications", "portfolio_positions", "trade_history", "daily_triggers"):
        with pytest.raises(reporting.ReportingError, match="forbidden"):
            store.put(table, {"id": "never"})


def test_report_workflow_is_independent_private_and_failure_visible():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/intraday_research_review.yml").read_text()
    assert "'*/15 * * * *'" in text
    assert "issues: write" in text
    assert "cancel-in-progress: false" in text
    assert "INTRADAY_SUPABASE_KEY" in text
    assert "FMP_API_KEY" not in text
    assert "vars.TRADING_RUNTIME_MODE || 'observe'" in text
    assert "continue-on-error" not in text
    assert "|| true" not in text
    assert "python research_entrypoint.py research-reporting" in text
    assert "SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}" in text


def test_private_reporting_schema_and_delivery_identity():
    root = Path(__file__).resolve().parents[1]
    text = (root / "migrations/20260930_add_intraday_reporting.sql").read_text()
    assert "UNIQUE (notification_id, recipient_hash)" in text
    assert "lease_until < now()" in text
    for table in (reporting.REPORTS, reporting.INCIDENTS, reporting.RECEIPTS, reporting.STATE):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in text
    assert "FROM PUBLIC, anon, authenticated" in text
    assert "daily_notifications" not in text


def output_events():
    day = dt.date(2026, 9, 30)
    current = dt.datetime.combine(day, dt.time(9, 30), reporting.NY)
    close = dt.datetime.combine(day, dt.time(16), reporting.NY)
    result = []
    while current <= close:
        output = {"origin": "decision_only_shadow", "hypothetical": True,
                  "timestamp": current.isoformat(), "equity": 1000, "cash": 1000,
                  "positions": [], "fills": [], "position_sales": [], "decisions": [],
                  "equity_curve": [{"timestamp": current.isoformat(), "equity": 1000}]}
        result.append({"id": f"shadow:{len(result) + 1}", "run_id": "shadow",
                       "sequence": len(result) + 1, "kind": "cycle", "session": day.isoformat(),
                       "occurred_at": current.isoformat(), "payload": {"output": output}})
        current += dt.timedelta(minutes=10)
    return result


def test_completed_positions_do_not_count_partial_sales_and_metrics_have_scopes():
    events = output_events()
    last = events[-1]["payload"]["output"]
    last["cash"] = last["equity"] = 1050
    last["equity_curve"] = [{"equity": 1050}, {"equity": 1040}, {"equity": 1050}]
    last["position_sales"] = [
        {"ticker": "ABC", "buy_date": "2026-09-30", "partial": True, "net_profit_loss": 20},
        {"ticker": "ABC", "buy_date": "2026-09-30", "partial": False, "net_profit_loss": 30}]
    last["fills"] = [{"commission": 1.5, "quote": 10, "price": 9.9, "shares": 10}]
    metrics, warning = reporting.shadow_performance(events, ["2026-09-30"])
    assert "checkpoints unavailable" in warning
    assert metrics["completed_positions"] == 1
    assert metrics["realized_pnl"] == 50
    assert metrics["unrealized_pnl"] == 0
    assert metrics["cash"] == metrics["equity"] == 1050
    assert metrics["costs"]["commission"] == 1.5
    assert metrics["costs"]["slippage"] == pytest.approx(1)
    assert metrics["drawdown"] is None
    assert metrics["contribution_concentration"] is None
    assert "within the report period" in metrics["performance_scope"]


def test_sell_intent_and_execution_notice_do_not_double_count_fill_records():
    events = output_events()
    output = events[-1]["payload"]["output"]
    output["decisions"] = [
        {"action": "SCALE_OUT", "portfolio_action": "SELL", "reason": "scale"},
        {"action": "SELL", "portfolio_action": "SELL", "reason": "scale"},
        {"action": "ARM_PROVE_IT", "portfolio_action": "HOLD", "reason": "arm"},
    ]
    output["fills"] = [{"side": "SELL", "commission": 1, "quote": 10, "price": 10, "shares": 1}]
    period = {"id": "daily:2026-09-30", "report_kind": "daily",
              "period_start": "2026-09-30", "period_end": "2026-09-30"}
    result = reporting.build_report(period, [], events, None, at("2026-09-30T16:31"))
    shadow = result["payload"]["shadow"]
    assert shadow["decisions"]["SELL"] == 2
    assert shadow["decisions"]["HOLD"] == 1
    assert shadow["observed_fill_records"]["SELL"] == 1
    assert "proposed action and its later execution notice" in result["body"]


@pytest.mark.parametrize("problem", ["gap", "sequence", "multi-run", "missing-output", "nonfinite"])
def test_incomplete_or_unsupported_shadow_evidence_refuses_pnl(problem):
    events = output_events()
    if problem == "gap":
        events[2]["kind"] = "gap"
    elif problem == "sequence":
        events.pop(2)
    elif problem == "multi-run":
        events[2]["run_id"] = "other"
    elif problem == "missing-output":
        del events[2]["payload"]["output"]
    else:
        events[2]["payload"]["output"]["equity"] = float("nan")
    metrics, warning = reporting.shadow_performance(events, ["2026-09-30"])
    assert metrics == {} and "unavailable" in warning


def test_shadow_starting_late_does_not_claim_complete_session_performance():
    metrics, warning = reporting.shadow_performance(output_events()[3:], ["2026-09-30"])
    assert metrics == {}
    assert "coverage" in warning


def test_last_minute_preclose_mark_counts_as_complete_sampled_session():
    events = output_events()
    final = events[-1]
    final["occurred_at"] = "2026-09-30T15:59:00-04:00"
    final["payload"]["output"]["timestamp"] = final["occurred_at"]
    final["payload"]["output"]["equity_curve"][0]["timestamp"] = final["occurred_at"]
    metrics, warning = reporting.shadow_performance(events, ["2026-09-30"])
    assert "checkpoints unavailable" in warning
    assert metrics["last_mark_at"] == "2026-09-30T15:59:00-04:00"
    assert metrics["equity"] == 1000


def frozen_artifact():
    value = {"artifact_type": "intraday_calibration_holdout", "version": 1,
             "recommendation": "research_only_manual_approval", "selection_plan_sha256": "frozen",
             "holdout_candidates_tested": 1,
             "training": {"observed_start": "2026-09-01T09:30:00-04:00",
                          "observed_end": "2026-09-10T16:00:00-04:00"},
             "holdout": {"observed_start": "2026-09-11T09:30:00-04:00",
                         "observed_end": "2026-09-18T16:00:00-04:00"},
             "baseline": {"status": "modeled", "summary": {
                 "final_equity_net": 1000, "equity_delta_vs_recorded_config_baseline": 0,
                 "n_completed_positions": 2, "n_distinct_sessions": 4}},
             "frozen_candidate": {"status": "modeled", "summary": {
                 "final_equity_net": 1010, "equity_delta_vs_recorded_config_baseline": 10,
                 "n_completed_positions": 2, "n_distinct_sessions": 4}}}
    value["artifact_sha256"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return value


def test_saved_frozen_calibration_is_archived_not_selected_or_rerun():
    artifact, store = frozen_artifact(), MemoryStore()
    key = reporting.save_calibration(store, artifact)
    assert key == artifact["artifact_sha256"]
    assert store.rows[reporting.CALIBRATIONS][key]["holdout_end"] == "2026-09-18"
    period = {"id": "weekly:2026-09-14", "report_kind": "weekly",
              "period_start": "2026-09-14", "period_end": "2026-09-20"}
    report = reporting.build_report(period, [], [], None, at("2026-09-21T08:00"), artifact)
    assert report["payload"]["calibration"]["available"]
    assert "Reused holdout data is not independent" in report["body"]
    assert "completed positions 2" in report["body"]


def test_tampered_frozen_artifact_cannot_be_published():
    artifact = frozen_artifact()
    artifact["frozen_candidate"]["summary"]["final_equity_net"] = 999999
    with pytest.raises(reporting.ReportingError, match="intact"):
        reporting.save_calibration(MemoryStore(), artifact)


def test_real_exchange_calendar_holidays_early_close_and_dst():
    from market_calendar import session_bounds
    assert session_bounds("2026-11-26") is None
    assert session_bounds("2026-11-27")[1].hour == 13
    assert session_bounds("2026-03-06")[0].astimezone(reporting.UTC).hour == 14
    assert session_bounds("2026-03-09")[0].astimezone(reporting.UTC).hour == 13


def test_telegram_http_200_without_ok_is_a_failure_and_redacts_response():
    class Response:
        status_code = 200
        def json(self):
            return {"ok": False, "description": "TOP_SECRET"}
    class HTTP:
        def post(self, *_args, **_kwargs):
            return Response()
    with pytest.raises(reporting.ReportingError) as error:
        reporting.Telegram("TOP_SECRET", ["one"], HTTP()).send("one", "body")
    assert "TOP_SECRET" not in str(error.value)


def test_chunk_retry_preserves_successful_chunk_receipts():
    class PartialTelegram(FakeTelegram):
        def send(self, recipient, body):
            if body.endswith("\nb") and self.fail:
                raise reporting.ReportingError("fake chunk failure")
            self.sent.append((recipient, body))
    telegram, store = PartialTelegram(), MemoryStore()
    telegram.recipients, telegram.fail = ["one"], {"once"}
    body = "a" * 3500 + "b"
    with pytest.raises(reporting.ReportingError):
        reporting.deliver(store, telegram, "long", body, at("2026-09-30T17:00"))
    telegram.fail.clear()
    reporting.deliver(store, telegram, "long", body, at("2026-09-30T17:15"))
    assert [chunk.split("\n", 1)[1] for _, chunk in telegram.sent] == ["a" * 3500, "b"]
    assert all("HYPOTHETICAL" in chunk for _, chunk in telegram.sent)


def test_report_partial_delivery_keeps_body_and_retries_failed_recipient(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-30"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    telegram.fail = {"two"}
    with pytest.raises(reporting.ReportingError):
        reporting.run(store, telegram, issues, now)
    body = store.rows[reporting.REPORTS]["daily:2026-09-30"]["body"]
    assert store.rows[reporting.REPORTS]["daily:2026-09-30"]["status"] == "pending"
    telegram.fail.clear()
    reporting.run(store, telegram, issues, now)
    assert store.rows[reporting.REPORTS]["daily:2026-09-30"]["status"] == "delivered"
    assert telegram.sent == [("one", body), ("two", body)]
    assert not issues.rows


def test_http_store_paginates_without_silent_truncation():
    calls = []
    class Response:
        status_code, content = 200, b"json"
        def __init__(self, rows):
            self.rows = rows
        def json(self):
            return self.rows
    class HTTP:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            offset = kwargs["params"]["offset"]
            return Response([{"id": str(i)} for i in range(500)] if offset == 0 else [{"id": "500"}])
    store = reporting.Store("https://unused", "unused", HTTP())
    assert len(store.all(reporting.REPORTS)) == 501
    assert [call[2]["params"]["offset"] for call in calls] == [0, 500]
    assert all(call[0] == "GET" for call in calls)
    with pytest.raises(reporting.ReportingError, match="truncated"):
        store.all(reporting.REPORTS, max_rows=500)


def test_single_writer_lease_prevents_duplicate_sweeps(monkeypatch):
    store, telegram, issues = MemoryStore(), FakeTelegram(), FakeIssues()
    store.claimed = True
    monkeypatch.setattr(reporting, "load_health", lambda _: pytest.fail("lease already owned"))
    assert reporting.run(store, telegram, issues, at("2026-09-30T17:00")) == {
        "leased_elsewhere": True, "runtime_mode": "observe"}
    assert not telegram.sent and not store.writes


def test_rejected_evidence_does_not_block_later_periods_or_retry_forever(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-29"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    def events(_store, _table, period):
        if period["period_start"] == "2026-09-29":
            raise reporting.ReportingError("Evidence exceeds bounded report limit")
        return []
    monkeypatch.setattr(reporting, "_period_events", events)
    with pytest.raises(reporting.ReportingError):
        reporting.run(store, telegram, issues, now)
    rejected = store.rows[reporting.REPORTS]["daily:2026-09-29"]
    assert rejected["payload"]["coverage"] is None
    assert rejected["payload"]["shadow"] is None
    assert rejected["status"] == "delivered"
    assert store.rows[reporting.REPORTS]["daily:2026-09-30"]["status"] == "delivered"
    assert "report-evidence:daily:2026-09-29" in issues.rows
    reporting.run(store, telegram, issues, now)
    assert len(telegram.sent) == 4


def test_runtime_budget_cannot_be_swallowed_as_a_rejected_report(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-30"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    def timeout(*_):
        raise reporting.RuntimeBudgetExceeded()
    monkeypatch.setattr(reporting, "_period_events", timeout)
    with pytest.raises(reporting.RuntimeBudgetExceeded):
        reporting.run(store, telegram, issues, now)
    assert not store.claimed
    assert not store.rows.get(reporting.REPORTS)


def test_actual_engine_outputs_produce_report_metrics_not_placeholder_rejection(records):
    import intraday_replay
    import shadow_engine
    from test_intraday_replay import DAY
    from test_shadow_engine import seed_and_frames

    data = intraday_replay.build_dataset(records, DAY, DAY)
    for event in data["events"]:
        for trigger in event.get("triggers", []):
            trigger["ai_grade"] = "A"
    seed, frames = seed_and_frames(data)
    state = shadow_engine.initialize(seed, seed["config"])
    opening_checkpoint = state
    shadow_events, source_events = [], []
    for index, frame in enumerate(frames, 1):
        state, output = shadow_engine.advance(state, frame)
        stamp = frame["captured_at"]
        shadow_events.append({"id": f"shadow:{index}", "run_id": "shadow", "sequence": index,
                              "kind": "cycle", "session": DAY, "occurred_at": stamp,
                              "payload": {"frame": frame, "output": output}})
        common = {"run_id": "observer", "session": DAY, "occurred_at": stamp}
        source_events += [
            {**common, "kind": "observer_snapshot", "payload": {"snapshot_at": stamp, "complete": True}},
            {**common, "kind": "quote_sample", "payload": {
                "complete": True, "missing_quotes": [], "quotes": [
                    {"ticker": ticker, "source": quote["source"], "price": quote["price"],
                     "provider_timestamp": quote["observed_at"]}
                    for ticker, quote in frame["events"][0]["market_observations"].items()]}}]
    period = {"id": f"daily:{DAY}", "report_kind": "daily", "period_start": DAY, "period_end": DAY}
    report = reporting.build_report(period, source_events, shadow_events, None, at(DAY + "T16:31"),
                                    start_checkpoint=opening_checkpoint, end_checkpoint=state)
    metrics = report["payload"]["shadow"]
    assert metrics["equity"] == state["equity"]
    assert metrics["cash"] == state["cash"]
    assert metrics["completed_positions"] == state["closed_positions"]
    assert metrics["decisions"]["BUY"] > 0
    assert metrics["costs"] is not None
    assert metrics["equity_pnl"] == pytest.approx(state["equity"] - opening_checkpoint["equity"])
    assert metrics["drawdown"] is not None
    assert not any("unavailable" in warning or "withheld" in warning
                   for warning in report["payload"]["evidence_warnings"])


def test_actual_frozen_shadow_evaluation_can_be_archived_and_displayed(records):
    from research import calibrate_intraday
    from test_shadow_calibration import training_and_holdout

    training, holdout = training_and_holdout(records)
    plan = calibrate_intraday.select(training, {
        "experiments": [{"name": "no_veto", "disable_ai_veto": True}]})
    artifact = calibrate_intraday.evaluate(plan, holdout)
    store = MemoryStore()
    key = reporting.save_calibration(store, artifact)
    row = store.rows[reporting.CALIBRATIONS][key]
    assert row["holdout_end"] == "2026-09-29"
    period = {"id": "weekly:2026-09-28", "report_kind": "weekly",
              "period_start": "2026-09-28", "period_end": "2026-10-04"}
    result = reporting.build_report(period, [], [], None, at("2026-10-05T08:00"), row["artifact"])
    assert result["payload"]["calibration"]["available"] is True
    assert "frozen_candidate: modeled" in result["body"]
    assert "Reused holdout data is not independent" in result["body"]
    assert "same reproduced hypothetical portfolio" in result["body"]


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_compatibility_modes_supervise_workers_and_require_evidence_for_recovery(monkeypatch, mode):
    now = at("2026-09-30T10:00")
    assert "shadow-progress" in reporting.health_failures(now, None, None, None, None, runtime_mode=mode)
    incident = {"id": "prior", "issue_key": "shadow-progress", "status": "open",
                "body": "Prior shadow progress failure"}
    store = MemoryStore({
        reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-30"}},
        reporting.INCIDENTS: {"prior": incident},
    })
    telegram, issues = FakeTelegram(), FakeIssues()
    health = healthy(now)
    health["shadow_output"] = None
    monkeypatch.setattr(reporting, "load_health", lambda _: health)
    with pytest.raises(reporting.CollectionAttention):
        reporting.run(store, telegram, issues, now, runtime_mode=mode)
    assert "shadow-progress" in issues.rows
    assert store.rows[reporting.INCIDENTS]["prior"]["status"] == "open"
    health["shadow_output"] = healthy(now)["shadow_output"]
    result = reporting.run(store, telegram, issues, now, runtime_mode=mode)
    assert result["runtime_mode"] == mode
    assert result["collection_monitoring"] == "expected"
    assert store.rows[reporting.INCIDENTS]["prior"]["status"] == "resolved"
    assert not issues.rows


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_reports_expect_continuous_research_without_claiming_live_entry_permission(monkeypatch, mode):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-30"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    reporting.run(store, telegram, issues, now, runtime_mode=mode)
    row = store.rows[reporting.REPORTS]["daily:2026-09-30"]
    assert row["payload"]["runtime_mode"] == mode
    assert row["payload"]["expected_services"] == ["intraday-observer", "shadow-worker"]
    assert "observer + shadow always expected" in row["body"]
    assert "Live entry permission is controlled separately in the dashboard" in row["body"]
    assert "real trading disabled" not in row["body"]
    assert "No calibrated recommendation" in row["body"]
    assert not issues.rows


@pytest.mark.parametrize("mode", ["observe", "live"])
def test_rejected_reports_still_expect_both_research_workers(mode):
    period = {"id": "daily:2026-09-30", "report_kind": "daily",
              "period_start": "2026-09-30", "period_end": "2026-09-30"}
    row = reporting.rejected_report(period, at("2026-09-30T17:00"), ValueError("bad evidence"), mode)
    assert row["payload"]["expected_services"] == ["intraday-observer", "shadow-worker"]


def test_unknown_runtime_mode_is_not_silently_treated_as_disabled():
    with pytest.raises(reporting.ReportingError, match="Unsupported TRADING_RUNTIME_MODE"):
        reporting.run(MemoryStore(), FakeTelegram(), FakeIssues(), at("2026-09-30T10:00"),
                      runtime_mode="typo")


def test_github_transport_never_publishes_private_report_body():
    requests_seen = []
    class Response:
        status_code = 200
        def __init__(self, data):
            self.data = data
        def json(self):
            return self.data
    class HTTP:
        def request(self, method, _url, **kwargs):
            requests_seen.append((method, kwargs.get("json")))
            return Response([] if method == "GET" else {"number": 1, **kwargs["json"]})
    private = "Account U_SECRET; HELD 17 shares; equity $12345; P&L +$900"
    issues = reporting.Issues("example/public-repo", "unused", HTTP())
    issues.ensure("report-evidence:daily:2026-09-30", private)
    posted = [data for method, data in requests_seen if method == "POST"][0]
    encoded = json.dumps(posted)
    for secret in ("U_SECRET", "HELD", "17 shares", "$12345", "$900"):
        assert secret not in encoded
    assert "daily:2026-09-30" in encoded
    assert "Evidence for this report was rejected" in encoded


def test_fallback_keeps_private_details_in_telegram_not_public_issue():
    issues, telegram = FakeIssues(), FakeTelegram()
    private = "Account U_SECRET; hypothetical equity $12345"
    reporting.fallback_alert(issues, telegram, "database", private)
    assert all(body == private for _, body in telegram.sent)
    assert "U_SECRET" not in issues.rows["database"]["body"]
    assert "$12345" not in issues.rows["database"]["body"]
    assert "alert-delivered:" in issues.rows["database"]["body"]


def test_public_issue_identifiers_are_whitelisted_not_account_data():
    with pytest.raises(reporting.ReportingError, match="Unsupported operational"):
        operational_issue_body("Account U_SECRET")


def sealed_checkpoint(**changes):
    state = {"version": 1, "origin": "decision_only_shadow", "seed_fingerprint": "same-seed",
             "engine_fingerprint": "same-engine", "config_fingerprint": "same-config",
             "equity": 1000, "commission": 0, "slippage": 0, "closed_positions": 0,
             "positions": {}, "last_quotes": {}, **changes}
    state["state_sha256"] = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return state


def test_equity_concentration_includes_unrealized_holdings_not_only_closed_sales():
    start = sealed_checkpoint(positions={"HELD": {"shares": 1}},
                              last_quotes={"HELD": {"price": 100}})
    end = sealed_checkpoint(equity=1020, positions={"HELD": {"shares": 1}},
                            last_quotes={"HELD": {"price": 120}})
    metrics, warning = reporting.checkpoint_metrics(start, end, [], [1020], {"equity": 1020})
    assert warning is None
    assert metrics["equity_pnl"] == 20
    assert metrics["completed_positions"] == 0
    assert metrics["contribution_concentration"]["by_ticker"] == {"HELD": 20}
    assert metrics["contribution_concentration"]["largest_positive_share"] == 1


def test_period_drawdown_includes_opening_equity_before_first_fill_commission():
    start = sealed_checkpoint()
    end = sealed_checkpoint(equity=999, commission=1, positions={"BOUGHT": {"shares": 1}},
                            last_quotes={"BOUGHT": {"price": 100}})
    fills = [{"side": "BUY", "ticker": "BOUGHT", "shares": 1, "price": 100, "quote": 100, "commission": 1}]
    metrics, warning = reporting.checkpoint_metrics(start, end, fills, [999], {"equity": 999})
    assert warning is None
    assert metrics["drawdown"] == pytest.approx(0.1)
    assert metrics["equity_pnl"] == -1
    assert metrics["costs"]["commission"] == 1


def test_missing_period_fill_or_corrupt_checkpoint_refuses_attribution():
    start = sealed_checkpoint()
    end = sealed_checkpoint(equity=999, commission=1, positions={"BOUGHT": {"shares": 1}},
                            last_quotes={"BOUGHT": {"price": 100}})
    metrics, warning = reporting.checkpoint_metrics(start, end, [], [999], {"equity": 999})
    assert metrics == {} and "does not reconcile" in warning
    end["equity"] = 1000
    metrics, warning = reporting.checkpoint_metrics(start, end, [], [1000], {"equity": 1000})
    assert metrics == {} and "does not reconcile" in warning


def test_period_checkpoint_lookup_uses_exact_before_and_after_sequences():
    calls = []
    class QueryStore:
        def select(self, table, params):
            calls.append((table, params))
            return [{"state": {"sequence": params["sequence"]}}]
    rows = [{"run_id": "run", "sequence": 17}, {"run_id": "run", "sequence": 30}]
    opening, closing = reporting.period_checkpoints(QueryStore(), rows)
    assert opening == {"sequence": "eq.16"}
    assert closing == {"sequence": "eq.30"}
    assert all(table == "intraday_shadow_checkpoints" for table, _ in calls)


def test_failed_recovery_lookup_never_announces_unverified_recovery():
    class LookupFailure(FakeIssues):
        def find(self, key):
            raise reporting.ReportingError("GitHub incident lookup unavailable")
    telegram = FakeTelegram()
    with pytest.raises(reporting.ReportingError, match="recovery notification withheld"):
        reporting.fallback_alert(LookupFailure(), telegram, "database", "RECOVERED", recovery=True)
    assert telegram.sent == []


@pytest.mark.parametrize("failure", ["lookup", "recipient"])
def test_recovery_delivery_failure_does_not_block_due_reports_or_health_incidents(monkeypatch, failure):
    now = at("2026-09-30T10:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-29"}}})
    telegram = FakeTelegram()
    class RecoveryLookupFailure(FakeIssues):
        def find(self, key):
            if key == "database":
                raise reporting.ReportingError("GitHub incident lookup unavailable")
            return super().find(key)
    issues = RecoveryLookupFailure() if failure == "lookup" else FakeIssues()
    if failure == "recipient":
        issues.ensure("database", "Prior verified database outage")
        telegram.fail = {"two"}
    health = healthy(now)
    health["snapshot"] = None
    monkeypatch.setattr(reporting, "load_health", lambda _: health)
    with pytest.raises(reporting.ReportingError):
        reporting.run(store, telegram, issues, now)
    assert "daily:2026-09-29" in store.rows[reporting.REPORTS]
    assert any(row["issue_key"] == "broker-snapshot" for row in store.rows[reporting.INCIDENTS].values())
    assert any(row["notification_id"] == "report:daily:2026-09-29"
               for row in store.rows[reporting.RECEIPTS].values())
    assert any("DAILY 2026-09-29" in body
               for recipient, body in telegram.sent if recipient == "one")
    if failure == "lookup":
        assert not any("private research database access restored" in body for _, body in telegram.sent)
        assert store.rows[reporting.REPORTS]["daily:2026-09-29"]["status"] == "delivered"
    else:
        assert store.rows[reporting.REPORTS]["daily:2026-09-29"]["status"] == "pending"
        assert "database" in issues.rows
    assert not store.claimed


def test_bad_recipient_cannot_starve_nine_due_periods_or_new_daily_reports(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-21"}}})
    telegram, issues = FakeTelegram(), FakeIssues()
    telegram.fail = {"two"}
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    expected = {period["id"] for period in reporting.due_periods(now, dt.date(2026, 9, 21))}
    assert len(expected) == 9
    counts, successful_sends = [], []
    for sweep in range(3):
        with pytest.raises(reporting.ReportingError):
            reporting.run(store, telegram, issues, now + dt.timedelta(minutes=15 * sweep))
        counts.append(len(store.rows[reporting.REPORTS]))
        successful_sends.append(len(telegram.sent))
    assert counts == [6, 9, 9]
    assert set(store.rows[reporting.REPORTS]) == expected
    assert {row["notification_id"] for row in store.rows[reporting.RECEIPTS].values()} == {
        "report:" + key for key in expected}
    assert all(row["status"] == "pending" for row in store.rows[reporting.REPORTS].values())
    assert successful_sends[2] == successful_sends[1] > successful_sends[0]

    with pytest.raises(reporting.ReportingError):
        reporting.run(store, telegram, issues, at("2026-10-01T17:00"))
    assert "daily:2026-10-01" in store.rows[reporting.REPORTS]
    assert any(row["notification_id"] == "report:daily:2026-10-01"
               for row in store.rows[reporting.RECEIPTS].values())
    assert all(recipient == "one" for recipient, _ in telegram.sent)
    assert not store.claimed


def test_github_delivery_escalation_failure_does_not_interrupt_report_batch(monkeypatch):
    now = at("2026-09-30T17:00")
    store = MemoryStore({reporting.STATE: {"scheduler": {"id": "scheduler", "started_on": "2026-09-21"}}})
    telegram = FakeTelegram()
    telegram.fail = {"two"}
    class EscalationFailure(FakeIssues):
        def ensure(self, key, body):
            raise reporting.ReportingError("GitHub issue creation unavailable")
    monkeypatch.setattr(reporting, "load_health", lambda _: healthy(now))
    with pytest.raises(reporting.ReportingError):
        reporting.run(store, telegram, EscalationFailure(), now)
    assert len(store.rows[reporting.REPORTS]) == 6
    assert len({row["notification_id"] for row in store.rows[reporting.RECEIPTS].values()}) == 6
    assert not store.claimed
