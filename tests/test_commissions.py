"""
Tests for commission-aware P&L.

The behaviour under test is not "does subtraction work" -- it is that an
UNRECORDED commission is never silently treated as zero. That distinction is the
entire point of the module: for six weeks every commission in this system was
effectively zero because RLS rejected every write to `ibkr_fills`, and nothing
anywhere reported it. These tests fail if that failure mode can return.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from commissions import (  # noqa: E402
    enrich_trades,
    summarize_realized,
    trade_commission_total,
    trade_net_pnl,
)


def _trade(profit_loss, buy_commission=None, sell_commission=None):
    return {
        "profit_loss": profit_loss,
        "buy_commission": buy_commission,
        "sell_commission": sell_commission,
    }


class TestTradeNetPnl:
    def test_both_legs_known_subtracts_and_marks_complete(self):
        net, complete = trade_net_pnl(_trade(100.0, 1.25, 1.75))
        assert net == 97.0
        assert complete is True

    def test_missing_commissions_returns_gross_but_flags_incomplete(self):
        # The critical case: no fee data must NOT be reported as a zero fee.
        net, complete = trade_net_pnl(_trade(100.0))
        assert net == 100.0
        assert complete is False

    def test_one_leg_missing_is_still_incomplete(self):
        net, complete = trade_net_pnl(_trade(100.0, buy_commission=1.25))
        assert net == 98.75
        assert complete is False, "a partial fee total must never look verified"

    def test_zero_commission_is_distinct_from_unknown(self):
        # An explicitly reported 0.0 is data; None is the absence of data.
        _, explicit = trade_commission_total(_trade(10.0, 0.0, 0.0))
        _, unknown = trade_commission_total(_trade(10.0, None, None))
        assert explicit is True
        assert unknown is False

    def test_losses_get_more_negative_not_less(self):
        net, _ = trade_net_pnl(_trade(-200.0, 1.0, 1.0))
        assert net == -202.0, "commissions must deepen a loss, never offset it"


class TestSummarizeRealized:
    def test_gross_and_net_reported_separately(self):
        summary = summarize_realized([
            _trade(100.0, 1.0, 1.0),
            _trade(-50.0, 1.0, 1.0),
        ])
        assert summary["realized_pnl_gross"] == 50.0
        assert summary["total_commission"] == 4.0
        assert summary["realized_pnl_net"] == 46.0
        assert summary["commission_complete"] is True

    def test_a_single_incomplete_trade_taints_the_total(self):
        summary = summarize_realized([
            _trade(100.0, 1.0, 1.0),
            _trade(-50.0),
        ])
        assert summary["commission_complete"] is False
        assert summary["trades_missing_commission"] == 1

    def test_empty_history_is_not_claimed_complete(self):
        summary = summarize_realized([])
        assert summary["realized_pnl_net"] == 0.0
        assert summary["commission_complete"] is False

    def test_matches_the_observed_production_discrepancy(self):
        # The live dashboard disagreed with itself by $480.91: total_pnl came
        # from IBKR cash (net) while sum(profit_loss) was gross. Once the fees
        # are recorded, net must close that gap exactly.
        trades = [_trade(1000.0, 240.455, 240.455)]
        summary = summarize_realized(trades)
        assert summary["total_commission"] == 480.91
        assert summary["realized_pnl_net"] == 519.09


class TestEnrichTrades:
    def test_adds_fields_without_mutating_input(self):
        original = _trade(100.0, 1.0, 1.0)
        enriched = enrich_trades([original])[0]
        assert enriched["net_profit_loss"] == 98.0
        assert enriched["commission_complete"] is True
        assert "net_profit_loss" not in original

    def test_gross_column_is_preserved_untouched(self):
        # profit_loss must stay gross so the exit-rule replay benchmarks in
        # decisions/ remain comparable with future runs.
        enriched = enrich_trades([_trade(100.0, 1.0, 1.0)])[0]
        assert enriched["profit_loss"] == 100.0

    def test_handles_string_numerics_from_postgrest(self):
        enriched = enrich_trades([{
            "profit_loss": "100.0",
            "buy_commission": "1.0",
            "sell_commission": "1.0",
        }])[0]
        assert enriched["net_profit_loss"] == 98.0
