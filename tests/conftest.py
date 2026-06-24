from zoneinfo import ZoneInfo
"""
conftest.py — Shared pytest fixtures and test helpers for the trading bot test suite.

All fixtures here are available to every test file without import.
Key design decisions:
  - ib.portfolio() is always used (never ib.positions()) — Bug 5 compliance
  - PortfolioItem mock uses .averageCost (not .avgCost) — PortfolioItem API
  - Supabase mock uses side_effect per table name for clean isolation
"""

import datetime
import sys
import os
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, call

# Make the project root importable from tests/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── PortfolioItem mock ────────────────────────────────────────────────────────

def make_portfolio_item(symbol: str, position: int = 100,
                        avg_cost: float = 100.0, sec_type: str = "STK") -> MagicMock:
    """
    Mimics an ib_insync PortfolioItem.
    IMPORTANT: uses .averageCost (PortfolioItem) NOT .avgCost (Position).
    This distinction is Bug #5 — never revert.
    """
    item = MagicMock()
    item.contract.symbol = symbol
    item.contract.secType = sec_type
    item.position = position
    item.averageCost = avg_cost
    return item


def make_ib_mock(symbols: list | None = None, avg_cost: float = 100.0) -> MagicMock:
    """
    Creates a mock IB instance whose portfolio() always returns the given symbols.
    Includes stubs for placeOrder, qualifyContracts, sleep, accountValues, openTrades.
    """
    ib = MagicMock()
    items = [make_portfolio_item(s, avg_cost=avg_cost) for s in (symbols or [])]
    ib.portfolio.return_value = items
    ib.accountValues.return_value = []
    ib.sleep.return_value = None
    ib.qualifyContracts.return_value = None
    ib.placeOrder.return_value = MagicMock()
    ib.reqExecutions.return_value = []
    ib.openTrades.return_value = []   # No open SELL orders by default
    ib.cancelOrder.return_value = None
    return ib


# ── Supabase position / trigger factories ─────────────────────────────────────

def make_position(ticker: str,
                  buy_price: float = 100.0,
                  days_ago: int = 5,
                  buy_source: str = "daily_triggers",
                  high_water_mark: float | None = None,
                  shares: int = 100,
                  is_power_hold: bool = False,
                  power_hold_expiry: str | None = None,
                  stop_loss: float | None = None,
                  profit_target: float | None = None) -> dict:
    """Factory for a portfolio_positions Supabase row."""
    buy_date = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_ago)
    ).isoformat()
    return {
        "ticker": ticker,
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "buy_source": buy_source,
        "buy_reason": f"Test: {ticker}",
        "high_water_mark": high_water_mark if high_water_mark is not None else buy_price,
        "stop_loss": stop_loss if stop_loss is not None else round(buy_price * 0.93, 2),
        "profit_target": profit_target if profit_target is not None else round(buy_price * 1.25, 2),
        "is_power_hold": is_power_hold,
        "power_hold_expiry": power_hold_expiry,
    }


def make_trigger(ticker: str,
                 close_price: float = 100.0,
                 volume_surge: float = 1.5,
                 pivot_distance_pct: float = -0.5,
                 days_ago: int = 0) -> dict:
    """Factory for a daily_triggers / momentum_triggers Supabase row."""
    triggered_at = (
        datetime.datetime.now(ZoneInfo('America/New_York')).date() - datetime.timedelta(days=days_ago)
    ).isoformat()
    return {
        "ticker": ticker,
        "triggered_at": triggered_at,
        "close_price": close_price,
        "volume_surge": volume_surge,
        "pivot_distance_pct": pivot_distance_pct,
    }


# ── Supabase mock ─────────────────────────────────────────────────────────────

