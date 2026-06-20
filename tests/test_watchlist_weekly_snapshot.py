"""
Tests for watchlist weekly-snapshot logic.

Guards the following invariants:
  1. _get_week_start() always returns Monday 00:00 UTC regardless of input day.
  2. save_screener_results() deletes the CURRENT week only, inserts fresh rows,
     and prunes rows older than 56 days — it never touches other weeks' data.
  3. get_screener_results() reads the current week vs previous week and correctly
     computes NEW / RETAINED / REMOVED change statuses.
  4. update_supabase_watchlist() (fundamental_screener) uses an upsert model:
       - SELECT existing rows for incoming tickers to distinguish new vs retained
       - INSERT brand-new tickers with weeks_retained=1
       - UPSERT retained tickers incrementing weeks_retained and refreshing scores
       - DELETE (prune) rows whose last_seen_at is older than WATCHLIST_PRUNE_DAYS

All Supabase calls are fully mocked — no network required.
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Make backend/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday(year: int, month: int, day: int) -> datetime.datetime:
    """Convenience constructor for a Monday 00:00 UTC datetime."""
    dt = datetime.datetime(year, month, day, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert dt.weekday() == 0, f"{dt.date()} is not a Monday"
    return dt


def _row(ticker: str, created_at: str, score: float = 0.5) -> dict:
    """Minimal watchlist Supabase row."""
    return {
        "ticker": ticker,
        "created_at": created_at,
        "composite_score": score,
        "q_eps_growth": 0.25,
        "a_eps_growth": 0.15,
        "revenue_growth": 0.10,
        "inst_count": 8,
        "company_name": ticker,
    }


def _make_supabase_mock() -> MagicMock:
    """Return a mock Supabase client whose .table() chains return MagicMock."""
    client = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[])
    client.table.return_value.select.return_value = chain
    client.table.return_value.delete.return_value = chain
    client.table.return_value.insert.return_value = chain
    return client


# ---------------------------------------------------------------------------
# Tests for _get_week_start (database.py)
# ---------------------------------------------------------------------------

class TestGetWeekStart:
    """_get_week_start always returns the Monday 00:00:00 UTC of the same ISO week."""

    def test_monday_returns_itself(self):
        from database import _get_week_start
        dt = datetime.datetime(2026, 6, 15, 14, 30, tzinfo=datetime.timezone.utc)  # Monday
        result = _get_week_start(dt)
        assert result.weekday() == 0
        assert result.date() == datetime.date(2026, 6, 15)
        assert result.hour == result.minute == result.second == 0

    def test_wednesday_returns_previous_monday(self):
        from database import _get_week_start
        dt = datetime.datetime(2026, 6, 17, 9, 0, tzinfo=datetime.timezone.utc)  # Wednesday
        result = _get_week_start(dt)
        assert result.date() == datetime.date(2026, 6, 15)

    def test_sunday_returns_monday_of_same_week(self):
        from database import _get_week_start
        dt = datetime.datetime(2026, 6, 21, 23, 59, tzinfo=datetime.timezone.utc)  # Sunday
        result = _get_week_start(dt)
        assert result.date() == datetime.date(2026, 6, 15)

    def test_friday_midnight_boundary(self):
        from database import _get_week_start
        dt = datetime.datetime(2026, 6, 19, 0, 0, tzinfo=datetime.timezone.utc)  # Friday
        result = _get_week_start(dt)
        assert result.date() == datetime.date(2026, 6, 15)

    def test_result_is_always_utc(self):
        from database import _get_week_start
        dt = datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc)
        result = _get_week_start(dt)
        assert result.tzinfo == datetime.timezone.utc


# ---------------------------------------------------------------------------
# Tests for save_screener_results (database.py)
# ---------------------------------------------------------------------------

class TestSaveScreenerResults:
    """save_screener_results must replace only the current week's rows."""

    def _make_results(self, tickers=("AAPL", "MSFT")) -> list[dict]:
        return [
            {
                "ticker": t,
                "total_score": 80,
                "details": {
                    "company_name": t,
                    "c_growth_yoy": 25.0,
                    "a_eps_growth_cagr": 15.0,
                    "c_rev_growth_yoy": 10.0,
                    "i_held_percent_inst": 75.0,
                },
            }
            for t in tickers
        ]

    @patch("database.get_supabase_client")
    def test_deletes_current_week_before_insert(self, mock_get_client):
        """The delete call must use gte(week_start) and lt(week_end)."""
        import database

        client = _make_supabase_mock()
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, 10, 0, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            database.save_screener_results(self._make_results())

        table_mock = client.table.return_value
        delete_chain = table_mock.delete.return_value

        delete_chain.gte.assert_called_once()
        gte_args = delete_chain.gte.call_args[0]
        assert gte_args[0] == "created_at"
        assert "2026-06-15" in gte_args[1]

        delete_chain.gte.return_value.lt.assert_called_once()
        lt_args = delete_chain.gte.return_value.lt.call_args[0]
        assert "2026-06-22" in lt_args[1]

    @patch("database.get_supabase_client")
    def test_insert_called_after_delete(self, mock_get_client):
        """insert() must be called after delete() — ordering matters."""
        import database

        client = _make_supabase_mock()
        mock_get_client.return_value = client
        call_order = []

        client.table.return_value.delete.return_value.gte.return_value \
            .lt.return_value.execute.side_effect = lambda: call_order.append("delete")
        client.table.return_value.insert.return_value.execute.side_effect = \
            lambda: call_order.append("insert")

        database.save_screener_results(self._make_results())

        assert call_order.index("delete") < call_order.index("insert")

    @patch("database.get_supabase_client")
    def test_prunes_rows_older_than_56_days(self, mock_get_client):
        """After insert, rows older than 56 days must be pruned."""
        import database

        client = _make_supabase_mock()
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, 12, 0, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            database.save_screener_results(self._make_results())

        assert client.table.return_value.delete.call_count >= 2

    @patch("database.get_supabase_client")
    def test_does_not_delete_previous_weeks_data(self, mock_get_client):
        """The current-week delete must NOT use neq() which would wipe all rows."""
        import database

        client = _make_supabase_mock()
        mock_get_client.return_value = client
        database.save_screener_results(self._make_results())

        delete_chain = client.table.return_value.delete.return_value
        assert not delete_chain.neq.called

    @patch("database.get_supabase_client")
    def test_empty_results_skips_insert(self, mock_get_client):
        """Empty screener results must not insert or delete anything."""
        import database

        client = _make_supabase_mock()
        mock_get_client.return_value = client
        database.save_screener_results([])

        client.table.return_value.insert.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for get_screener_results (database.py)
