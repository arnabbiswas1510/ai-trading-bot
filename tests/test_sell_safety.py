"""Offline broker simulations for duplicate-disposal and cancellation races."""
import datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest
from ib_insync import IB, MarketOrder, Order, OrderStatus, Stock, Trade

import execution_agent as ea
from broker_positions import (
    BrokerPositionError, cancel_confirmed_sells, confirmed_stock_positions,
    submit_sell_orders,
)
from tests.conftest import make_ib_mock, make_position, make_supabase_mock, make_ibkr_fill

ACCOUNT = "U12941651"


def position(qty, account=ACCOUNT):
    return NS(account=account, position=qty, avgCost=18.64,
              contract=NS(symbol="SHIP", secType="STK", conId=123))


def broker(qty=1004):
    ib = make_ib_mock()
    ib.positions.return_value = [position(qty)]
    def qualify(*contracts):
        for contract in contracts:
            contract.conId = 123
        return list(contracts)
    ib.qualifyContracts.side_effect = qualify
    return ib


def stock():
    return Stock("SHIP", "SMART", "USD", conId=123)


def sell_trade(status="Submitted", account=ACCOUNT, client_id=1):
    order = MarketOrder("SELL", 1004, account=account, clientId=client_id)
    return NS(contract=stock(), order=order,
              orderStatus=NS(status=status, filled=0, remaining=1004, avgFillPrice=0))


def market_sell(ib, qty=1004):
    return submit_sell_orders(ib, stock(),
                              [MarketOrder("SELL", qty, account=ACCOUNT)], ACCOUNT)


@pytest.mark.parametrize("qty", [0, -1004, 100, 1003.5])
def test_stale_full_size_sell_cannot_open_or_increase_short(qty):
    ib = broker(qty)
    with pytest.raises(BrokerPositionError):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


def test_latest_position_callback_wins_including_zero():
    ib = broker()
    ib.reqPositions.side_effect = None
    ib.reqPositions.return_value = [position(1004), position(0)]
    assert [p.position for p in confirmed_stock_positions(ib, ACCOUNT)] == [0]
    with pytest.raises(BrokerPositionError):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


def test_latest_position_callback_cannot_hide_short():
    ib = broker()
    ib.reqPositions.side_effect = None
    ib.reqPositions.return_value = [position(1004), position(-1004)]
    with pytest.raises(BrokerPositionError, match="Unexpected short"):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


def test_duplicate_symbol_with_different_contract_is_ambiguous():
    ib = broker()
    second = position(1004)
    second.contract.conId = 456
    ib.positions.return_value.append(second)
    with pytest.raises(BrokerPositionError, match="ambiguous"):
        market_sell(ib)


def test_position_timeout_blocks_sell_and_restores_timeout():
    ib = broker()
    ib.RequestTimeout = 25
    ib.reqPositions.side_effect = TimeoutError()
    with pytest.raises(BrokerPositionError, match="snapshot failed"):
        market_sell(ib)
    assert ib.RequestTimeout == 25
    ib.placeOrder.assert_not_called()


def test_order_snapshot_timeout_blocks_sell():
    ib = broker()
    ib.reqAllOpenOrders.side_effect = TimeoutError()
    with pytest.raises(BrokerPositionError, match="open-order snapshot failed"):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


def test_pending_cancellation_is_not_permission_to_sell():
    ib = broker()
    trade = sell_trade()
    ib.openTrades.return_value = [trade]
    with patch("broker_positions.time.monotonic", side_effect=[0, 11]):
        with pytest.raises(BrokerPositionError, match="not confirmed"):
            cancel_confirmed_sells(ib, ACCOUNT, "SHIP")
    ib.cancelOrder.assert_called_once_with(trade.order)
    ib.placeOrder.assert_not_called()


def test_real_ib_local_cancelled_status_cannot_override_broker_open_response():
    ib = IB()
    ib.client.clientId = 1
    ib.isConnected = lambda: True
    ib.client.cancelOrder = MagicMock()
    trade = Trade(stock(), MarketOrder(
        "SELL", 1004, account=ACCOUNT, clientId=1, orderId=7),
        OrderStatus(status="Inactive"))
    ib.wrapper.trades[ib.wrapper.orderKey(1, 7, 0)] = trade
    ib.reqAllOpenOrders = lambda: [trade]  # Broker still reports this order open.
    with pytest.raises(BrokerPositionError, match="live SELL orders remain"):
        cancel_confirmed_sells(ib, ACCOUNT, "SHIP")
    assert trade.orderStatus.status == "Cancelled"  # Synthesized by ib_insync, not IBKR.
    ib.client.cancelOrder.assert_called_once()


