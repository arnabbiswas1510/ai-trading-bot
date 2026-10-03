import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import uuid

import pytest

import intraday_capture as capture
import intraday_observer as observer


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def emit(self, *args):
        for handler in list(self.handlers):
            handler(*args)


def forbidden(*args, **kwargs):
    raise AssertionError("Test broker forbids all brokerage writes")


class FakeIB:
    def __init__(self):
        self.client = NS(
            reqAccountUpdates=lambda *args: None, placeOrder=forbidden,
            cancelOrder=forbidden, reqGlobalCancel=forbidden)
        self.errorEvent = Event()
        self.disconnectedEvent = Event()
        self.accountValueEvent = Event()
        self.updatePortfolioEvent = Event()
        self.wrapper = NS(openOrder=lambda *args: None)
        self.live = False
        self.accounts = ["U1"]
        self.connect_calls = []
        self.reads = []
        self.positions_error = False
        self.disconnect_on_positions = False
        self.account_download_empty = False
        self.connect_error = False
        self.orders = []
        self.executions = []
        self.contract = NS(conId=10, symbol="ABC", secType="STK", currency="USD", exchange="SMART")
        self.positions = [
            NS(account="U1", contract=self.contract, position=-2, avgCost=20),
            NS(account="U2", contract=self.contract, position=100, avgCost=20),
        ]

    placeOrder = cancelOrder = reqGlobalCancel = forbidden

    def connect(self, host, port, **kwargs):
        self.connect_calls.append(kwargs)
        assert kwargs["readonly"] is True
        assert kwargs["clientId"] not in (0, 1)
        if self.connect_error:
            raise ConnectionError("offline")
        self.live = True

    def disconnect(self):
        self.live = False
        self.disconnectedEvent.emit()

    def isConnected(self):
        return self.live

    def managedAccounts(self):
        return self.accounts

    def reqPositions(self):
        self.reads.append("positions")
        if self.disconnect_on_positions:
            self.live = False
            self.disconnectedEvent.emit()
        if self.positions_error:
            raise TimeoutError("positionEnd missing")
        return self.positions

    def reqAllOpenOrders(self):
        self.reads.append("all_open_orders")
        for trade in self.orders:
            self.wrapper.openOrder(getattr(trade.order, "orderId", 0),
                                   trade.contract, trade.order,
                                   getattr(trade, "orderStatus", NS()))
        return self.orders

    def reqAccountUpdates(self, account):
        self.reads.append("account_updates")
        if self.account_download_empty:
            return
        for owner in ("U1", "U2"):
            self.accountValueEvent.emit(NS(account=owner, tag="NetLiquidation",
                                           value="1000", currency="USD"))
            self.updatePortfolioEvent.emit(NS(
                account=owner, contract=self.contract, position=-2, marketPrice=21,
                marketValue=-42, averageCost=20, unrealizedPNL=-2, realizedPNL=0))

    def reqExecutions(self, execution_filter):
        self.reads.append("executions")
        assert execution_filter.acctCode == "U1"
        return self.executions

    def fills(self):
        return self.executions

    def sleep(self, seconds):
        time.sleep(min(seconds, 0.002))


@pytest.fixture
def root():
    path = Path(__file__).parent / f".observer-test-{uuid.uuid4().hex}"
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def recorder(root):
    return capture.Recorder(observer.collector_config(), spool=root / "observer.sqlite3",
                            health_id="intraday-observer", mode="observer")


def connected(recorder, fake=None):
    fake = fake or FakeIB()
    broker = observer.ReadOnlyBroker(fake, recorder)
    broker.connect("offline-test", 4000, 71)
    return broker, fake


