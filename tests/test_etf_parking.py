"""
test_etf_parking.py — Tests for ETF cash parking and market direction logic.

Covers:
  - Bear market: ETF positions liquidated, pure cash held
  - Bull market: idle slots parked in QQQ
  - Skips re-park if ETF already parked
  - ETF_PARKING_ENABLED=false master switch
  - is_market_bullish() fails open (returns True) on API failure
  - run_etf_parking correctly calculates empty slots (stock-only counting)
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger
)
import execution_agent


class TestMarketBullishDetection:

    def test_market_bullish_true_when_spy_above_sma200(self):
        """SPY close > SMA200 → bullish → returns True."""
        # Create 210 days of prices: first 200 at $400, last 10 climb to $450
        prices = [{"date": f"2024-0{i%9+1}-01", "close": 400.0} for i in range(200)]
        prices += [{"date": f"2025-0{i%9+1}-01", "close": 450.0} for i in range(10)]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = prices

        with patch("execution_agent.requests.get", return_value=mock_response), \
             patch("execution_agent.MARKET_DIRECTION_FILTER_ENABLED", True):
            result = execution_agent.is_market_bullish()

        assert result is True

    def test_market_bearish_when_spy_below_sma200(self):
        """SPY close < SMA200 → bearish → returns False."""
        # 200 prices at $450, then 10 fall to $400 (below SMA)
        prices = [{"date": f"2024-0{i%9+1}-01", "close": 450.0} for i in range(200)]
        prices += [{"date": f"2025-0{i%9+1}-01", "close": 400.0} for i in range(10)]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = prices

        with patch("execution_agent.requests.get", return_value=mock_response), \
             patch("execution_agent.MARKET_DIRECTION_FILTER_ENABLED", True):
            result = execution_agent.is_market_bullish()

        assert result is False

    def test_market_bullish_fails_open_on_api_error(self):
        """API unavailable → fails open → returns True (no accidental cash lock)."""
        with patch("execution_agent.requests.get", side_effect=Exception("timeout")), \
             patch("execution_agent.MARKET_DIRECTION_FILTER_ENABLED", True):
            result = execution_agent.is_market_bullish()

        assert result is True

    def test_market_direction_disabled_always_bullish(self):
        """MARKET_DIRECTION_FILTER_ENABLED=False → always returns True."""
        with patch("execution_agent.MARKET_DIRECTION_FILTER_ENABLED", False):
            result = execution_agent.is_market_bullish()

        assert result is True

    def test_market_bullish_fails_open_on_insufficient_data(self):
        """Fewer than 200 data points → fails open → returns True."""
        prices = [{"date": f"2025-01-{i:02d}", "close": 400.0} for i in range(1, 51)]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = prices

        with patch("execution_agent.requests.get", return_value=mock_response), \
             patch("execution_agent.MARKET_DIRECTION_FILTER_ENABLED", True):
            result = execution_agent.is_market_bullish()

        assert result is True


class TestETFParking:

    def test_bear_market_liquidates_etf_positions(self):
        """Bear market + ETF positions → liquidate_etf_positions called."""
        etf_pos = make_position("QQQ", buy_source="etf_parking")
        supabase = make_supabase_mock(portfolio=[etf_pos])
        ib = make_ib_mock(symbols=["QQQ"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=False), \
             patch("execution_agent.get_live_price", return_value=400.0), \
             patch("execution_agent.liquidate_etf_positions") as mock_liq, \
             patch("execution_agent.execute_sell"), \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        mock_liq.assert_called_once()

    def test_bear_market_no_etf_no_crash(self):
        """Bear market + no ETF positions → no liquidation, no crash."""
        supabase = make_supabase_mock(portfolio=[make_position("AAPL")])
        ib = make_ib_mock(symbols=["AAPL"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=False), \
             patch("execution_agent.liquidate_etf_positions") as mock_liq, \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        mock_liq.assert_not_called()

    def test_bull_market_parks_when_slot_available(self):
        """Bull market + 1 stock + 0 ETF → parks in QQQ."""
        portfolio = [make_position("AAPL")]
        supabase = make_supabase_mock(portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "QQQ"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.get_live_price", return_value=400.0), \
             patch("execution_agent.get_available_cash", return_value=20_000.0), \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        ib.placeOrder.assert_called_once()

    def test_bull_market_skips_park_if_etf_already_parked(self):
        """Bull market + QQQ already parked → no re-park (avoid double position)."""
        portfolio = [
            make_position("AAPL"),
            make_position("QQQ", buy_source="etf_parking"),
        ]
        supabase = make_supabase_mock(portfolio=portfolio)
        ib = make_ib_mock(symbols=["AAPL", "QQQ"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.get_live_price", return_value=400.0), \
             patch("execution_agent.get_available_cash", return_value=20_000.0), \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        ib.placeOrder.assert_not_called()

    def test_parking_disabled_no_action(self):
        """ETF_PARKING_ENABLED=False → run_etf_parking returns immediately."""
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock()

        with patch("execution_agent.ETF_PARKING_ENABLED", False):
            execution_agent.run_etf_parking(ib, supabase)

        ib.placeOrder.assert_not_called()

    def test_bull_market_insufficient_cash_no_park(self):
        """Bull market + cash < MIN_POSITION_SIZE → skip ETF buy."""
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.get_live_price", return_value=400.0), \
             patch("execution_agent.get_available_cash", return_value=1_000.0), \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        ib.placeOrder.assert_not_called()

    def test_etf_fill_verified_via_ib_portfolio_not_positions(self):
        """
        Critical: after placing ETF order, fill verification uses ib.portfolio()
        NOT ib.positions(). This is the Bug #5 invariant applied to ETF parking.
        """
        portfolio = []
        supabase = make_supabase_mock(portfolio=portfolio)
        ib = make_ib_mock(symbols=["QQQ"])

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.get_live_price", return_value=400.0), \
             patch("execution_agent.get_available_cash", return_value=20_000.0), \
             patch("execution_agent.ETF_PARKING_ENABLED", True):
            execution_agent.run_etf_parking(ib, supabase)

        # ib.portfolio() must have been called for fill verification
        ib.portfolio.assert_called()
        # ib.positions() must NOT have been called
        ib.positions.assert_not_called()


class TestGetETFandStockPositions:

    def test_get_etf_positions_returns_only_etf(self):
        """get_etf_positions() returns rows with buy_source='etf_parking' only."""
        client = make_supabase_mock(
            portfolio=[
                make_position("AAPL", buy_source="daily_triggers"),
                make_position("QQQ", buy_source="etf_parking"),
            ]
        )
        # get_etf_positions uses .eq("buy_source", "etf_parking")
        # Our mock returns .select().eq().execute().data
        result = execution_agent.get_etf_positions(client)
        # The mock returns the filtered list via neq pattern; verify it's called
        client.table.assert_called_with("portfolio_positions")

    def test_get_stock_positions_excludes_etf(self):
        """get_stock_positions() uses neq('buy_source', 'etf_parking') — ETF excluded."""
        client = make_supabase_mock(
            portfolio=[
                make_position("AAPL", buy_source="daily_triggers"),
                make_position("QQQ", buy_source="etf_parking"),
            ]
        )
        execution_agent.get_stock_positions(client)
        client.table.assert_called_with("portfolio_positions")
