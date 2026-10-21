"""Offline checks for the manual read-only calibration evidence report."""
import copy
import datetime as dt
import json

import pytest
import requests

from backend import calibration_daily_health as h

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 21, 2, tzinfo=UTC)
DAY = "2026-10-20"
W = h.window(DAY, NOW)


def workflow(start="2026-10-19T22:00:00+00:00", *, success=True):
    return {
        "run": {"id": 123, "run_attempt": 1, "run_started_at": start,
                "updated_at": start, "status": "completed",
                "conclusion": "success" if success else "failure"},
        "jobs": [{"completed_at": start, "steps": [
            {"name": name, "conclusion": "success" if success else ("failure" if i == 0 else "skipped")}
            for i, name in enumerate(h.STAGES)]}],
    }


def report():
    return {
        "schema": 1, "report_kind": "daily", "period_start": DAY, "period_end": DAY,
        "status": "delivered", "created_at": "2026-10-20T20:35:00+00:00",
        "delivered_at": "2026-10-20T20:36:00+00:00", "evidence_warnings": [],
        "coverage": dict(sessions_expected=1, sessions_with_quotes=1, quote_frames=79,
                         complete_quote_frames=79, broker_snapshots=79, complete_broker_snapshots=79,
                         missing_quote_observations=0, capture_gap_events=0, restart_events=0,
                         process_restarts_at_least=0, observed_runs=1, gaps_over_10min=[],
                         broker_gaps_over_10min=[], sessions_without_quotes=[]),
    }


def frames(empty=False, w=W):
    rows = []
    ticks, at = [], w.opened
    while at < w.closed:
        ticks.append(at)
        at += dt.timedelta(minutes=5)
    ticks.append(w.closed - dt.timedelta(minutes=1))
    for at in ticks:
        if at == ticks[-1]:
            events = [{"type": "end_mark", "timestamp": at.isoformat()}]
        elif (at - w.opened).total_seconds() % 900 == 0:
            events = [{"type": "buy_cycle", "timestamp": at.isoformat(),
                       "triggers": [] if empty else [{"ticker": "TEST"}]},
                      {"type": "monitor", "timestamp": at.isoformat()}]
        else:
            events = [{"type": "quote", "timestamp": at.isoformat()}]
        rows.append(dict(run_id="run1", session=w.day.isoformat(), sequence=len(rows) + 1, occurred_at=at.isoformat(),
                         kind="cycle", events=events))
    return rows


POLICY = {"min_completed_positions": 30, "min_distinct_sessions": 5,
          "min_improvement_usd": 500, "max_drawdown_increase_pp": 0,
          "max_worst_loss_increase_usd": 0, "min_positive_tickers": 3,
          "max_largest_contributor_fraction": 0.4}


class MemoryReader:
    def __init__(self):
        self.fail = set()
        self.calls = []
        self.rows_by_table = {
            "daily_triggers": [{"triggered_at": DAY}],
            "intraday_research_reports": [report()],
            "intraday_shadow_events": frames(),
            "intraday_capture_health": [dict(last_seen_at=NOW.isoformat(), last_error=None,
                                             pending_events=0, dropped_events=0, enabled=True, spool_available=True)],
            "intraday_shadow_health": [dict(last_seen_at=NOW.isoformat(), last_error=None,
                                            status="running", metrics=dict(pending_uploads=0))],
            "intraday_calibration_health": [dict(last_seen_at=NOW.isoformat(), last_error=None, status="no_change")],
            "intraday_calibration_settings": [{"value": dict(enabled=True, training_sessions=5,
                                                            evaluation_sessions=5, risk_policy=POLICY)}],
            "intraday_calibration_proposals": [dict(status="no_change", updated_at=NOW.isoformat())],
            "intraday_shadow_runs": [dict(id="run1", status="running")],
        }

    def rows(self, table, fields, filters=None, **kwargs):
        self.calls.append((table, fields, filters, kwargs))
        if table in self.fail:
            raise h.EvidenceUnavailable("Source request failed.")
        return copy.deepcopy(self.rows_by_table[table])

    def workflow(self, start, end):
        if "github" in self.fail:
            raise h.EvidenceUnavailable("GitHub rate limit or source unavailable.")
        return workflow((start + dt.timedelta(hours=2)).isoformat())


