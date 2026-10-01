"""Offline manual-tool regressions using broker-shaped orders and executions."""
import datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from ib_insync import Stock, Order, OrderStatus, Trade, Execution, Fill, Position

import broker_positions
import execution_agent
import force_sell
import managed_exit


ACCOUNT = "U12941651"


class Broker:
    def __init__(self, quantity=1004):
        self.contract = Stock("SHIP", "SMART", "USD", conId=123)
        self.quantity = quantity
        self.other_positions = []
        self.trades = []
        self.submissions = []
        self.cancellations = []
        self.client = NS(clientId=1)
        self.RequestTimeout = 0
        self.cancel_ack = True
        self.fill_quantity = None
        self.on_cancel = None
        self.on_sleep = None
        self.on_qualify = None
        self.position_error = None
        self.disconnected = False

    def managedAccounts(self):
        return [ACCOUNT, "OTHER"]

    def isConnected(self):
        return not self.disconnected

    def connect(self, *args, **kwargs):
        return self

    def disconnect(self):
        self.disconnected = True

    def reqPositions(self):
        if self.position_error:
            raise self.position_error
        return ([Position(ACCOUNT, self.contract, self.quantity, 18.49)]
                + self.other_positions)

    def reqAllOpenOrders(self):
        return self.openTrades()

    def openTrades(self):
        return [t for t in self.trades if t.orderStatus.status not in
                ("Filled", "Cancelled", "ApiCancelled")]

    def qualifyContracts(self, contract):
        contract.conId = self.contract.conId
        if self.on_qualify:
            self.on_qualify()
        return [contract]

    def pending(self, quantity=1004, account=ACCOUNT, client_id=1):
        order = Order(action="SELL", orderType="TRAIL", totalQuantity=quantity,
                      account=account, clientId=client_id, orderId=len(self.trades) + 1)
        trade = Trade(self.contract, order,
                      OrderStatus(status="Submitted", remaining=quantity))
        self.trades.append(trade)
        return trade

    def fill(self, trade, quantity, price=20.0):
        execution = Execution(
            execId=f"{trade.order.orderId}-{len(trade.fills)}",
            acctNumber=trade.order.account, side="SLD", shares=quantity,
            price=price, orderId=trade.order.orderId, clientId=trade.order.clientId,
        )
        trade.fills.append(Fill(trade.contract, execution, None, datetime.datetime.now()))
        trade.orderStatus.filled += quantity
        trade.orderStatus.remaining -= quantity
        trade.orderStatus.status = (
            "Filled" if trade.orderStatus.remaining == 0 else "Submitted")
        trade.orderStatus.avgFillPrice = price
        self.quantity -= quantity

    def placeOrder(self, contract, order):
        order.orderId = len(self.trades) + 1
        order.clientId = self.client.clientId
        trade = Trade(contract, order,
                      OrderStatus(status="Submitted", remaining=order.totalQuantity))
        self.trades.append(trade)
        self.submissions.append(trade)
        if order.orderType in ("LMT", "MKT"):
            quantity = order.totalQuantity if self.fill_quantity is None else self.fill_quantity
            if quantity:
                self.fill(trade, quantity)
        return trade

    def cancelOrder(self, order):
        trade = next(t for t in self.trades if t.order is order)
        self.cancellations.append(trade)
        if self.on_cancel:
            self.on_cancel(trade)
        if self.cancel_ack and trade.orderStatus.status != "Filled":
            trade.orderStatus.status = "Cancelled"
        elif trade.orderStatus.status != "Filled":
            trade.orderStatus.status = "PendingCancel"

    def sleep(self, seconds):
        if self.on_sleep:
            callback, self.on_sleep = self.on_sleep, None
            callback()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in manual safety tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(execution_agent, "fetch_ibkr_delayed_price",
                        lambda *args: (20.0, "offline quote"))
    monkeypatch.setattr(force_sell, "_notify", lambda *args: None)
    monkeypatch.setattr(managed_exit, "_notify", lambda *args: None)
    monkeypatch.setattr(managed_exit, "current_price", lambda *args: (20.0, "offline"))
    monkeypatch.setenv("IBKR_ACCOUNT", ACCOUNT)


@pytest.fixture
def position():
    return dict(ticker="SHIP", shares=1004, buy_price=18.49,
                buy_date="2026-09-18T13:31:00+00:00")


