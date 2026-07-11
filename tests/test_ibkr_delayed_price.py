"""
tests/test_ibkr_delayed_price.py

Unit tests for fetch_ibkr_delayed_price() -- the function that fetches
a stock price via IBKR delayed market data (reqMarketDataType=3).

Covers:
  1. Ask price available -> returns ask
  2. Ask is 0/NaN, last available -> returns last
  3. Both ask and last are 0 -> returns (0.0, '')
  4. Both ask and last are NaN -> treated as 0, returns (0.0, '')
  5. reqTickers returns empty list -> returns (0.0, '')
  6. reqTickers raises an exception -> returns (0.0, ''), live mode still restored
  7. reqMarketDataType(1) is ALWAYS called (live mode restored), even on exception
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from execution_agent import fetch_ibkr_delayed_price


# -- helpers ------------------------------------------------------------------

def _make_ticker(ask, last):
    """Build a minimal ib_insync Ticker-like mock."""
    t = MagicMock()
    t.ask  = ask
    t.last = last
    return t


def _make_ib(ticker_mock=None, raise_on_tickers=None):
    """Build an IB mock whose reqTickers() returns [ticker_mock] or raises."""
    ib = MagicMock()
    if raise_on_tickers:
        ib.reqTickers.side_effect = raise_on_tickers
    else:
        ib.reqTickers.return_value = [ticker_mock] if ticker_mock is not None else []
    return ib


# -- tests --------------------------------------------------------------------

class TestFetchIbkrDelayedPrice:

    def test_returns_ask_when_ask_is_positive(self):
        """Primary path: ask > 0 -> price = ask, method = 'ask'."""
        ib = _make_ib(_make_ticker(ask=150.25, last=149.80))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 150.25
        assert method == "ask"

    def test_falls_back_to_last_when_ask_is_zero(self):
        """ask == 0 (market closed) -> falls back to last."""
        ib = _make_ib(_make_ticker(ask=0.0, last=149.80))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 149.80
        assert method == "last"

    def test_falls_back_to_last_when_ask_is_nan(self):
        """ask == NaN (no data) -> treated as 0, falls back to last."""
        ib = _make_ib(_make_ticker(ask=float('nan'), last=149.80))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 149.80
        assert method == "last"

    def test_returns_zero_when_both_ask_and_last_are_zero(self):
        """Both 0 -> no valid price, returns (0.0, '')."""
        ib = _make_ib(_make_ticker(ask=0.0, last=0.0))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 0.0
        assert method == ""

    def test_returns_zero_when_both_ask_and_last_are_nan(self):
        """Both NaN -> treated as 0, returns (0.0, '')."""
        ib = _make_ib(_make_ticker(ask=float('nan'), last=float('nan')))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 0.0
        assert method == ""

    def test_returns_zero_when_reqtickers_returns_empty_list(self):
        """reqTickers returns [] -> no ticker to read, returns (0.0, '')."""
        ib = _make_ib(ticker_mock=None)
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 0.0
        assert method == ""

    def test_returns_zero_on_exception_from_reqtickers(self):
        """reqTickers raises -> exception is swallowed, returns (0.0, '')."""
        ib = _make_ib(raise_on_tickers=RuntimeError("gateway disconnected"))
        price, method = fetch_ibkr_delayed_price(ib, MagicMock())
        assert price == 0.0
        assert method == ""

    def test_always_restores_live_mode_on_success(self):
        """reqMarketDataType(1) must be the last call even on success."""
        ib = _make_ib(_make_ticker(ask=200.0, last=199.5))
        fetch_ibkr_delayed_price(ib, MagicMock())
        assert call(3) in ib.reqMarketDataType.call_args_list
        assert ib.reqMarketDataType.call_args_list[-1] == call(1)

    def test_always_restores_live_mode_on_exception(self):
        """reqMarketDataType(1) must be called even when reqTickers raises."""
        ib = _make_ib(raise_on_tickers=ConnectionError("lost connection"))
        fetch_ibkr_delayed_price(ib, MagicMock())
        assert ib.reqMarketDataType.call_args_list[-1] == call(1)

    def test_switches_to_delayed_mode_before_requesting(self):
        """reqMarketDataType(3) must be called BEFORE reqTickers."""
        call_order = []
        ib = MagicMock()
        ib.reqMarketDataType.side_effect = lambda x: call_order.append(f"type:{x}")
        ib.reqTickers.side_effect = lambda c: (call_order.append("tickers"), [_make_ticker(100.0, 99.0)])[1]

        fetch_ibkr_delayed_price(ib, MagicMock())

        assert call_order.index("type:3") < call_order.index("tickers"), \
            "reqMarketDataType(3) must be called before reqTickers"