def result(reader=None, **kwargs):
    return h.daily_health(DAY, reader=reader or MemoryReader(), now=NOW, **kwargs)


def checks(report):
    return {row["id"]: row for row in report["checks"]}


def test_complete_contract_and_no_automatic_authorization():
    data = result()
    assert data["overall_status"] == "pass"
    assert data["schema_version"] == 1
    assert data["session_complete"] is True
    assert data["timezone"] == "America/New_York"
    assert h.stamp(data["checked_at"])
    for row in data["checks"]:
        assert set(row) == {"id", "title", "status", "summary", "next_step", "scope", "evidence_at"}
        assert row["scope"] in ("current", "session", "next_session")
    assert "authorize trading" in " ".join(data["limitations"])


@pytest.mark.parametrize(("now", "expected"), [
    ("2026-10-19T12:00:00+00:00", "2026-10-16"),  # Monday before open
    ("2026-10-18T22:00:00+00:00", "2026-10-16"),  # Sunday
    ("2026-12-25T22:00:00+00:00", "2026-12-24"),  # Christmas holiday
    ("2026-11-27T18:01:00+00:00", "2026-11-27"),  # Friday early close
    ("2026-11-27T17:59:00+00:00", "2026-11-25"),  # Thanksgiving prior session
    ("2026-03-09T19:59:00+00:00", "2026-03-06"),  # DST now closes 20 UTC
    ("2026-03-09T20:01:00+00:00", "2026-03-09"),
])
def test_completed_session_calendar(now, expected):
    assert h.window(now=h.stamp(now)).day.isoformat() == expected


@pytest.mark.parametrize("day", ["bad", "20261020", "2026-02-30", "2026-10-18", "2026-12-25", "1900-01-01", "2026-10-22"])
def test_invalid_future_holiday_or_out_of_calendar(day):
    with pytest.raises(ValueError, match="NYSE session"):
        h.window(day, NOW)


def test_selected_in_progress_waits_instead_of_claiming_completed():
    w = h.window(DAY, h.stamp("2026-10-20T17:00:00+00:00"))
    assert not w.complete
    assert h.assess_workflow(None, w, upcoming=True)["status"] == "wait"
    assert h.assess_report([], w)[0]["status"] == "wait"


def test_october_starvation_not_hidden_by_healthy_heartbeats_or_training_count():
    reader = MemoryReader()
    reader.rows_by_table["daily_triggers"] = [{"triggered_at": "2026-10-08"}]
    reader.rows_by_table["intraday_shadow_events"] = frames(empty=True)
    reader.workflow = lambda start, end: workflow((start + dt.timedelta(hours=2)).isoformat(), success=False)
    data = result(reader)
    by_id = checks(data)
    assert data["overall_status"] == "fail"
    assert by_id["observer-worker"]["status"] == by_id["calibration-worker"]["status"] == "pass"
    assert by_id["shadow-inputs"]["status"] == "fail"
    assert "0 candidate inputs" in by_id["shadow-inputs"]["summary"]
    assert "2026-10-08" in by_id["trigger-freshness"]["summary"]
    assert "has no fresh rows" in by_id["trigger-freshness"]["summary"]
    assert "skipped" in by_id["screening-session-inputs"]["summary"]


def test_no_qualifiers_after_success_is_wait_not_false_productive_pass():
    reader = MemoryReader()
    reader.rows_by_table["daily_triggers"] = []
    reader.rows_by_table["intraday_shadow_events"] = frames(empty=True)
    data = result(reader)
    assert checks(data)["screening-session-inputs"]["status"] == "pass"
    assert checks(data)["shadow-inputs"]["status"] == "wait"
    assert data["overall_status"] == "wait"


