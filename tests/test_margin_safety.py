"""
test_margin_safety.py — Tests for the margin-cash safety layer.

Covers two critical invariants introduced on 2026-07-21 after the TRV
over-buy incident (bot deployed ~$60K using ~$35K of borrowed IBKR margin):

  Test Suite 1 — get_own_cash() / get_margin_loan() differentiation:
    Verify that own deposited cash (TotalCashValue > 0) is correctly
    distinguished from borrowed margin cash (TotalCashValue < 0).

  Test Suite 2 — run_market_open_buys() hard block:
    Verify that NO buy orders are placed when a margin loan is active,
    even when AvailableFunds shows plenty of tradeable capacity.

Design notes:
  - All IBKR interaction goes through ib.accountValues(), which returns a
    list of AccountValue namedtuple-like objects with .tag, .value, .currency.
  - We build minimal fakes of AccountValue instead of the full ib_insync type
    so tests run without an IBKR connection.
  - The notifier is silenced globally by conftest._silence_notifier.
  - No Supabase / network calls are made.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent
from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_position, make_trigger,
)


# ── AccountValue fake ─────────────────────────────────────────────────────────

class _AV:
    """Minimal fake for ib_insync.AccountValue (tag / value / currency triple)."""
    __slots__ = ("tag", "value", "currency")

    def __init__(self, tag: str, value: str, currency: str = "USD"):
        self.tag      = tag
        self.value    = value
        self.currency = currency


def _make_ib_with_account_values(*account_values) -> MagicMock:
    """
    Return a make_ib_mock() whose ib.accountValues() returns the given list.
    All other IBKR interactions are stubbed out by make_ib_mock().
    """
    ib = make_ib_mock(symbols=[])
    ib.accountValues.return_value = list(account_values)
    return ib


# ═══════════════════════════════════════════════════════════════════════════════
# Test Suite 1 — Cash differentiation (get_own_cash / get_margin_loan)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOwnCashDifferentiation:
    """
    get_own_cash() must return only the account owner's deposited money
    (TotalCashValue ≥ 0).  It must NEVER return margin-borrowed cash.

    get_margin_loan() must return the absolute value of TotalCashValue when
    it is negative, and 0.0 when no loan is active.
    """

    # ── Positive / own money cases ────────────────────────────────────────────

    def test_positive_total_cash_value_is_returned_as_own_cash(self):
        """Standard case: $50K in own cash, no margin. get_own_cash returns 50K."""
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "50000.00"),
            _AV("AvailableFunds",  "75000.00"),   # IBKR adds margin headroom — must be IGNORED
            _AV("NetLiquidation", "150000.00"),
        )
        result = execution_agent.get_own_cash(ib)
        assert result == 50_000.00, (
            f"Expected 50000.00 (own TotalCashValue), got {result}. "
            "get_own_cash() must read TotalCashValue, NOT AvailableFunds."
        )

    def test_own_cash_is_not_inflated_by_available_funds(self):
        """
        AvailableFunds can be higher than TotalCashValue because IBKR adds
        margin lending capacity. get_own_cash must ignore AvailableFunds entirely.
        This was the root cause of the TRV over-buy incident.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "25000.00"),
            _AV("AvailableFunds",  "60000.00"),   # $35K of this is borrowed — must NOT be used
        )
        own  = execution_agent.get_own_cash(ib)
        avail_funds = 60_000.00  # what the old code would have returned

        assert own == 25_000.00, (
            "get_own_cash() returned the AvailableFunds value instead of "
            "TotalCashValue. This would allow borrowing borrowed margin money."
        )
        assert own < avail_funds, (
            "Sanity check: own_cash must be less than AvailableFunds "
            "when a margin loan is in play."
        )

    def test_no_margin_loan_returns_zero_for_get_margin_loan(self):
        """When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0."""
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "30000.00"),
        )
        assert execution_agent.get_margin_loan(ib) == 0.0, (
            "get_margin_loan() should return 0.0 when TotalCashValue is positive."
        )

    def test_own_cash_capped_at_net_liquidation(self):
        """
        Edge case: TotalCashValue cannot exceed NetLiquidation in a real account.
        If IBKR ever returns a value that violates this, we cap defensively.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "200000.00"),
            _AV("NetLiquidation",  "150000.00"),  # lower — cap must apply
        )
        result = execution_agent.get_own_cash(ib)
        assert result == 150_000.00, (
            "get_own_cash() must cap at NetLiquidation when TotalCashValue "
            f"exceeds it. Got {result}."
        )

    # ── Negative / margin loan cases ──────────────────────────────────────────

    def test_negative_total_cash_value_returns_zero_own_cash(self):
        """
        When TotalCashValue < 0, a margin loan is active.
        get_own_cash must return 0.0 — we have zero of our own cash free.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-1738.41"),    # account is in debt by $1,738
            _AV("AvailableFunds",  "45000.00"),   # IBKR still shows buying power — irrelevant
        )
        result = execution_agent.get_own_cash(ib)
        assert result == 0.0, (
            f"get_own_cash() should return 0.0 when TotalCashValue is negative "
            f"(margin loan active). Got {result}."
        )

    def test_negative_total_cash_correctly_measured_as_margin_loan(self):
        """
        get_margin_loan() must return the absolute value of a negative
        TotalCashValue — that is the amount borrowed from IBKR.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-1738.41"),
        )
        loan = execution_agent.get_margin_loan(ib)
        assert loan == pytest.approx(1738.41, abs=0.01), (
            f"get_margin_loan() should return 1738.41 (the borrowed amount). Got {loan}."
        )

    def test_large_margin_loan_fully_measured(self):
        """
        Stress case: large margin loan (like the TRV incident ~$35K borrowed).
        get_margin_loan must capture the full amount.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-35000.00"),
        )
        assert execution_agent.get_margin_loan(ib) == pytest.approx(35_000.00, abs=0.01)

    def test_get_own_cash_returns_zero_when_tag_missing(self):
        """
        If IBKR returns no TotalCashValue tag at all (e.g. data feed lag),
        get_own_cash() must return 0.0 safely — never raise.
        """
        ib = _make_ib_with_account_values(
            _AV("AvailableFunds", "50000.00"),   # only this tag present
        )
        result = execution_agent.get_own_cash(ib)
        assert result == 0.0, (
            "get_own_cash() must return 0.0 gracefully when TotalCashValue tag "
            "is absent, not raise an exception."
        )

    def test_non_usd_currencies_are_ignored(self):
        """
        IBKR returns separate AccountValue rows per currency.
        Only the USD row should count; EUR/GBP rows must be ignored.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-5000.00", currency="EUR"),  # EUR margin — must be ignored
            _AV("TotalCashValue",  "30000.00", currency="USD"),  # USD own cash — this is the one
        )
        own  = execution_agent.get_own_cash(ib)
        loan = execution_agent.get_margin_loan(ib)

        assert own  == 30_000.00, "USD own cash must be 30K; EUR row must be ignored."
        assert loan == 0.0,       "No USD margin loan exists; EUR row must be ignored."

    def test_deprecated_get_available_cash_delegates_to_get_own_cash(self):
        """
        get_available_cash() is the old API. After the fix, it must delegate
        to get_own_cash() — not read AvailableFunds — so old call sites
        automatically get the margin-safe value without code changes.
        """
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "42000.00"),
            _AV("AvailableFunds",  "80000.00"),  # higher due to margin headroom
        )
        # Both functions must return the same value (TotalCashValue)
        assert execution_agent.get_available_cash(ib) == execution_agent.get_own_cash(ib), (
            "get_available_cash() must delegate to get_own_cash() after the fix. "
            "It should NOT return AvailableFunds."
        )
        assert execution_agent.get_available_cash(ib) == 42_000.00


# ═══════════════════════════════════════════════════════════════════════════════
# Test Suite 2 — Hard block: no trades with borrowed money
# ═══════════════════════════════════════════════════════════════════════════════

def _run_buys_with_margin_scenario(ib, supabase_mock, live_price=105.0):
    """
    Runs run_market_open_buys() with standard patches.
    Does NOT patch get_own_cash or get_margin_loan — those read from
    ib.accountValues() which we control via the ib mock.
    Patches everything else so the only variable is the margin/cash state.
    """
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.is_market_bullish", return_value=True), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return ib


class TestNoBuysWithBorrowedMoney:
    """
    run_market_open_buys() must NEVER place an order when TotalCashValue < 0,
    regardless of what IBKR's AvailableFunds reports.
    """

    def test_margin_loan_blocks_all_buys_completely(self):
        """
        Core regression test for the TRV incident.

        Scenario:
          - TotalCashValue = -$1,738  (margin loan active — account in debt)
          - AvailableFunds = $45,000  (IBKR still shows buying power)
          - 1 open position, 3 free slots → buys would normally be triggered

        Expected: NO order placed. The margin loan gate must fire first.
        """
        portfolio  = [make_position("TRV", shares=68, buy_price=365.36)]
        trigger    = make_trigger("AAPL", close_price=195.0)
        supabase   = make_supabase_mock(
            daily_triggers=[trigger],
            portfolio=portfolio,
        )

        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-1738.41"),   # margin loan — gate must fire
            _AV("AvailableFunds",  "45000.00"),  # plenty of IBKR-reported capacity
            _AV("NetLiquidation", "100000.00"),
        )
        ib.portfolio.return_value = []   # positions() fallback also empty for buy path

        _run_buys_with_margin_scenario(ib, supabase)

        ib.placeOrder.assert_not_called(), (
            "placeOrder() was called despite an active margin loan. "
            "The hard block in run_market_open_buys() failed."
        )

    def test_large_margin_loan_blocks_buys(self):
        """
        Stress case mimicking the original TRV incident: ~$35K margin loan
        from an over-sized buy.  Buys must remain blocked.
        """
        portfolio = [make_position("TRV", shares=165, buy_price=365.36)]
        supabase  = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA", close_price=140.0)],
            portfolio=portfolio,
        )
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-35000.00"),  # large margin loan
            _AV("AvailableFunds",  "60000.00"),
        )

        _run_buys_with_margin_scenario(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_zero_own_cash_blocks_buys(self):
        """
        Edge case: TotalCashValue is exactly $0.00 — nothing deposited is
        liquid. The MIN_POSITION_SIZE gate ($5K floor) will stop the buy,
        but the margin gate should fire first and be the reason.
        """
        portfolio = []  # empty portfolio — 4 slots available
        supabase  = make_supabase_mock(
            daily_triggers=[make_trigger("MSFT", close_price=420.0)],
            portfolio=portfolio,
        )
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "0.00"),
            _AV("AvailableFunds",  "50000.00"),
        )

        _run_buys_with_margin_scenario(ib, supabase)
        ib.placeOrder.assert_not_called()

    def test_positive_own_cash_allows_buy_when_all_other_gates_pass(self):
        """
        Positive control: when TotalCashValue is healthy (own money, no loan),
        a qualifying trigger DOES result in a buy order.
        This ensures the margin gate is not accidentally over-blocking.
        """
        portfolio = [make_position(t) for t in ["AAPL", "MSFT", "NVDA"]]  # 3 held, 1 free slot
        supabase  = make_supabase_mock(
            daily_triggers=[make_trigger("CRWD", close_price=100.0)],
            portfolio=portfolio,
        )
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "25000.00"),   # healthy own cash — no margin loan
            _AV("AvailableFunds",  "25000.00"),
            _AV("NetLiquidation", "125000.00"),
        )

        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=100.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.notifier"), \
             patch("execution_agent.execute_sell"), \
             patch("execution_agent.place_trailing_stop"):
            execution_agent.run_market_open_buys(ib)

        ib.placeOrder.assert_called_once(), (
            "placeOrder() was NOT called even though own cash is positive "
            "and all buy gates should pass. The margin guard is over-blocking."
        )

    def test_margin_loan_sends_telegram_alert(self):
        """
        When a margin loan is detected, the user must receive a Telegram alert.
        Verify notifier.notify_error() is called with a message mentioning
        the loan amount.
        """
        portfolio = [make_position("TRV")]
        supabase  = make_supabase_mock(
            daily_triggers=[make_trigger("NVDA")],
            portfolio=portfolio,
        )
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-5000.00"),
        )

        mock_notifier = MagicMock()
        with patch("execution_agent.supabase", supabase), \
             patch("execution_agent.get_live_price", return_value=105.0), \
             patch("execution_agent.is_market_bullish", return_value=True), \
             patch("execution_agent.notifier", mock_notifier), \
             patch("execution_agent.execute_sell"):
            execution_agent.run_market_open_buys(ib)

        mock_notifier.notify_error.assert_called_once()
        alert_msg = mock_notifier.notify_error.call_args[0][0]
        assert "5,000" in alert_msg or "5000" in alert_msg, (
            f"Telegram alert should mention the loan amount. Got: {alert_msg!r}"
        )
        assert "margin" in alert_msg.lower() or "loan" in alert_msg.lower(), (
            f"Telegram alert should mention 'margin' or 'loan'. Got: {alert_msg!r}"
        )

    def test_margin_loan_blocks_multiple_triggers(self):
        """
        Even when multiple high-scoring triggers are queued, none should
        result in orders when a margin loan is active.
        """
        portfolio = []   # 4 free slots — the bot would normally buy up to 4
        supabase  = make_supabase_mock(
            daily_triggers=[
                make_trigger("AAPL",  close_price=195.0, final_score=95),
                make_trigger("NVDA",  close_price=140.0, final_score=90),
                make_trigger("CRWD",  close_price=380.0, final_score=85),
                make_trigger("MELI",  close_price=210.0, final_score=80),
            ],
            portfolio=portfolio,
        )
        ib = _make_ib_with_account_values(
            _AV("TotalCashValue", "-2500.00"),  # even a small loan blocks everything
            _AV("AvailableFunds",  "80000.00"),
        )

        _run_buys_with_margin_scenario(ib, supabase)
        ib.placeOrder.assert_not_called(), (
            f"Expected 0 orders, got {ib.placeOrder.call_count}. "
            "Margin loan must block ALL triggers, not just some."
        )