@pytest.mark.parametrize("quantity", [0, -1004])
def test_force_sell_never_sells_flat_or_short(quantity, position):
    ib, db = Broker(quantity), MagicMock()
    with pytest.raises(broker_positions.BrokerPositionError):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    assert not ib.submissions
    db.table.assert_not_called()


def test_force_sell_rechecks_after_qualification(position):
    ib, db = Broker(), MagicMock()
    ib.on_qualify = lambda: setattr(ib, "quantity", 0)
    with pytest.raises(broker_positions.BrokerPositionError):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    assert not ib.submissions
    db.table.assert_not_called()


def test_force_sell_stop_filling_during_cancel_does_not_sell_again(position):
    ib, db = Broker(), MagicMock()
    ib.pending()
    ib.on_cancel = lambda trade: ib.fill(trade, 1004)
    with pytest.raises(broker_positions.BrokerPositionError):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    assert not ib.submissions
    db.table.assert_not_called()


@pytest.mark.parametrize("tool", ["force", "managed"])
def test_unacknowledged_cancellation_blocks_manual_replacement(tool, position, monkeypatch):
    ib, db = Broker(), MagicMock()
    ib.pending()
    ib.cancel_ack = False
    ticks = iter([0, 11])
    monkeypatch.setattr(broker_positions.time, "monotonic", lambda: next(ticks))
    with pytest.raises(broker_positions.BrokerPositionError, match="cancellation"):
        if tool == "force":
            force_sell._place_sell(ib, db, position, ACCOUNT)
        else:
            managed_exit.market_exit(ib, ib.contract, 1004, ACCOUNT)
    assert not ib.submissions
    db.table.assert_not_called()


def test_foreign_client_sell_blocks_force_sell(position):
    ib, db = Broker(), MagicMock()
    ib.pending(client_id=2)
    with pytest.raises(broker_positions.BrokerPositionError, match="client"):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    assert not ib.submissions
    assert not ib.cancellations
    db.table.assert_not_called()


def test_force_sell_ignores_other_account_inventory_and_orders(position):
    ib, db = Broker(), MagicMock()
    ib.other_positions = [Position("OTHER", ib.contract, -5000, 18.49)]
    foreign = ib.pending(account="OTHER")
    result = force_sell._place_sell(ib, db, position, ACCOUNT)
    assert result["shares"] == 1004
    assert ib.submissions[0].order.lmtPrice == 19.90
    assert foreign not in ib.cancellations
    db.table("portfolio_positions").delete.assert_called_once()


@pytest.mark.parametrize("filled", [0, 400])
def test_force_sell_cancels_pending_remainder_without_closing_ledger(filled, position):
    ib, db = Broker(), MagicMock()
    ib.fill_quantity = filled
    assert force_sell._place_sell(ib, db, position, ACCOUNT) is None
    assert ib.submissions[0].orderStatus.status == "Cancelled"
    assert ib.quantity == 1004 - filled
    db.table.assert_not_called()


def test_filled_status_alone_cannot_archive(position):
    ib, db = Broker(), MagicMock()
    ib.fill_quantity = 0
    def status_without_fill():
        ib.quantity = 0
        ib.submissions[0].orderStatus.status = "Filled"
    ib.on_sleep = status_without_fill
    with pytest.raises(broker_positions.BrokerPositionError, match="own fills"):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    db.table.assert_not_called()


def test_short_after_own_fill_does_not_archive(position):
    ib, db = Broker(), MagicMock()
    ib.on_sleep = lambda: setattr(ib, "quantity", -1004)
    with pytest.raises(broker_positions.BrokerPositionError, match="short"):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    db.table.assert_not_called()


def test_position_request_failure_cannot_close_ledger(position):
    ib, db = Broker(), MagicMock()
    ib.on_sleep = lambda: setattr(ib, "position_error", TimeoutError())
    with pytest.raises(broker_positions.BrokerPositionError):
        force_sell._place_sell(ib, db, position, ACCOUNT)
    db.table.assert_not_called()