def test_later_success_does_not_certify_preceding_failed_feed():
    reader = MemoryReader()
    reader.rows_by_table["intraday_shadow_events"] = frames(empty=True)
    reader.workflow = lambda start, end: workflow((start + dt.timedelta(hours=2)).isoformat(),
                                                success=start >= W.closed)
    rows = checks(result(reader))
    assert rows["screening-next-session"]["status"] == "pass"
    assert rows["screening-session-inputs"]["status"] == rows["shadow-inputs"]["status"] == "fail"


@pytest.mark.parametrize("failure", ["github", "intraday_research_reports", "intraday_capture_health",
                                    "intraday_shadow_events", "intraday_calibration_settings"])
def test_source_failure_isolation(failure):
    reader = MemoryReader()
    reader.fail.add(failure)
    data = result(reader)
    assert data["overall_status"] == "unknown"
    assert checks(data)["calibration-worker"]["status"] == "pass"
    if failure != "intraday_research_reports":
        assert checks(data)["source-coverage"]["status"] == "pass"
    else:
        assert checks(data)["report-delivery"]["status"] == "unknown"


@pytest.mark.parametrize("field", ["sessions_expected", "quote_frames", "broker_snapshots",
                                  "capture_gap_events", "gaps_over_10min"])
def test_missing_required_report_fields_never_default_to_zero(field):
    reader = MemoryReader()
    del reader.rows_by_table["intraday_research_reports"][0]["coverage"][field]
    assert checks(result(reader))["source-coverage"]["status"] == "unknown"


@pytest.mark.parametrize("bad", [None, True, -1, "0"])
def test_invalid_required_counter_is_unknown(bad):
    reader = MemoryReader()
    reader.rows_by_table["intraday_research_reports"][0]["coverage"]["capture_gap_events"] = bad
    assert checks(result(reader))["source-coverage"]["status"] == "unknown"


def test_saved_restartstorm_gaps_and_withheld_performance_fail():
    reader = MemoryReader()
    saved = reader.rows_by_table["intraday_research_reports"][0]
    saved["coverage"].update(process_restarts_at_least=185, capture_gap_events=35,
                             complete_quote_frames=35)
    saved["evidence_warnings"] = ["Shadow performance withheld: source evidence is incomplete."]
    data = result(reader)
    assert data["overall_status"] == "fail"
    assert "185" in checks(data)["source-coverage"]["summary"]
    assert "evidence warnings 1" in checks(data)["source-coverage"]["summary"]


def test_missing_report_and_mismatched_old_report_unknown():
    reader = MemoryReader()
    reader.rows_by_table["intraday_research_reports"] = []
    assert checks(result(reader))["source-coverage"]["status"] == "unknown"
    reader.rows_by_table["intraday_research_reports"] = [{**report(), "period_start": "2026-10-19"}]
    assert checks(result(reader))["source-coverage"]["status"] == "unknown"


def test_preclose_report_does_not_certify_completed_day():
    saved = report()
    saved["created_at"] = W.opened.isoformat()
    assessed = h.assess_report([saved], W)[0]
    assert assessed["status"] == "wait"
    assert assessed["next_step"].strip()


def test_every_nonpass_row_has_an_actionable_next_step():
    reader = MemoryReader()
    reader.fail.add("github")
    reader.rows_by_table["intraday_research_reports"] = []
    reader.rows_by_table["intraday_shadow_events"] = frames(empty=True)
    reader.rows_by_table["intraday_calibration_settings"][0]["value"]["risk_policy"] = None
    reader.rows_by_table["intraday_capture_health"][0]["spool_available"] = False
    reader.rows_by_table["intraday_calibration_proposals"] = [{"status": "blocked"}]
    for row in result(reader)["checks"]:
        if row["status"] != "pass":
            assert row["next_step"].strip(), row["id"]


@pytest.mark.parametrize("change", ["run", "gap", "sequence", "endmark", "time"])
def test_shadow_day_continuity_failures(change):
    rows = frames()
    if change == "run":
        rows[-1]["run_id"] = "replacement"
    elif change == "gap":
        rows[3]["kind"] = "gap"
    elif change == "sequence":
        rows[3]["sequence"] = 1000
    elif change == "endmark":
        rows[-1]["events"].pop()
    else:
        del rows[3:8]
    assert h.assess_shadow(rows, W, {"status": "pass"})["status"] == "fail"