def test_stale_foreign_client_cache_does_not_override_empty_fresh_snapshot():
    ib = broker()
    ib.openTrades.return_value = [sell_trade(client_id=77)]
    ib.reqAllOpenOrders.side_effect = None
    ib.reqAllOpenOrders.return_value = []
    assert cancel_confirmed_sells(ib, ACCOUNT, "SHIP") == 0
    market_sell(ib)
    ib.placeOrder.assert_called_once()


def test_same_symbol_different_contract_does_not_authorize_a_sale():
    ib = broker()
    wrong_contract = stock()
    wrong_contract.conId = 456
    with pytest.raises(BrokerPositionError, match="in this contract"):
        submit_sell_orders(ib, wrong_contract,
                           [MarketOrder("SELL", 1004, account=ACCOUNT)], ACCOUNT)
    ib.placeOrder.assert_not_called()


def test_order_fills_during_cancel_then_replacement_is_blocked():
    ib = broker()
    trade = sell_trade()
    ib.openTrades.return_value = [trade]

    def filled_while_cancelling(order):
        trade.orderStatus.status = "Filled"
        ib.positions.return_value = [position(0)]

    ib.cancelOrder.side_effect = filled_while_cancelling
    assert cancel_confirmed_sells(ib, ACCOUNT, "SHIP") == 1
    with pytest.raises(BrokerPositionError, match="long position"):
        ea.place_protective_stops(ib, stock(),
                                  1004, 0.0119, 18.45, ACCOUNT)
    ib.placeOrder.assert_not_called()


def test_other_account_orders_are_not_cancelled():
    ib = broker()
    ib.openTrades.return_value = [sell_trade(account="OTHER")]
    assert cancel_confirmed_sells(ib, ACCOUNT, "SHIP") == 0
    ib.cancelOrder.assert_not_called()


def test_stock_cleanup_does_not_cancel_same_symbol_option():
    ib = broker()
    trade = sell_trade()
    trade.contract.secType = "OPT"
    ib.openTrades.return_value = [trade]
    assert cancel_confirmed_sells(ib, ACCOUNT, "SHIP") == 0
    ib.cancelOrder.assert_not_called()


def test_other_client_orders_block_instead_of_being_ignored():
    ib = broker()
    ib.reqAllOpenOrders.side_effect = None
    ib.reqAllOpenOrders.return_value = [sell_trade(client_id=77)]
    with pytest.raises(BrokerPositionError, match="another API client"):
        cancel_confirmed_sells(ib, ACCOUNT, "SHIP")
    ib.cancelOrder.assert_not_called()
    with pytest.raises(BrokerPositionError, match="existing SELL"):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


@pytest.mark.parametrize("status", ["Inactive", "PendingCancel", "PendingSubmit"])
def test_nonterminal_order_blocks_new_sell(status):
    ib = broker()
    ib.openTrades.return_value = [sell_trade(status)]
    with pytest.raises(BrokerPositionError, match="existing SELL"):
        market_sell(ib)
    ib.placeOrder.assert_not_called()


@pytest.mark.parametrize("oca", [False, True])
def test_both_oca_legs_exist_before_transmission(oca):
    ib = broker()
    contract = stock()
    submitted = []

    def place(contract, order):
        submitted.append(order)
        return NS(contract=contract, order=order,
                  orderStatus=NS(status="PendingSubmit"))

    ib.placeOrder.side_effect = place
    if oca:
        group, _ = ea.place_oca_exit(ib, contract, 1004, 18.40, 0.006, ACCOUNT)
    else:
        group, _ = ea.place_protective_stops(ib, contract, 1004, 0.0119, 18.45, ACCOUNT)
    assert [o.transmit for o in submitted] == [False, True]
    assert all(o.ocaGroup == group and o.ocaType == 1 for o in submitted)
    assert all(o.totalQuantity == 1004 for o in submitted)


def test_unrelated_sell_orders_cannot_be_submitted_as_a_group():
    ib = broker()
    with pytest.raises(BrokerPositionError, match="blocking OCA"):
        submit_sell_orders(ib, stock(),
                           [MarketOrder("SELL", 1004, account=ACCOUNT),
                            MarketOrder("SELL", 1004, account=ACCOUNT)], ACCOUNT)
    ib.placeOrder.assert_not_called()