def make_supabase_mock(
    daily_triggers: list | None = None,
    momentum_triggers: list | None = None,
    portfolio: list | None = None,
    trade_history_recent: list | None = None,
    cash_balance: float | None = None,
) -> MagicMock:
    """
    Returns a MagicMock Supabase client where each table's queries return
    realistic data without leaking across tables.

    IMPORTANT: table mocks are CACHED — every call to client.table("X") returns
    the SAME mock object, allowing post-hoc assertions on .insert, .update, etc.

    Usage:
        client = make_supabase_mock(daily_triggers=[make_trigger("NVDA")],
                                    portfolio=[make_position("AAPL")])
        with patch("execution_agent.supabase", client): ...
    """
    daily_triggers = daily_triggers or []
    momentum_triggers = momentum_triggers or []
    portfolio = portfolio or []
    trade_history_recent = trade_history_recent or []

    etf_positions = [p for p in portfolio if p.get("buy_source") == "etf_parking"]
    stock_positions = [p for p in portfolio if p.get("buy_source") != "etf_parking"]

    # Cache: same table name → same mock object. Required for post-call assertions.
    _cache: dict[str, MagicMock] = {}

    def _table(name: str) -> MagicMock:
        if name in _cache:
            return _cache[name]

        t = MagicMock()

        if name == "daily_triggers":
            t.select.return_value.gte.return_value.execute.return_value.data = daily_triggers
            t.select.return_value.eq.return_value.execute.return_value.data = daily_triggers
            t.insert.return_value.execute.return_value = MagicMock()
            t.delete.return_value.lt.return_value.execute.return_value = MagicMock()

        elif name == "momentum_triggers":
            t.select.return_value.gte.return_value.execute.return_value.data = momentum_triggers
            t.insert.return_value.execute.return_value = MagicMock()
            t.delete.return_value.lt.return_value.execute.return_value = MagicMock()

        elif name == "portfolio_positions":
            # Default select (no filter): full portfolio
            t.select.return_value.execute.return_value.data = portfolio

            # .neq("buy_source", "etf_parking") → stock positions only
            t.select.return_value.neq.return_value.execute.return_value.data = stock_positions

            # .eq() dispatch: filter by buy_source correctly
            def _eq_side_effect(column, value):
                m = MagicMock()
                if column == "buy_source" and value == "etf_parking":
                    m.execute.return_value.data = etf_positions
                elif column == "buy_source":
                    # e.g. eq("buy_source", "daily_triggers") — return matching
                    m.execute.return_value.data = [
                        p for p in portfolio if p.get("buy_source") == value
                    ]
                else:
                    m.execute.return_value.data = portfolio
                # Support chaining: .eq().gte(), .eq().lt()
                m.gte.return_value.execute.return_value.data = portfolio
                m.lt.return_value.execute.return_value.data = portfolio
                return m

            t.select.return_value.eq.side_effect = _eq_side_effect

            t.insert.return_value.execute.return_value = MagicMock()
            t.update.return_value.eq.return_value.execute.return_value = MagicMock()
            t.delete.return_value.eq.return_value.execute.return_value = MagicMock()
            t.delete.return_value.lt.return_value.execute.return_value = MagicMock()

        elif name == "trade_history":
            def _th_eq(col, val):
                m = MagicMock()
                m.gte.return_value.execute.return_value.data = trade_history_recent
                m.execute.return_value.data = trade_history_recent
                return m
            t.select.return_value.eq.side_effect = _th_eq
            t.insert.return_value.execute.return_value = MagicMock()

        elif name == "account_balances":
            bal_data = [{"value": str(cash_balance)}] if cash_balance else []
            # We chain select().eq().order().limit().execute()
            m_select = MagicMock()
            m_eq = MagicMock()
            m_order = MagicMock()
            m_limit = MagicMock()
            m_limit.execute.return_value.data = bal_data
            m_order.limit.return_value = m_limit
            m_eq.order.return_value = m_order
            m_eq.execute.return_value.data = bal_data # in case order isn't used
            m_select.eq.return_value = m_eq
            t.select.return_value = m_select
            t.upsert.return_value.execute.return_value = MagicMock()

        elif name == "cash_flows":
            t.insert.return_value.execute.return_value = MagicMock()

        _cache[name] = t
        return t

    client = MagicMock()
    client.table.side_effect = _table
    return client


# ── OHLCV data factory for screener tests ─────────────────────────────────────

def make_ohlcv_data(n_days: int = 120,
                    base_price: float = 100.0,
                    base_volume: int = 1_000_000,
                    current_close: float | None = None,
                    current_volume: int | None = None,
                    rolling_high: float | None = None) -> list[dict]:
    """
    Generates synthetic OHLCV data (as a list of dicts, like FMP returns).
    Prices are stable around base_price, rolling high defaults to base_price * 1.01.
    Override last-day close/volume to trigger or suppress a breakout signal.
    """
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_days, freq="B")
    high_price = rolling_high if rolling_high is not None else base_price * 1.01

    closes  = [base_price] * n_days
    highs   = [high_price] * n_days
    volumes = [base_volume] * n_days

    if current_close is not None:
        closes[-1] = current_close
    if current_volume is not None:
        volumes[-1] = current_volume

    records = []
    for i, d in enumerate(dates):
        records.append({
            "date":   d.strftime("%Y-%m-%d"),
            "open":   closes[i] * 0.99,
            "high":   highs[i],
            "low":    closes[i] * 0.97,
            "close":  closes[i],
            "volume": volumes[i],
        })
    return records


# ── pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_ib():
    """Default IB mock with no open positions."""
    return make_ib_mock(symbols=[])


@pytest.fixture
def mock_supabase_empty():
    """Supabase mock with no data in any table."""
    return make_supabase_mock()


# ── CRITICAL: Silence real Telegram for all tests ─────────────────────────────
# execution_agent.notifier is a module-level TelegramNotifier singleton that
# initialises with live credentials when the module is imported on a server.
# Without this fixture, any test that exercises a successful buy/sell path
# will fire REAL Telegram messages to the user's phone.
#
# This fixture is autouse=True (applies to every test) and session-scoped
# (mocked once for the entire pytest run — efficient and always safe).
@pytest.fixture(autouse=True, scope="session")
def _silence_notifier():
    """Auto-mock execution_agent.notifier for every test in the suite."""
    with patch("execution_agent.notifier"):
        yield