def test_missing_shadow_triggers_not_assumed_empty():
    rows = frames()
    del rows[0]["events"][0]["triggers"]
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_shadow(rows, W, {"status": "pass"})


def test_realistic_quote_frames_do_not_substitute_for_15minute_decisions():
    rows = frames()
    assert len(rows) == 79
    normal = h.assess_shadow(rows, W, {"status": "pass"})
    assert normal["status"] == "pass"
    assert "26 buy cycles" in normal["summary"] and "26/26 matured" in normal["summary"]
    for row in rows[1:-1]:
        row["events"] = [{"type": "quote", "timestamp": row["occurred_at"]}]
    assessed = h.assess_shadow(rows, W, {"status": "pass"})
    assert assessed["status"] == "fail"
    assert "Missing timely paired decisions in 25" in assessed["summary"]


def test_monitor_required_in_every_matured_buy_slot():
    rows = frames()
    rows[3]["events"].pop()
    assert h.assess_shadow(rows, W, {"status": "pass"})["status"] == "fail"


@pytest.mark.parametrize(("lag", "expected"), [(120, "pass"), (121, "fail")])
def test_decision_acquisition_grace_boundary(lag, expected):
    rows = frames()
    at = W.opened + dt.timedelta(seconds=lag)
    rows[0]["occurred_at"] = at.isoformat()
    for event in rows[0]["events"]:
        event["timestamp"] = at.isoformat()
    assert h.assess_shadow(rows, W, {"status": "pass"})["status"] == expected


def test_only_matured_slots_required_for_in_progress_session():
    now = W.opened + dt.timedelta(minutes=16)
    w = h.window(DAY, now)
    rows = frames()[:4]
    rows[-1]["events"] = [{"type": "quote", "timestamp": rows[-1]["occurred_at"]}]
    assessed = h.assess_shadow(rows, w, {"status": "pass"})
    assert assessed["status"] == "wait" and "1/1 matured" in assessed["summary"]
    w = h.window(DAY, W.opened + dt.timedelta(minutes=17))
    assert h.assess_shadow(rows, w, {"status": "pass"})["status"] == "fail"


def test_early_close_expected_slots_and_relative_closing_mark():
    w = h.window("2026-11-27", h.stamp("2026-11-27T20:00:00+00:00"))
    rows = frames(w=w)
    assessed = h.assess_shadow(rows, w, {"status": "pass"})
    assert assessed["status"] == "pass"
    assert "14/14 matured" in assessed["summary"]
    assert h.stamp(rows[-1]["occurred_at"]).astimezone(h.NY).strftime("%H:%M") == "12:59"
    rows[-1]["events"] = [{"type": "quote", "timestamp": rows[-1]["occurred_at"]}]
    rows[3]["events"].append({"type": "end_mark", "timestamp": rows[3]["occurred_at"]})
    assessed = h.assess_shadow(rows, w, {"status": "pass"})
    assert assessed["status"] == "fail" and "No end mark" in assessed["summary"]


def test_current_health_separate_stale_queue_and_waiting_training():
    reader = MemoryReader()
    reader.rows_by_table["intraday_capture_health"][0]["last_seen_at"] = W.closed.isoformat()
    reader.rows_by_table["intraday_shadow_health"][0]["metrics"]["pending_uploads"] = 2
    reader.rows_by_table["intraday_calibration_health"][0].update(
        status="waiting_for_data", last_error="Need 5 complete training sessions")
    data = checks(result(reader))
    assert data["source-coverage"]["status"] == "pass"
    assert data["observer-worker"]["status"] == "fail"
    assert data["shadow-worker"]["status"] == data["calibration-worker"]["status"] == "wait"


def test_unknown_queue_field_does_not_claim_healthy():
    reader = MemoryReader()
    del reader.rows_by_table["intraday_shadow_health"][0]["metrics"]["pending_uploads"]
    assert checks(result(reader))["shadow-worker"]["status"] == "unknown"


