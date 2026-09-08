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
    items = []
    for pos in positions:
        item = MagicMock()
        item.contract.symbol = pos["ticker"]
        item.contract.secType = "STK"
        item.position = pos["shares"]
        item.averageCost = pos["buy_price"]
        item.marketPrice = 0.0   # no live IBKR mark -> FMP fallback (get_live_price)
        items.append(item)
    ib.portfolio.return_value = items
    ib.reqPositions.return_value = None
    ib.openOrders.return_value = []
    ib.openTrades.return_value = []
    account = "DU1234567"
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
    return f


def _make_scale_ib(pre_qty, ticker="AAPL"):
    ib = MagicMock()
    item = MagicMock()
    item.contract.symbol = ticker
    item.contract.secType = "STK"
    item.position = pre_qty
    ib.portfolio.return_value = [item]
    ib.managedAccounts.return_value = ["DU1234567"]
    return ib


def _scale_client():
    client = MagicMock()
    ins = MagicMock(); ins.data = [{"id": 42}]
    client.table.return_value.insert.return_value.execute.return_value = ins
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return client


class TestExecuteScaleOutInternals:
    """Direct tests of execute_scale_out — fills-based accounting, cancel-first."""

    def _patches(self):
        return [
            patch("execution_agent.cancel_ticker_sell_orders"),
            patch("execution_agent.place_protective_stops"),
            patch("execution_agent.trade_commission", return_value=1.0),
            patch("execution_agent.record_trade_commissions"),
            patch("execution_agent.get_ibkr_account", return_value="DU1234567"),
            patch("execution_agent.notifier"),
        ]

    def test_books_from_fills_not_portfolio_delta(self):
        """Sold qty and price come from trade.fills, immune to a concurrent fill."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.orderStatus.avgFillPrice = 0.0            # force use of fills path
        trade.fills = [_make_fill(6, 104.0), _make_fill(3, 106.0)]  # 9 sh, wavg 104.67
        ib.placeOrder.return_value = trade

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

    def test_cancels_bracket_before_selling(self):
        """cancel_ticker_sell_orders must run before the market order is placed."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.orderStatus.avgFillPrice = 104.0
        trade.fills = [_make_fill(9, 104.0)]

        order_of_calls = []
        ib.placeOrder.side_effect = lambda *a, **k: (order_of_calls.append("place"), trade)[1]

        import contextlib
        with contextlib.ExitStack() as es:
            cancel_mock = es.enter_context(patch("execution_agent.cancel_ticker_sell_orders",
                                                 side_effect=lambda *a, **k: order_of_calls.append("cancel")))
            es.enter_context(patch("execution_agent.place_protective_stops"))
            es.enter_context(patch("execution_agent.trade_commission", return_value=1.0))
            es.enter_context(patch("execution_agent.record_trade_commissions"))
            es.enter_context(patch("execution_agent.get_ibkr_account", return_value="DU1234567"))
            es.enter_context(patch("execution_agent.notifier"))
            buy_date = datetime.date(2026, 6, 11)
            execution_agent.execute_scale_out(
                ib, client, _make_pos(shares=30), "AAPL", 30, 9,
                100.0, buy_date, "CANSLIM breakout", 104.0, 4.5, 0.07, 90.0)

        assert order_of_calls[0] == "cancel"
        assert "place" in order_of_calls
        assert order_of_calls.index("cancel") < order_of_calls.index("place")

    def test_no_fill_restores_full_bracket_and_aborts(self):
        """If nothing fills, the position must not be booked and gets its bracket back."""
        ib = _make_scale_ib(pre_qty=30)
        client = _scale_client()
        trade = MagicMock()
        trade.orderStatus.status = "Cancelled"
        trade.fills = []                      # nothing filled
        ib.placeOrder.return_value = trade

        import contextlib
        with contextlib.ExitStack() as es:
            es.enter_context(patch("execution_agent.cancel_ticker_sell_orders"))
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