def test_failed_second_leg_cancels_staged_first_leg():
    ib = broker()
    staged = []

    def place(contract, order):
        if staged:
            raise RuntimeError("second submission failed")
        trade = NS(contract=contract, order=order,
                   orderStatus=NS(status="PendingSubmit"))
        order.clientId = 1
        staged.append(trade)
        ib.openTrades.return_value = staged
        return trade

    def cancel(order):
        staged[0].orderStatus.status = "Cancelled"

    ib.placeOrder.side_effect = place
    ib.cancelOrder.side_effect = cancel
    with pytest.raises(RuntimeError, match="second submission"):
        ea.place_protective_stops(ib, stock(),
                                  1004, 0.0119, 18.45, ACCOUNT)
    assert staged[0].order.transmit is False
    ib.cancelOrder.assert_called_once_with(staged[0].order)


@pytest.mark.parametrize("after_qty,status", [(-1004, "Filled"), (0, "Cancelled")])
def test_flat_or_short_is_not_proof_our_sell_filled(after_qty, status):
    ib = broker()
    client = make_supabase_mock(portfolio=[make_position("SHIP", shares=1004)])

    def place(contract, order):
        ib.positions.return_value = [position(after_qty)]
        return NS(contract=contract, order=order, fills=[],
                  orderStatus=NS(status=status, filled=1004 if status == "Filled" else 0,
                                 remaining=0, avgFillPrice=18.42))

    ib.placeOrder.side_effect = place
    with patch.object(ea, "notifier"):
        ok = ea.execute_sell(ib, client, "SHIP", 1004, 18.64,
                             datetime.datetime(2026, 9, 18), "breakout", 18.42, "test")
    assert ok is False
    client.table("portfolio_positions").delete.assert_not_called()
    client.table("trade_history").insert.assert_not_called()


def test_real_filled_flat_exit_still_archives():
    ib = broker()
    client = make_supabase_mock(portfolio=[make_position("SHIP", shares=1004)])

    def place(contract, order):
        ib.positions.return_value = [position(0)]
        return NS(contract=contract, order=order, fills=[],
                  orderStatus=NS(status="Filled", filled=1004, remaining=0, avgFillPrice=18.42))

    ib.placeOrder.side_effect = place
    with patch.object(ea, "notifier"), patch.object(ea, "trade_commission", return_value=1), \
            patch.object(ea, "_write_breakout_learning_row"):
        ok = ea.execute_sell(ib, client, "SHIP", 1004, 18.64,
                             datetime.datetime(2026, 9, 18), "breakout", 18.42, "test")
    assert ok is True
    client.table("portfolio_positions").delete.assert_called_once()


def test_short_appearing_during_reconcile_pricing_blocks_balance_write():
    ib = broker()
    client = make_supabase_mock(portfolio=[make_position("SHIP", shares=1004)])

    def price(*args):
        ib.positions.return_value = [position(-1004)]
        return 18.49, "IBKR"

    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier, \
            patch.object(ea, "get_position_price", side_effect=price), \
            patch.object(ea, "build_ibkr_price_map", return_value={}):
        ea.reconcile_with_ibkr(ib)
    assert "RECONCILIATION BLOCKED" in notifier.notify_error.call_args.args[0]
    client.table("account_balances").upsert.assert_not_called()
    client.table("portfolio_positions").delete.assert_not_called()


def test_reconcile_refuses_two_full_sales_even_if_account_is_now_flat():
    ib = make_ib_mock(["SPY"])
    pos = make_position("SHIP", shares=1004, buy_date="2026-09-18T16:34:53+00:00")
    fills = [
        make_ibkr_fill("SHIP", 18.423984, 1004, exec_id="first-sale",
                       fill_time="2026-09-21T13:31:55+00:00"),
        make_ibkr_fill("SHIP", 18.490092, 1004, exec_id="second-sale",
                       fill_time="2026-09-21T13:44:31+00:00"),
    ]
    client = make_supabase_mock(portfolio=[pos], ibkr_fills=fills)
    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier, \
            patch.object(ea, "get_live_price", return_value=18.49), \
            patch.object(ea, "build_ibkr_price_map", return_value={}):
        ea.reconcile_with_ibkr(ib)
    assert "2008" in notifier.notify_error.call_args.args[0]
    client.table("portfolio_positions").delete.assert_not_called()
    client.table("trade_history").insert.assert_not_called()