def test_known_upload_failure_is_actionable_and_waiting_reason_is_specific():
    reader = MemoryReader()
    reader.rows_by_table["intraday_shadow_health"][0]["status"] = "upload_error"
    reader.rows_by_table["intraday_calibration_health"][0]["status"] = "waiting_for_holdout"
    rows = checks(result(reader))
    assert rows["shadow-worker"]["status"] == "fail"
    assert rows["calibration-worker"]["status"] == "wait"
    assert "reserved future-session observations" in rows["calibration-worker"]["summary"]


def test_risk_policy_absent_wait_invalid_unknown_and_disabled_wait():
    reader = MemoryReader()
    value = reader.rows_by_table["intraday_calibration_settings"][0]["value"]
    value["risk_policy"] = None
    assert checks(result(reader))["calibration-policy"]["status"] == "wait"
    value["risk_policy"] = {"arbitrary": 1}
    assert checks(result(reader))["calibration-policy"]["status"] == "unknown"
    value.update(risk_policy=POLICY, enabled=False)
    assert checks(result(reader))["calibration-policy"]["status"] == "wait"


def campaign(**kwargs):
    return dict(status="evaluating", run_id="run1", evaluation_start="2026-10-20",
                evaluation_end="2026-10-26", last_evaluated_session=DAY, evaluation_complete=False,
                evaluation_sessions=["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"],
                **kwargs)


def test_future_holdout_wait_replaced_source_block_and_overdue():
    row = campaign()
    runs = [{"id": "run1", "status": "running"}]
    assert h.assess_campaigns([row], runs, W)["status"] == "wait"
    assert h.assess_campaigns([row], [{"id": "run2", "status": "running"}], W)["status"] == "fail"
    assert h.assess_campaigns([row], [{"id": "run1", "status": "blocked"}], W)["status"] == "fail"
    row.update(evaluation_start="2026-10-12", evaluation_end=DAY, last_evaluated_session="2026-10-19")
    row["evaluation_sessions"] = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
                                  "2026-10-16", "2026-10-19", DAY]
    assert h.assess_campaigns([row], runs, W)["status"] == "fail"


@pytest.mark.parametrize(("state", "expected"), [("no_change", "pass"), ("blocked", "fail"),
                                               ("deferred", "wait"), ("ready", "wait")])
def test_campaign_states_are_not_approval(state, expected):
    assert h.assess_campaigns([{"status": state}], [{"id": "run1", "status": "running"}], W)["status"] == expected


@pytest.mark.parametrize(("latest", "expected"), [("no_change", "pass"), ("ready", "wait")])
def test_historical_blocked_campaign_does_not_mask_newer_result(latest, expected):
    rows = [{"status": latest}, {"status": "blocked", "run_id": "retired"}]
    assessed = h.assess_campaigns(rows, [{"id": "current", "status": "running"}], W)
    assert assessed["status"] == expected


def test_pending_campaign_takes_priority_over_retained_completed_records():
    rows = [{"status": "no_change"}, campaign()]
    assessed = h.assess_campaigns(rows, [{"id": "run1", "status": "running"}], W)
    assert assessed["status"] == "wait" and "Reserved future evaluation" in assessed["summary"]
    rows = [{"status": "no_change"}, {"status": "deferred"}]
    assert h.assess_campaigns(rows, [{"id": "run1", "status": "running"}], W)["status"] == "wait"


@pytest.mark.parametrize("rows", [[], [{"status": "no_change"}], [{"status": "ready"}]])
def test_current_blocked_source_fails_without_an_evaluating_campaign(rows):
    assessed = h.assess_campaigns(rows, [{"id": "current", "status": "blocked"}], W)
    assert assessed["status"] == "fail" and "latest current shadow source" in assessed["summary"]


