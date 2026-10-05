import json
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

import intraday_capture as capture


@pytest.fixture
def recorder():
    root = Path(__file__).parent / f".capture-test-{uuid.uuid4().hex}"
    rec = capture.Recorder({"replay_config": {"cooling_off_days": 7}},
                           spool=root / "capture.sqlite3")
    yield rec
    shutil.rmtree(root, ignore_errors=True)


def test_immutable_queue_and_sequence_gap_are_visible(recorder):
    recorder.queue = capture.queue.Queue(maxsize=1)
    source = {"triggers": [{"ticker": "ABC", "price": 5}]}
    recorder.emit("candidate_universe", source)
    source["triggers"][0]["price"] = 999
    recorder.emit("phase", {})
    first = recorder.queue.get_nowait()
    assert first["payload"]["triggers"][0]["price"] == 5
    assert recorder.dropped == 1
    assert "queue full" in recorder.last_error
    next_event = recorder.emit("phase", {})
    assert next_event["sequence"] == first["sequence"] + 2


def test_worker_constructs_real_synchronous_supabase_client(recorder, monkeypatch):
    import supabase

    create_client = supabase.create_client
    clients = []

    def construct(*args, **kwargs):
        try:
            client = create_client(*args, **kwargs)
            clients.append(client)
            return client
        finally:
            recorder.stopping.set()

    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "sb_secret_offline_test")
    monkeypatch.setattr(supabase, "create_client", construct)
    monkeypatch.setattr(recorder, "process_snapshot_jobs", lambda *_: None)
    monkeypatch.setattr(recorder, "refresh_universe", lambda *_: None)
    recorder.run()
    assert len(clients) == 1
    assert clients[0].options.postgrest_client_timeout == 10
    assert recorder.last_error is None


def test_spool_survives_restart_and_retry_never_overwrites(recorder):
    db = recorder.open_spool()
    recorder.emit("candidate_universe", {"triggers": [{"ticker": "ABC"}]})
    recorder.journal(db)
    db.close()
    restarted = capture.Recorder(recorder.config, spool=recorder.spool)
    db = restarted.open_spool()
    assert "ABC" in restarted.symbols
    client = MagicMock()
    client.table().upsert().execute.side_effect = RuntimeError("network down")
    with pytest.raises(RuntimeError):
        restarted.flush(db, client)
    assert db.execute("select count(*) from pending").fetchone()[0] == 1
    client.table().upsert().execute.side_effect = None
    restarted.flush(db, client)
    assert client.table().upsert.call_args.kwargs["ignore_duplicates"] is True
    assert db.execute("select count(*) from pending").fetchone()[0] == 0
    db.close()


def test_spool_limit_preserves_old_events_and_reports_loss(recorder):
    recorder.max_spool_events = 1
    db = recorder.open_spool()
    recorder.emit("phase", {"phase": "buy"})
    recorder.emit("phase", {"phase": "monitor"})
    recorder.journal(db)
    assert recorder.dropped == 1
    row = db.execute("select event from pending").fetchone()[0]
    assert json.loads(row)["payload"]["phase"] == "buy"
    db.close()


def test_archive_discovery_does_not_renew_expired_symbol_horizon(recorder):
    db = recorder.open_spool()
    recorder.emit("candidate_universe", {"phase": "discovery",
                  "triggers": [{"ticker": "OLD", "triggered_at": "2025-01-01"}]})
    recorder.journal(db)
    assert recorder.symbols["OLD"]["last_seen_at"] == "2025-01-01T00:00:00+00:00"
    http = MagicMock()
    recorder.sample(http)
    http.get.assert_not_called()
    db.close()


def test_sampling_and_zero_quantity_broker_rows_do_not_renew_membership(recorder):
    db = recorder.open_spool()
    seen_at = capture.now()
    recorder.symbols["ABC"] = {"ticker": "ABC", "first_seen_at": seen_at,
                               "last_seen_at": seen_at}
    recorder.emit("broker_snapshot", {"positions": [{"ticker": "ABC", "position": 0}]})
    recorder.journal(db)
    http = MagicMock()
    http.get().json.return_value = []
    recorder.sample(http)
    assert recorder.symbols["ABC"]["last_seen_at"] == seen_at
    db.close()


