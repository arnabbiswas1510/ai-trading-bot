import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import uuid
from unittest.mock import MagicMock

import pytest

import shadow_engine
from shadow_inputs import InputGap, build_seed, completed_daily_bars, InputProducer, PublicMarketData, ReadOnlySources
from shadow_store import ShadowStore, StoreError, fingerprint
from shadow_worker import Worker, session_ticks, next_tick
from test_intraday_replay import records, DAY
from test_shadow_engine import seed_and_frames
from intraday_replay import build_dataset

UTC = dt.timezone.utc


@pytest.fixture
def directory():
    path = Path.cwd() / "tests" / (".shadow-test-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class Cloud:
    def __init__(self, fail=False):
        self.fail, self.rows, self.target = fail, {}, None

    def table(self, target):
        assert target.startswith("intraday_shadow_")
        self.target = target
        return self

    def upsert(self, body):
        self.body = copy.deepcopy(body)
        return self

    def execute(self):
        if self.fail:
            raise ConnectionError("offline")
        key = self.body.get("id") or (self.body["run_id"], self.body["sequence"])
        self.rows[(self.target, key)] = self.body
        return self


class TriggerTableClient:
    """Mirror the real composite key; reject the nonexistent id column."""
    def __init__(self, rows):
        self.rows, self.page_orders = rows, []

    def table(self, table):
        assert table == "daily_triggers"
        client = self

        class Query:
            def __init__(self):
                self.columns, self.filters = [], []

            def select(self, fields):
                assert fields == "*"
                return self

            def gte(self, column, value):
                self.filters.append((column, value))
                return self

            def order(self, column, desc=False):
                assert column in ("triggered_at", "ticker"), f"Unknown column: {column}"
                self.columns.append((column, desc))
                return self

            def range(self, start, end):
                self.start, self.end = start, end
                return self

            def execute(self):
                client.page_orders.append(tuple(self.columns))
                rows = [r for r in client.rows if all(r[c] >= v for c, v in self.filters)]
                rows.sort(key=lambda r: tuple(r[c] for c, _ in self.columns),
                          reverse=self.columns[0][1])
                return MagicMock(data=rows[self.start:self.end + 1])

        return Query()


@pytest.mark.parametrize("descending", [False, True])
def test_trigger_source_paginates_by_both_real_key_columns(descending):
    rows = [{"ticker": f"T{i % 7}", "triggered_at": (
        dt.date(2026, 1, 1) + dt.timedelta(days=i // 7)).isoformat()} for i in range(601)]
    client = TriggerTableClient(list(reversed(rows)))
    result = ReadOnlySources(client).read(
        "daily_triggers", order=("triggered_at", "ticker"), descending=descending)
    assert result["rows"] == sorted(
        rows, key=lambda r: (r["triggered_at"], r["ticker"]), reverse=descending)
    assert client.page_orders == [
        (("triggered_at", descending), ("ticker", descending))] * 2


def test_store_restart_staging_and_exactly_once_fill(directory, records):
    seed, frames = seed_and_frames(build_dataset(records, DAY, DAY))
    for event in frames[0]["events"]:
        for trigger in event.get("triggers", []):
            trigger["ai_grade"] = "A"
    initial = shadow_engine.initialize(seed, seed["config"])
    envelope = dict(cycle_key="test", occurred_at=frames[0]["captured_at"],
                    session=DAY, engine_frame=frames[0], source_evidence={"immutable": True})
    path = directory / "shadow.sqlite3"
    store = ShadowStore(path)
    run_id = store.create_run(seed, seed["config"], initial, shadow_engine.engine_fingerprint())
    store.stage(run_id, envelope)
    with pytest.raises(StoreError, match="Another"):
        ShadowStore(path)
    conflicting = copy.deepcopy(envelope)
    conflicting["source_evidence"]["immutable"] = False
    with pytest.raises(StoreError, match="Conflicting"):
        store.stage(run_id, conflicting)
    store.close()
    store = ShadowStore(path)
    restored = shadow_engine.restore(store.active()["state"])
    pending = store.pending(run_id)
    new_state, output = shadow_engine.advance(restored, pending["engine_frame"])
    assert len(output["fills"]) == 1
    assert store.commit_cycle(run_id, pending, new_state, output)
    assert not store.commit_cycle(run_id, pending, new_state, output)
    cash = store.active()["state"]["cash"]
    store.close()
    store = ShadowStore(path)
    assert store.pending(run_id) is None
    assert store.active()["state"]["cash"] == cash
    cloud = Cloud(fail=True)
    with pytest.raises(ConnectionError):
        store.upload(cloud)
    cloud.fail = False
    store.upload(cloud)
    store.upload(cloud)
    cycles = [r for (table, _), r in cloud.rows.items() if table == "intraday_shadow_events"]
    assert len(cycles) == 1 and len(cycles[0]["payload"]["output"]["fills"]) == 1
    with pytest.raises(StoreError, match="non-shadow"):
        store._enqueue("portfolio_positions", {})
    store.close()


def test_worker_replays_pending_without_refetch_then_blocks_missing_prices(directory, records):
    seed, frames = seed_and_frames(build_dataset(records, DAY, DAY))
    frame = frames[0]
    frame["frame_id"] = frame["captured_at"]
    envelope = dict(cycle_key=frame["frame_id"], occurred_at=frame["captured_at"],
                    session=DAY, engine_frame=frame)
    class Producer:
        config = seed["config"]
        def frame(self, *args, **kwargs):
            raise AssertionError("Must use the durably staged input")
    store = ShadowStore(directory / "worker.sqlite3")
    run_id = store.create_run(seed, seed["config"], shadow_engine.initialize(seed, seed["config"]),
                              shadow_engine.engine_fingerprint())
    store.stage(run_id, envelope)
    now = dt.datetime.fromisoformat(DAY + "T10:00:00-04:00")
    worker = Worker(store, Producer(), Cloud(), clock=lambda: now)
    assert worker.tick()
    assert not worker.tick()
    assert store.active()["status"] == "blocked"
    assert store.active()["sequence"] == 3
    events = [json.loads(row[0]) for row in store.db.execute(
        "SELECT row_json FROM events ORDER BY sequence")]
    assert [event["kind"] for event in events] == ["cycle", "gap", "recovery_queued"]
    assert store.new_run_request(store.active())["run_id"] == run_id
    store.close()


@pytest.mark.parametrize("change", ["build_label", "setting", "engine_source"])
def test_build_label_alone_does_not_block_existing_shadow_run(directory, records, monkeypatch, change):
    seed, frames = seed_and_frames(build_dataset(records, DAY, DAY))
    seed["config"]["git_commit"] = "original-build"
    first, second = frames[:2]
    first["frame_id"] = first["captured_at"]
    second["frame_id"] = second["captured_at"]
    store = ShadowStore(directory / "provenance.sqlite3")
    state = shadow_engine.initialize(seed, seed["config"])
    run_id = store.create_run(seed, seed["config"], state, shadow_engine.engine_fingerprint())
    initial = dict(cycle_key=first["frame_id"], occurred_at=first["captured_at"],
                   session=DAY, engine_frame=first)
    store.stage(run_id, initial)
    state, output = shadow_engine.advance(state, first)
    store.commit_cycle(run_id, initial, state, output)
    class Producer:
        config = copy.deepcopy(seed["config"])
        def frame(self, *args, **kwargs):
            return dict(cycle_key=second["frame_id"], occurred_at=second["captured_at"],
                        session=DAY, engine_frame=second)
    producer = Producer()
    producer.config["git_commit"] = "dashboard-only-build"
    if change == "setting":
        producer.config["decision_config"]["min_trigger_score"] += 1
    elif change == "engine_source":
        monkeypatch.setattr(shadow_engine, "engine_fingerprint", lambda: "changed-engine-source")
    worker = Worker(store, producer, Cloud(),
                    clock=lambda: dt.datetime.fromisoformat(second["captured_at"]))
    assert worker.tick() is (change == "build_label")
    assert store.active()["status"] == ("running" if change == "build_label" else "blocked")
    assert store.active()["config"]["git_commit"] == "original-build"
    assert store.active()["state"]["effective_config"]["git_commit"] == "original-build"
    store.close()


def flat_evidence():
    now = dt.datetime.fromisoformat("2026-09-30T10:00:00-04:00")
    def snapshot(stamp):
        return {"payload": {
            "account": "U_TEST", "complete": True, "connected": True, "atomic": False,
            "started_at": stamp.isoformat(), "snapshot_at": stamp.isoformat(),
            "component_times": {k: {"requested_at": stamp.isoformat(), "completed_at": stamp.isoformat()}
                                for k in ("positions", "open_orders_all_clients", "account_download", "executions")},
            "positions": [], "portfolio_marks": [], "open_orders": [], "fills": [],
            "commissions_complete": True,
            "account_values": [{"account": "U_TEST", "currency": "USD", "tag": "NetLiquidation", "value": "12345"}],
        }}
    pair = [snapshot(now - dt.timedelta(minutes=5)), snapshot(now)]
    proof = {"rows": [], "complete": True, "requested_at": now.isoformat(), "received_at": now.isoformat()}
    config = {"replay_config": {"cooling_off_days": 3}}
    return pair, proof, now, config


def test_actual_flat_seed_uses_observed_cash_not_fake_balance():
    pair, proof, now, config = flat_evidence()
    seed = build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)
    assert seed["account"]["cash"] == 12345
    assert seed["positions"] == []
    assert seed["coherence"] == "adjacent_concordant_completed_requests_not_atomic"


def test_seed_ignores_text_account_tags_and_preserves_raw_evidence():
    pair, proof, now, config = flat_evidence()
    tags = ["SettledCashByDate", "SettledCashByDate-S", "$LEDGER-AccountOrGroup",
            "$LEDGER-Cryptocurrency", "$LEDGER-Currency", "$LEDGER-RealCurrency"]
    for snapshot in pair:
        values = snapshot["payload"]["account_values"]
        values[0]["modelCode"] = ""
        values.extend({"account": "U_TEST", "modelCode": "", "currency": "USD",
                       "tag": tag, "value": "non-numeric broker metadata"} for tag in tags)
    seed = build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)
    assert seed["account"]["net_liquidation"] == seed["account"]["cash"] == 12345
    assert seed["source_evidence"]["observer"] == pair


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("mutation", [
    lambda rows: rows.clear(),
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows.append(dict(rows[0], value="99999")),
    lambda rows: rows[0].update(value=None),
    lambda rows: rows[0].update(value="not-numeric"),
    lambda rows: rows[0].update(value="nan"),
    lambda rows: rows[0].update(value="inf"),
    lambda rows: rows[0].update(value="-inf"),
    lambda rows: rows[0].update(value=True),
    lambda rows: rows[0].update(value="0"),
    lambda rows: rows[0].update(value="-1"),
    lambda rows: rows[0].update(account="FOREIGN"),
    lambda rows: rows[0].pop("account"),
    lambda rows: rows[0].update(modelCode="MODEL"),
    lambda rows: rows[0].update(modelCode=None),
    lambda rows: rows[0].update(currency="EUR"),
    lambda rows: rows.append(dict(rows[0], modelCode="MODEL")),
    lambda rows: rows.append(dict(rows[0], account="FOREIGN", tag="$LEDGER-Currency", value="USD")),
])
def test_seed_rejects_invalid_ambiguous_or_unscoped_equity(side, mutation):
    pair, proof, now, config = flat_evidence()
    mutation(pair[side]["payload"]["account_values"])
    with pytest.raises(InputGap):
        build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)


def test_actual_long_seed_preserves_flags_protection_and_cash(records):
    pair, proof, now, _ = flat_evidence()
    raw = records[0]["payload"]
    position = dict(raw["positions"][0], buy_commission=0.35, hwm_date="2026-09-25")
    proof["rows"] = [position]
    empty = dict(proof, rows=[])
    contract = {"conId": 1, "secType": "STK", "currency": "USD"}
    for record in pair:
        snapshot = record["payload"]
        snapshot["positions"] = [
            {"ticker": "HELD", "position": 10, "avgCost": 100, "account": "U_TEST", "contract": contract}]
        snapshot["portfolio_marks"] = [
            {"ticker": "HELD", "position": 10, "marketPrice": 101, "account": "U_TEST", "contract": contract}]
        snapshot["open_orders"] = [{
            "ticker": "HELD", "contract": contract,
            "order": {"account": "U_TEST", "totalQuantity": 10, "action": "SELL",
                      "orderType": kind, "orderId": i, "permId": i + 100, "clientId": 1,
                      "parentId": 0, "tif": "GTC", "ocaType": 1, "ocaGroup": "PROT_HELD_test",
                      "outsideRth": False, "triggerMethod": 0,
                      "trailingPercent": 10, "trailStopPrice": 90.9, "auxPrice": 95},
            "status": {"filled": 0, "remaining": 10, "status": "Submitted"},
        } for i, kind in enumerate(("TRAIL", "STP"), 1)]
    seed = build_seed(pair, proof, proof, empty, empty, account="U_TEST",
                      as_of=now, config=raw["config"])
    state = shadow_engine.initialize(seed, raw["config"])
    assert state["cash"] == 11335
    assert state["positions"]["HELD"]["shares"] == 10
    assert state["positions"]["HELD"]["closed_above_entry"] is True
    assert state["positions"]["HELD"]["broker_anchor"] == pytest.approx(101)
    broken = copy.deepcopy(proof)
    broken["rows"][0]["buy_commission"] = None
    with pytest.raises(InputGap, match="commission"):
        build_seed(pair, broken, broken, empty, empty, account="U_TEST",
                   as_of=now, config=raw["config"])
    pair[1]["payload"]["open_orders"][0]["status"]["remaining"] = None
    with pytest.raises(InputGap, match="filled/remaining"):
        build_seed(pair, proof, proof, empty, empty, account="U_TEST",
                   as_of=now, config=raw["config"])


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p[1]["payload"].update(account="OTHER"), "account"),
    (lambda p: p[1]["payload"].update(commissions_complete=False), "commissions"),
    (lambda p: p[0]["payload"].update(snapshot_at="2026-09-30T09:40:00-04:00"), "stale"),
    (lambda p: p[1]["payload"]["component_times"].pop("positions"), "timestamp"),
])
def test_missing_or_stale_snapshot_blocks(mutation, match):
    pair, proof, now, config = flat_evidence()
    mutation(pair)
    with pytest.raises(InputGap, match=match):
        build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)


