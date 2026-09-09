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
from unittest.mock import patch, MagicMock

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

    def test_fast_path_ignores_other_account(self):
        """A second linked account's portfolio items must never be priced."""
        ib = make_ib_mock()
        ib.managedAccounts.return_value = ["U12941651", "U13359115"]
        mine = make_portfolio_item("NVDA", market_price=150.0)
        mine.account = "U12941651"
        other = make_portfolio_item("ZZZZ", market_price=99.0)
        other.account = "U13359115"
        ib.portfolio.return_value = [mine, other]
        m = build_ibkr_price_map(ib)
        assert set(m.keys()) == {"NVDA"}          # other account ignored
        assert m["NVDA"].marketPrice == 150.0

    def test_pnl_single_fallback_when_portfolio_empty(self):
        """Multi-account login: portfolio() is empty, so marks come from
        reqPnLSingle (value/shares), scoped to the target account only."""
        ib = make_ib_mock()
        ib.portfolio.return_value = []            # not served for multi-account
        ib.managedAccounts.return_value = ["U12941651", "U13359115"]
        ib.positions.return_value = [
            _pos_mock("NVDA", "U12941651", shares=10, conId=1),
            _pos_mock("ZZZZ", "U13359115", shares=5,  conId=2),   # other account
        ]

        def _pnl(account, model, conId):
            s = MagicMock()
            s.value = 1500.0 if conId == 1 else 777.0
            s.unrealizedPnL = 200.0 if conId == 1 else 50.0
            return s
        ib.reqPnLSingle.side_effect = _pnl

        m = build_ibkr_price_map(ib)
        assert set(m.keys()) == {"NVDA"}          # other account never priced
        assert m["NVDA"].marketPrice == 150.0     # 1500 / 10
        assert m["NVDA"].marketValue == 1500.0
        assert m["NVDA"].unrealizedPNL == 200.0

    def test_pnl_single_nan_value_is_skipped(self):
        """A NaN reqPnLSingle value must not fabricate a mark."""
        ib = make_ib_mock()
        ib.portfolio.return_value = []
        ib.managedAccounts.return_value = ["U12941651", "U13359115"]
        ib.positions.return_value = [_pos_mock("NVDA", "U12941651", 10, 1)]
        nan_s = MagicMock()
        nan_s.value = float("nan")
        nan_s.unrealizedPnL = float("nan")
        ib.reqPnLSingle.return_value = nan_s
        assert build_ibkr_price_map(ib) == {}

    def test_ttl_cache_reuses_snapshot_until_forced(self):
        ib = make_ib_mock()
        ib.portfolio.return_value = [make_portfolio_item("NVDA", market_price=150.0)]
        first = build_ibkr_price_map(ib)
        assert first["NVDA"].marketPrice == 150.0
        # Change the underlying data; a cached call must NOT see it.
        ib.portfolio.return_value = [make_portfolio_item("NVDA", market_price=999.0)]
        assert build_ibkr_price_map(ib)["NVDA"].marketPrice == 150.0
        # force=True bypasses the cache.
        assert build_ibkr_price_map(ib, force=True)["NVDA"].marketPrice == 999.0


def _pos_mock(symbol, account, shares, conId):
    """Mimic an ib_insync Position (has .account, .position, no marketPrice)."""
    p = MagicMock()
    p.account = account
    p.contract.symbol = symbol
    p.contract.secType = "STK"
    p.contract.conId = conId
    p.position = shares
    return p


class TestIbkrTargetPositions:
    """ibkr_target_positions() — the multi-account-safe holdings source."""

    def test_filters_to_target_account_and_drops_zero(self):
        from execution_agent import ibkr_target_positions
        ib = make_ib_mock()
        ib.managedAccounts.return_value = ["U12941651", "U13359115"]
        ib.positions.return_value = [
            _pos_mock("NVDA", "U12941651", shares=10, conId=1),
            _pos_mock("ZZZZ", "U13359115", shares=5,  conId=2),   # other account
            _pos_mock("SOLD", "U12941651", shares=0,  conId=3),   # closed
        ]
        assert ibkr_target_positions(ib) == {"NVDA": 10}