def test_managed_market_exit_uses_residual_after_partial_trail_fill():
    ib = Broker()
    trail = ib.pending()
    ib.on_cancel = lambda trade: ib.fill(trade, 400) if trade is trail else None
    trade = managed_exit.market_exit(ib, ib.contract, 1004, ACCOUNT, trades=[trail])
    assert trade.order.orderType == "MKT"
    assert trade.order.totalQuantity == 604
    assert ib.quantity == 0
    assert force_sell._confirmed_exit_fill(
        ib, ib.contract, [trail, trade], ACCOUNT, 1004) == (1004, 20.0)


def test_managed_market_exit_does_not_duplicate_cancel_race_fill():
    ib = Broker()
    trail = ib.pending()
    ib.on_cancel = lambda trade: ib.fill(trade, 1004)
    assert managed_exit.market_exit(
        ib, ib.contract, 1004, ACCOUNT, trades=[trail]) is None
    assert not ib.submissions
    assert force_sell._confirmed_exit_fill(
        ib, ib.contract, [trail], ACCOUNT, 1004) == (1004, 20.0)


@pytest.mark.parametrize("field,value", [
    ("acctNumber", "OTHER"), ("side", "BOT"), ("orderId", 999), ("clientId", 2),
])
def test_managed_archive_rejects_unrelated_executions(field, value, position):
    ib, db = Broker(), MagicMock()
    trade = ib.pending()
    ib.fill(trade, 1004)
    setattr(trade.fills[0].execution, field, value)
    with pytest.raises(broker_positions.BrokerPositionError, match="unrelated"):
        managed_exit.archive(db, position, 1004, 20, "manual",
                             ib=ib, contract=ib.contract, trades=[trade], account=ACCOUNT)
    db.table.assert_not_called()


@pytest.mark.parametrize("quantity", [500, -1004])
def test_managed_archive_refuses_residual_or_short(quantity, position):
    ib, db = Broker(), MagicMock()
    trade = ib.pending()
    ib.fill(trade, 1004)
    ib.quantity = quantity
    with pytest.raises(broker_positions.BrokerPositionError):
        managed_exit.archive(db, position, 1004, 20, "manual",
                             ib=ib, contract=ib.contract, trades=[trade], account=ACCOUNT)
    db.table.assert_not_called()


def run_managed(monkeypatch, ib, db, position):
    db.table.return_value.select.return_value.execute.return_value.data = [position]
    monkeypatch.setattr(managed_exit, "IB", lambda: ib)
    monkeypatch.setattr(managed_exit, "create_client", lambda *args: db)
    monkeypatch.setattr(managed_exit, "SUPABASE_URL", "offline")
    monkeypatch.setattr(managed_exit, "SUPABASE_KEY", "offline")
    monkeypatch.setattr(managed_exit.sys, "argv", ["managed_exit.py", "SHIP", "--yes"])
    return managed_exit.main()


def test_managed_main_archives_own_trail_fill(monkeypatch, position):
    ib, db = Broker(), MagicMock()
    ib.on_sleep = lambda: ib.fill(ib.submissions[0], 1004)
    run_managed(monkeypatch, ib, db, position)
    assert len(ib.submissions) == 1
    assert ib.submissions[0].order.orderType == "TRAIL"
    db.table("trade_history").insert.assert_called_once()
    db.table("portfolio_positions").delete.assert_called_once()
    assert ib.disconnected


def test_managed_main_never_reports_short_as_success(monkeypatch, position, capsys):
    ib, db = Broker(), MagicMock()
    ib.on_sleep = lambda: setattr(ib, "quantity", -1004)
    with pytest.raises(broker_positions.BrokerPositionError, match="short"):
        run_managed(monkeypatch, ib, db, position)
    db.table("trade_history").insert.assert_not_called()
    db.table("portfolio_positions").delete.assert_not_called()
    assert "All requested positions exited" not in capsys.readouterr().out
    assert ib.disconnected


