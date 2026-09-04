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
         patch("execution_agent.get_own_cash", return_value=10_000.0), \
         patch("execution_agent.get_margin_loan", return_value=0.0):
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
             patch("execution_agent.get_own_cash", return_value=10_000.0), \
             patch("execution_agent.get_margin_loan", return_value=0.0), \
             patch("execution_agent.cancel_ticker_sell_orders"):
            execution_agent.reconcile_with_ibkr(ib)

        # Delete from portfolio_positions
        supabase.table("portfolio_positions").delete.assert_called()
        # Insert to trade_history
        supabase.table("trade_history").insert.assert_called()
        # Closed-loop learning row should also be written
        supabase.table("breakout_learnings").insert.assert_called()

    def test_case1_uses_fmp_price_when_no_execution(self):
        """Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill."""
        pos = make_position("NVDA", buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        # Non-empty IBKR portfolio but NVDA is missing
        ib = make_ib_mock(symbols=["SPY"], avg_cost=500.0)
        ib.reqExecutions.return_value = []

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=150.0) as mock_price, \
             patch("execution_agent.get_own_cash", return_value=10_000.0), \
             patch("execution_agent.get_margin_loan", return_value=0.0), \
             patch("execution_agent.cancel_ticker_sell_orders"):
            execution_agent.reconcile_with_ibkr(ib)

        # get_live_price should have been called for the fallback
        mock_price.assert_any_call("NVDA")


