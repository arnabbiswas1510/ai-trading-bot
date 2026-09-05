"""
Price-source resolution for dashboard position values.

The dashboard cannot reach the brokerage (the web container has no IBKR access
by design), so it renders whatever reconcile_with_ibkr() last persisted onto
portfolio_positions. When those columns are empty it has to decide what to show
instead, and -- more importantly -- has to say which source it used.

These tests pin the precedence and the labelling. The labelling is the point:
an unlabelled FMP price sitting next to a broker-sourced one is exactly the
ambiguity that motivated sourcing position values from IBKR in the first place.
See decisions/2026-09-03_ibkr-sourced-position-values.md.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Import from backend.pricing, NOT backend.main. main.py imports FastAPI at
# module scope, and FastAPI ships only in backend/requirements.txt (the
# trading-bot image) -- not in the root requirements.txt that CI installs. An
# earlier version of this file imported backend.main and broke the Daily
# Screener workflow at pytest collection, which runs before the screener steps.
from pricing import resolve_position_price


def make_pos(**kw):
    base = {
        "ticker": "AAPL",
        "shares": 100,
        "buy_price": 100.0,
        "current_price": None,
        "ibkr_synced_at": None,
    }
    base.update(kw)
    return base


class TestIBKRWins:
    def test_ibkr_mark_is_preferred_over_a_live_fmp_quote(self):
        """
        IBKR is what orders fill against. Preferring a fresher third-party quote
        is what caused fill-vs-decision mismatches, so recency does not win here.
        """
        pos = make_pos(current_price=111.25, ibkr_synced_at="2026-09-04T15:47:00-04:00")
        assert resolve_position_price(pos, 999.0) == (111.25, "IBKR")

    def test_stale_ibkr_mark_still_beats_fmp(self):
        """A stale broker price is labelled with its timestamp and is recoverable."""
        pos = make_pos(current_price=90.0, ibkr_synced_at="2026-01-02T15:47:00-05:00")
        assert resolve_position_price(pos, 105.0) == (90.0, "IBKR")


class TestAttributionRequiresBothColumns:
    """
    A price and its sync timestamp are written together. Half a pair means the
    number cannot be attributed to the broker, so it must not be labelled IBKR.
    """

    def test_price_without_timestamp_is_not_treated_as_a_broker_mark(self):
        pos = make_pos(current_price=111.25, ibkr_synced_at=None)
        price, source = resolve_position_price(pos, 105.0)
        assert source == "FMP" and price == 105.0

    def test_timestamp_without_price_is_not_treated_as_a_broker_mark(self):
        pos = make_pos(current_price=None, ibkr_synced_at="2026-09-04T15:47:00-04:00")
        price, source = resolve_position_price(pos, 105.0)
        assert source == "FMP" and price == 105.0


class TestFMPFallback:
    def test_fmp_used_when_ibkr_has_never_marked_the_position(self):
        pos = make_pos()
        assert resolve_position_price(pos, 105.5) == (105.5, "FMP")

    @pytest.mark.parametrize("bad", [None, 0.0, -1.0])
    def test_unusable_quote_falls_through_to_cost_basis(self, bad):
        """
        FMP returns 0.0 for an unknown or delisted symbol. Displaying that as a
        price would report the position as worthless.
        """
        pos = make_pos(buy_price=100.0)
        assert resolve_position_price(pos, bad) == (100.0, "COST_BASIS")


class TestCostBasisIsLast:
    def test_cost_basis_is_labelled_not_disguised(self):
        """
        Cost basis is not a market price -- it drives unrealized P&L to exactly
        $0.00, which reads as a genuinely flat book. It is only ever acceptable
        when labelled, which is why the source is returned alongside it.
        """
        pos = make_pos(buy_price=42.0)
        price, source = resolve_position_price(pos, None)
        assert price == 42.0
        assert source == "COST_BASIS"

    def test_every_source_is_named(self):
        """No path may return an unlabelled price."""
        cases = [
            (make_pos(current_price=1.0, ibkr_synced_at="t"), None),
            (make_pos(), 2.0),
            (make_pos(), None),
        ]
        for pos, fmp_price in cases:
            _, source = resolve_position_price(pos, fmp_price)
            assert source in {"IBKR", "FMP", "COST_BASIS"}
