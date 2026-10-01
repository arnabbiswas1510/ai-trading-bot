"""
tests/test_scale_out.py

Tests for the Partial Scale-Out rule — the winner->loser give-back reducer.

When an open position's PEAK gain first reaches SCALE_OUT_TRIGGER_PCT, the
monitor loop sells SCALE_OUT_FRACTION of the shares at market (booking a realised
profit a later fade cannot erase) and lets the remainder ride the UNCHANGED
Prove-It stop. It must fire exactly once, and must NOT fire below the trigger,
after it has already scaled, or on a power-held leader.

See decisions/2026-09-08_partial-scale-out.md and the register entry in
decisions/provisional_decisions.json.

buy_date mapping for mock now=2026-06-17 (see test_prove_it_stop.py).
"""

import datetime
import pytest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import execution_agent

BD_DAY4 = "2026-06-11T12:00:00+00:00"


def _make_pos(ticker="AAPL", buy_price=100.0, buy_date=BD_DAY4, shares=30,
              highest_unrealized_pct=0.0, scaled_out=False):
    return {
        "ticker": ticker, "buy_price": buy_price, "buy_date": buy_date,
        "buy_reason": "CANSLIM breakout", "shares": shares,
        "stop_loss_pct": 0.07,
        "highest_unrealized_pct": highest_unrealized_pct,
        "hwm_price": buy_price, "hwm_date": None,
        "entry_rs_score": 90, "live_rs_score": 90,
        "closed_above_entry": True, "entry_atr_pct": 3.0,
        "exit_armed": False, "exit_armed_at": None, "exit_armed_reason": None,
        "scaled_out": scaled_out, "scaled_out_at": None,
    }


def _make_ib(positions):
    ib = MagicMock()
    account = "DU1234567"
    ib.isConnected.return_value = True
    ib.RequestTimeout = 0
    ib.client.clientId = 1
    items = []
    for pos in positions:
        item = MagicMock()
        item.contract.symbol = pos["ticker"]
        item.contract.secType = "STK"
        item.contract.conId = int.from_bytes(pos["ticker"].encode(), "big")
        item.account = account
        item.position = pos["shares"]
        item.averageCost = pos["buy_price"]
        item.marketPrice = 0.0   # no live IBKR mark -> FMP fallback (get_live_price)
        items.append(item)
    ib.portfolio.return_value = items
    ib.positions.return_value = items
    ib.reqPositions.side_effect = lambda: ib.positions()
    _nan_pnl = MagicMock()
    _nan_pnl.value = float("nan")
    _nan_pnl.unrealizedPnL = float("nan")
    ib.reqPnLSingle.return_value = _nan_pnl
    ib.cancelPnLSingle.return_value = None
    ib.openOrders.return_value = []
    ib.openTrades.return_value = []
    ib.reqAllOpenOrders.side_effect = lambda: [
        trade for trade in ib.openTrades()
        if trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled")
    ]
    def qualify(*contracts):
        for contract in contracts:
            contract.conId = int.from_bytes(contract.symbol.encode(), "big")
        return list(contracts)
    ib.qualifyContracts.side_effect = qualify
    ib.managedAccounts.return_value = [account]
    av = MagicMock()
    av.tag = "NetLiquidation"; av.currency = "USD"; av.value = "100000"; av.account = account
    ib.accountValues.return_value = [av]
    return ib


def _make_sb(positions):
    sb = MagicMock()
    pos_res = MagicMock(); pos_res.data = positions
    sb.table.return_value.select.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return sb


def _run(ib, sb, live_price, hour=11, minute=30):
    tz = ZoneInfo("America/New_York")
    now_mock = datetime.datetime(2026, 6, 17, hour, minute, tzinfo=tz)
    with patch("execution_agent.supabase", sb), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent._fetch_ohlcv", return_value=[]), \
         patch("execution_agent._fetch_current_rs", return_value=90), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_protective_stops", return_value=("PROT_MOCK", 0.07)), \
         patch("execution_agent.place_trailing_stop", return_value=("TS_MOCK", 0.07)), \
         patch("execution_agent.execute_scale_out", return_value=True) as mock_scale, \
         patch("execution_agent.execute_sell") as mock_sell, \
         patch("execution_agent.arm_exit") as mock_arm, \
         patch("execution_agent.datetime") as mock_dt:
        mock_dt.datetime.now.side_effect = lambda *a, **kw: now_mock
        mock_dt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_dt.date.fromisoformat.side_effect = datetime.date.fromisoformat
        mock_dt.date.today.return_value = now_mock.date()
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta
        execution_agent.monitor_portfolio_intraday(ib)
    return mock_scale, mock_sell, mock_arm


