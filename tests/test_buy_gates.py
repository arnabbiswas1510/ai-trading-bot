from zoneinfo import ZoneInfo
"""
test_buy_gates.py — Tests for all 7 buy gates in run_market_open_buys().

Each test verifies one gate: when the gate condition is met, no IBKR order is
placed. When the gate passes, the order IS placed.

Critical invariant tested here:
  - ETF parking positions do NOT count as stock slots (Bug #7)
  - Cooling-off uses trade_history.sell_date, NOT created_at (Bug #2)
  - Momentum cascade has its own ETF pre-flight (Bug #8)
  - Position sizing divides by stock slots only, not total slots
"""

import datetime
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_buys(ib, supabase_mock, live_price=105.0, available_cash=20_000.0,
              is_bullish=True):
    """
    Runs run_market_open_buys() with standard patches applied.
    Returns the mock_ib so callers can inspect placeOrder calls.

    NOTE: notifier is patched here to prevent real Telegram messages from
    firing when tests run on the server where live credentials are present.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.get_available_cash", return_value=available_cash), \
         patch("execution_agent.is_market_bullish", return_value=is_bullish), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return ib


# ── Gate 1: Stock slot capacity ───────────────────────────────────────────────

class TestGate1StockSlots:

    def test_gate1_four_stock_positions_blocks_all_buys(self):
        """Gate 1: 4 stock positions → portfolio full → no order placed."""
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA", "AMZN"]]
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("TSLA")],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=["AAPL", "MSFT", "NVDA", "AMZN"])
        _run_buys(ib, supabase)
        ib.placeOrder.assert_not_called()