def test_daily_evaluation_progress_required_before_final_reserved_date():
    row = campaign()
    row["last_evaluated_session"] = None
    assessed = h.assess_campaigns([row], [{"id": "run1", "status": "running"}], W)
    assert assessed["status"] == "fail"
    assert "overdue through reserved session 2026-10-20" in assessed["summary"]
    before_grace = h.window(DAY, W.closed + dt.timedelta(minutes=29))
    assert h.assess_campaigns([row], [{"id": "run1", "status": "running"}], before_grace)["status"] == "wait"
    before_first = h.window(DAY, W.opened)
    assert h.assess_campaigns([row], [{"id": "run1", "status": "running"}], before_first)["status"] == "wait"


@pytest.mark.parametrize("bad", ["2026-10-24", "2026-10-19", "not-a-date"])
def test_progress_must_belong_to_reserved_trading_sessions(bad):
    row = campaign()
    row["last_evaluated_session"] = bad
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_campaigns([row], [{"id": "run1", "status": "running"}], W)


@pytest.mark.parametrize("reserved", [
    [], ["2026-10-20", "2026-10-20", "2026-10-26"],
    ["2026-10-20", "2026-10-24", "2026-10-26"],
    ["2026-10-26", "2026-10-20"], ["bad"],
])
def test_invalid_reserved_date_lists_are_unknown(reserved):
    row = campaign()
    row["evaluation_sessions"] = reserved
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_campaigns([row], [{"id": "run1", "status": "running"}], W)


@pytest.mark.parametrize("final", [False, True])
def test_final_progress_requires_consistent_completed_campaign_state(final):
    row = campaign()
    row.update(evaluation_end=DAY, evaluation_sessions=[DAY], evaluation_complete=final)
    assessed = h.assess_campaigns([row], [{"id": "run1", "status": "running"}], W)
    assert assessed["status"] == "fail"
    assert "Final evaluation evidence" in assessed["summary"]


def test_final_flag_cannot_claim_completion_before_reserved_end():
    row = campaign()
    row["evaluation_complete"] = True
    assert h.assess_campaigns([row], [{"id": "run1", "status": "running"}], W)["status"] == "fail"


def test_no_change_baseline_is_valid_but_inconsistent_evaluation_completion_is_not():
    row = campaign()
    row.update(status="no_change", last_evaluated_session=None, evaluation_complete=None)
    runs = [{"id": "run1", "status": "running"}]
    assert h.assess_campaigns([row], runs, W)["status"] == "pass"
    row.update(evaluation_sessions=[DAY], evaluation_end=DAY, last_evaluated_session=DAY,
               evaluation_complete=False)
    assert h.assess_campaigns([row], runs, W)["status"] == "fail"
    row["evaluation_complete"] = True
    assert h.assess_campaigns([row], runs, W)["status"] == "pass"


def test_future_workflow_completion_and_delivery_receipt_cannot_pass():
    data = workflow()
    data["jobs"][0]["completed_at"] = (NOW + dt.timedelta(minutes=1)).isoformat()
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_workflow(data, W)
    saved = report()
    saved["delivered_at"] = (NOW + dt.timedelta(minutes=1)).isoformat()
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_report([saved], W)