def test_cached_broker_snapshot_is_account_scoped_and_plain():
    ib = MagicMock()
    contract = NS(symbol="ABC", conId=1, secType="STK", currency="USD", exchange="SMART")
    own = NS(account="U1", contract=contract, position=4, marketPrice=20.0)
    other = NS(account="U2", contract=contract, position=500, marketPrice=20.0)
    ib.portfolio.return_value = [own, other]
    ib.positions.return_value = [own, other]
    ib.openTrades.return_value = []
    ib.accountValues.return_value = [NS(account="U1", tag="NetLiquidation", value="1000", currency="USD"),
                                     NS(account="U2", tag="NetLiquidation", value="99999", currency="USD")]
    result = capture.broker_snapshot(ib, "U1")
    assert len(result["positions"]) == len(result["account_values"]) == 1
    assert result["position_quantities"][0]["position"] == 4
    assert result["positions"][0]["provider_timestamp"] is None
    json.dumps(result, allow_nan=False)
    ib.reqTickers.assert_not_called()
    ib.reqPositions.assert_not_called()


def test_quote_validation_and_actual_receipt_time(recorder):
    stamp = capture.dt.datetime.now(capture.UTC).timestamp()
    recorder.symbols = {ticker: {"last_seen_at": capture.now()} for ticker in ("ABC", "BAD", "OLD")}
    http = MagicMock()
    http.get().json.return_value = [
        {"symbol": "ABC", "price": 20, "timestamp": stamp},
        {"symbol": "BAD", "price": float("nan"), "timestamp": stamp},
        {"symbol": "OLD", "price": 20, "timestamp": stamp - 3600},
    ]
    recorder.sample(http)
    event = recorder.queue.get_nowait()
    assert event["kind"] == "quote_sample"
    assert [q["ticker"] for q in event["payload"]["quotes"]] == ["ABC"]
    assert event["payload"]["complete"] is False
    assert event["occurred_at"] >= event["payload"]["received_at"]
    assert http.get.call_args.kwargs["timeout"] == (3, 10)


def test_symbol_limit_is_explicit_not_silent_truncation(recorder):
    recorder.max_symbols = 1
    recorder.symbols = {ticker: {"last_seen_at": capture.now()} for ticker in ("ABC", "DEF")}
    http = MagicMock()
    recorder.sample(http)
    http.get.assert_not_called()
    assert recorder.queue.get_nowait()["payload"]["reason"] == "symbol_limit_exceeded"


def test_initial_ledger_reads_have_upper_cutoff_and_missing_data_is_unknown(recorder):
    client = MagicMock()
    client.table().select().lte().order().range().execute.return_value.data = []
    client.table().select().lte().gte().order().range().execute.return_value.data = []
    client.reset_mock()
    broker = {"snapshot_at": "2026-09-30T14:00:00+00:00"}
    recorder.initial_state(client, broker)
    assert client.table().select().lte.call_count == 3
    assert all(c.args[1] == broker["snapshot_at"]
               for c in client.table().select().lte.call_args_list)
    event = recorder.queue.get_nowait()
    assert event["payload"]["atomic"] is False
    assert event["payload"]["complete"] is True
    client.table().select().lte.side_effect = RuntimeError("unavailable")
    recorder.initial_state(client, broker)
    event = recorder.queue.get_nowait()
    assert event["payload"]["complete"] is False
    assert event["payload"]["trade_history"] is None


def test_main_thread_snapshot_seed_does_not_reread_mutable_holdings(recorder):
    client = MagicMock()
    client.table().select().lte().gte().order().range().execute.return_value.data = []
    client.reset_mock()
    broker = {"snapshot_at": "2026-09-30T13:30:10+00:00"}
    seed = {"portfolio_positions": [{"ticker": "ABC", "shares": 4,
                                     "scaled_out_at": "2026-09-29T14:00:00+00:00"}],
            "portfolio_received_at": "2026-09-30T13:30:09+00:00"}
    recorder.initial_state(client, broker, seed)
    assert [c.args[0] for c in client.table.call_args_list] == ["trade_history", "ibkr_fills"]
    event = recorder.queue.get_nowait()
    assert event["kind"] == "portfolio_snapshot"
    assert event["payload"]["coherence"] == "main_thread_adjacent_observations"
    assert event["payload"]["portfolio_positions"] == seed["portfolio_positions"]
    assert event["payload"]["effective_config"] == recorder.config


def test_disabled_phase_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(capture, "_recorder", None)
    ib = MagicMock()
    @capture.capture_phase("buy")
    def cycle(broker):
        return 42
    assert cycle(ib) == 42
    assert ib.mock_calls == []


def test_trigger_decisions_are_append_events_even_when_audit_write_fails(monkeypatch, recorder):
    import trigger_audit
    monkeypatch.setattr(capture, "_recorder", recorder)
    client = MagicMock()
    client.table().upsert().execute.side_effect = RuntimeError("offline")
    trigger_audit.record_trigger_decision(client, {"ticker": "ABC"}, "SKIPPED", "SLOTS_FULL")
    event = recorder.queue.get_nowait()
    assert event["kind"] == "trigger_decision"
    assert event["payload"]["price"] is None
    assert event["payload"]["reason_code"] == "SLOTS_FULL"


