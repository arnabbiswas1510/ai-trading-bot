"""
test_trailing_stop.py — Unit tests for place_trailing_stop() and the
self-healing trailing stop mechanism.

Covers:
  - place_trailing_stop(): correct TRAIL order type, trailingPercent, GTC
  - place_trailing_stop(): no profit-target limit order is ever placed
  - cancel_ticker_sell_orders(): cancels only active SELL orders for the target ticker
  - execute_sell(): cancel_ticker_sell_orders() called BEFORE placing MarketOrder

ARCHITECTURE CONTEXT:
  place_trailing_stop() is the sole exit order placed at buy time. IBKR tracks
  the HWM price tick-by-tick and fires the stop automatically. No limit order is
  ever placed (profit target concept eliminated). Self-healing re-places the
  trailing stop when none exists, anchoring from the current market price.
"""

import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position
)
import execution_agent


# -- place_trailing_stop() unit tests --

class TestPlaceTrailingStop:
    """
    place_trailing_stop() places exactly ONE GTC TRAIL order.
    No LimitOrder (profit target) should ever be placed.
    """

    def test_places_exactly_one_trail_order(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "NVDA"
        execution_agent.place_trailing_stop(ib, contract, shares=100, stop_loss_pct=0.07)
        assert ib.placeOrder.call_count == 1

    def test_order_is_trail_type(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "AAPL"
        execution_agent.place_trailing_stop(ib, contract, shares=50, stop_loss_pct=0.07)
        placed_order = ib.placeOrder.call_args.args[1]
        assert getattr(placed_order, 'orderType', '') == 'TRAIL'

    def test_trailing_percent_correct(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "MSFT"
        execution_agent.place_trailing_stop(ib, contract, shares=80, stop_loss_pct=0.07)
        placed_order = ib.placeOrder.call_args.args[1]
        assert abs(placed_order.trailingPercent - 7.0) < 0.01

    def test_order_is_gtc(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "TSLA"
        execution_agent.place_trailing_stop(ib, contract, shares=30, stop_loss_pct=0.07)
        placed_order = ib.placeOrder.call_args.args[1]
        assert placed_order.tif == 'GTC'

    def test_order_is_sell(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "CRWD"
        execution_agent.place_trailing_stop(ib, contract, shares=20, stop_loss_pct=0.07)
        placed_order = ib.placeOrder.call_args.args[1]
        assert placed_order.action == 'SELL'

    def test_no_limit_order_placed(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "META"
        execution_agent.place_trailing_stop(ib, contract, shares=40, stop_loss_pct=0.07)
        orders = [c.args[1] for c in ib.placeOrder.call_args_list]
        limit_placed = any(getattr(o, 'orderType', '') == 'LMT' for o in orders)
        assert not limit_placed, "No LimitOrder should ever be placed"

    def test_returns_group_string(self):
        ib = make_ib_mock()
        contract = MagicMock()
        contract.symbol = "AMZN"
        group = execution_agent.place_trailing_stop(ib, contract, shares=10, stop_loss_pct=0.07)
        assert isinstance(group, str) and len(group) > 0


# -- cancel_ticker_sell_orders() unit tests --

class TestCancelTickerSellOrders:

    def _make_trade(self, symbol, action, status='Submitted'):
        t = MagicMock()
        t.contract.symbol = symbol
        t.order.action = action
        t.orderStatus.status = status
        return t

    def test_cancels_active_sell_for_ticker(self):
        ib = make_ib_mock()
        trade = self._make_trade("AAPL", "SELL", "Submitted")
        ib.openTrades.return_value = [trade]
        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")
        ib.cancelOrder.assert_called_once_with(trade.order)
        assert cancelled == 1

    def test_does_not_cancel_buy_orders(self):
        ib = make_ib_mock()
        buy_trade = self._make_trade("AAPL", "BUY", "Submitted")
        ib.openTrades.return_value = [buy_trade]
        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")
        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_does_not_cancel_different_ticker(self):
        ib = make_ib_mock()
        other_trade = self._make_trade("NVDA", "SELL", "Submitted")
        ib.openTrades.return_value = [other_trade]
        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")
        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_does_not_cancel_already_filled_order(self):
        ib = make_ib_mock()
        filled_trade = self._make_trade("AAPL", "SELL", "Filled")
        ib.openTrades.return_value = [filled_trade]
        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")
        ib.cancelOrder.assert_not_called()
        assert cancelled == 0

    def test_returns_zero_when_no_open_orders(self):
        ib = make_ib_mock()
        ib.openTrades.return_value = []
        cancelled = execution_agent.cancel_ticker_sell_orders(ib, "AAPL")
        assert cancelled == 0
        ib.cancelOrder.assert_not_called()


# -- execute_sell() cancels trailing stop before market sell --

class TestExecuteSellCancelsTrailingStopFirst:

    def test_cancel_called_before_market_order(self):
        pos = make_position("CRWD", buy_price=200.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["CRWD"])
        ib.portfolio.return_value = []
        call_order = []

        def _track_cancel(ib_, ticker_):
            call_order.append(f"cancel:{ticker_}")
            return 0

        def _track_place(contract, order):
            call_order.append(f"place:{getattr(order, 'action', '?')}")
            trade_mock = MagicMock()
            trade_mock.orderStatus.status = "Filled"
            trade_mock.orderStatus.avgFillPrice = 200.0
            return trade_mock

        ib.placeOrder.side_effect = _track_place

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.cancel_ticker_sell_orders", side_effect=_track_cancel):
            execution_agent.execute_sell(
                ib, supabase, "CRWD",
                shares=100, buy_price=200.0,
                buy_date=datetime.datetime.now(datetime.timezone.utc),
                buy_reason="daily_triggers", current_price=210.0,
                reason="Plateau Rotation"
            )

        cancel_idx = next((i for i, s in enumerate(call_order) if "cancel:CRWD" in s), -1)
        sell_idx   = next((i for i, s in enumerate(call_order) if "place:SELL"  in s), -1)
        assert cancel_idx >= 0
        assert sell_idx >= 0
        assert cancel_idx < sell_idx

    def test_supabase_not_updated_if_sell_not_confirmed(self):
        pos = make_position("CRWD", buy_price=200.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["CRWD"])
        ib.portfolio.return_value = [
            make_portfolio_item("CRWD", position=100, avg_cost=200.0)
        ]
        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.cancel_ticker_sell_orders", return_value=0):
            execution_agent.execute_sell(
                ib, supabase, "CRWD",
                shares=100, buy_price=200.0,
                buy_date=datetime.datetime.now(datetime.timezone.utc),
                buy_reason="daily_triggers", current_price=210.0,
                reason="Plateau Rotation"
            )
        supabase.table("portfolio_positions").delete.assert_not_called()