def test_http_payload_and_errors_never_echo_secrets(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b"not json U12345 secret-key"
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    reader = h.Reader.__new__(h.Reader)
    with pytest.raises(h.EvidenceUnavailable) as exc:
        reader._get("https://example.invalid", {}, {})
    assert "secret" not in str(exc.value) and "U12345" not in str(exc.value)
    def fail(*args, **kwargs):
        raise requests.ConnectionError("URL?apikey=secret-key account U12345")
    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(h.EvidenceUnavailable) as exc:
        reader._get("https://example.invalid", {}, {})
    assert "secret" not in str(exc.value)


def test_saved_error_and_warning_contents_are_not_echoed():
    reader = MemoryReader()
    reader.rows_by_table["intraday_capture_health"][0]["last_error"] = "key=SECRET account U123456"
    reader.rows_by_table["intraday_research_reports"][0]["evidence_warnings"] = ["key=SECRET"]
    text = json.dumps(result(reader))
    assert "SECRET" not in text and "U123456" not in text


def test_row_bound_rejects_server_cap_and_allows_explicit_latest(monkeypatch):
    reader = h.Reader.__new__(h.Reader)
    reader.url, reader.key = "https://example.invalid", "offline"
    monkeypatch.setattr(reader, "_get", lambda *args: ([{}], {"Content-Range": "0-0/2001"}))
    with pytest.raises(h.EvidenceUnavailable, match="truncated"):
        reader.rows("intraday_shadow_events", "sequence")
    assert reader.rows("daily_triggers", "triggered_at", latest=True, limit=1) == [{}]


def test_request_limits_and_get_only(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        headers = {"Content-Range": "*/0"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b"[]"
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr(requests, "get", get)
    reader = h.Reader.__new__(h.Reader)
    reader.url, reader.key = "https://example.invalid", "offline"
    assert reader.rows("intraday_shadow_events", "sequence") == []
    url, kwargs = calls[0]
    assert kwargs["timeout"] == (3, 8)
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
    assert kwargs["params"]["limit"] == 2000
    assert kwargs["headers"]["Prefer"] == "count=exact"


def test_byte_bound_rejects_partial_payload(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b"x" * (h.MAX_BYTES + 1)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(h.EvidenceUnavailable, match="4 MiB"):
        h.Reader.__new__(h.Reader)._get("https://example.invalid", {}, {})


def test_exhausted_request_budget_performs_no_further_io(monkeypatch):
    reader = h.Reader.__new__(h.Reader)
    reader.deadline = 99
    monkeypatch.setattr(h.time, "monotonic", lambda: 100)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pytest.fail("Budget exhausted"))
    with pytest.raises(h.EvidenceUnavailable, match="45-second"):
        reader._get("https://example.invalid", {}, {})


def test_budget_expiring_during_read_discards_partial_data(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            monkeypatch.setattr(h.time, "monotonic", lambda: 200)
            yield b"[]"
    reader = h.Reader.__new__(h.Reader)
    reader.deadline = 150
    monkeypatch.setattr(h.time, "monotonic", lambda: 100)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(h.EvidenceUnavailable, match="no partial response"):
        reader._get("https://example.invalid", {}, {})


@pytest.mark.parametrize("latest_success", [True, False])
def test_latest_workflow_attempt_overrides_older_result(monkeypatch, latest_success):
    reader = h.Reader.__new__(h.Reader)
    old = workflow()["run"]
    new = {**old, "id": 124, "run_attempt": 2, "run_started_at": "2026-10-20T01:00:00+00:00",
           "conclusion": "success" if latest_success else "failure"}
    calls = []
    def get(url, params, headers):
        calls.append(url)
        if url.endswith("/runs"):
            return {"workflow_runs": [old, new], "total_count": 2}, {}
        data = workflow(new["run_started_at"], success=latest_success)
        return {"jobs": data["jobs"], "total_count": 1}, {}
    monkeypatch.setattr(reader, "_get", get)
    data = reader.workflow(h.session_bounds("2026-10-19")[1], W.opened)
    assert "/124/attempts/2/jobs" in calls[-1]
    assert h.assess_workflow(data, W)["status"] == ("pass" if latest_success else "fail")


def test_successful_workflow_with_missing_stage_unknown():
    data = workflow()
    data["jobs"][0]["steps"].pop()
    with pytest.raises(h.EvidenceUnavailable):
        h.assess_workflow(data, W)


def test_skipped_technical_stage_cannot_pass_with_successful_job():
    data = workflow()
    data["jobs"][0]["steps"][2]["conclusion"] = "skipped"
    assert h.assess_workflow(data, W)["status"] == "fail"


def test_known_failed_setup_is_failure_not_hidden_by_absent_stage_records():
    data = workflow(success=False)
    data["jobs"][0]["steps"] = []
    assessed = h.assess_workflow(data, W)
    assert assessed["status"] == "fail"
    assert "not recorded" in assessed["summary"]


def test_current_intraday_heartbeat_without_output_fails():
    w = h.window(DAY, h.stamp("2026-10-20T16:00:00+00:00"))
    row = {"last_seen_at": w.now.isoformat(), "last_persisted_at": W.opened.isoformat(),
           "last_error": None, "pending_events": 0, "dropped_events": 0,
           "enabled": True, "spool_available": True}
    actual = h.assess_worker([row], w, "observer")
    assert actual["status"] == "fail"
    assert "no persisted output" in actual["summary"]


def test_worker_disabled_spool_missing_and_future_heartbeat():
    row = MemoryReader().rows_by_table["intraday_capture_health"][0]
    row["spool_available"] = False
    assert h.assess_worker([row], W, "observer")["status"] == "fail"
    row["spool_available"], row["enabled"] = True, False
    assert h.assess_worker([row], W, "observer")["status"] == "wait"
    row["last_seen_at"] = (NOW + dt.timedelta(minutes=2)).isoformat()
    assert h.assess_worker([row], W, "observer")["status"] == "fail"


def test_pending_report_delivery_does_not_claim_receipt():
    saved = report()
    saved.update(status="pending", delivered_at=None)
    assert h.assess_report([saved], W)[1]["status"] == "wait"


def test_github_truncation_and_malformed_counts_unknown(monkeypatch):
    reader = h.Reader.__new__(h.Reader)
    monkeypatch.setattr(reader, "_get", lambda *args: (
        {"workflow_runs": [workflow()["run"]], "total_count": 101}, {}))
    with pytest.raises(h.EvidenceUnavailable):
        reader.workflow(h.session_bounds("2026-10-19")[1], W.opened)


def test_database_bad_response_unknown(monkeypatch):
    reader = h.Reader.__new__(h.Reader)
    reader.url, reader.key = "https://example.invalid", "offline"
    for payload, headers in [({"message": "SECRET"}, {}), ([{}], {"Content-Range": "*/*"})]:
        monkeypatch.setattr(reader, "_get", lambda *args: (payload, headers))
        with pytest.raises(h.EvidenceUnavailable):
            reader.rows("intraday_shadow_events", "sequence")


def test_successful_but_late_workflow_cannot_validate_intraday_inputs():
    data = workflow()
    data["jobs"][0]["completed_at"] = W.closed.isoformat()
    assert h.assess_workflow(data, W)["status"] == "fail"


def test_workflow_missing_window_and_utc_midnight():
    assert h.assess_workflow(None, W)["status"] == "fail"
    assert h.assess_workflow(workflow("2026-10-20T01:00:00+00:00"), W)["status"] == "pass"


def test_oct20_afterclose_run_created_oct21_utc_only_prepares_oct21_session():
    now = h.stamp("2026-10-21T02:39:00+00:00")
    reader = MemoryReader()
    reader.rows_by_table["intraday_shadow_events"] = frames(empty=True)
    reader.workflow = lambda start, end: (
        workflow("2026-10-21T02:01:00+00:00") if start == W.closed
        else workflow("2026-10-19T22:00:00+00:00", success=False))
    data = checks(h.daily_health(DAY, now=now, reader=reader))
    assert data["screening-next-session"]["status"] == "pass"
    assert data["screening-next-session"]["scope"] == "next_session"
    assert "NEXT session" in data["screening-next-session"]["summary"]
    assert data["trigger-freshness"]["status"] == "pass"
    assert data["trigger-freshness"]["scope"] == "current"
    assert data["screening-session-inputs"]["status"] == "fail"
    assert data["shadow-inputs"]["status"] == "fail"


def test_endpoint_readonly_no_auth_and_no_store(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend import calibration_api as api
    calls = []
    saved = result()
    def daily(date):
        calls.append(date)
        if date == "invalid":
            raise ValueError("PRIVATE")
        return saved
    monkeypatch.setattr(api.calibration_daily_health, "daily_health", daily)
    monkeypatch.setattr(api, "get_store", lambda: pytest.fail("Daily check must not use mutating store paths"))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get("/api/calibration/daily-health")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls == [None]
        response = client.get("/api/calibration/daily-health?session_date=invalid")
        assert response.status_code == 400 and response.headers["cache-control"] == "no-store"
        assert "PRIVATE" not in response.text
        assert client.post("/api/calibration/daily-health").status_code == 405