# ---------------------------------------------------------------------------

class TestGetScreenerResults:
    """get_screener_results must compute NEW/RETAINED/REMOVED correctly by ISO week."""

    def _setup_client(self, curr_tickers: list[str], prev_tickers: list[str],
                      curr_week_start: str, prev_week_start: str) -> MagicMock:
        client = MagicMock()

        curr_rows = [_row(t, curr_week_start + "T10:00:00+00:00") for t in curr_tickers]
        prev_rows = [{"ticker": t, "created_at": prev_week_start + "T10:00:00+00:00"}
                     for t in prev_tickers]

        def table_side_effect(table_name):
            table = MagicMock()

            def select_fn(cols):
                chain = MagicMock()

                def gte_fn(col, val):
                    inner = MagicMock()

                    def execute_fn():
                        if curr_week_start in val:
                            return MagicMock(data=curr_rows if cols == "*" else
                                            [{"ticker": r["ticker"]} for r in curr_rows])
                        return MagicMock(data=prev_rows if cols == "ticker" else [])

                    inner.execute = execute_fn
                    inner.lt = MagicMock(return_value=MagicMock(
                        execute=lambda: MagicMock(data=prev_rows if cols == "ticker" else [])
                    ))
                    return inner

                chain.gte = gte_fn
                chain.order = MagicMock(return_value=MagicMock(
                    limit=MagicMock(return_value=MagicMock(
                        execute=lambda: MagicMock(data=curr_rows)
                    ))
                ))
                chain.execute = lambda: MagicMock(data=curr_rows)
                return chain

            table.select = select_fn
            table.delete = MagicMock(return_value=MagicMock(
                gte=MagicMock(return_value=MagicMock(
                    lt=MagicMock(return_value=MagicMock(execute=lambda: None))
                )),
                lt=MagicMock(return_value=MagicMock(execute=lambda: None)),
            ))
            table.insert = MagicMock(return_value=MagicMock(execute=lambda: None))
            return table

        client.table.side_effect = table_side_effect
        return client

    @patch("database.get_supabase_client")
    def test_new_tickers_marked_as_new(self, mock_get_client):
        """Tickers in current week but absent last week must have change_status='NEW'."""
        import database

        curr = ["AAPL", "MSFT"]
        prev = ["MSFT"]
        client = self._setup_client(curr, prev, "2026-06-15", "2026-06-08")
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            result = database.get_screener_results()

        statuses = {r["ticker"]: r["change_status"] for r in result["watchlist"]}
        assert statuses.get("AAPL") == "NEW"
        assert statuses.get("MSFT") == "RETAINED"

    @patch("database.get_supabase_client")
    def test_removed_tickers_reported(self, mock_get_client):
        """Tickers in previous week but absent this week must appear in 'removed'."""
        import database

        curr = ["AAPL"]
        prev = ["AAPL", "NVDA", "TSLA"]
        client = self._setup_client(curr, prev, "2026-06-15", "2026-06-08")
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            result = database.get_screener_results()

        removed = set(result["removed"])
        assert "NVDA" in removed
        assert "TSLA" in removed
        assert "AAPL" not in removed

    @patch("database.get_supabase_client")
    def test_empty_current_week_returns_empty(self, mock_get_client):
        """No current-week data → watchlist and removed are both empty."""
        import database

        client = MagicMock()
        empty_result = MagicMock(data=[])

        def select_chain(*args, **kwargs):
            chain = MagicMock()
            chain.gte.return_value.execute.return_value = empty_result
            chain.gte.return_value.lt.return_value.execute.return_value = empty_result
            chain.order.return_value.limit.return_value.execute.return_value = empty_result
            chain.execute.return_value = empty_result
            return chain

        client.table.return_value.select.side_effect = select_chain
        mock_get_client.return_value = client

        result = database.get_screener_results()
        assert result == {"watchlist": [], "removed": []}

    @patch("database.get_supabase_client")
    def test_no_previous_week_all_retained(self, mock_get_client):
        """First-ever run (no previous week data) → all tickers are RETAINED, none NEW."""
        import database

        curr = ["AAPL", "MSFT"]
        client = self._setup_client(curr, [], "2026-06-15", "2026-06-08")
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            result = database.get_screener_results()

        statuses = {r["ticker"]: r["change_status"] for r in result["watchlist"]}
        for s in statuses.values():
            assert s == "RETAINED"
        assert result["removed"] == []

    @patch("database.get_supabase_client")
    def test_results_sorted_by_score_descending(self, mock_get_client):
        """Watchlist must be returned in descending composite_score order."""
        import database

        client = MagicMock()
        curr_rows = [
            _row("LOW_SCORE", "2026-06-15T10:00:00+00:00", score=0.2),
            _row("HIGH_SCORE", "2026-06-15T10:00:00+00:00", score=0.9),
            _row("MID_SCORE",  "2026-06-15T10:00:00+00:00", score=0.5),
        ]
        empty = MagicMock(data=[])

        def select_chain(cols):
            chain = MagicMock()
            chain.gte.return_value.execute.return_value = MagicMock(data=curr_rows if cols == "*" else [])
            chain.gte.return_value.lt.return_value.execute.return_value = empty
            chain.order.return_value.limit.return_value.execute.return_value = empty
            chain.execute.return_value = MagicMock(data=curr_rows if cols == "*" else [])
            return chain

        client.table.return_value.select.side_effect = select_chain
        mock_get_client.return_value = client

        fixed_now = datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc)
        with patch("database.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            result = database.get_screener_results()

        tickers = [r["ticker"] for r in result["watchlist"]]
        assert tickers[0] == "HIGH_SCORE"
        assert tickers[-1] == "LOW_SCORE"


# ---------------------------------------------------------------------------
# Tests for update_supabase_watchlist (fundamental_screener.py)
# Updated for upsert model: SELECT existing → INSERT new → UPSERT retained → prune
# ---------------------------------------------------------------------------

class TestUpdateSupabaseWatchlistWeekly:
    """Guards the fundamental screener's upsert-based watchlist behaviour.

    Contract:
      - SELECT existing rows by ticker to identify new vs retained
      - INSERT brand-new tickers with weeks_retained=1
      - UPSERT retained tickers (increment weeks_retained, refresh scores)
      - DELETE (prune) rows with last_seen_at older than WATCHLIST_PRUNE_DAYS
    """

    def _make_candidates(self, tickers=("AAPL", "NVDA")) -> list[dict]:
        return [{"ticker": t, "company_name": t, "composite_score": 0.8,
                 "q_eps_growth": 0.3, "a_eps_growth": 0.2,
                 "revenue_growth": 0.15, "inst_count": 9}
                for t in tickers]

    def _make_mock_with_existing(self, existing_tickers: list[str]) -> MagicMock:
        """Return a Supabase mock where the given tickers already exist in the DB."""
        client = MagicMock()
        existing_rows = [
            {"ticker": t, "weeks_retained": 2, "first_seen_at": "2026-06-07T00:00:00+00:00"}
            for t in existing_tickers
        ]
        # SELECT ... .in_(...) → existing rows
        client.table.return_value.select.return_value \
            .in_.return_value.execute.return_value.data = existing_rows
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()
        client.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        client.table.return_value.delete.return_value.lt.return_value \
            .execute.return_value = MagicMock()
        return client

    @patch("fundamental_screener.get_supabase_client")
    def test_new_tickers_are_inserted_not_upserted(self, mock_get_client):
        """Tickers absent from DB must go through insert(), not upsert()."""
        import fundamental_screener

        client = self._make_mock_with_existing([])
        mock_get_client.return_value = client

        fundamental_screener.update_supabase_watchlist(
            self._make_candidates(("AAPL", "NVDA"))
        )

        client.table.return_value.insert.assert_called_once()
        inserted = client.table.return_value.insert.call_args[0][0]
        assert {r["ticker"] for r in inserted} == {"AAPL", "NVDA"}

    @patch("fundamental_screener.get_supabase_client")
    def test_new_tickers_get_weeks_retained_one(self, mock_get_client):
        """Newly inserted tickers must start with weeks_retained=1."""
        import fundamental_screener

        client = self._make_mock_with_existing([])
        mock_get_client.return_value = client

        fundamental_screener.update_supabase_watchlist(self._make_candidates(("TSLA",)))

        inserted = client.table.return_value.insert.call_args[0][0]
        tsla = next(r for r in inserted if r["ticker"] == "TSLA")
        assert tsla["weeks_retained"] == 1

    @patch("fundamental_screener.get_supabase_client")
    def test_retained_tickers_are_upserted_with_incremented_count(self, mock_get_client):
        """Tickers already in DB (weeks_retained=2) must be upserted with weeks_retained=3."""
        import fundamental_screener

        client = self._make_mock_with_existing(["AAPL"])
        mock_get_client.return_value = client

        fundamental_screener.update_supabase_watchlist(self._make_candidates(("AAPL",)))

        client.table.return_value.upsert.assert_called_once()
        upserted = client.table.return_value.upsert.call_args[0][0]
        aapl = next(r for r in upserted if r["ticker"] == "AAPL")
        assert aapl["weeks_retained"] == 3  # was 2, incremented to 3

    @patch("fundamental_screener.get_supabase_client")
    def test_mixed_new_and_retained_routed_correctly(self, mock_get_client):
        """AAPL retained → upsert; NVDA new → insert. Routing must be correct."""
        import fundamental_screener

        client = self._make_mock_with_existing(["AAPL"])
        mock_get_client.return_value = client

        fundamental_screener.update_supabase_watchlist(
            self._make_candidates(("AAPL", "NVDA"))
        )

        client.table.return_value.insert.assert_called_once()
        inserted = client.table.return_value.insert.call_args[0][0]
        assert any(r["ticker"] == "NVDA" for r in inserted)
        assert not any(r["ticker"] == "AAPL" for r in inserted)

        client.table.return_value.upsert.assert_called_once()
        upserted = client.table.return_value.upsert.call_args[0][0]
        assert any(r["ticker"] == "AAPL" for r in upserted)
        assert not any(r["ticker"] == "NVDA" for r in upserted)

    @patch("fundamental_screener.get_supabase_client")
    def test_prune_uses_last_seen_at_not_created_at(self, mock_get_client):
        """Prune must delete by last_seen_at (not created_at) to respect upsert model."""
        import fundamental_screener

        client = self._make_mock_with_existing([])
        mock_get_client.return_value = client

        fundamental_screener.update_supabase_watchlist(self._make_candidates())

        delete_mock = client.table.return_value.delete.return_value
        delete_mock.lt.assert_called_once()
        lt_args = delete_mock.lt.call_args[0]
        assert lt_args[0] == "last_seen_at", (
            f"Prune must filter on last_seen_at (upsert model), got: {lt_args[0]}"
        )
