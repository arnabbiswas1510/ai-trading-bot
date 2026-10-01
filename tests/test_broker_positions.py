from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from broker_positions import (
    BrokerPositionError, confirmed_stock_positions, require_no_short_positions,
)
from tests.conftest import make_ib_mock, make_position, make_supabase_mock, make_trigger
from tests.test_buy_gates import _run_buys
import execution_agent as ea


ACCOUNT = "U12941651"


def position(quantity, account=ACCOUNT, symbol="SHIP"):
    return NS(account=account, position=quantity,
              contract=NS(symbol=symbol, secType="STK", conId=123), avgCost=18.49)


def test_completed_snapshot_retains_shorts_and_filters_other_accounts():
    ib = make_ib_mock()
    ib.reqPositions.side_effect = None
    ib.reqPositions.return_value = [position(-1004), position(500, "OTHER")]
    ib.portfolio.return_value = []
    assert [p.position for p in confirmed_stock_positions(ib, ACCOUNT)] == [-1004]
    assert ib.RequestTimeout == 0
    with pytest.raises(BrokerPositionError, match="SHIP -1004"):
        require_no_short_positions(ib, ACCOUNT)
    ib.placeOrder.assert_not_called()


@pytest.mark.parametrize("rows", [None, "unavailable", [position(float("nan"))],
                                  [position(100, account=None)],
                                  [NS(account=ACCOUNT, position=100, contract=None)]])
def test_invalid_snapshot_cannot_authorize_orders(rows):
    ib = make_ib_mock()
    ib.reqPositions.side_effect = None
    ib.reqPositions.return_value = rows
    with pytest.raises(BrokerPositionError):
        confirmed_stock_positions(ib, ACCOUNT)
    assert ib.RequestTimeout == 0


def test_snapshot_timeout_restores_existing_timeout():
    ib = make_ib_mock()
    ib.RequestTimeout = 25
    ib.reqPositions.side_effect = TimeoutError("not complete")
    with pytest.raises(BrokerPositionError, match="TimeoutError"):
        confirmed_stock_positions(ib, ACCOUNT)
    assert ib.RequestTimeout == 25


def test_disconnected_snapshot_is_not_empty_account():
    ib = make_ib_mock()
    ib.isConnected.return_value = False
    with pytest.raises(BrokerPositionError, match="disconnected"):
        confirmed_stock_positions(ib, ACCOUNT)
    ib.reqPositions.assert_not_called()


def test_disconnect_during_request_does_not_authorize_orders():
    ib = make_ib_mock()
    ib.isConnected.side_effect = [True, False]
    with pytest.raises(BrokerPositionError, match="while reading"):
        confirmed_stock_positions(ib, ACCOUNT)


@pytest.mark.parametrize("db_positions", [[], [make_position("SHIP", shares=1004)]])
def test_short_quarantines_reconciliation_even_with_empty_portfolio(db_positions):
    ib = make_ib_mock()
    ib.positions.return_value = [position(-1004)]
    client = make_supabase_mock(portfolio=db_positions)
    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier:
        ea.reconcile_with_ibkr(ib)
    assert "SHIP -1004" in notifier.notify_error.call_args.args[0]
    for table in ("portfolio_positions", "trade_history", "account_balances"):
        client.table(table).delete.assert_not_called()
        client.table(table).insert.assert_not_called()
        client.table(table).update.assert_not_called()
        client.table(table).upsert.assert_not_called()
    ib.placeOrder.assert_not_called()


def test_stale_positive_portfolio_cannot_hide_fresh_short():
    ib = make_ib_mock(["SHIP"])
    ib.positions.return_value = [position(-1004)]
    client = make_supabase_mock(portfolio=[make_position("SHIP", shares=1004)])
    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier:
        ea.reconcile_with_ibkr(ib)
    assert "SHIP -1004" in notifier.notify_error.call_args.args[0]
    client.table("trade_history").insert.assert_not_called()


def test_unknown_inventory_leaves_reconciliation_state_untouched():
    ib = make_ib_mock()
    ib.reqPositions.side_effect = TimeoutError()
    client = make_supabase_mock(portfolio=[make_position("SHIP", shares=1004)])
    with patch.object(ea, "supabase", client), patch.object(ea, "notifier") as notifier:
        ea.reconcile_with_ibkr(ib)
    assert "RECONCILIATION BLOCKED" in notifier.notify_error.call_args.args[0]
    client.table.assert_not_called()


@pytest.mark.parametrize("inventory", [[position(-1004)], None])
def test_new_buys_blocked_on_short_or_unknown_inventory(inventory):
    ib = make_ib_mock()
    ib.reqPositions.side_effect = None
    ib.reqPositions.return_value = inventory
    client = make_supabase_mock(daily_triggers=[make_trigger("AAPL")])
    _run_buys(ib, client)
    ib.placeOrder.assert_not_called()
    client.table("portfolio_positions").insert.assert_not_called()


def test_other_account_short_does_not_block_target_account():
    ib = make_ib_mock()
    ib.positions.return_value = [position(-1004, "OTHER")]
    assert require_no_short_positions(ib, ACCOUNT) == []


def test_short_arriving_after_preflight_blocks_buy_submission():
    ib = make_ib_mock()
    ib.reqPositions.side_effect = [[], [position(-1004)]]
    client = make_supabase_mock(daily_triggers=[make_trigger("AAPL")])
    _run_buys(ib, client)
    assert ib.reqPositions.call_count == 2
    ib.placeOrder.assert_not_called()


@pytest.mark.parametrize("snapshots", [[[-1004]], [[], [-1004]]])
def test_manual_buy_cannot_cover_unexpected_short(snapshots):
    import force_buy
    ib = make_ib_mock()
    ib.reqPositions.side_effect = [[position(q) for q in rows] for rows in snapshots]
    client = make_supabase_mock()
    with patch.object(force_buy, "get_ibkr_price", return_value=100.0):
        result = force_buy._place_buy(
            ib, client, make_trigger("AAPL"), 20_000, set(), ACCOUNT,
            ea.ZoneInfo("America/New_York"), interactive=False,
        )
    assert result is None
    assert ib.reqPositions.call_count == len(snapshots)
    ib.placeOrder.assert_not_called()