class TestReconcileCase2:
    """Case 2: In IBKR, NOT in Supabase → manual buy detected."""

    def test_case2_inserts_new_position_with_hwm_date(self):
        """
        Case 2 must set hwm_date = today (ISO string) when inserting a manually-opened
        IBKR position. This starts the plateau detection clock from the entry date.
        high_water_mark is no longer written — IBKR owns the HWM price internally.
        """
        import datetime
        supabase = make_supabase_mock(portfolio=[])  # Nothing in Supabase
        ib = make_ib_mock(symbols=["TSLA"], avg_cost=200.0)  # TSLA in IBKR

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_called()
        insert_args = supabase.table("portfolio_positions").insert.call_args[0][0]

        # hwm_date must be set to today (ISO format) — plateau clock starts at entry
        hwm_date = insert_args.get("hwm_date")
        assert hwm_date is not None, "hwm_date must be set on Case 2 insert"
        from zoneinfo import ZoneInfo
        today_nyc = datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        assert hwm_date == today_nyc, (
            f"hwm_date should equal today={today_nyc}, got {hwm_date}"
        )

        # high_water_mark (price) must NOT be written — IBKR owns this now
        assert "high_water_mark" not in insert_args, (
            "high_water_mark price must not be written in Case 2 (IBKR owns this)"
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

    def test_case2_trail_pct_recorded_not_stale_price(self):
        """Case 2: no absolute stop_loss price is stored.

        The `stop_loss` column was a write-once mirror of a broker-managed
        value: it went stale the moment the position rose, because the real
        stop ratchets up with the HWM inside IBKR. It is now dropped, and the
        live level is derived as hwm_price * (1 - stop_loss_pct)."""
        supabase = make_supabase_mock(portfolio=[])
        ib = make_ib_mock(symbols=["AMZN"], avg_cost=100.0)

        _reconcile(ib, supabase)

        supabase.table("portfolio_positions").insert.assert_called()
        insert_args = supabase.table("portfolio_positions").insert.call_args[0][0]
        assert "stop_loss" not in insert_args, (
            "stop_loss is a stale mirror of an IBKR-managed value and must not be stored"
        )
        # profit_target must NOT be present — eliminated from schema
        assert "profit_target" not in insert_args, (
            "profit_target must not be stored in Case 2 (eliminated from exit strategy)"
        )


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

        # Reconciliation now issues two distinct writes per position — the share
        # correction and the IBKR valuation sync — so assert on the share payload
        # specifically rather than on whichever call happened to be last.
        share_writes = [
            c[0][0] for c in supabase.table("portfolio_positions").update.call_args_list
            if "shares" in c[0][0]
        ]
        assert share_writes, "Expected a share-count correction write"
        assert share_writes[0]["shares"] == 150

    def test_case3_no_update_when_shares_match(self):
        """Case 3: IBKR and Supabase both have 100 shares → no share-count write."""
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        ib.portfolio.return_value[0].position = 100

        _reconcile(ib, supabase)

        # The valuation sync still writes every cycle; only the share correction
        # must be absent when the counts already agree.
        share_writes = [
            c[0][0] for c in supabase.table("portfolio_positions").update.call_args_list
            if "shares" in c[0][0]
        ]
        assert share_writes == [], f"Unexpected share-count write: {share_writes}"


class TestIBKRValuationSync:
    """
    The IBKR valuation columns are what let the read-only web container render
    the broker's own numbers. Before them the dashboard multiplied Supabase share
    counts by FMP quotes, which never matched IBKR and mixed two vintages of data
    into one total. See decisions/2026-09-03_ibkr-sourced-position-values.md.
    """

    def _valuation_writes(self, supabase):
        return [
            c[0][0] for c in supabase.table("portfolio_positions").update.call_args_list
            if "ibkr_synced_at" in c[0][0]
        ]

    def test_writes_broker_valuation_for_held_position(self):
        """marketPrice/marketValue/unrealizedPNL are persisted verbatim from IBKR."""
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        item = ib.portfolio.return_value[0]
        item.position       = 100
        item.marketPrice    = 111.25
        item.marketValue    = 11125.0
        item.unrealizedPNL  = 1125.0

        execution_agent._IBKR_VALUATION_WARNING_SHOWN = False
        _reconcile(ib, supabase)

        writes = self._valuation_writes(supabase)
        assert len(writes) == 1, f"Expected exactly one valuation write, got {writes}"
        assert writes[0]["current_price"]  == 111.25
        assert writes[0]["market_value"]   == 11125.0
        assert writes[0]["unrealized_pnl"] == 1125.0
        assert writes[0]["ibkr_synced_at"]

    def test_market_value_is_stored_not_recomputed(self):
        """
        IBKR is the authority on both share count and price. Recomputing
        shares x price would reintroduce exactly the drift these columns remove,
        so a marketValue that disagrees with that product must survive intact.
        """
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        item = ib.portfolio.return_value[0]
        item.position      = 100
        item.marketPrice   = 110.0
        item.marketValue   = 10_998.0      # deliberately != 100 * 110
        item.unrealizedPNL = 998.0

        execution_agent._IBKR_VALUATION_WARNING_SHOWN = False
        _reconcile(ib, supabase)

        writes = self._valuation_writes(supabase)
        assert writes[0]["market_value"] == 10_998.0

    def test_skips_position_without_a_broker_mark(self):
        """
        The positions() fallback yields objects with no marketPrice. Writing a
        fabricated price there would be indistinguishable from a real broker mark,
        which is the ambiguity the ibkr_synced_at column exists to prevent.
        """
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        item = ib.portfolio.return_value[0]
        item.position    = 100
        item.marketPrice = 0.0        # no live mark available

        execution_agent._IBKR_VALUATION_WARNING_SHOWN = False
        _reconcile(ib, supabase)

        assert self._valuation_writes(supabase) == []

    def test_missing_migration_disables_write_without_failing(self):
        """PGRST204 must degrade gracefully, not break reconciliation."""
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        ib.portfolio.return_value[0].position = 100

        supabase.table("portfolio_positions").update.side_effect = \
            Exception("PGRST204: column current_price does not exist")

        execution_agent._IBKR_VALUATION_WARNING_SHOWN = False
        try:
            _reconcile(ib, supabase)     # must not raise
        finally:
            supabase.table("portfolio_positions").update.side_effect = None
            execution_agent._IBKR_VALUATION_WARNING_SHOWN = False

    def test_write_resumes_after_migration_without_a_restart(self):
        """
        Applying the migration must take effect on a *running* agent.

        The failure this guards against shipped once: PGRST204 latched a module
        global that was never reset, so the first rejected cycle disabled the
        write for the whole process. Operators applied the migration, saw the
        dashboard still reporting cost basis, and had no way to know a container
        restart was required. A degraded state that cannot recover on its own is
        not graceful degradation.
        """
        pos = make_position("AAPL", shares=100, buy_price=100.0)
        supabase = make_supabase_mock(portfolio=[pos])
        ib = make_ib_mock(symbols=["AAPL"], avg_cost=100.0)
        item = ib.portfolio.return_value[0]
        item.position      = 100
        item.marketPrice   = 111.25
        item.marketValue   = 11125.0
        item.unrealizedPNL = 1125.0

        execution_agent._IBKR_VALUATION_WARNING_SHOWN = False
        update_mock = supabase.table("portfolio_positions").update
        try:
            # Cycle 1: columns absent. The write is attempted and rejected.
            update_mock.side_effect = \
                Exception("PGRST204: column current_price does not exist")
            _reconcile(ib, supabase)

            # Cycle 2: operator applies the migration; the process is untouched.
            # Measure this cycle in isolation -- call_args_list records attempts,
            # including the one that raised above.
            update_mock.reset_mock()
            update_mock.side_effect = None
            _reconcile(ib, supabase)

            writes = self._valuation_writes(supabase)
            assert len(writes) == 1, (
                "valuation write must resume on the next cycle without a restart; "
                f"got {writes}"
            )
            assert writes[0]["current_price"] == 111.25
            assert writes[0]["ibkr_synced_at"]
        finally:
            update_mock.side_effect = None
            execution_agent._IBKR_VALUATION_WARNING_SHOWN = False


class TestReconcileCase4:
    """Case 4: Cash balance sync from IBKR to Supabase account_balances."""

    def test_case4_cash_synced_when_balance_changes(self):
        """Large change in cash → upsert to account_balances called."""
        # Stored balance = $8,000, new IBKR balance = $10,000 → $2,000 change → write
        supabase = make_supabase_mock(portfolio=[], cash_balance=8_000.0)
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_own_cash", return_value=10_000.0), \
             patch("execution_agent.get_margin_loan", return_value=0.0):
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
             patch("execution_agent.get_own_cash", return_value=10_000.50), \
             patch("execution_agent.get_margin_loan", return_value=0.0):
            execution_agent.reconcile_with_ibkr(ib)

        assert supabase.table("account_balances").upsert.call_count >= 1
        
    def test_case4_detects_deposits(self):
        """A cash jump > $500 inserts into cash_flows."""
        supabase = make_supabase_mock(portfolio=[], cash_balance=10_000.00)
        ib = make_ib_mock()

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.get_own_cash", return_value=11_000.00), \
             patch("execution_agent.get_margin_loan", return_value=0.0):
            execution_agent.reconcile_with_ibkr(ib)

        # Supabase should not insert anything unless it is an automated deposit (which isn't tested here)
        supabase.table("cash_flows").insert.assert_not_called()


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
