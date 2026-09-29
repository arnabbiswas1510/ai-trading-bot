"""Arithmetic guards for the leave-one-out jackknife in exit_rule_replay.

The jackknife answers the mandatory "is this edge carried by one trade?"
question, so its arithmetic has to be exactly right — a subtly wrong
leave-one-out could pass a fragile ladder change that then loses real money.

These tests stub `_trade_delta` with deterministic per-(trade, config) values so
they run with no network and no price bars, and assert the three properties the
report depends on:

  1. full_edge == challenger.net - baseline.net   (additivity)
  2. drop1 == full_edge - (largest single positive edge)   (leave-one-out)
  3. name-level concentration sums a multi-leg position across its legs
"""
import datetime as dt

import pytest

import research.exit_rule_replay as r


def _trade(ticker: str, hour: int = 10) -> r.Trade:
    ts = dt.datetime(2026, 8, 31, hour, 0, tzinfo=dt.timezone.utc)
    return r.Trade(
        ticker=ticker, buy_price=100.0, buy_ts=ts,
        sell_price=100.0, sell_ts=ts + dt.timedelta(days=1),
        shares=1, profit_loss=0.0, sell_reason="test",
    )


def _cfg(label: str) -> r.ExitConfig:
    return r.ExitConfig(label=label, proveit=True)


@pytest.fixture
def stub_deltas(monkeypatch):
    """Deterministic delta lookup keyed by (config label, trade ticker+hour)."""
    table: dict = {}

    def fake(trade, cfg):
        return table.get((cfg.label, trade.ticker, trade.buy_ts.hour), 0.0), None

    monkeypatch.setattr(r, "_trade_delta", fake)
    return table


def test_full_edge_is_additive(stub_deltas):
    base = _cfg("BASE")
    chal = _cfg("CHAL")
    trades = [_trade("AAA"), _trade("BBB"), _trade("CCC")]
    # baseline deltas
    stub_deltas[("BASE", "AAA", 10)] = 0.0
    stub_deltas[("BASE", "BBB", 10)] = 0.0
    stub_deltas[("BASE", "CCC", 10)] = 0.0
    # challenger deltas
    stub_deltas[("CHAL", "AAA", 10)] = 500.0
    stub_deltas[("CHAL", "BBB", 10)] = 300.0
    stub_deltas[("CHAL", "CCC", 10)] = -100.0

    res = r.jackknife(trades, [base, chal], base)
    assert len(res) == 1
    row = res[0]
    assert row["full_edge"] == pytest.approx(700.0)          # 500 + 300 - 100
    # leave-one-out drops the single most-favorable trade (AAA, +500)
    assert row["drop1"] == pytest.approx(200.0)              # 700 - 500
    # leave-three-out drops all three positive-ish -> 700 - (500+300-100)=0
    assert row["drop3"] == pytest.approx(0.0)
    assert row["n_helped"] == 2
    assert row["n_hurt"] == 1
    # top1 share is 500/700
    assert row["top1_share"] == pytest.approx(71.4, abs=0.1)


def test_name_level_concentration_sums_legs(stub_deltas):
    base = _cfg("BASE")
    chal = _cfg("CHAL")
    # NTRA in two legs (different hours), plus one other name
    trades = [_trade("NTRA", 13), _trade("NTRA", 14), _trade("XYZ", 10)]
    stub_deltas[("CHAL", "NTRA", 13)] = 400.0
    stub_deltas[("CHAL", "NTRA", 14)] = 400.0
    stub_deltas[("CHAL", "XYZ", 10)] = 200.0

    row = r.jackknife(trades, [base, chal], base)[0]
    assert row["full_edge"] == pytest.approx(1000.0)
    # no single ROW is >50%, but NTRA across both legs is 800/1000 = 80%
    assert row["top1_share"] == pytest.approx(40.0)
    assert row["top_name"] == "NTRA"
    assert row["top_name_edge"] == pytest.approx(800.0)
    assert row["top_name_share"] == pytest.approx(80.0)


def test_baseline_excluded_from_challengers(stub_deltas):
    base = _cfg("BASE")
    chal = _cfg("CHAL")
    trades = [_trade("AAA")]
    res = r.jackknife(trades, [base, chal], base)
    labels = [row["label"] for row in res]
    assert "BASE" not in labels and "CHAL" in labels


def test_report_runs_without_crashing(stub_deltas, capsys):
    base = _cfg("BASE")
    chal = _cfg("CHAL")
    trades = [_trade("AAA"), _trade("BBB")]
    stub_deltas[("CHAL", "AAA", 10)] = 900.0
    stub_deltas[("CHAL", "BBB", 10)] = 50.0
    r.report_jackknife(trades, [base, chal], base)
    out = capsys.readouterr().out
    assert "JACKKNIFE" in out
    # AAA is 900/950 = 95% of the edge -> must be flagged NOT SHIPPABLE
    assert "NOT SHIPPABLE" in out