def test_imports_no_execution_or_trading_modules():
    result = subprocess.run([
        sys.executable, "-c",
        "import sys; import intraday_observer; "
        "assert not ({'execution_agent', 'execution_agent_ref', 'buying', "
        "'selling', 'reconciliation', 'force_buy', 'force_sell', 'managed_exit'} & set(sys.modules))"
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_guard_denies_high_low_and_raw_capabilities(recorder):
    broker, fake = connected(recorder)
    for target in (broker, fake, fake.client):
        for name in ("placeOrder", "cancelOrder", "reqGlobalCancel", "reqAutoOpenOrders",
                     "exerciseOptions", "replaceFA"):
            with pytest.raises(PermissionError):
                getattr(target, name)(None)
    for name in ("client", "wrapper", "sendMsg", "reqOpenOrders"):
        with pytest.raises(PermissionError):
            getattr(broker, name)
    assert fake.RaiseRequestErrors


@pytest.mark.parametrize("accounts,requested", [
    ([], None), (["U1", "DU1"], None), (["U1", "U2"], None), (["U1"], "U2"),
])
def test_account_ambiguity_fails(accounts, requested):
    with pytest.raises(observer.ObservationError):
        observer.select_account(accounts, requested)


def test_explicit_account_allowed():
    assert observer.select_account(["U1", "DU1"], "U1") == "U1"


@pytest.mark.parametrize("client_id", [0, 1, -1])
def test_execution_and_binding_client_ids_forbidden(recorder, client_id):
    fake = FakeIB()
    broker = observer.ReadOnlyBroker(fake, recorder)
    with pytest.raises(observer.ObservationError):
        broker.connect("offline", 4000, client_id)
    assert not fake.connect_calls


def test_fresh_signed_snapshot_keeps_short_zero_other_assets_and_order_owners(recorder):
    broker, fake = connected(recorder)
    fake.positions.extend([
        NS(account="U1", contract=NS(conId=20, symbol="ZERO", secType="STK"), position=5, avgCost=4),
        NS(account="U1", contract=NS(conId=20, symbol="ZERO", secType="STK"), position=0, avgCost=4),
        NS(account="U1", contract=NS(conId=30, symbol="OPT", secType="OPT"), position=-1, avgCost=3),
    ])
    for client_id, account in ((1, "U1"), (99, "U1"), (42, "U2")):
        fake.orders.append(NS(contract=fake.contract, order=NS(
            account=account, clientId=client_id, orderId=client_id, permId=1000 + client_id),
            orderStatus=NS(status="Submitted")))
    event = broker.snapshot()
    data = event["payload"]
    assert event["kind"] == "observer_snapshot"
    assert data["mode"] == "observer" and data["replay_ready"] is False
    assert data["capture_mode"] == "observer"
    assert data["decision_inputs_available"] is False
    assert {p["ticker"]: p["position"] for p in data["positions"]} == {"ABC": -2, "ZERO": 0, "OPT": -1}
    assert len(data["short_positions"]) == 2
    assert [o["order"]["clientId"] for o in data["open_orders"]] == [1, 99]
    assert data["account_values"][0]["account"] == "U1"
    assert len(data["account_values"]) == len(data["portfolio_marks"]) == 1
    assert data["portfolio_marks"][0]["provider_timestamp"] is None
    assert data["complete"] and not data["atomic"]
    assert set(data["component_times"]) == {"positions", "open_orders_all_clients", "account_download", "executions"}
    assert fake.reads == ["positions", "all_open_orders", "account_updates", "executions"]
    assert "portfolio_snapshot" not in [e["kind"] for e in recorder.queue.queue]


@pytest.mark.parametrize("fault", ["positions_error", "disconnect_on_positions", "account_download_empty"])
def test_incomplete_snapshot_never_claims_success(recorder, fault):
    broker, fake = connected(recorder)
    setattr(fake, fault, True)
    with pytest.raises((observer.ObservationError, TimeoutError)):
        broker.snapshot()
    assert not any(e["kind"] == "observer_snapshot" for e in recorder.queue.queue)
    assert not fake.accountValueEvent.handlers
    assert not fake.updatePortfolioEvent.handlers


def test_broker_request_errors_and_disconnect_are_persistable_observations(recorder):
    broker, fake = connected(recorder)
    fake.errorEvent.emit(12, 321, "request failed", None)
    fake.live = False
    fake.disconnectedEvent.emit()
    assert "disconnected" in recorder.last_error
    events = list(recorder.queue.queue)
    assert any(e["kind"] == "observer_broker_error" and e["payload"]["code"] == 321 for e in events)
    assert any(e["kind"] == "capture_gap" for e in events)
    with pytest.raises(observer.ObservationError):
        broker.snapshot()


def test_broker_error_during_request_invalidates_snapshot(recorder):
    broker, fake = connected(recorder)
    original = fake.reqPositions
    def failed():
        fake.errorEvent.emit(1, 321, "request rejected")
        return original()
    fake.reqPositions = failed
    with pytest.raises(observer.ObservationError, match="broker error"):
        broker.snapshot()
    assert not any(e["kind"] == "observer_snapshot" for e in recorder.queue.queue)


def test_unscoped_order_is_not_silently_omitted(recorder):
    broker, fake = connected(recorder)
    fake.orders = [NS(order=NS(account=""), contract=fake.contract)]
    with pytest.raises(observer.ObservationError, match="no account"):
        broker.snapshot()


def test_real_sdk_amended_order_uses_fresh_callback_not_cached_trade(recorder):
    from ib_insync import Order, OrderState, Stock
    from ib_insync.wrapper import Wrapper

    fake = FakeIB()
    fake.wrapper = Wrapper(fake)
    fake.client.updateReqId = lambda *args: None
    fake.openOrderEvent = Event()
    broker, _ = connected(recorder, fake)
    contract = Stock("ABC", "SMART", "USD", conId=10)
    original_callback = fake.wrapper.openOrder
    observations = [
        Order(orderId=7, permId=123, clientId=99, account="U1", action="SELL",
              orderType="TRAIL", totalQuantity=2, trailingPercent=5,
              trailStopPrice=95, tif="GTC"),
        Order(orderId=7, permId=123, clientId=99, account="U1", action="SELL",
              orderType="TRAIL", totalQuantity=2, trailingPercent=2,
              trailStopPrice=108, tif="DAY"),
    ]
    states = ["Submitted", "PreSubmitted"]
    def request_orders():
        future = fake.wrapper.startReq("openOrders")
        order = observations.pop(0)
        fake.wrapper.openOrder(order.orderId, contract, order,
                               OrderState(status=states.pop(0)))
        # An order from a different account must still go through the SDK,
        # but must never enter this observer's scoped snapshot.
        fake.wrapper.openOrder(8, contract, Order(
            orderId=8, permId=124, clientId=42, account="U2", action="SELL",
            orderType="TRAIL", totalQuantity=100, trailingPercent=9, tif="GTC"),
            OrderState(status="Submitted"))
        fake.wrapper.openOrderEnd()
        return future.result()
    fake.reqAllOpenOrders = request_orders
    first = broker.snapshot()["payload"]["open_orders"]
    assert fake.wrapper.openOrder == original_callback
    second = broker.snapshot()["payload"]["open_orders"]
    assert fake.wrapper.openOrder == original_callback
    assert len(first) == len(second) == 1
    assert first[0]["order"]["trailingPercent"] == 5
    assert second[0]["order"]["trailingPercent"] == 2
    assert second[0]["order"]["trailStopPrice"] == 108
    assert second[0]["order"]["tif"] == "DAY"
    assert second[0]["order"]["clientId"] == 99
    assert second[0]["status"]["status"] == "PreSubmitted"
    assert second[0]["status"]["filled"] is None
    assert second[0]["source"] == "IBKR_OPEN_ORDER_CALLBACK"
    assert second[0]["received_at"]
    # Demonstrate the real SDK defect remains in its cache, without using it.
    cached = fake.wrapper.trades[fake.wrapper.orderKey(99, 7, 123)]
    assert cached.order.trailingPercent == 5
    assert cached.order.trailStopPrice == 95
    assert cached.order.tif == "GTC"
    assert len(fake.connect_calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "disconnect", "broker_error", "missing_callback"])
def test_fresh_order_callback_restored_when_request_fails(recorder, failure):
    broker, fake = connected(recorder)
    original = fake.wrapper.openOrder
    def request_orders():
        if failure == "timeout":
            raise TimeoutError("openOrderEnd missing")
        if failure == "disconnect":
            fake.live = False
        if failure == "broker_error":
            fake.errorEvent.emit(7, 321, "request rejected")
        if failure == "missing_callback":
            return [NS()]
        return []
    fake.reqAllOpenOrders = request_orders
    with pytest.raises(observer.ObservationError):
        broker.snapshot()
    assert fake.wrapper.openOrder == original
    assert not any(e["kind"] == "observer_snapshot" for e in recorder.queue.queue)


def test_fill_account_execid_and_delayed_commission_are_retained(recorder):
    broker, fake = connected(recorder)
    fill = NS(contract=fake.contract,
              execution=NS(execId="exec-1", acctNumber="U1", side="SLD", shares=2, price=21,
                           clientId=99, orderId=7, permId=123),
              commissionReport=NS(execId="", commission=0, currency="USD"))
    other = NS(contract=fake.contract, execution=NS(execId="other", acctNumber="U2"),
               commissionReport=NS(execId="other"))
    fake.executions = [fill, other]
    first = broker.snapshot()["payload"]
    assert len(first["fills"]) == 1
    assert first["fills"][0]["execution"]["execId"] == "exec-1"
    assert not first["commissions_complete"]
    fill.commissionReport = NS(execId="exec-1", commission=1.25, currency="USD", realizedPNL=-3)
    broker.pump(0)
    events = [e["payload"] for e in recorder.queue.queue if e["kind"] == "observer_fill"]
    assert len(events) == 2
    assert all(e["account"] == "U1" for e in events)
    assert events[-1]["commission"]["commission"] == 1.25
    assert events[-1]["execution"]["clientId"] == 99


def test_other_thread_cannot_use_broker(recorder):
    broker, _ = connected(recorder)
    errors = []
    def attempt():
        try:
            broker.snapshot()
        except observer.ObservationError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    assert errors and "owning main thread" in errors[0]


def test_spool_health_and_modes_independent(recorder, root):
    live = capture.Recorder({}, spool=root / "execution.sqlite3")
    assert recorder.spool != live.spool
    for rec in (recorder, live):
        db = rec.open_spool()
        rec.emit("source_snapshot", {"positions": [{"ticker": "ABC", "position": -1}]})
        rec.journal(db)
        client = MagicMock()
        rec.health(db, client)
        data = client.table().upsert.call_args.args[0]
        assert data["id"] == rec.health_id
        assert data["config"]["mode"] == rec.mode
        event = json.loads(db.execute("SELECT event FROM pending").fetchone()[0])
        if rec is recorder:
            assert event["payload"]["mode"] == "observer"
            assert event["payload"]["capture_mode"] == "observer"
            assert data["config"]["capture_mode"] == "observer"
            assert event["payload"]["replay_ready"] is False
        else:
            assert "mode" not in event["payload"]
        assert db.execute("SELECT count(*) FROM snapshot_jobs").fetchone()[0] == 0
        assert "ABC" in rec.symbols
        db.close()


def test_observer_mode_cannot_be_overridden_by_event_payload(recorder):
    event = recorder.emit("candidate_universe", {
        "mode": "execution", "capture_mode": "execution", "replay_ready": True,
    })
    assert event["payload"]["capture_mode"] == "observer"
    assert event["payload"]["mode"] == "observer"
    assert event["payload"]["replay_ready"] is False
    assert recorder.config["capture_mode"] == "observer"
    assert recorder.config["replay_ready"] is False


def worker_setup(monkeypatch, *, upload_fails=False):
    import supabase
    client = MagicMock()
    if upload_fails:
        client.table().upsert().execute.side_effect = RuntimeError("offline")
    monkeypatch.setattr(supabase, "create_client", lambda *args, **kwargs: client)
    monkeypatch.setenv("SUPABASE_URL", "https://offline.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "not-a-credential")
    # Exercise real lifecycle/journal/upload; discovery and FMP have separate tests.
    monkeypatch.setattr(capture.Recorder, "refresh_universe", lambda *args: None)
    monkeypatch.setattr(capture.Recorder, "sample", lambda *args: None)
    client.table().select().order().range().execute.return_value.data = []
    return client


def arguments(root, *extra):
    return observer.parser().parse_args([
        "--spool", str(root / "observer.sqlite3"), "--persist-timeout", "1",
        "--reconnect-delay", ".001", *extra,
    ])


def test_once_real_worker_upload_and_clean_shutdown(root, monkeypatch):
    client = worker_setup(monkeypatch)
    created = []
    def factory(*args, **kwargs):
        rec = capture.Recorder(*args, **kwargs)
        created.append(rec)
        return rec
    fake = FakeIB()
    code = observer.run(arguments(root, "--once"), ib_factory=lambda: fake, recorder_factory=factory)
    assert code == 0
    assert not fake.live
    assert not created[0].thread.is_alive()
    assert created[0].queue.empty()
    assert created[0].last_uploaded_sequence > 0
    assert any(call.args and call.args[0] == "intraday_capture_events"
               for call in client.table.call_args_list)
    db = sqlite3.connect(str(created[0].spool))
    assert any(json.loads(row[0])["kind"] == "observer_shutdown"
               for row in db.execute("SELECT event FROM pending"))
    db.close()


def test_once_upload_failure_is_nonzero_and_spool_retained(root, monkeypatch):
    worker_setup(monkeypatch, upload_fails=True)
    fake = FakeIB()
    assert observer.run(arguments(root, "--once"), ib_factory=lambda: fake) == 2
    db = sqlite3.connect(str(root / "observer.sqlite3"))
    rows = [json.loads(row[0]) for row in db.execute("SELECT event FROM pending")]
    assert any(e["kind"] == "observer_snapshot" for e in rows)
    db.close()


def test_once_spool_failure_is_nonzero(root, monkeypatch):
    worker_setup(monkeypatch)
    monkeypatch.setattr(capture.Recorder, "open_spool", lambda self: (_ for _ in ()).throw(OSError()))
    assert observer.run(arguments(root, "--once"), ib_factory=FakeIB) == 2


def test_reconnects_have_total_budget_and_never_retry_forever(root, monkeypatch):
    worker_setup(monkeypatch)
    fake = FakeIB()
    fake.connect_error = True
    assert observer.run(arguments(root, "--max-reconnects", "2"), ib_factory=lambda: fake) == 2
    assert len(fake.connect_calls) == 3
    assert not fake.live


def test_reconnect_can_recover_then_stop_cleanly(root, monkeypatch):
    worker_setup(monkeypatch)
    fake = FakeIB()
    original = fake.connect
    stop = threading.Event()
    def connect(*args, **kwargs):
        fake.connect_error = not fake.connect_calls
        original(*args, **kwargs)
    fake.connect = connect
    original_snapshot = observer.ReadOnlyBroker.snapshot
    def snapshot(self):
        event = original_snapshot(self)
        stop.set()
        return event
    monkeypatch.setattr(observer.ReadOnlyBroker, "snapshot", snapshot)
    assert observer.run(arguments(root), ib_factory=lambda: fake, stop_event=stop) == 0
    assert len(fake.connect_calls) == 2


def test_same_execution_spool_rejected_before_connect(root, monkeypatch):
    import config
    monkeypatch.setattr(config, "INTRADAY_CAPTURE_SPOOL", str(root / "observer.sqlite3"))
    fake = FakeIB()
    assert observer.run(arguments(root), ib_factory=lambda: fake) == 2
    assert not fake.connect_calls


def test_cancelled_diagnostic_is_not_success(root, monkeypatch):
    worker_setup(monkeypatch)
    stop = threading.Event()
    stop.set()
    fake = FakeIB()
    assert observer.run(arguments(root, "--once"), ib_factory=lambda: fake, stop_event=stop) == 2
    assert not fake.connect_calls


def test_sigterm_requests_clean_stop_and_restores_handlers(monkeypatch):
    handlers, restored = {}, []
    def register(sig, handler):
        if sig not in handlers:
            handlers[sig] = handler
            return "original"
        restored.append((sig, handler))
    monkeypatch.setattr(observer.signal, "signal", register)
    def fake_run(args, *, stop_event):
        handlers[observer.signal.SIGTERM](None, None)
        assert stop_event.is_set()
        return 0
    monkeypatch.setattr(observer, "run", fake_run)
    assert observer.main([]) == 0
    assert restored == [(observer.signal.SIGINT, "original"), (observer.signal.SIGTERM, "original")]


def test_compose_observer_runs_independently_alongside_protection():
    import yaml
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    service = compose["services"]["intraday-observer"]
    assert "profiles" not in service
    assert "profiles" not in compose["services"]["execution-agent"]
    assert service["command"] == ["python", "research_entrypoint.py", "intraday-observer"]
    assert "depends_on" not in service
    assert service["restart"] == "unless-stopped"
    assert not any(value.startswith("INTRADAY_CAPTURE_ENABLED=") for value in service["environment"])
    assert "trading-control:/app/control" not in service["volumes"]
