"""
test_sell_logic.py -- Tests for monitor_portfolio_intraday() and run_market_open_buys() sell rules.

Covers:
  - Trailing stop self-healing (re-places when absent, anchors from current price)
  - hwm_date updated when price rises; NOT updated when price falls
  - No Python-side stop-loss or profit-target enforcement (IBKR owns these)
  - Moving Average Exit (EMA-21 EOD check)
  - EOD Plateau Rotation (3:45pm, portfolio full, fresh trigger, stale hwm_date)
  - No limit order placed at buy time

ARCHITECTURE NOTE (post HWM Plateau Rotation refactor):
  IBKR manages the trailing stop HWM price tick-by-tick.
  Python only tracks hwm_date (date of last new high) for plateau detection.
  No profit target, no power hold, no morning stale rotation.
  monitor_portfolio_intraday() roles:
    1. Update hwm_date when a new intraday high is seen
    2. Self-heal trailing stop if IBKR orders are missing
    3. EMA-21 EOD exit check
    4. EOD Plateau Rotation (3:45pm window)
  reconcile_with_ibkr() (Case 1) detects IBKR-closed positions and archives them.
"""

import datetime
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger
)
import execution_agent


# -- Helpers --

def _run_monitor(ib, supabase_mock, live_prices=None, is_eod=False, is_bullish=True):
    """
    Runs monitor_portfolio_intraday() with standard patches.
    live_prices: dict of {ticker: price}. Defaults to $100 for all.
    is_eod: if True, patches datetime so is_ma_window and is_eod_window are True.
    """
    prices = live_prices or {}

    def _price(ticker):
        return prices.get(ticker, 100.0)

    tz = ZoneInfo("America/New_York")
    if is_eod:
        now_mock = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)  # 3:50 PM ET
    else:
        now_mock = datetime.datetime(2026, 6, 20, 11, 30, tzinfo=tz)  # 11:30 AM ET

    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", side_effect=_price), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_trailing_stop", return_value="TS_MOCK") as mock_ts, \
         patch("execution_agent.execute_sell") as mock_sell, \
         patch("execution_agent.datetime") as mock_datetime:
        mock_datetime.datetime.now.side_effect = lambda *a, **kw: now_mock
        mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
        mock_datetime.date.today.return_value = now_mock.date()
        mock_datetime.timezone = datetime.timezone
        mock_datetime.timedelta = datetime.timedelta
        execution_agent.monitor_portfolio_intraday(ib)
        return mock_sell, mock_ts


# -- Trailing stop self-healing --

class TestSelfHealingTrailingStop:
    """
    If no open SELL orders exist for a position, monitor must re-place the
    trailing stop (using place_trailing_stop, NOT place_oca_bracket).
    """

    def test_self_healing_places_trailing_stop_when_no_sell_orders(self):
        """No open SELL orders -> place_trailing_stop called for self-healing."""
        pos = make_position("NVDA", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["NVDA"])
        ib.openTrades.return_value = []  # No open sell orders

        _, mock_ts = _run_monitor(ib, supabase, live_prices={"NVDA": 105.0})
        mock_ts.assert_called_once()

    def test_self_healing_not_called_when_sell_order_exists(self):
        """Trailing stop already in IBKR -> no self-healing."""
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        _, mock_ts = _run_monitor(ib, supabase, live_prices={"AAPL": 105.0})
        mock_ts.assert_not_called()

    def test_ibkr_stop_not_python_code_enforced(self):
        """Even when price is below stop level, Python does NOT call execute_sell.
        IBKR fires the trailing stop order automatically."""
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        # Price at 87 -- well below a 7% trailing stop from $100 entry
        mock_sell, _ = _run_monitor(ib, supabase, live_prices={"AAPL": 87.0})
        mock_sell.assert_not_called()


# -- hwm_date tracking --