def test_phase_early_return_does_not_claim_unevaluated_inputs(monkeypatch, recorder):
    monkeypatch.setattr(capture, "_recorder", recorder)
    monkeypatch.setattr(capture, "snapshot", lambda ib: None)
    @capture.capture_phase("buy")
    def cycle(ib):
        capture.emit("buy_gate", gate="schema", passed=False)
        return "blocked"
    assert cycle(None) == "blocked"
    events = [recorder.queue.get_nowait() for _ in range(3)]
    assert [e["kind"] for e in events] == ["buy_cycle", "buy_gate", "buy_cycle"]
    assert events[-1]["payload"]["unevaluated_inputs"] == "not_evaluated"
    assert events[0]["payload"]["cycle_id"] == events[-1]["payload"]["cycle_id"]


def test_full_book_keeps_all_trigger_inputs_and_does_not_request_quotes(monkeypatch, recorder):
    import execution_agent as ea
    from tests.test_buy_gates import _run_buys
    from tests.conftest import make_supabase_mock, make_ib_mock, make_position, make_trigger
    monkeypatch.setattr(capture, "_recorder", recorder)
    monkeypatch.setattr(capture, "snapshot", lambda ib: None)
    names = [f"HELD{i}" for i in range(ea.MAX_POSITIONS)]
    triggers = [{**make_trigger("VETO"), "ai_grade": "D"}, make_trigger("CANDIDATE")]
    client = make_supabase_mock(daily_triggers=triggers, portfolio=[make_position(t) for t in names])
    ib = make_ib_mock(symbols=names)
    _run_buys(ib, client)
    events = list(recorder.queue.queue)
    universe = next(e for e in events if e["kind"] == "candidate_universe")
    assert {t["ticker"] for t in universe["payload"]["triggers"]} == {"VETO", "CANDIDATE"}
    assert not any(e["kind"] == "candidate_quote" for e in events)
    assert sum(e["kind"] == "trigger_decision" for e in events) == 2
    ib.placeOrder.assert_not_called()


def test_effective_configuration_contains_no_credentials(monkeypatch):
    import execution_agent as ea
    monkeypatch.setenv("FMP_API_KEY", "never-record-me")
    monkeypatch.setenv("SUPABASE_KEY", "nor-me")
    snapshot = capture.effective_config(ea)
    assert snapshot["decision_config"]["max_positions"] == ea.MAX_POSITIONS
    assert snapshot["exit_config"]["scale_out_enabled"] == ea.SCALE_OUT_ENABLED
    assert "never-record-me" not in json.dumps(snapshot)
    assert "SUPABASE_KEY" not in json.dumps(snapshot)


def test_batch_quote_requests_are_at_most_100_symbols(recorder):
    recorder.symbols = {f"T{i}": {"last_seen_at": capture.now()} for i in range(101)}
    http = MagicMock()
    http.get().json.return_value = []
    http.reset_mock()
    recorder.sample(http)
    assert [len(c.kwargs["params"]["symbols"].split(",")) for c in http.get.call_args_list] == [100, 1]
    assert recorder.queue.qsize() == 1
    assert len(recorder.queue.get_nowait()["payload"]["requested_symbols"]) == 101


def test_periodic_cache_ticks_do_not_journal_unchanged_state_every_five_seconds(monkeypatch, recorder):
    import execution_agent as ea
    monkeypatch.setattr(capture, "_recorder", recorder)
    monkeypatch.setattr(ea, "get_ibkr_account", lambda ib: "U1")
    data = {"snapshot_at": capture.now(), "position_quantities": [],
            "open_orders": [], "connected": True}
    monkeypatch.setattr(capture, "broker_snapshot", lambda ib, account: dict(data))
    capture.snapshot(None, periodic=True)
    capture.snapshot(None, periodic=True)
    assert recorder.queue.qsize() == 1
    data["position_quantities"] = [{"ticker": "ABC", "position": 10}]
    capture.snapshot(None, periodic=True)
    assert recorder.queue.qsize() == 2
    capture.snapshot(None)
    assert recorder.queue.qsize() == 3


def test_http_failure_records_missing_quotes_without_secret_url(monkeypatch, recorder):
    recorder.symbols = {"ABC": {"last_seen_at": capture.now()}}
    http = MagicMock()
    http.get.side_effect = RuntimeError("https://provider.invalid?apikey=NEVER-LOG")
    recorder.sample(http)
    event = recorder.queue.get_nowait()
    assert event["payload"]["complete"] is False
    assert event["payload"]["missing_quotes"] == ["ABC"]
    assert "NEVER-LOG" not in json.dumps(event)
    assert "NEVER-LOG" not in recorder.last_error