def test_short_appearing_during_reconcile_cleanup_cannot_be_archived():
    ib = make_ib_mock(["SPY"])
    pos = make_position("SHIP", shares=1004, buy_date="2026-09-18T16:34:53+00:00")
    client = make_supabase_mock(
        portfolio=[pos], ibkr_fills=[make_ibkr_fill(
            "SHIP", 18.42, 1004, fill_time="2026-09-21T13:31:55+00:00")])

    def cancel(*args):
        ib.positions.return_value.append(position(-1004))

    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier, \
            patch.object(ea, "get_live_price", return_value=18.49), \
            patch.object(ea, "build_ibkr_price_map", return_value={}), \
            patch.object(ea, "cancel_ticker_sell_orders", side_effect=cancel):
        ea.reconcile_with_ibkr(ib)
    assert "Unexpected short" in notifier.notify_error.call_args.args[0]
    client.table("portfolio_positions").delete.assert_not_called()
    client.table("trade_history").insert.assert_not_called()


@pytest.mark.parametrize("total", [1004, 2008])
def test_unscoped_flex_aggregate_cannot_bypass_closing_evidence(total):
    ib = make_ib_mock(["SPY"])
    pos = make_position("SHIP", shares=1004, buy_date="2026-09-18T16:34:53+00:00")
    client = make_supabase_mock(portfolio=[pos])
    flex = dict(sell_price=18.457, sell_date="2026-09-21",
                total_shares=total, source="Flex unscoped aggregate")
    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier, \
            patch.object(ea, "get_live_price", return_value=18.49), \
            patch.object(ea, "build_ibkr_price_map", return_value={}), \
            patch.object(ea, "fetch_trade_confirms_for_ticker", return_value=flex):
        ea.reconcile_with_ibkr(ib)
    assert "Flex reports" in notifier.notify_error.call_args.args[0]
    client.table("portfolio_positions").delete.assert_not_called()
    client.table("trade_history").insert.assert_not_called()


@pytest.mark.parametrize("stop_remainder", [0, 16])
def test_replacement_stop_filling_during_scale_out_does_not_advance_cutoff(stop_remainder):
    ib = broker(30)
    pos = make_position("SHIP", shares=30)
    client = make_supabase_mock(portfolio=[pos])
    fill_time = datetime.datetime(2026, 9, 21, 14, 0, tzinfo=datetime.timezone.utc)
    fill = NS(execution=NS(shares=9, price=19.50, time=fill_time))
    trade = NS(orderStatus=NS(status="Filled", avgFillPrice=19.50),
               fills=[fill])

    def partial_fill(contract, order):
        ib.positions.return_value = [position(21)]
        return trade

    def stop_fills(*args):
        ib.positions.return_value = [position(stop_remainder)]
        return "PROT_TEST", 0.01

    ib.placeOrder.side_effect = partial_fill
    with patch.object(ea, "notifier"), \
            patch.object(ea, "place_protective_stops", side_effect=stop_fills):
        ok = ea.execute_scale_out(
            ib, client, pos, "SHIP", 30, 9, 18.64,
            datetime.date(2026, 9, 18), "breakout", 19.50, 5, 0.01, 18.40)
    assert ok is False
    assert not pos.get("scaled_out")
    client.table("portfolio_positions").update.assert_not_called()
    client.table("trade_history").insert.assert_not_called()
    if stop_remainder:
        # A later reconcile must not erase the mismatch and enable another trim.
        reconcile_ib = make_ib_mock(["SHIP"])
        reconcile_ib.positions.return_value = [position(stop_remainder)]
        with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier, \
                patch.object(ea, "get_live_price", return_value=19.50), \
                patch.object(ea, "build_ibkr_price_map", return_value={}):
            ea.reconcile_with_ibkr(reconcile_ib)
        assert "ledger records 30" in notifier.notify_error.call_args.args[0]
        client.table("portfolio_positions").update.assert_not_called()
        with patch.object(ea, "notifier"), patch.object(ea, "cancel_ticker_sell_orders") as cancel:
            retry = ea.execute_scale_out(
                ib, client, pos, "SHIP", 30, 9, 18.64,
                datetime.date(2026, 9, 18), "breakout", 19.50, 5, 0.01, 18.40)
        assert retry is False
        cancel.assert_not_called()
        assert ib.placeOrder.call_count == 1