@pytest.mark.parametrize("quantity", [-10, 0.5])
def test_actual_short_and_fraction_are_not_flattened(quantity):
    pair, proof, now, config = flat_evidence()
    position = {"ticker": "BAD", "position": quantity, "avgCost": 100, "account": "U_TEST",
                "contract": {"conId": 1, "secType": "STK", "currency": "USD"}}
    for row in pair:
        row["payload"]["positions"] = [position]
    with pytest.raises(InputGap, match="quantity|quantities"):
        build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)


def test_future_and_unfinished_today_bars_never_reach_indicators():
    now = dt.datetime.fromisoformat("2026-09-30T15:45:00-04:00")
    rows = [{"date": day, "open": 100, "high": 102, "low": 99, "close": close, "volume": 100}
            for day, close in (("2026-09-29", 101), ("2026-09-30", 999), ("2026-10-01", 9999))]
    accepted, rejected = completed_daily_bars(rows, now)
    assert [r["date"] for r in accepted] == ["2026-09-29"]
    assert rejected == ["2026-09-30", "2026-10-01"]


def test_calendar_early_close_and_missed_close():
    ticks = session_ticks(dt.date(2026, 11, 27))
    assert ticks[-1].hour == 12 and ticks[-1].minute == 59
    assert session_ticks(dt.date(2026, 11, 26)) == []
    run = {"state": {"last_frame_id": ticks[-2].isoformat()}}
    assert next_tick(run, ticks[-1] + dt.timedelta(hours=10)) == ticks[-1]


