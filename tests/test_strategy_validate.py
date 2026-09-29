"""
Pins the pure scoring helpers of research/strategy_validate.py (fidelity item #6).

The harness answers the four AGENTS.md money-decision questions over the offline
daily backtest. The end-to-end run needs the benchmark dataset, so these tests
target the deterministic helpers — net/expectancy, the drop-top-k concentration
test, the walk-forward fold grouping and the percentile math — on synthetic trade
lists, plus one thin smoke test that the random-entry null wiring runs.
"""
from __future__ import annotations

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "research")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

import strategy_validate as sv  # noqa: E402


def _t(ticker, buy_date, net, partial=False):
    return {"ticker": ticker, "buy_date": buy_date,
            "net_profit_loss": net, "profit_loss": net, "partial": partial}


def test_net_and_closed_ignore_nothing_but_count_partials_in_net():
    trades = [_t("A", "2024-01-01", 100.0),
              _t("A", "2024-01-01", 50.0, partial=True),
              _t("B", "2024-01-02", -30.0)]
    assert sv._net(trades) == 120.0            # 100 + 50 − 30
    assert len(sv._closed(trades)) == 2        # partial excluded from closed count


def test_expectancy_is_net_over_closed_count():
    trades = [_t("A", "2024-01-01", 100.0),
              _t("B", "2024-01-02", -40.0)]
    # net 60 over 2 closed → 30
    assert sv._expectancy(trades) == 30.0


def test_drop_top_k_removes_richest_full_exits():
    trades = [_t("WIN", "2024-01-01", 1000.0),
              _t("MID", "2024-01-02", 100.0),
              _t("LOSS", "2024-01-03", -200.0)]
    net1, names1 = sv.drop_top_k(trades, 1)
    assert net1 == -100.0                       # 100 − 200
    assert names1 == ["WIN +1000"]
    net2, names2 = sv.drop_top_k(trades, 2)
    assert net2 == -200.0                        # only the loss remains
    assert [x.split()[0] for x in names2] == ["WIN", "MID"]


def test_drop_top_k_keeps_scaleouts_with_their_parent_gone():
    # A scale-out cannot exist without its full exit; dropping the full exit still
    # leaves the partial in the kept set (it was already banked).
    trades = [_t("WIN", "2024-01-01", 1000.0),
              _t("WIN", "2024-01-01", 200.0, partial=True),
              _t("LOSS", "2024-01-02", -50.0)]
    net1, _ = sv.drop_top_k(trades, 1)
    # WIN full exit dropped; WIN partial (+200) and LOSS (−50) remain → 150
    assert net1 == 150.0


def test_walk_forward_folds_group_by_buy_date_order():
    trades = [_t(f"T{i}", f"2024-01-{i:02d}", (10 if i % 2 else -10))
              for i in range(1, 9)]
    folds = sv.walk_forward_folds(trades, 4)
    assert len(folds) == 4
    assert all(fd["n"] == 2 for fd in folds)
    # windows are contiguous and ordered
    assert folds[0]["window"][0] == "2024-01-01"
    assert folds[-1]["window"][1] == "2024-01-08"


def test_walk_forward_attaches_partials_to_parent_fold():
    trades = [_t("A", "2024-01-01", 100.0),
              _t("A", "2024-01-01", 25.0, partial=True),
              _t("B", "2024-02-01", -40.0)]
    folds = sv.walk_forward_folds(trades, 2)
    assert len(folds) == 2
    # fold 0 holds A's full exit + its partial → net 125
    assert folds[0]["net"] == 125.0
    assert folds[1]["net"] == -40.0


def test_percentile_of_value_vs_distribution():
    dist = [-100.0, 0.0, 50.0, 100.0, 200.0]
    assert sv._percentile_of(50.0, dist) == 60.0    # 3 of 5 ≤ 50
    assert sv._percentile_of(-200.0, dist) == 0.0
    assert sv._percentile_of(300.0, dist) == 100.0


def test_pctl_interpolates():
    xs = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert sv._pctl(xs, 0) == 0.0
    assert sv._pctl(xs, 100) == 40.0
    assert sv._pctl(xs, 50) == 20.0


def test_random_null_distribution_runs_and_is_reproducible():
    universe = sv.sb._universe("all")
    a = sv.random_null_distribution(universe, "2023-08-01", "2024-02-01",
                                    100_000.0, 5, trials=3)
    b = sv.random_null_distribution(universe, "2023-08-01", "2024-02-01",
                                    100_000.0, 5, trials=3)
    assert len(a) == 3
    assert a == b          # seeded per trial → deterministic