class TestScaleOutTrigger:
    def test_fires_once_at_trigger(self):
        """A position whose peak reaches +4% is scaled out 33%."""
        pos = _make_pos(shares=30)
        price = 100.0 * (1 + execution_agent.SCALE_OUT_TRIGGER_PCT) + 0.50  # ~+4.5%
        mock_scale, mock_sell, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert mock_scale.called, "scale-out should fire at/above the trigger"
        args = mock_scale.call_args.args
        # signature: (ib, client, pos, ticker, total_shares, scale_shares, ...)
        assert args[3] == "AAPL"
        assert args[4] == 30                       # total shares
        assert args[5] == int(30 * execution_agent.SCALE_OUT_FRACTION)  # 33% -> 9
        # scaling replaces the normal exit path this cycle
        mock_sell.assert_not_called()

    def test_does_not_fire_below_trigger(self):
        pos = _make_pos(shares=30)
        price = 100.0 * (1 + execution_agent.SCALE_OUT_TRIGGER_PCT) - 1.00  # ~+3%
        mock_scale, _, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert not mock_scale.called

    def test_does_not_refire_when_already_scaled(self):
        pos = _make_pos(shares=20, scaled_out=True)
        price = 100.0 * (1 + execution_agent.SCALE_OUT_TRIGGER_PCT) + 2.00  # ~+6%
        mock_scale, _, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert not mock_scale.called

    def test_uses_stored_peak_even_if_price_pulled_back(self):
        """Peak, not current price, arms the rule — a prior +4% still scales."""
        pos = _make_pos(shares=30, highest_unrealized_pct=4.5)
        price = 100.0 * 1.01   # currently only +1%, but peak was +4.5%
        mock_scale, _, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert mock_scale.called


class TestScaleOutGuards:
    def test_tiny_position_not_scaled(self):
        """A 1-share position cannot be split, so the rule is skipped."""
        pos = _make_pos(shares=1)
        price = 100.0 * 1.06
        mock_scale, _, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert not mock_scale.called

    def test_disabled_flag_suppresses(self):
        pos = _make_pos(shares=30)
        price = 100.0 * 1.05
        with patch("execution_agent.SCALE_OUT_ENABLED", False):
            mock_scale, _, _ = _run(_make_ib([pos]), _make_sb([pos]), price)
        assert not mock_scale.called


def _make_fill(shares, price):
    f = MagicMock()
    f.execution.shares = shares
    f.execution.price = price
    f.execution.time = datetime.datetime(2026, 6, 17, 15, 30, tzinfo=datetime.timezone.utc)
    return f


def _make_scale_ib(pre_qty, ticker="AAPL"):
    return _make_ib([_make_pos(ticker=ticker, shares=pre_qty)])


def _set_scale_trade(ib, trade, order_of_calls=None):
    """Apply this order's actual fills to broker inventory when submitted."""
    def place(contract, order):
        if order_of_calls is not None:
            order_of_calls.append("place")
        trade.contract = contract
        trade.order = order
        order.clientId = ib.client.clientId
        filled = sum(fill.execution.shares for fill in trade.fills)
        trade.orderStatus.filled = filled
        trade.orderStatus.remaining = order.totalQuantity - filled
        for item in ib.positions():
            if item.account == order.account and item.contract.conId == contract.conId:
                item.position -= filled
        ib.openTrades.return_value = [trade]
        return trade
    ib.placeOrder.side_effect = place


def _scale_client():
    client = MagicMock()
    ins = MagicMock(); ins.data = [{"id": 42}]
    client.table.return_value.insert.return_value.execute.return_value = ins
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return client