def test_public_quotes_use_one_batch_and_keep_provider_time():
    now = dt.datetime.fromisoformat("2026-09-30T10:00:00-04:00")
    class HTTP:
        calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self
        def raise_for_status(self):
            pass
        def json(self):
            return [{"symbol": s, "price": 100, "timestamp": now.timestamp()}
                    for s in ("HELD", "VETOED", "PAPER_ONLY")]
    http = HTTP()
    quotes, evidence = PublicMarketData(http, "synthetic-key", lambda: now).quotes(
        ["HELD", "VETOED", "PAPER_ONLY"])
    assert set(quotes) == {"HELD", "VETOED", "PAPER_ONLY"}
    assert len(http.calls) == 1
    assert quotes["PAPER_ONLY"]["provider_timestamp"] == now.astimezone(UTC).isoformat()
    assert "apikey" not in str(evidence)


def shadow_quote_response(rows=None, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = [] if rows is None else rows
    if status >= 400:
        response.raise_for_status.side_effect = RuntimeError("HTTP failure; apikey=DO-NOT-LOG")
    return response


def test_shadow_quotes_45_symbol_fallback_preserves_mode_raw_data_and_provenance():
    now = dt.datetime.fromisoformat("2026-10-04T11:00:00-04:00")
    friday = now - dt.timedelta(days=2)
    symbols = [f"T{i:02}" for i in range(45)]
    http = MagicMock()
    http.get.side_effect = [shadow_quote_response(status=402)] + [
        shadow_quote_response([{"symbol": s, "price": 20, "timestamp": friday.timestamp(),
                                "volume": 100}]) for s in symbols]
    market = PublicMarketData(http, "DO-NOT-LOG", lambda: now)
    quotes, evidence = market.quotes(symbols)
    assert len(quotes) == 45 and http.get.call_count == 46
    assert evidence[0]["endpoint"] == "batch-quote" and evidence[0]["status_code"] == 402
    assert evidence[-1]["request_count"] == evidence[-1]["request_limit"] == 46
    assert evidence[-1]["endpoint_mode"] == "quote"
    assert evidence[-1]["fallback_reason"] == "batch_quote_http_402"
    assert all(q["provider_timestamp"] == friday.astimezone(UTC).isoformat()
               and q["received_at"] == now.isoformat() and q["raw"]["volume"] == 100
               and q["endpoint"] == "stable/quote" for q in quotes.values())
    assert all(c.kwargs["allow_redirects"] is False for c in http.get.call_args_list)
    assert "DO-NOT-LOG" not in str(evidence)
    http.reset_mock()
    http.get.side_effect = [
        shadow_quote_response([{"symbol": s, "price": 20, "timestamp": friday.timestamp()}])
        for s in symbols]
    _, evidence = market.quotes(symbols)
    assert http.get.call_count == 45
    assert all(c.args[0].endswith("/stable/quote") for c in http.get.call_args_list)
    assert evidence[-1]["request_count"] == 45


@pytest.mark.parametrize("individual", [False, True])
def test_shadow_share_class_quotes_preserve_internal_and_provider_identities(individual):
    from quote_transport import fmp_symbol
    now = dt.datetime.fromisoformat("2026-10-04T11:00:00-04:00")
    symbols = ["BRK.B", "MOG.A", "XYZ"]
    rows = [{"symbol": fmp_symbol(s), "price": 100, "timestamp": now.timestamp()}
            for s in symbols]
    http = MagicMock()
    http.get.side_effect = ([shadow_quote_response(status=402)] + [
        shadow_quote_response([row]) for row in rows] if individual else [
            shadow_quote_response(rows)])
    quotes, evidence = PublicMarketData(http, "DO-NOT-LOG", lambda: now).quotes(symbols)
    assert set(quotes) == set(symbols)
    assert quotes["MOG.A"]["provider_symbol"] == "MOG-A"
    assert quotes["MOG.A"]["raw"] == rows[1]
    assert quotes["MOG.A"]["provider_timestamp"] == now.astimezone(UTC).isoformat()
    assert evidence[0]["parameters"] == {"symbols": "BRK-B,MOG-A,XYZ"}
    assert evidence[0]["symbol_map"] == {"BRK-B": "BRK.B", "MOG-A": "MOG.A", "XYZ": "XYZ"}
    assert "DO-NOT-LOG" not in str(evidence)
    if individual:
        assert [c.kwargs["params"]["symbol"] for c in http.get.call_args_list[1:]] == [
            "BRK-B", "MOG-A", "XYZ"]


def test_full_104_symbol_shadow_quote_universe_including_share_class():
    from quote_transport import fmp_symbol
    now = dt.datetime.now(UTC)
    symbols = ["MOG.A"] + [f"T{i:03}" for i in range(103)]
    http = MagicMock()

    def get(url, *, params, **kwargs):
        if url.endswith("batch-quote") or params["symbol"] == "MOG.A":
            return shadow_quote_response(status=402)
        return shadow_quote_response([
            {"symbol": params["symbol"], "price": 100, "timestamp": now.timestamp()}])

    http.get.side_effect = get
    quotes, evidence = PublicMarketData(http, "synthetic", lambda: now).quotes(symbols)
    assert set(quotes) == set(symbols)
    assert http.get.call_count == evidence[-1]["request_count"] == 105
    assert all(quotes[s]["raw"]["symbol"] == fmp_symbol(s) for s in symbols)


@pytest.mark.parametrize("returned", ["MOG-B", "MOG.A", "OTHER"])
def test_share_class_response_cannot_substitute_another_identity(returned):
    http = MagicMock()
    http.get.return_value = shadow_quote_response([
        {"symbol": returned, "price": 100, "timestamp": 123}])
    with pytest.raises(InputGap, match="Unrequested"):
        PublicMarketData(http, "synthetic").quotes(["MOG.A"])


def test_ambiguous_share_class_aliases_fail_before_requesting_quotes():
    http = MagicMock()
    with pytest.raises(InputGap, match="ambiguous_provider_symbol"):
        PublicMarketData(http, "synthetic").quotes(["MOG.A", "MOG-A"])
    http.get.assert_not_called()


@pytest.mark.parametrize("ticker,expected", [
    ("MOG.A", "MOG-A"), ("BRK.B", "BRK-B"), ("MOG-A", "MOG-A"),
    ("VOD.L", "VOD.L"), ("BHP.AX", "BHP.AX"), ("ABC", "ABC"),
])
def test_fmp_symbol_translation_is_limited_to_dotted_ab_share_classes(ticker, expected):
    from quote_transport import fmp_symbol
    assert fmp_symbol(ticker) == expected


def test_shadow_history_translates_share_class_and_preserves_cache_identity():
    from market_calendar import session_bounds
    now = dt.datetime.fromisoformat("2026-10-04T11:00:00-04:00")
    days = [now.date() - dt.timedelta(days=i) for i in range(100, 0, -1)]
    bars = [dict(date=d.isoformat(), open=100, high=101, low=99, close=100,
                 volume=1000) for d in days if session_bounds(d)]
    http = MagicMock()
    http.get.return_value = shadow_quote_response(bars)
    market = PublicMarketData(http, "synthetic", lambda: now)
    history = market.history("MOG.A", now)
    assert http.get.call_args.kwargs["params"]["symbol"] == "MOG-A"
    assert history["parameters"]["symbol"] == "MOG-A"
    assert history["symbol_map"] == {"MOG-A": "MOG.A"}
    assert ("MOG.A", "2026-10-03") in market._history_cache
    assert market.history("MOG.A", now) == history
    assert http.get.call_count == 1


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_shadow_only_402_enables_fallback_without_leaking_errors(status):
    http = MagicMock()
    http.get.return_value = shadow_quote_response(status=status)
    market = PublicMarketData(http, "DO-NOT-LOG")
    with pytest.raises(InputGap, match="quote_request_failed") as error:
        market.quotes(["ABC"])
    assert "DO-NOT-LOG" not in str(error.value)
    assert http.get.call_count == 1 and market.quote_endpoint == "batch-quote"


@pytest.mark.parametrize("rows", [
    [],
    ["not-a-quote"],
    [{"symbol": "OTHER", "price": 20, "timestamp": 123}],
    [{"symbol": "ABC", "price": 20}],
    [{"symbol": "ABC", "price": 20, "timestamp": True}],
    [{"symbol": "ABC", "price": 20, "timestamp": "123"}],
    [{"symbol": "ABC", "price": 20, "timestamp": float("nan")}],
    [{"symbol": "ABC", "price": 20, "timestamp": float("inf")}],
    [{"symbol": "ABC", "price": 20, "timestamp": 1e100}],
    [{"symbol": "ABC", "price": True, "timestamp": 123}],
    [{"symbol": "ABC", "price": float("nan"), "timestamp": 123}],
    [{"symbol": "ABC", "price": 20, "timestamp": 123}] * 2,
    [{"symbol": "ABC", "price": 20, "timestamp": 123},
     {"symbol": "OTHER", "price": 20, "timestamp": 123}],
])
@pytest.mark.parametrize("individual", [False, True])
def test_shadow_quotes_fail_closed_on_incomplete_or_malformed_coverage(rows, individual):
    http = MagicMock()
    http.get.side_effect = ([shadow_quote_response(status=402)] if individual else []) + [
        shadow_quote_response(rows)]
    with pytest.raises(InputGap):
        PublicMarketData(http, "synthetic").quotes(["ABC"])


def test_shadow_quote_budget_discards_partial_results_and_preserves_fallback(monkeypatch):
    import shadow_inputs
    now = dt.datetime.fromisoformat("2026-09-30T10:00:00-04:00")
    elapsed = [0.0]
    monkeypatch.setattr(shadow_inputs.time, "monotonic", lambda: elapsed[0])
    http = MagicMock()
    market = PublicMarketData(http, "synthetic", lambda: now)
    market.quote_budget = 2.5
    def get(url, *, params, timeout, allow_redirects):
        assert sum(timeout) <= 2.5 - elapsed[0]
        if url.endswith("batch-quote"):
            return shadow_quote_response(status=402)
        elapsed[0] += 1.5
        return shadow_quote_response([{"symbol": params["symbol"], "price": 20,
                                       "timestamp": now.timestamp()}])
    http.get.side_effect = get
    with pytest.raises(InputGap, match="quote_budget_exhausted"):
        market.quotes(["ABC", "DEF", "GHI"])
    assert http.get.call_count == 3 and market.quote_endpoint == "quote"
    http.reset_mock()
    http.get.side_effect = [shadow_quote_response(status=429)]
    with pytest.raises(InputGap, match="quote_request_failed"):
        market.quotes(["ABC", "DEF", "GHI"])
    assert http.get.call_count == 1


def test_shadow_frame_rejects_old_friday_quotes_on_sunday():
    from research_configuration import effective_config
    from market_calendar import trading_days_between
    now = dt.datetime.fromisoformat("2026-10-04T11:00:00-04:00")
    friday = dt.datetime.fromisoformat("2026-10-02T16:00:00-04:00")
    class Sources:
        def read(self, *args, **kwargs):
            return {"rows": [], "complete": True, "requested_at": now.isoformat(),
                    "received_at": now.isoformat()}
    http = MagicMock()
    http.get.side_effect = [shadow_quote_response(status=402), shadow_quote_response([
        {"symbol": "ABC", "price": 20, "timestamp": friday.timestamp()}])]
    market = PublicMarketData(http, "synthetic", lambda: now)
    bars, day = [], now.date() - dt.timedelta(days=500)
    while day < now.date():
        if trading_days_between(day, day + dt.timedelta(days=1)):
            price = 100 + len(bars)
            bars.append(dict(date=day.isoformat(), open=price, high=price + 2,
                             low=price - 1, close=price + 1, volume=10000))
        day += dt.timedelta(days=1)
    market.histories = lambda symbols, as_of: {
        s: {"bars": bars, "available_at": now.isoformat()} for s in symbols}
    producer = InputProducer(Sources(), market, "U_TEST", effective_config(), clock=lambda: now)
    with pytest.raises(InputGap, match="stale/future provider quote"):
        producer.frame({"positions": {}, "universe": ["ABC"], "cash": 1000},
                       now.isoformat(), now)


@pytest.mark.parametrize("recorded_atr", ["absent", None, 0.0, 8.0])
def test_producer_outputs_real_engine_frames_and_prices_shadow_only_holdings(recorded_atr):
    from research_configuration import effective_config
    from market_calendar import trading_days_between
    pair, proof, now, _ = flat_evidence()
    config = effective_config()
    for row in pair:
        row["payload"]["account_values"][0]["value"] = "100000"
    seed = build_seed(pair, proof, proof, proof, proof, account="U_TEST", as_of=now, config=config)
    state = shadow_engine.initialize(seed, config)
    trigger = {
        "ticker": "CAND", "triggered_at": now.isoformat(), "trigger_type": "BREAKOUT",
        "final_score": 99, "quality_score": 99, "adjusted_score": None, "ai_rating": 99,
        "ai_grade": "A", "next_earnings_date": None, "volume_surge": 2,
        "pivot_distance_pct": None, "close_price": 100, "atr_pct": recorded_atr,
    }
    if recorded_atr == "absent":
        trigger.pop("atr_pct")
    rows = [trigger, dict(trigger, ticker="VETOED", ai_grade="D")]
    source_client = TriggerTableClient(rows)
    class Market:
        universes = []
        def history(self, symbol, as_of):
            bars, day = [], as_of.date() - dt.timedelta(days=500)
            while day < as_of.date():
                if trading_days_between(day, day + dt.timedelta(days=1)):
                    price = 100 + len(bars)
                    bars.append(dict(date=day.isoformat(), open=price, high=price + 2,
                                     low=price - 1, close=price + 1, volume=10000))
                day += dt.timedelta(days=1)
            return {"bars": bars, "available_at": as_of.isoformat()}
        def quotes(self, symbols):
            self.universes.append(set(symbols))
            return {s: {"price": 100, "provider_timestamp": now.isoformat(),
                        "received_at": now.isoformat(), "source": "FMP"} for s in symbols}, []
    market = Market()
    producer = InputProducer(ReadOnlySources(source_client, clock=lambda: now),
                             market, "U_TEST", config, clock=lambda: now)
    frame = producer.frame(state, now.isoformat(), now)
    state, output = shadow_engine.advance(state, frame["engine_frame"])
    assert [f["ticker"] for f in output["fills"]] == ["CAND"]
    captured_atr = frame["engine_frame"]["events"][0]["triggers"][0]["atr_pct"]
    assert captured_atr == (None if recorded_atr == "absent" else recorded_atr)
    expected_stop = (config["replay_config"]["atr_stop_max_pct"] if recorded_atr == 8.0
                     else config["exit_config"]["stop_loss_pct"])
    assert state["positions"]["CAND"]["stop_loss_pct"] == expected_stop
    assert "VETOED" in market.universes[-1]
    diagnostic = frame["source_evidence"]["derived_indicators"]["CAND"]
    assert diagnostic["atr14"] > 0
    assert diagnostic["atr_pct"] == pytest.approx(100 * diagnostic["atr14"] / diagnostic["previous_close"])
    assert diagnostic["atr_pct_unit"] == "percentage_points"
    rows.clear()
    now += dt.timedelta(minutes=5)
    frame = producer.frame(state, now.isoformat(), now, decision_cycle=False)
    state, output = shadow_engine.advance(state, frame["engine_frame"])
    assert "CAND" in market.universes[-1]  # not in actual account or current candidates
    assert "VETOED" in market.universes[-1]  # still available to alternative configurations
    assert source_client.page_orders == [
        (("triggered_at", False), ("ticker", False))] * 2


@pytest.mark.parametrize("start,expected_key,eod,closing", [
    ("09:30:30", "09:30:00", False, False),
    ("09:34:30", "09:34:30", False, False),
    ("15:50:30", "15:50:00", True, False),
    ("15:59:10", "15:59:00", True, True),
])
def test_real_worker_initializes_at_093030_without_preseed_events(directory, start, expected_key, eod, closing):
    from research_configuration import effective_config
    from market_calendar import trading_days_between
    clock = [dt.datetime.fromisoformat(f"2026-09-30T{start}-04:00")]
    config = effective_config()
    pair_template, _, _, _ = flat_evidence()
    class Sources:
        def read(self, table, *args, **kwargs):
            began = clock[0]
            clock[0] += dt.timedelta(milliseconds=50)
            rows = []
            if table == "daily_triggers":
                rows = [{
                    "ticker": "CAND", "triggered_at": began.date().isoformat(), "trigger_type": "BREAKOUT",
                    "final_score": 99, "quality_score": 99, "adjusted_score": None, "ai_rating": 99,
                    "ai_grade": "A", "next_earnings_date": None, "volume_surge": 2,
                    "pivot_distance_pct": None, "close_price": 100, "atr_pct": None,
                }]
            return {"rows": rows, "complete": True, "requested_at": began.isoformat(),
                    "received_at": clock[0].isoformat()}
        def observer_pair(self, account):
            pair = copy.deepcopy(pair_template)
            for index, record in enumerate(pair):
                stamp = clock[0] - dt.timedelta(minutes=5 if index == 0 else 0)
                raw = record["payload"]
                raw.update(started_at=stamp.isoformat(), snapshot_at=stamp.isoformat())
                raw["account_values"][0]["value"] = "100000"
                for times in raw["component_times"].values():
                    times.update(requested_at=stamp.isoformat(), completed_at=stamp.isoformat())
            return pair
    class Market:
        def history(self, symbol, as_of):
            bars, day = [], as_of.date() - dt.timedelta(days=500)
            while day < as_of.date():
                if trading_days_between(day, day + dt.timedelta(days=1)):
                    price = 100 + len(bars)
                    bars.append(dict(date=day.isoformat(), open=price, high=price + 2,
                                     low=price - 1, close=price + 1, volume=10000))
                day += dt.timedelta(days=1)
            return {"bars": bars, "available_at": as_of.isoformat()}
        def quotes(self, symbols):
            clock[0] += dt.timedelta(milliseconds=250)
            return {s: {"price": 100, "provider_timestamp": clock[0].isoformat(),
                        "received_at": clock[0].isoformat(), "source": "FMP"} for s in symbols}, []
    producer = InputProducer(Sources(), Market(), "U_TEST", config, clock=lambda: clock[0])
    store = ShadowStore(directory / "realtime.sqlite3")
    worker = Worker(store, producer, Cloud(), clock=lambda: clock[0])
    assert worker.tick()
    run = store.active()
    assert run["state"]["last_frame_id"] == f"2026-09-30T{expected_key}-04:00"
    assert dt.datetime.fromisoformat(run["state"]["last_timestamp"]) > dt.datetime.fromisoformat(
        run["seed"]["timestamp"])
    assert run["state"]["positions"]["CAND"]["shares"] > 0
    frame = store.db.execute("SELECT frame FROM inputs").fetchone()[0]
    import json
    kinds = [e["type"] for e in json.loads(frame)["engine_frame"]["events"]]
    assert ("eod_latch" in kinds) is eod
    assert ("end_mark" in kinds) is closing
    first_trigger = json.loads(frame)["engine_frame"]["events"][0]["triggers"][0]
    assert first_trigger["triggered_at"] == "2026-09-30"
    assert dt.datetime.fromisoformat(first_trigger["observed_at"]).time() != dt.time()
    assert worker.flush()
    store.close()


def test_daily_history_batch_uses_bounded_parallelism_and_cache(monkeypatch):
    import threading
    now = dt.datetime.fromisoformat("2026-09-30T10:00:00-04:00")
    market = PublicMarketData(None, "synthetic", lambda: now)
    called, lock = [], threading.Lock()
    def history(symbol, as_of):
        with lock:
            called.append((symbol, threading.current_thread().name))
        return {"bars": [], "available_at": as_of.isoformat()}
    monkeypatch.setattr(market, "history", history)
    result = market.histories([f"T{i}" for i in range(250)], now)
    assert len(result) == 250
    assert all(name.startswith("shadow-history") for _, name in called)
    assert len({name for _, name in called}) <= 8
    cached = {"bars": [], "available_at": now.isoformat()}
    market._history_cache[("CACHED", "2026-09-29")] = cached
    assert PublicMarketData.history(market, "CACHED", now) == cached


@pytest.mark.parametrize("value,expected", [
    ("2026-09-30", dt.date(2026, 9, 30)),
    ("2026-09-29", dt.date(2026, 9, 29)),
    ("2026-09-30T09:30:00-04:00", dt.date(2026, 9, 30)),
    ("2026-09-30T00:30:00+00:00", dt.date(2026, 9, 29)),
])
def test_trigger_date_preserves_date_granularity_and_timestamp_compatibility(value, expected):
    from research.live_rule_replay import trigger_date
    received = dt.datetime.fromisoformat("2026-09-30T09:30:30-04:00")
    assert trigger_date(value, received) == expected


@pytest.mark.parametrize("value", [
    "2026-10-01", "2026-09-30T09:31:00-04:00", "2026-09-30T09:30:00",
    "2026-02-30", "", None,
])
def test_trigger_date_rejects_future_invalid_and_naive_timestamp_inputs(value):
    from research.live_rule_replay import trigger_date, ReplayInputError
    received = dt.datetime.fromisoformat("2026-09-30T09:30:30-04:00")
    with pytest.raises(ReplayInputError):
        trigger_date(value, received)


def test_recorded_capture_replay_preserves_date_only_triggers(records):
    for record in records:
        for trigger in record["payload"].get("triggers", []):
            trigger["triggered_at"] = DAY
    dataset = build_dataset(records, DAY, DAY)
    triggers = [t for e in dataset["events"] for t in e.get("triggers", [])]
    assert triggers
    assert all(t["triggered_at"] == DAY for t in triggers)
    assert all("T" in t["observed_at"] for t in triggers)