class TestHwmDateTracking:
    """
    hwm_date (date of last intraday high) is the only HWM data Python tracks.
    IBKR owns the HWM price for the trailing stop.
    """

    def test_hwm_date_updated_when_price_rises(self):
        """New intraday high (price > buy_price) -> hwm_date written to Supabase."""
        pos = make_position("MSFT", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["MSFT"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "MSFT"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=115.0), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # Verify hwm_date update was called
        update_calls = supabase.table("portfolio_positions").update.call_args_list
        hwm_date_written = any(
            "hwm_date" in (call_args[0][0] if call_args[0] else {})
            for call_args in update_calls
        )
        assert hwm_date_written, "hwm_date must be written to Supabase when price makes new intraday high"

    def test_hwm_date_not_updated_when_price_falls(self):
        """Price does not exceed buy_price (or last seen peak) -> no hwm_date update."""
        pos = make_position("MSFT", buy_price=130.0)  # current < buy_price
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["MSFT"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "MSFT"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=115.0), \
             patch("execution_agent.execute_sell"):
            execution_agent.monitor_portfolio_intraday(ib)

        # hwm_date should NOT be written if current < buy_price (default intraday peak)
        update_calls = supabase.table("portfolio_positions").update.call_args_list
        hwm_date_written = any(
            "hwm_date" in (call_args[0][0] if call_args[0] else {})
            for call_args in update_calls
        )
        assert not hwm_date_written, "hwm_date must NOT be written when price did not make a new intraday high"


# -- Moving Average Calculations --

class TestMovingAverageCalculations:

    def test_calculate_sma(self):
        closes = [10.0, 20.0, 30.0, 40.0]
        assert execution_agent.calculate_sma(closes, 3) == 30.0
        assert execution_agent.calculate_sma(closes, 5) is None

    def test_calculate_ema(self):
        closes = [10.0, 11.0, 12.0]
        assert abs(execution_agent.calculate_ema(closes, 2) - 11.5) < 1e-6
        assert execution_agent.calculate_ema(closes, 4) is None

    @patch("execution_agent.fetch_historical_closes_with_dates")
    def test_get_ma_value_appends_current_price_if_not_today(self, mock_fetch):
        mock_fetch.return_value = [
            {"date": "2026-06-18", "close": 100.0},
            {"date": "2026-06-19", "close": 102.0}
        ]
        with patch("execution_agent.datetime") as mock_date:
            tz = ZoneInfo("America/New_York")
            mock_date.date.today.return_value = datetime.date(2026, 6, 20)
            mock_date.datetime.now.side_effect = lambda *args, **kwargs: datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)
            mock_date.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            val = execution_agent.get_ma_value("AAPL", 104.0, "SMA", 2)
            assert val == 103.0


# -- Moving Average Exits --