class TestExecuteScaleOutInternals:
    """Direct tests of execute_scale_out — fills-based accounting, cancel-first."""

    @pytest.mark.parametrize("broker_qty", [21, 31])
    def test_quantity_mismatch_blocks_before_cancelling_or_selling(self, broker_qty):
        ib = _make_scale_ib(pre_qty=broker_qty)
        client = _scale_client()
        pos = _make_pos(shares=30)
        original = dict(pos)

        with patch("execution_agent.notifier") as notifier:
            ok = execution_agent.execute_scale_out(
                ib, client, pos, "AAPL", 30, 9, 100.0,
                datetime.date(2026, 6, 11), "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert ok is False
        ib.cancelOrder.assert_not_called()
        ib.placeOrder.assert_not_called()
        client.table.assert_not_called()
        assert pos == original
        assert ib.positions()[0].position == broker_qty
        notifier.notify_error.assert_called_once()
        assert "quantity mismatch" in notifier.notify_error.call_args.args[0]

    def test_partial_stop_fill_during_cancellation_blocks_scale_out(self):
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        pos = _make_pos(shares=30)
        original = dict(pos)
        holding = ib.positions()[0]
        stop = MagicMock()
        stop.contract = holding.contract
        stop.order.action = "SELL"
        stop.order.orderType = "STP"
        stop.order.totalQuantity = 30
        stop.order.account = holding.account
        stop.order.clientId = ib.client.clientId
        stop.orderStatus.status = "Submitted"
        stop.orderStatus.filled = 0
        stop.orderStatus.remaining = 30
        stop.orderStatus.avgFillPrice = 0.0
        stop.fills = []
        ib.openTrades.return_value = [stop]

        def fill_then_acknowledge_cancel(order):
            assert order is stop.order
            stop.fills = [_make_fill(9, 104.0)]
            stop.orderStatus.filled = 9
            stop.orderStatus.remaining = 21
            stop.orderStatus.avgFillPrice = 104.0
            holding.position -= 9
            stop.orderStatus.status = "Cancelled"
        ib.cancelOrder.side_effect = fill_then_acknowledge_cancel

        with patch("execution_agent.notifier") as notifier:
            ok = execution_agent.execute_scale_out(
                ib, client, pos, "AAPL", 30, 9, 100.0,
                datetime.date(2026, 6, 11), "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert ok is False
        ib.cancelOrder.assert_called_once_with(stop.order)
        ib.placeOrder.assert_not_called()
        client.table.assert_not_called()
        assert pos == original
        assert holding.position == 21
        assert stop.orderStatus.status == "Cancelled"
        notifier.notify_error.assert_called_once()
        assert "quantity changed during cancellation" in notifier.notify_error.call_args.args[0]

    def _patches(self):
        return [
            patch("execution_agent.place_protective_stops"),
            patch("execution_agent.trade_commission", return_value=1.0),
            patch("execution_agent.record_trade_commissions"),
            patch("execution_agent.get_ibkr_account", return_value="DU1234567"),
            patch("execution_agent.notifier"),
        ]

    def test_books_from_fills_not_portfolio_delta(self):
        """Fill-weighted accounting must agree with the independently read holding."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.orderStatus.avgFillPrice = 0.0            # force use of fills path
        trade.fills = [_make_fill(6, 104.0), _make_fill(3, 106.0)]  # 9 sh, wavg 104.67
        _set_scale_trade(ib, trade)

        import contextlib
        with contextlib.ExitStack() as es:
            for p in self._patches():
                es.enter_context(p)
            buy_date = datetime.date(2026, 6, 11)
            ok = execution_agent.execute_scale_out(
                ib, client, _make_pos(shares=30), "AAPL", 30, 9,
                100.0, buy_date, "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert ok is True
        # trade_history insert used the fills-derived qty (9) and wavg price.
        insert_call = client.table.return_value.insert.call_args.args[0]
        assert insert_call["shares"] == 9
        assert abs(insert_call["sell_price"] - (6*104.0 + 3*106.0)/9) < 1e-6
        assert ib.positions()[0].position == 21
        update = client.table.return_value.update.call_args.args[0]
        assert update["shares"] == 21
        assert update["scaled_out"] is True

    def test_cancels_bracket_before_selling(self):
        """cancel_ticker_sell_orders must run before the market order is placed."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.orderStatus.avgFillPrice = 104.0
        trade.fills = [_make_fill(9, 104.0)]

        order_of_calls = []
        _set_scale_trade(ib, trade, order_of_calls)
        bracket = MagicMock()
        bracket.contract.symbol = "AAPL"
        bracket.contract.secType = "STK"
        bracket.contract.conId = ib.positions()[0].contract.conId
        bracket.order.action = "SELL"
        bracket.order.account = "DU1234567"
        bracket.order.clientId = 1
        bracket.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [bracket]

        def acknowledge_cancel(order):
            assert order is bracket.order
            order_of_calls.append("cancel")
            bracket.orderStatus.status = "Cancelled"
        ib.cancelOrder.side_effect = acknowledge_cancel

        import contextlib
        with contextlib.ExitStack() as es:
            es.enter_context(patch("execution_agent.place_protective_stops"))
            es.enter_context(patch("execution_agent.trade_commission", return_value=1.0))
            es.enter_context(patch("execution_agent.record_trade_commissions"))
            es.enter_context(patch("execution_agent.get_ibkr_account", return_value="DU1234567"))
            es.enter_context(patch("execution_agent.notifier"))
            buy_date = datetime.date(2026, 6, 11)
            ok = execution_agent.execute_scale_out(
                ib, client, _make_pos(shares=30), "AAPL", 30, 9,
                100.0, buy_date, "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert order_of_calls[0] == "cancel"
        assert "place" in order_of_calls
        assert order_of_calls.index("cancel") < order_of_calls.index("place")
        assert ok is True
        assert bracket.orderStatus.status == "Cancelled"

    def test_no_fill_restores_full_bracket_and_aborts(self):
        """If nothing fills, the position must not be booked and gets its bracket back."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Cancelled"
        trade.orderStatus.avgFillPrice = 0.0
        trade.fills = []                      # nothing filled
        _set_scale_trade(ib, trade)

        import contextlib
        with contextlib.ExitStack() as es:
            prot = es.enter_context(patch("execution_agent.place_protective_stops"))
            es.enter_context(patch("execution_agent.trade_commission", return_value=1.0))
            es.enter_context(patch("execution_agent.record_trade_commissions"))
            es.enter_context(patch("execution_agent.get_ibkr_account", return_value="DU1234567"))
            es.enter_context(patch("execution_agent.notifier"))
            buy_date = datetime.date(2026, 6, 11)
            ok = execution_agent.execute_scale_out(
                ib, client, _make_pos(shares=30), "AAPL", 30, 9,
                100.0, buy_date, "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert ok is False
        client.table.return_value.insert.assert_not_called()   # nothing booked
        # bracket restored on the FULL pre-sale quantity
        assert prot.called
        assert prot.call_args.args[2] == 30
