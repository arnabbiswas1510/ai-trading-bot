"""
An unreachable database must not be reported as an empty portfolio.

On 2026-09-06 a DietPi reboot left every Docker container without an external
DNS resolver. The backend could not reach Supabase, and because get_positions()
and get_trade_history() swallowed the exception and returned [], the dashboard
rendered a pristine $100,000 / 0 positions / 0 trades slate -- visually
identical to a liquidated account, with no error anywhere on screen. Had the
market been open there would have been no signal that the bot was blind.

These tests pin the corrected contract: a failure to READ is raised, never
returned as data. The distinction between "the broker holds nothing" and "we
could not find out" is the whole point.

See decisions/2026-09-06_fail-loudly-on-unreachable-database.md.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# backend.main is deliberately NOT imported: it pulls in FastAPI at module
# scope, which is absent from the root requirements.txt that CI installs, and
# doing so previously broke the Daily Screener at pytest collection time.
import database


class _Boom(Exception):
    """Stands in for the DNS / transport failures Supabase surfaces."""


@pytest.fixture
def unreachable_supabase(monkeypatch):
    def _raise():
        raise _Boom("[Errno -3] Temporary failure in name resolution")

    monkeypatch.setattr(database, "get_supabase_client", _raise)


def test_get_positions_raises_rather_than_returning_empty(unreachable_supabase):
    with pytest.raises(database.DataSourceUnavailable):
        database.get_positions()


def test_get_trade_history_raises_rather_than_returning_empty(unreachable_supabase):
    with pytest.raises(database.DataSourceUnavailable):
        database.get_trade_history()


def test_original_cause_is_preserved(unreachable_supabase):
    """The operator needs the underlying error to diagnose it -- here, DNS."""
    with pytest.raises(database.DataSourceUnavailable) as exc:
        database.get_positions()
    assert isinstance(exc.value.__cause__, _Boom)
    assert "name resolution" in str(exc.value)


def test_unavailable_is_not_confusable_with_a_normal_empty_result():
    """
    An empty database is legitimate and must still return [] -- the fix must
    not turn "no open positions" into an error.
    """

    class _EmptyResult:
        data = []

    class _Query:
        def select(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def execute(self):
            return _EmptyResult()

    class _Client:
        def table(self, *a, **k):
            return _Query()

    import unittest.mock as mock

    with mock.patch.object(database, "get_supabase_client", lambda: _Client()):
        assert database.get_positions() == []
        assert database.get_trade_history() == []