class TestMovingAverageExits:

    def test_ma_exit_triggers_on_eod_breach(self):
        """Price below threshold near market close -> execute_sell called."""
        pos = make_position("AAPL", buy_price=100.0, buy_date="2026-06-10T12:00:00+00:00")
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        hist_data = [{"date": f"2026-06-{i:02d}", "close": 100.0} for i in range(1, 22)]
        tz = ZoneInfo("America/New_York")
        eod_time = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.fetch_historical_closes_with_dates", return_value=hist_data), \
             patch("execution_agent.get_live_price", return_value=98.0), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.EXIT_MA_TRIGGER_ENABLED", True), \
             patch("execution_agent.EXIT_MA_TYPE", "EMA"), \
             patch("execution_agent.EXIT_MA_WINDOW", 21), \
             patch("execution_agent.EXIT_MA_BUFFER_PCT", 0.01), \
             patch("execution_agent.EXIT_MA_EOD_ONLY", True), \
             patch("execution_agent.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: eod_time
            mock_datetime.date.today.return_value = eod_time.date()
            mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mock_datetime.timezone = datetime.timezone
            mock_datetime.timedelta = datetime.timedelta
            execution_agent.monitor_portfolio_intraday(ib)
            mock_sell.assert_called_once()
            args, kwargs = mock_sell.call_args
            assert args[2] == "AAPL"
            assert "EMA-21 Exit" in args[8]

    def test_ma_exit_does_not_trigger_within_buffer(self):
        """Price below MA but within buffer -> no exit."""
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        hist_data = [{"date": f"2026-06-{i:02d}", "close": 100.0} for i in range(1, 22)]
        tz = ZoneInfo("America/New_York")
        eod_time = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.fetch_historical_closes_with_dates", return_value=hist_data), \
             patch("execution_agent.get_live_price", return_value=99.5), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.EXIT_MA_TRIGGER_ENABLED", True), \
             patch("execution_agent.EXIT_MA_TYPE", "EMA"), \
             patch("execution_agent.EXIT_MA_WINDOW", 21), \
             patch("execution_agent.EXIT_MA_BUFFER_PCT", 0.01), \
             patch("execution_agent.EXIT_MA_EOD_ONLY", True), \
             patch("execution_agent.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: eod_time
            mock_datetime.date.today.return_value = eod_time.date()
            mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mock_datetime.timezone = datetime.timezone
            mock_datetime.timedelta = datetime.timedelta
            execution_agent.monitor_portfolio_intraday(ib)
            mock_sell.assert_not_called()

    def test_ma_exit_skipped_outside_eod_window(self):
        """Outside 3:45-4:00 PM and EOD_ONLY enabled -> no exit."""
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        hist_data = [{"date": f"2026-06-{i:02d}", "close": 100.0} for i in range(1, 22)]
        tz = ZoneInfo("America/New_York")
        midday = datetime.datetime(2026, 6, 20, 11, 30, tzinfo=tz)

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.fetch_historical_closes_with_dates", return_value=hist_data), \
             patch("execution_agent.get_live_price", return_value=95.0), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.EXIT_MA_TRIGGER_ENABLED", True), \
             patch("execution_agent.EXIT_MA_TYPE", "EMA"), \
             patch("execution_agent.EXIT_MA_WINDOW", 21), \
             patch("execution_agent.EXIT_MA_BUFFER_PCT", 0.01), \
             patch("execution_agent.EXIT_MA_EOD_ONLY", True), \
             patch("execution_agent.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: midday
            mock_datetime.date.today.return_value = midday.date()
            mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mock_datetime.timezone = datetime.timezone
            mock_datetime.timedelta = datetime.timedelta
            execution_agent.monitor_portfolio_intraday(ib)
            mock_sell.assert_not_called()

    def test_ma_exit_failsafe_on_fmp_error(self):
        """FMP historical fetch returns empty -> no exit and no crash."""
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"])
        mock_trade = MagicMock()
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        ib.openTrades.return_value = [mock_trade]

        tz = ZoneInfo("America/New_York")
        eod_time = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.fetch_historical_closes_with_dates", return_value=[]), \
             patch("execution_agent.get_live_price", return_value=95.0), \
             patch("execution_agent.execute_sell") as mock_sell, \
             patch("execution_agent.EXIT_MA_TRIGGER_ENABLED", True), \
             patch("execution_agent.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *args, **kwargs: eod_time
            mock_datetime.date.today.return_value = eod_time.date()
            mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mock_datetime.timezone = datetime.timezone
            mock_datetime.timedelta = datetime.timedelta
            execution_agent.monitor_portfolio_intraday(ib)
            mock_sell.assert_not_called()


# -- EOD Plateau Rotation --

class TestPlateauRotation:
    """
    EOD plateau rotation: at 3:45-4pm, if portfolio is full AND fresh breakout
    trigger exists AND a position has had no new HWM in PLATEAU_DAYS days,
    sell the most-stalled position.
    """

    def _eod_monitor(self, positions, daily_triggers, live_prices=None):
        """Helper: run monitor in EOD window."""
        supabase = make_supabase_mock(portfolio=positions, daily_triggers=daily_triggers)
        symbols = [p["ticker"] for p in positions]
        ib = make_ib_mock(symbols=symbols)
        mock_trade = MagicMock()
        mock_trade.order.action = "SELL"
        mock_trade.orderStatus.status = "Submitted"
        # Give each position a sell order so self-healing doesn''t interfere
        def _open_trades():
            trades = []
            for sym in symbols:
                t = MagicMock()
                t.contract.symbol = sym
                t.order.action = "SELL"
                t.orderStatus.status = "Submitted"
                trades.append(t)
            return trades
        ib.openTrades.side_effect = _open_trades

        prices = live_prices or {p["ticker"]: 100.0 for p in positions}

        tz = ZoneInfo("America/New_York")
        eod_time = datetime.datetime(2026, 6, 20, 15, 50, tzinfo=tz)

        sold_tickers = []
        def _capture_sell(ib_, client, ticker, *args, **kwargs):
            sold_tickers.append(ticker)
            return True

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", side_effect=lambda t: prices.get(t, 100.0)), \
             patch("execution_agent.execute_sell", side_effect=_capture_sell), \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.datetime") as mock_datetime:
            mock_datetime.datetime.now.side_effect = lambda *a, **kw: eod_time
            mock_datetime.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mock_datetime.date.fromisoformat.side_effect = datetime.date.fromisoformat
            mock_datetime.date.today.return_value = eod_time.date()
            mock_datetime.timezone = datetime.timezone
            mock_datetime.timedelta = datetime.timedelta
            execution_agent.monitor_portfolio_intraday(ib)

        return sold_tickers

    def test_mandatory_time_stop_at_day_7_low_return(self):
        """Position held >= 7 trading days with < 2% return is sold unconditionally at EOD."""
        portfolio = [
            make_position("AAPL", buy_price=100.0, buy_date="2026-06-10T12:00:00+00:00"), # ~8 trading days
            make_position("MSFT", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("NVDA", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("AMZN", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
        ]

        with patch("execution_agent.get_live_price", return_value=101.0):
            sold = self._eod_monitor(portfolio, daily_triggers=[])
        
        assert "AAPL" in sold, f"Day 7 position AAPL should have been sold via Time-Stop, got {sold}"

    def test_mandatory_time_stop_does_not_fire_at_day_6(self):
        """Position held 6 trading days with < 2% return is NOT sold if no triggers / no drift."""
        portfolio = [
            make_position("AAPL", buy_price=100.0, buy_date="2026-06-12T12:00:00+00:00"), # ~6 trading days
            make_position("MSFT", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("NVDA", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("AMZN", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
        ]

        with patch("execution_agent.get_live_price", return_value=101.0):
            sold = self._eod_monitor(portfolio, daily_triggers=[])
        
        assert sold == [], f"Day 6 position should not be auto-sold, got {sold}"

    def test_rank_and_replace_swap(self):
        """Days 3-6 position with RS decay is swapped if a +15 point gap trigger exists."""
        portfolio = [
            make_position("AAPL", buy_price=100.0, buy_date="2026-06-14T12:00:00+00:00", entry_rs_score=90, entry_final_score=50), # Day 4
            make_position("MSFT", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("NVDA", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("AMZN", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
        ]
        # TSLA score 70 (+20 gap)
        triggers = [make_trigger("TSLA", final_score=70)]

        with patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent._fetch_ohlcv", return_value=[]), \
             patch("execution_agent._fetch_current_rs", return_value=75): # decay 90 -> 75
            sold = self._eod_monitor(portfolio, triggers)

        assert "AAPL" in sold, f"AAPL should be rotated out for TSLA, got {sold}"

    def test_rank_and_replace_skips_when_no_triggers(self):
        """Days 3-6 position with decay is NOT swapped if no triggers exist."""
        portfolio = [
            make_position("AAPL", buy_price=100.0, buy_date="2026-06-14T12:00:00+00:00", entry_rs_score=90, entry_final_score=50),
            make_position("MSFT", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("NVDA", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
            make_position("AMZN", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
        ]

        with patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent._fetch_ohlcv", return_value=[]), \
             patch("execution_agent._fetch_current_rs", return_value=75):
            sold = self._eod_monitor(portfolio, daily_triggers=[])

        assert sold == [], f"No rotation should occur without triggers, got {sold}"

    def test_rank_and_replace_skips_when_portfolio_not_full(self):
        """Days 3-6 position with decay is NOT swapped if portfolio has open slots (not full)."""
        portfolio = [
            make_position("AAPL", buy_price=100.0, buy_date="2026-06-14T12:00:00+00:00", entry_rs_score=90, entry_final_score=50),
            make_position("MSFT", buy_price=100.0, buy_date="2026-06-18T12:00:00+00:00"),
        ]
        triggers = [make_trigger("TSLA", final_score=70)]

        with patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent._fetch_ohlcv", return_value=[]), \
             patch("execution_agent._fetch_current_rs", return_value=75):
            sold = self._eod_monitor(portfolio, triggers)

        assert sold == [], f"No rotation should occur if portfolio not full, got {sold}"


# -- No LMT at buy time --

class TestBuyBracketNoLimitAtBuyTime:
    """
    run_market_open_buys() must NOT submit any LimitOrder (profit target).
    Only a TrailingStopOrder is placed after the buy fills.
    """

    def test_buy_places_only_trailing_stop(self):
        """run_market_open_buys() must place exactly 1 TRAIL sell -- no LMT."""
        trigger = make_trigger("NVDA", close_price=100.0, volume_surge=2.0,
                               pivot_distance_pct=-0.5)
        supabase = make_supabase_mock(daily_triggers=[trigger], portfolio=[])
        ib = make_ib_mock(symbols=[])
        ib.managedAccounts.return_value = ["DU12345"]

        order_types_placed = []

        def _track_place(contract, order):
            order_types_placed.append((
                getattr(order, "action", "?"),
                getattr(order, "orderType", "?"),
            ))
            trade_mock = MagicMock()
            trade_mock.orderStatus.status = "Submitted"
            trade_mock.orderStatus.avgFillPrice = 101.0
            return trade_mock

        ib.placeOrder.side_effect = _track_place
        ib.portfolio.return_value = [
            make_portfolio_item("NVDA", position=99, avg_cost=101.0)
        ]

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_available_cash", return_value=10000.0), \
             patch("execution_agent.MAX_POSITIONS", 1):
            execution_agent.run_market_open_buys(ib)

        sell_types = [order_type for action, order_type in order_types_placed if action == "SELL"]
        limit_sells = [t for t in sell_types if t == "LMT"]
        trail_sells = [t for t in sell_types if t == "TRAIL"]

        assert len(limit_sells) == 0, (
            f"No LimitOrder (profit target) should be placed. Found: {limit_sells}"
        )
        assert len(trail_sells) == 1, (
            f"Exactly one TRAIL stop should be placed. Found: {trail_sells}"
        )
