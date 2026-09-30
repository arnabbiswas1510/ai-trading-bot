"""Reports must not turn descriptive legacy outcomes into causal live P&L."""

import pytest

from research import ai_value_replay as ai
from research import entry_quality_review as quality


@pytest.mark.parametrize("pnl", [-100.0, 0.0, 100.0])
def test_legacy_veto_pool_is_not_reported_as_realised_cost(pnl, capsys):
    row = dict(ticker="TEST", date="2026-09-01", quality=75, entry=100.0,
               exit_price=100.0 + pnl / 100, ret_pct=pnl / 100,
               pnl=pnl, reason="historical", no_bars=False)
    ai.report_veto_audit(
        dict(scored=[row], results=[row], total=pnl,
             winners_removed=int(pnl > 0), losers_avoided=int(pnl <= 0)),
        10_000,
    )
    output = capsys.readouterr().out
    assert "HISTORICAL SIMPLIFICATION" in output
    assert "Hypothetical vetoed-pool P&L" in output
    assert "veto COST money" not in output
    assert "veto SAVED money" not in output


def test_full_book_association_does_not_claim_missed_trades(monkeypatch, capsys):
    import config

    monkeypatch.setattr(config, "MAX_POSITIONS", 5)
    monkeypatch.setattr("sys.argv", ["entry_quality_review.py"])
    monkeypatch.setattr(quality, "OUTCOME", "max_gain_20d_pct")
    triggers = [dict(ticker="HELD0", triggered_at="2026-09-01",
                     max_gain_20d_pct=12.0)]
    trades = [dict(ticker=f"HELD{i}", buy_date="2026-08-31",
                   sell_date="2026-09-02") for i in range(5)]
    monkeypatch.setattr(quality, "_get",
                        lambda table, params, verify: triggers if table == "trigger_history" else trades)
    quality.main()
    output = capsys.readouterr().out
    assert "FULL-BOOK ASSOCIATION" in output
    assert "NOT a count of missed trades" in output
    assert "could not be\n  bought" not in output
