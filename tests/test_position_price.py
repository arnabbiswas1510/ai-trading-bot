"""
tests/test_position_price.py

Unit tests for get_position_price() and build_ibkr_price_map() — the IBKR-first
price source for live positions (dashboard, exit rules and account valuation).

Live trades fill against IBKR, so exit decisions must be priced on IBKR's own
mark (PortfolioItem.marketPrice). FMP is a fallback ONLY, used when IBKR has no
usable mark for a ticker. See decisions/2026-09-04_ibkr-first-live-pricing.md.

Covers:
  1. IBKR marketPrice present   -> returns (ibkr_price, 'ibkr')
  2. Ticker not in portfolio    -> FMP fallback, ('fmp')
  3. IBKR marketPrice <= 0       -> FMP fallback
  4. IBKR marketPrice is NaN     -> FMP fallback
  5. Both IBKR and FMP fail      -> (0.0, 'fmp')
  6. Precomputed map is used and ib.portfolio() is NOT re-read
  7. build_ibkr_price_map() maps symbol -> PortfolioItem
  8. ib.portfolio() raising is non-fatal -> empty map, FMP fallback
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from execution_agent import get_position_price, build_ibkr_price_map
from tests.conftest import make_ib_mock, make_portfolio_item


class TestGetPositionPrice:

    def test_returns_ibkr_mark_when_present(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [make_portfolio_item("NVDA", market_price=150.25)]
        with patch("execution_agent.get_live_price") as fmp:
            price, source = get_position_price(ib, "NVDA")
        assert price == 150.25
        assert source == "ibkr"
        fmp.assert_not_called()   # IBKR won -> FMP must never be hit

    def test_falls_back_to_fmp_when_ticker_absent(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [make_portfolio_item("AAPL", market_price=200.0)]
        with patch("execution_agent.get_live_price", return_value=99.5) as fmp:
            price, source = get_position_price(ib, "NVDA")
        assert price == 99.5
        assert source == "fmp"
        fmp.assert_called_once_with("NVDA")

    def test_falls_back_to_fmp_when_ibkr_mark_zero(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [make_portfolio_item("NVDA", market_price=0.0)]
        with patch("execution_agent.get_live_price", return_value=101.0):
            price, source = get_position_price(ib, "NVDA")
        assert price == 101.0
        assert source == "fmp"

    def test_falls_back_to_fmp_when_ibkr_mark_nan(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [make_portfolio_item("NVDA", market_price=float("nan"))]
        with patch("execution_agent.get_live_price", return_value=102.0):
            price, source = get_position_price(ib, "NVDA")
        assert price == 102.0
        assert source == "fmp"

    def test_returns_zero_when_both_sources_fail(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = []
        with patch("execution_agent.get_live_price", return_value=0.0):
            price, source = get_position_price(ib, "NVDA")
        assert price == 0.0
        assert source == "fmp"

    def test_precomputed_map_avoids_reading_portfolio(self):
        ib = make_ib_mock()
        ib.portfolio.side_effect = AssertionError("ib.portfolio() must not be called")
        ib_map = {"NVDA": make_portfolio_item("NVDA", market_price=175.0)}
        with patch("execution_agent.get_live_price"):
            price, source = get_position_price(ib, "NVDA", ib_map)
        assert price == 175.0
        assert source == "ibkr"


class TestBuildIbkrPriceMap:

    def test_maps_symbol_to_portfolio_item(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [
            make_portfolio_item("NVDA", market_price=150.0),
            make_portfolio_item("AAPL", market_price=200.0),
        ]
        m = build_ibkr_price_map(ib)
        assert set(m.keys()) == {"NVDA", "AAPL"}
        assert m["NVDA"].marketPrice == 150.0

    def test_portfolio_exception_is_non_fatal(self):
        ib = make_ib_mock()
        ib.portfolio.side_effect = RuntimeError("gateway disconnected")
        assert build_ibkr_price_map(ib) == {}