def quote_response(rows=None, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = [] if rows is None else rows
    if status >= 400:
        response.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    return response


def test_batch_entitlement_fallback_covers_45_symbols_and_records_provenance(recorder):
    tickers = [f"T{i:02}" for i in range(45)]
    recorder.symbols = {t: {"last_seen_at": capture.now()} for t in tickers}
    stamp = capture.dt.datetime.now(capture.UTC).timestamp()
    http = MagicMock()
    http.get.side_effect = [quote_response(status=402)] + [
        quote_response([{"symbol": t, "price": 20, "timestamp": stamp}]) for t in tickers]
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is True
    assert payload["missing_quotes"] == payload["errors"] == []
    assert [q["ticker"] for q in payload["quotes"]] == tickers
    assert all(q["endpoint"] == "stable/quote" for q in payload["quotes"])
    assert payload["endpoint_mode"] == "quote"
    assert payload["fallback_reason"] == "batch_quote_http_402"
    assert payload["endpoints_attempted"] == ["batch-quote", "quote"]
    assert payload["request_count"] == payload["request_limit"] == 46
    assert recorder.last_error is None
    calls = http.get.call_args_list
    assert calls[0].args[0].endswith("/stable/batch-quote")
    assert [c.kwargs["params"]["symbol"] for c in calls[1:]] == tickers
    assert all(c.kwargs["allow_redirects"] is False for c in calls)

    http.reset_mock()
    http.get.side_effect = [
        quote_response([{"symbol": t, "price": 20, "timestamp": stamp}]) for t in tickers]
    recorder.sample(http)
    assert http.get.call_count == 45
    assert all(c.args[0].endswith("/stable/quote") for c in http.get.call_args_list)
    assert recorder.queue.get_nowait()["payload"]["complete"] is True
    client = MagicMock()
    recorder.health(None, client)
    config = client.table().upsert.call_args.args[0]["config"]
    assert config["quote_endpoint"] == "quote"
    assert config["quote_fallback_reason"] == "batch_quote_http_402"


@pytest.mark.parametrize("individual", [False, True])
def test_recorder_share_class_alias_keeps_internal_identity_and_provenance(recorder, individual):
    recorder.symbols = {s: {"last_seen_at": capture.now()} for s in ("MOG.A", "ZZZ")}
    stamp = capture.dt.datetime.now(capture.UTC).timestamp()
    rows = [{"symbol": s, "price": 20, "timestamp": stamp} for s in ("MOG-A", "ZZZ")]
    http = MagicMock()
    http.get.side_effect = ([quote_response(status=402)] + [
        quote_response([r]) for r in rows] if individual else [quote_response(rows)])
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is True
    assert payload["errors"] == payload["missing_quotes"] == []
    assert [q["ticker"] for q in payload["quotes"]] == ["MOG.A", "ZZZ"]
    assert payload["quotes"][0]["provider_symbol"] == "MOG-A"
    assert payload["quote_requests"][0]["parameters"] == {"symbols": "MOG-A,ZZZ"}
    assert payload["quote_requests"][0]["symbol_map"] == {"MOG-A": "MOG.A", "ZZZ": "ZZZ"}
    assert "apikey" not in json.dumps(payload)


def test_recorder_does_not_merge_ambiguous_aliases(recorder):
    recorder.symbols = {s: {"last_seen_at": capture.now()} for s in ("MOG.A", "MOG-A")}
    http = MagicMock()
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is False
    assert payload["quotes"] == []
    assert payload["errors"][0]["reason"] == "ambiguous_provider_symbol"
    http.get.assert_not_called()


def test_recorder_rejects_wrong_share_class(recorder):
    recorder.symbols = {"MOG.A": {"last_seen_at": capture.now()}}
    http = MagicMock()
    http.get.return_value = quote_response([
        {"symbol": "MOG-B", "price": 20, "timestamp": capture.dt.datetime.now(capture.UTC).timestamp()}])
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is False
    assert payload["quotes"] == []
    assert payload["missing_quotes"] == ["MOG.A"]


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_only_batch_402_enables_individual_fallback(recorder, status):
    recorder.symbols = {"ABC": {"last_seen_at": capture.now()}}
    http = MagicMock()
    http.get.return_value = quote_response(status=status)
    recorder.sample(http)
    assert http.get.call_count == 1
    assert recorder.quote_endpoint == "batch-quote"
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is False
    assert payload["missing_quotes"] == ["ABC"]
    assert payload["fallback_reason"] is None


def test_individual_request_failure_stops_fanout_without_retry(recorder):
    recorder.symbols = {t: {"last_seen_at": capture.now()} for t in ("ABC", "DEF", "GHI")}
    http = MagicMock()
    http.get.side_effect = [quote_response(status=402), quote_response(status=429)]
    recorder.sample(http)
    assert http.get.call_count == 2
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["missing_quotes"] == ["ABC", "DEF", "GHI"]
    assert payload["complete"] is False
    assert recorder.next_quote_symbol == "DEF"


def test_quote_budget_stops_requests_and_rotates_unserved_symbols(recorder, monkeypatch):
    recorder.sample_seconds = 5
    recorder.max_quote_age = 600
    recorder.symbols = {t: {"last_seen_at": capture.now()} for t in ("ABC", "DEF", "GHI")}
    elapsed = [0.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: elapsed[0])
    stamp = capture.dt.datetime.now(capture.UTC).timestamp()
    timeouts = []

    def get(url, *, params, timeout, allow_redirects):
        assert sum(timeout) <= 2.5 - elapsed[0]
        timeouts.append(timeout)
        if url.endswith("batch-quote"):
            return quote_response(status=402)
        elapsed[0] += 1.5
        return quote_response([{"symbol": params["symbol"], "price": 20, "timestamp": stamp}])

    http = MagicMock()
    http.get.side_effect = get
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["budget_seconds"] == 2.5
    assert payload["request_count"] == 3
    assert [q["ticker"] for q in payload["quotes"]] == ["ABC"]
    assert payload["missing_quotes"] == ["DEF", "GHI"]
    assert any(e["reason"] == "quote_budget_exhausted" for e in payload["errors"])
    assert timeouts[-1] == (0.5, 0.5)
    assert recorder.next_quote_symbol == "DEF"

    http.reset_mock()
    http.get.side_effect = lambda url, **kw: quote_response([
        {"symbol": kw["params"]["symbol"], "price": 20, "timestamp": stamp}])
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert http.get.call_args_list[0].kwargs["params"]["symbol"] == "DEF"
    assert payload["complete"] is True
    assert set(q["ticker"] for q in payload["quotes"]) == {"ABC", "DEF", "GHI"}


def test_quote_budget_is_capped_by_sampling_freshness_and_worker_liveness(recorder):
    recorder.sample_seconds = recorder.max_quote_age = 1000
    assert recorder.quote_budget_seconds() == 30
    recorder.sample_seconds = 10
    assert recorder.quote_budget_seconds() == 5
    recorder.max_quote_age = 4
    assert recorder.quote_budget_seconds() == 2


@pytest.mark.parametrize("bad_row", [
    {"symbol": "ABC", "price": 20},
    {"symbol": "ABC", "price": 20, "timestamp": 1},
    {"symbol": "ABC", "price": float("nan"), "timestamp": 1},
    {"symbol": "OTHER", "price": 20, "timestamp": 1},
    "not-a-quote",
])
def test_individual_fallback_preserves_strict_quote_validation(recorder, bad_row):
    recorder.symbols = {"ABC": {"last_seen_at": capture.now()}}
    http = MagicMock()
    http.get.side_effect = [quote_response(status=402), quote_response([bad_row])]
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert payload["complete"] is False
    assert payload["quotes"] == []
    assert payload["missing_quotes"] == ["ABC"]


@pytest.mark.parametrize("individual", [False, True])
def test_quote_duplicates_and_unrequested_rows_are_not_recorded(recorder, individual):
    recorder.symbols = {"ABC": {"last_seen_at": capture.now()}}
    stamp = capture.dt.datetime.now(capture.UTC).timestamp()
    rows = [{"symbol": t, "price": 20, "timestamp": stamp} for t in ("ABC", "ABC", "OTHER")]
    http = MagicMock()
    http.get.side_effect = ([quote_response(status=402)] if individual else []) + [quote_response(rows)]
    recorder.sample(http)
    payload = recorder.queue.get_nowait()["payload"]
    assert [q["ticker"] for q in payload["quotes"]] == ["ABC"]
    assert payload["complete"] is False
    assert {e["reason"] for e in payload["errors"]} == {
        "duplicate_quote_row", "unrequested_quote_row"}


def test_stop_during_entitlement_probe_does_not_start_individual_requests(recorder):
    recorder.symbols = {"ABC": {"last_seen_at": capture.now()}}
    http = MagicMock()

    def get(*args, **kwargs):
        recorder.stopping.set()
        return quote_response(status=402)

    http.get.side_effect = get
    recorder.sample(http)
    assert http.get.call_count == 1
    assert recorder.quote_endpoint == "batch-quote"


def test_worker_purges_daily_and_sends_health_when_market_closed(monkeypatch, recorder):
    import supabase
    client = MagicMock()
    client.table().select().order().range().execute.return_value.data = []
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-only")
    monkeypatch.setattr(supabase, "create_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(recorder, "refresh_universe", lambda client: None)
    monkeypatch.setattr(recorder, "sample", lambda http: None)
    original = recorder.health
    def health(db, client):
        original(db, client)
        recorder.stopping.set()
    monkeypatch.setattr(recorder, "health", health)
    recorder.run()
    client.rpc.assert_called_once_with("purge_intraday_capture", {"keep_days": recorder.retention_days})
    payload = client.table().upsert.call_args.args[0]
    assert payload["config"]["spool_available"] is True
    assert payload["config"]["max_spool_events"] == 100000


def test_disk_failure_publishes_health_without_leaking_exception_text(monkeypatch, recorder):
    import supabase
    client = MagicMock()
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-only")
    monkeypatch.setattr(supabase, "create_client", lambda *args, **kwargs: client)
    def unavailable():
        raise OSError("do-not-publish-secret")
    monkeypatch.setattr(recorder, "open_spool", unavailable)
    original = recorder.health
    def health(db, client):
        original(db, client)
        recorder.stopping.set()
    monkeypatch.setattr(recorder, "health", health)
    recorder.run()
    payload = client.table().upsert.call_args.args[0]
    assert payload["config"]["spool_available"] is False
    assert payload["last_error"] == "worker: OSError"
    assert "do-not-publish-secret" not in json.dumps(payload)


def test_shutdown_stops_accepting_new_events(recorder):
    recorder.stopping.set()
    assert recorder.emit("monitor", {}) is None
    assert recorder.queue.empty()


def test_web_import_closure_needs_no_broker_sdk_or_network_modules():
    script = """
import importlib.abc
import sys
import threading
class DenyLiveModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'ib_insync', 'execution_agent', 'execution_agent_ref', 'requests', 'supabase'
        }:
            raise ImportError('web image must not import ' + fullname)
sys.meta_path.insert(0, DenyLiveModules())
before = threading.active_count()
import decision_core
import trigger_audit
import intraday_capture
assert intraday_capture._recorder is None
assert threading.active_count() == before
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=30)
    assert result.returncode == 0, result.stderr


def test_worker_can_warm_up_before_broker_connection_without_touching_ib(monkeypatch):
    import execution_agent as ea
    monkeypatch.setattr(capture, "_recorder", None)
    monkeypatch.setattr(capture, "INTRADAY_CAPTURE_ENABLED", True)
    monkeypatch.setattr(capture.Recorder, "start", lambda self: None)
    monkeypatch.setattr(capture.atexit, "register", lambda fn: None)
    broker_probe = MagicMock()
    monkeypatch.setattr(capture, "snapshot", broker_probe)
    capture.start(None, ea)
    first = capture._recorder
    capture.start(None, ea)
    assert capture._recorder is first
    assert first.broker_attached is False
    assert first.queue.qsize() == 1
    broker_probe.assert_not_called()


def test_all_producer_error_paths_return_while_worker_logging_is_blocked(monkeypatch, recorder):
    import execution_agent as ea
    monkeypatch.setattr(capture, "_recorder", recorder)
    monkeypatch.setattr(capture, "_startup_error", None)
    monkeypatch.setattr(ea, "get_ibkr_account", lambda ib: "U1")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def blocked_sink(*args, **kwargs):
        calls.append(threading.get_ident())
        entered.set()
        release.wait(5)

    def unavailable(*args, **kwargs):
        raise RuntimeError("instrumentation unavailable")

    monkeypatch.setattr(capture.LOG, "error", blocked_sink)
    monkeypatch.setattr(capture.LOG, "exception", blocked_sink)
    monkeypatch.setattr(capture, "broker_snapshot", unavailable)
    monkeypatch.setattr(capture, "effective_config", unavailable)
    recorder.queue = capture.queue.Queue(maxsize=1)
    recorder.emit("already_queued", {})
    recorder.error("worker reporting test")
    reporter = threading.Thread(target=recorder.log_errors, daemon=True)
    reporter.start()
    assert entered.wait(1)

    def produce():
        try:
            recorder.emit("queue_overflow", {})
            capture.emit("invalid", value=object())
            capture.snapshot(None)
            capture.observe_portfolio(None, [], "buy")
            capture.record_order(None, "market_buy_submitted")
            capture.start(object(), ea)
            capture._recorder = None
            capture.start(None, ea)
        finally:
            finished.set()

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    try:
        assert finished.wait(1), "optional capture tried to log synchronously on the producer"
        assert calls == [reporter.ident]
        assert recorder.dropped >= 1
        assert capture._startup_error["message"] == "startup: RuntimeError"
    finally:
        release.set()
        reporter.join(2)
        producer.join(2)


def test_worker_error_reporting_is_coalesced_without_clearing_health(monkeypatch, recorder):
    logger = MagicMock()
    monkeypatch.setattr(capture.LOG, "error", logger)
    for _ in range(100):
        recorder.error("provider unavailable")
    recorder.log_errors()
    for _ in range(100):
        recorder.error("provider still unavailable")
    recorder.log_errors()
    logger.assert_called_once()
    assert logger.call_args.args[1] == 100
    assert recorder.last_error == "provider still unavailable"


@pytest.mark.parametrize("capacity,expected_saved,expected_dropped", [
    (100000, 1500, 0), (1000, 1000, 500),
])
def test_shutdown_drains_every_accepted_event_and_accounts_for_capacity(
        monkeypatch, recorder, capacity, expected_saved, expected_dropped):
    recorder.max_spool_events = capacity
    for index in range(1500):
        recorder.emit("shutdown_test", {"index": index})
    monkeypatch.setattr(capture, "_recorder", recorder)
    recorder.stopping.set()
    recorder.start()
    assert capture.stop()
    assert not recorder.thread.is_alive()
    assert recorder.queue.empty()
    assert recorder.queue.unfinished_tasks == 0
    assert recorder.dropped == expected_dropped
    restarted = capture.Recorder(recorder.config, spool=recorder.spool)
    db = restarted.open_spool()
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == expected_saved
    assert restarted.dropped == expected_dropped
    if expected_dropped:
        assert "spool full" in restarted.last_error
    db.close()


def test_shutdown_waits_for_inflight_producer_before_final_drain(monkeypatch, recorder):
    import threading

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_event = recorder._event

    def delayed_event(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original_event(*args, **kwargs)

    def worker():
        assert recorder.stopping.wait(2)
        db = recorder.open_spool()
        try:
            recorder.drain(db)
        finally:
            db.close()

    def shutdown():
        try:
            assert capture.stop()
        finally:
            finished.set()

    monkeypatch.setattr(recorder, "_event", delayed_event)
    monkeypatch.setattr(capture, "_recorder", recorder)
    recorder.thread = threading.Thread(target=worker, daemon=True)
    recorder.thread.start()
    producer = threading.Thread(target=lambda: recorder.emit("inflight", {}), daemon=True)
    producer.start()
    assert entered.wait(1)
    stopper = threading.Thread(target=shutdown, daemon=True)
    stopper.start()
    try:
        assert not finished.wait(0.05)
    finally:
        release.set()
        producer.join(2)
        stopper.join(2)
        recorder.thread.join(2)
    assert finished.is_set()
    db = recorder.open_spool()
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1
    assert recorder.queue.empty()
    db.close()


def _history_client():
    client = MagicMock()
    client.table().select().lte().gte().order().range().execute.return_value.data = []
    client.reset_mock()
    return client


def _durable_seed(recorder):
    broker = {"snapshot_at": "2026-09-28T13:30:00+00:00"}
    seed = recorder.emit("portfolio_snapshot_seed", {
        "snapshot_at": broker["snapshot_at"], "broker_snapshot": broker,
        "portfolio_positions": [{"ticker": "ORIGINAL", "shares": 4}],
        "portfolio_received_at": broker["snapshot_at"],
        "effective_config": recorder.config,
    })
    db = recorder.open_spool()
    recorder.journal(db)
    return db, seed


def test_old_run_seed_recovers_after_raw_upload_with_original_config_and_idempotent_marker(recorder):
    db, seed = _durable_seed(recorder)
    client = _history_client()
    recorder.flush(db, client)
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    db.close()
    current_config = {"replay_config": {"cooling_off_days": 90}, "git_commit": "different-code"}
    restarted = capture.Recorder(current_config, spool=recorder.spool)
    db = restarted.open_spool()
    restarted.process_snapshot_jobs(db, client)
    event = json.loads(db.execute("SELECT event FROM pending").fetchone()[0])
    assert event["kind"] == "portfolio_snapshot"
    assert event["run_id"] == restarted.run_id
    assert event["payload"]["source_run_id"] == recorder.run_id
    assert event["payload"]["source_occurred_at"] == seed["occurred_at"]
    assert event["payload"]["snapshot_at"] == seed["payload"]["snapshot_at"]
    assert event["payload"]["effective_config"] == recorder.config
    assert event["payload"]["portfolio_positions"] == [{"ticker": "ORIGINAL", "shares": 4}]
    assert event["payload"]["source_times"]["ibkr_fills"]["filter_gte"] == "2026-08-29T13:30:00+00:00"
    snapshot_id = event["id"]
    assert db.execute("SELECT snapshot_event_id FROM snapshot_jobs").fetchone()[0] == snapshot_id
    client.table().upsert().execute.side_effect = RuntimeError("upload interrupted")
    with pytest.raises(RuntimeError):
        restarted.flush(db, client)
    db.close()

    third = capture.Recorder(current_config, spool=recorder.spool)
    db = third.open_spool()
    reads_before = client.table().select.call_count
    third.process_snapshot_jobs(db, client)
    assert client.table().select.call_count == reads_before
    assert db.execute("SELECT id FROM pending").fetchall() == [(snapshot_id,)]
    client.table().upsert().execute.side_effect = None
    third.flush(db, client)
    third.process_snapshot_jobs(db, client)
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM snapshot_jobs").fetchone()[0] == 1
    db.close()


def test_snapshot_output_and_completion_marker_commit_atomically(recorder):
    db, seed = _durable_seed(recorder)
    client = _history_client()
    recorder.flush(db, client)
    db.execute("CREATE TRIGGER interrupt_marker BEFORE UPDATE ON snapshot_jobs "
               "BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END")
    with pytest.raises(capture.sqlite3.IntegrityError, match="simulated interruption"):
        recorder.process_snapshot_jobs(db, client)
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
    assert db.execute("SELECT snapshot_event_id FROM snapshot_jobs").fetchone()[0] is None
    db.execute("DROP TRIGGER interrupt_marker")
    recorder.process_snapshot_jobs(db, client)
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"intraday-snapshot:{seed['id']}"))
    assert db.execute("SELECT id FROM pending").fetchall() == [(expected,)]
    recorder.process_snapshot_jobs(db, client)
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1
    db.close()


def test_seed_without_original_config_is_retained_not_relabelled(recorder):
    recorder.emit("portfolio_snapshot_seed", {
        "snapshot_at": "2026-09-28T13:30:00+00:00",
        "broker_snapshot": {"snapshot_at": "2026-09-28T13:30:00+00:00"},
        "portfolio_positions": [], "portfolio_received_at": "2026-09-28T13:30:00+00:00",
    })
    db = recorder.open_spool()
    recorder.journal(db)
    recorder.process_snapshot_jobs(db, _history_client())
    assert "original effective configuration" in recorder.last_error
    assert db.execute("SELECT snapshot_event_id FROM snapshot_jobs").fetchone()[0] is None
    assert not db.execute("SELECT id FROM pending WHERE json_extract(event,'$.kind')='portfolio_snapshot'").fetchall()
    db.close()


def test_failed_history_read_retains_seed_for_next_process(recorder):
    db, seed = _durable_seed(recorder)
    client = _history_client()
    client.table().select().lte().gte().order().range().execute.side_effect = RuntimeError("offline")
    recorder.process_snapshot_jobs(db, client)
    assert "historical reads incomplete" in recorder.last_error
    assert db.execute("SELECT snapshot_event_id FROM snapshot_jobs").fetchone()[0] is None
    recorder.flush(db, client)
    db.close()
    restarted = capture.Recorder(recorder.config, spool=recorder.spool)
    db = restarted.open_spool()
    client.table().select().lte().gte().order().range().execute.side_effect = None
    restarted.process_snapshot_jobs(db, client)
    event = json.loads(db.execute("SELECT event FROM pending").fetchone()[0])
    assert event["payload"]["seed_event_id"] == seed["id"]
    assert event["payload"]["recovered_from_prior_run"] is True
    assert db.execute("SELECT snapshot_event_id FROM snapshot_jobs").fetchone()[0] == event["id"]
    db.close()


def test_failed_old_seeds_do_not_starve_later_valid_snapshot_jobs(recorder):
    for _ in range(25):
        recorder.emit("portfolio_snapshot_seed", {"effective_config": None})
    db, seed = _durable_seed(recorder)
    client = _history_client()
    recorder.process_snapshot_jobs(db, client)
    assert db.execute("SELECT count(*) FROM snapshot_jobs WHERE snapshot_event_id IS NOT NULL").fetchone()[0] == 0
    recorder.process_snapshot_jobs(db, client)
    assert db.execute("SELECT seed_id FROM snapshot_jobs WHERE snapshot_event_id IS NOT NULL").fetchall() == [(seed["id"],)]
    assert db.execute("SELECT count(*) FROM snapshot_jobs WHERE snapshot_event_id IS NULL").fetchone()[0] == 25
    db.close()
