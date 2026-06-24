"""
test_reconcile.py — Tests for reconcile_with_ibkr() four reconcile cases.

Critical invariants:
  - Uses ib.portfolio() NOT ib.positions() everywhere (Bug #5)
  - Case 2 sets high_water_mark = avg_cost on manual IBKR buy (Bug #4)
  - Uses averageCost attribute (PortfolioItem), NOT avgCost (Position)
  - Case 4 cash sync skips write when balance change < $1
"""

import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger
)
import execution_agent


def _reconcile(ib, supabase_mock):
    """Runs reconcile_with_ibkr with the given mocks."""
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=100.0), \
         patch("execution_agent.get_available_cash", return_value=10_000.0):
        execution_agent.reconcile_with_ibkr(ib)


class TestReconcileCase1:
    """Case 1: In Supabase, NOT in IBKR → closed by IBKR (trailing stop / limit / TWS)."""

    def test_case1_removes_from_portfolio_and_logs_trade(self):
        """Position in Supabase but not IBKR → archived to trade_history.
        IBKR portfolio must be non-empty (else Guard 1 fires and skips all Case 1).
        We keep a DIFFERENT ticker in IBKR so the guard is satisfied.
        """
        pos = make_position("AAPL", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        # IBKR has SPY but NOT AAPL — Guard 1 won't fire (portfolio non-empty)
        ib = make_ib_mock(symbols=["SPY"], avg_cost=500.0)
        ib.reqExecutions.return_value = []

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_available_cash", return_value=10_000.0), \
             patch("execution_agent.cancel_ticker_sell_orders"):
            execution_agent.reconcile_with_ibkr(ib)

        # Delete from portfolio_positions
        supabase.table("portfolio_positions").delete.assert_called()
        # Insert to trade_history
        supabase.table("trade_history").insert.assert_called()

    def test_case1_uses_fmp_price_when_no_execution(self):
        """Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill."""
        pos = make_position("NVDA", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        # Non-empty IBKR portfolio but NVDA is missing
        ib = make_ib_mock(symbols=["SPY"], avg_cost=500.0)
        ib.reqExecutions.return_value = []

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=150.0) as mock_price, \
             patch("execution_agent.get_available_cash", return_value=10_000.0), \
             patch("execution_agent.cancel_ticker_sell_orders"):
            execution_agent.reconcile_with_ibkr(ib)

        # get_live_price should have been called for the fallback
        mock_price.assert_any_call("NVDA")


class TestReconcileCase2:
    """Case 2: In IBKR, NOT in Supabase → manual buy detected."""

    def test_case2_inserts_new_position_with_high_water_mark(self):
        """
        Bug #4 regression: Case 2 must set high_water_mark = avg_cost.
        Without this, high_water_mark is NULL and trailing stop is based on $0.
        """
        supabase = make_supabase_mock(portfolio=[])  # Nothing in Supabase
        ib = make_ib_mock(symbols=["TSLA"], avg_cost=200.0)  # TSLA in IBKR

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_called()
        insert_args = supabase.table("portfolio_positions").insert.call_args[0][0]

        # high_water_mark must equal avg_cost — Bug #4 invariant
        assert insert_args.get("high_water_mark") == 200.0, (
            f"high_water_mark should equal avg_cost=200.0, got {insert_args.get('high_water_mark')}"
        )

    def test_case2_uses_averagecost_attribute_not_avgcost(self):
        """
        Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).
        The code must read p.averageCost, not p.avgCost.
        Each test uses a separate mock to avoid shared call_args state.
        """
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock(symbols=["CRWD"], avg_cost=250.0)

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_called()
        insert_args = supabase.table("portfolio_positions").insert.call_args[0][0]
        assert insert_args.get("buy_price") == 250.0, (
            f"buy_price should be 250.0 (from averageCost). Got {insert_args.get('buy_price')}"
        )

    def test_case2_skips_position_with_zero_avg_cost(self):
        """Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)."""
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock(symbols=["WEIRD"], avg_cost=0.0)

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_not_called()

    def test_case2_stop_loss_and_profit_target_computed_correctly(self):
        """Case 2: stop_loss = avg_cost * 0.93, profit_target = avg_cost * 1.25."""
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock(symbols=["AMZN"], avg_cost=100.0)

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_called()
        insert_args = supabase.table("portfolio_positions").insert.call_args[0][0]
        assert abs(insert_args["stop_loss"] - 93.0) < 0.01
        assert abs(insert_args["profit_target"] - 125.0) < 0.01


class TestReconcileCase3:
    """Case 3: In both, share count differs → update Supabase."""

    def test_case3_updates_share_count_on_mismatch(self):
        """IBKR has 150 shares, Supabase says 100 → update Supabase to 150."""
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])

        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        # Set IBKR to have 150 shares
        ib.portfolio.return_value[0].position = 150

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").update.assert_called()
        update_data = supabase.table("portfolio_positions").update.call_args[0][0]
        assert update_data["shares"] == 150

    def test_case3_no_update_when_shares_match(self):
        """Case 3: IBKR and Supabase both have 100 shares → no update."""
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        ib.portfolio.return_value[0].position = 100

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").update.assert_not_called()


class TestReconcileCase4:
    """Case 4: Cash balance sync from IBKR to Supabase account_balances."""

    def test_case4_cash_synced_when_balance_changes(self):
        """Large change in cash → upsert to account_balances called."""
        # Stored balance = $8,000, new IBKR balance = $10,000 → $2,000 change → write
        supabase = make_supabase_mock(portfolio=[], cash_balance=8_000.0)
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_available_cash", return_value=10_000.0):
            execution_agent.reconcile_with_ibkr(ib)

        # Either upsert was called (change ≥ $1) OR it's a first-write scenario
        # — both are valid "sync" outcomes. Check Supabase was touched.
        upsert_called = supabase.table("account_balances").upsert.called
        assert upsert_called, (
            "Expected account_balances to be updated with snapshots"
        )

    def test_case4_cash_sync_writes_daily_snapshots(self):
        """New logic: write daily snapshots for cash, positions_value, total_value."""
        supabase = make_supabase_mock(portfolio=[], cash_balance=10_000.00)
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_available_cash", return_value=10_000.50):
            execution_agent.reconcile_with_ibkr(ib)

        assert supabase.table("account_balances").upsert.call_count >= 3
        
    def test_case4_detects_deposits(self):
        """A cash jump > $500 inserts into cash_flows."""
        supabase = make_supabase_mock(portfolio=[], cash_balance=10_000.00)
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_available_cash", return_value=11_000.00):
            execution_agent.reconcile_with_ibkr(ib)

        supabase.table("cash_flows").insert.assert_called_once()
        insert_args = supabase.table("cash_flows").insert.call_args[0][0]
        assert insert_args["amount"] == 1000.0
        assert insert_args["description"] == "Auto-detected Deposit"


class TestReconcileUsesPortfolioNotPositions:
    """
    Critical: reconcile_with_ibkr() must use ib.portfolio() everywhere.
    ib.positions() is a subscription-based call that may return empty, causing
    false "in sync" and missed positions. (Bug #5)
    """

    def test_reconcile_calls_ib_portfolio_not_ib_positions(self):
        """
        The reconcile function must ONLY call ib.portfolio(), never ib.positions().
        """
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)

        _reconcile(ib, supabase)

        ib.portfolio.assert_called()
        ib.positions.assert_not_called()