def test_managed_main_restores_only_residual_after_confirmed_market_cancel(monkeypatch, position):
    ib, db = Broker(), MagicMock()
    ib.fill_quantity = 400
    monkeypatch.setattr(
        managed_exit, "parse_deadline",
        lambda value: datetime.datetime.now(managed_exit.NY) - datetime.timedelta(minutes=1),
    )
    place = ib.placeOrder
    def place_and_finish_restored_trail(contract, order):
        if order.orderType == "TRAIL" and ib.submissions:
            assert ib.submissions[-1].orderStatus.status == "Cancelled"
            assert order.totalQuantity == 604
        trade = place(contract, order)
        if order.orderType == "TRAIL" and len(ib.submissions) > 1:
            ib.on_sleep = lambda: ib.fill(trade, 604)
        return trade
    monkeypatch.setattr(ib, "placeOrder", place_and_finish_restored_trail)
    run_managed(monkeypatch, ib, db, position)
    assert [t.order.orderType for t in ib.submissions] == ["TRAIL", "MKT", "TRAIL"]
    logged = db.table("trade_history").insert.call_args.args[0]
    assert logged["shares"] == 1004
    assert logged["sell_price"] == 20.0
    assert ib.quantity == 0


def test_managed_main_does_not_restore_after_unconfirmed_market_cancel(monkeypatch, position):
    ib, db = Broker(), MagicMock()
    ib.fill_quantity = 0
    monkeypatch.setattr(
        managed_exit, "parse_deadline",
        lambda value: datetime.datetime.now(managed_exit.NY) - datetime.timedelta(minutes=1),
    )
    place = ib.placeOrder
    def place_with_pending_cancel(contract, order):
        trade = place(contract, order)
        if order.orderType == "MKT":
            ib.cancel_ack = False
        return trade
    monkeypatch.setattr(ib, "placeOrder", place_with_pending_cancel)
    ticks = iter(range(0, 1000, 11))
    monkeypatch.setattr(broker_positions.time, "monotonic", lambda: next(ticks))
    with pytest.raises(broker_positions.BrokerPositionError, match="cancellation"):
        run_managed(monkeypatch, ib, db, position)
    assert [t.order.orderType for t in ib.submissions] == ["TRAIL", "MKT"]
    db.table("trade_history").insert.assert_not_called()
    db.table("portfolio_positions").delete.assert_not_called()


@pytest.mark.parametrize("tool", ["force", "managed"])
def test_initial_missing_400_blocks_before_cancelling_protection(tool, monkeypatch, position):
    ib, db = Broker(604), MagicMock()
    protection = ib.pending(quantity=604)
    with pytest.raises(broker_positions.BrokerPositionError, match="agreed ledger"):
        if tool == "force":
            force_sell._place_sell(ib, db, position, ACCOUNT)
        else:
            run_managed(monkeypatch, ib, db, position)
    assert not ib.submissions
    assert not ib.cancellations
    assert protection.orderStatus.status == "Submitted"
    db.table("trade_history").insert.assert_not_called()
    db.table("portfolio_positions").delete.assert_not_called()
    db.table("portfolio_positions").update.assert_not_called()


@pytest.mark.parametrize("tool", ["force", "managed"])
def test_old_stop_filling_400_during_cancel_blocks_replacement(tool, monkeypatch, position):
    ib, db = Broker(), MagicMock()
    protection = ib.pending()
    ib.on_cancel = lambda trade: ib.fill(trade, 400)
    with pytest.raises(broker_positions.BrokerPositionError, match="agreed ledger"):
        if tool == "force":
            force_sell._place_sell(ib, db, position, ACCOUNT)
        else:
            run_managed(monkeypatch, ib, db, position)
    assert not ib.submissions
    assert protection in ib.cancellations
    assert ib.quantity == 604
    db.table("trade_history").insert.assert_not_called()
    db.table("portfolio_positions").delete.assert_not_called()
    db.table("portfolio_positions").update.assert_not_called()


def test_managed_ongoing_unexplained_reduction_blocks_before_cancel():
    ib = Broker(604)
    trail = ib.pending()
    with pytest.raises(broker_positions.BrokerPositionError, match="do not explain"):
        managed_exit.market_exit(ib, ib.contract, 1004, ACCOUNT, trades=[trail])
    assert not ib.submissions
    assert not ib.cancellations


def test_managed_ongoing_external_fill_during_cancel_blocks_replacement():
    ib = Broker()
    own_trail = ib.pending()
    external = ib.pending()
    ib.on_cancel = lambda trade: ib.fill(trade, 400) if trade is external else None
    with pytest.raises(broker_positions.BrokerPositionError, match="do not explain"):
        managed_exit.market_exit(ib, ib.contract, 1004, ACCOUNT, trades=[own_trail])
    assert not ib.submissions
    assert ib.quantity == 604
