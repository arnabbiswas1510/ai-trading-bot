"""
Pins the commission + slippage model (trade_costs.py, backtest-fidelity item #3)
and proves the two invariants that make it safe:

  1. Costs are charged to CASH only. They lower NET P&L / final equity but leave
     GROSS per-trade P&L and — critically — the exit-RULE behaviour untouched.
  2. Enabling costs never changes which exit fires: the exit math keys off the
     unslipped QUOTE, so the exit-reason counts are byte-identical with costs on
     or off. A cost model that moved a stop would be a silent strategy change.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "research"), os.path.join(ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from trade_costs import CostModel, build_cost_model  # noqa: E402


# ── CostModel arithmetic ──────────────────────────────────────────────────────

def test_commission_uses_per_share_when_above_min():
    m = CostModel(commission_per_share=0.0035, commission_min=0.35, slippage_bps=0.0)
    # 1000 shares × 0.0035 = 3.50 > 0.35 floor
    assert m.commission(1000, 50.0) == pytest.approx(3.50)


def test_commission_hits_min_floor_on_small_orders():
    m = CostModel(commission_per_share=0.0035, commission_min=0.35, slippage_bps=0.0)
    # 10 shares × 0.0035 = 0.035 → floored to 0.35
    assert m.commission(10, 50.0) == pytest.approx(0.35)


def test_commission_zero_shares_is_free():
    m = build_cost_model()
    assert m.commission(0, 50.0) == 0.0


def test_slippage_is_adverse_on_both_sides():
    m = CostModel(commission_per_share=0.0, commission_min=0.0, slippage_bps=10.0)  # 0.10%
    assert m.buy_fill(100.0) == pytest.approx(100.10)   # buy pays more
    assert m.sell_fill(100.0) == pytest.approx(99.90)   # sell receives less
    assert m.slippage_frac == pytest.approx(0.0010)


def test_zero_cost_model_is_a_noop():
    free = CostModel(commission_per_share=0.0, commission_min=0.0, slippage_bps=0.0)
    assert free.commission(1000, 50.0) == 0.0
    assert free.buy_fill(50.0) == 50.0
    assert free.sell_fill(50.0) == 50.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("BACKTEST_COMMISSION_PER_SHARE", "0.0065")
    monkeypatch.setenv("BACKTEST_SLIPPAGE_BPS", "12")
    import importlib
    import trade_costs as tc
    importlib.reload(tc)
    try:
        m = tc.build_cost_model()
        assert m.commission_per_share == pytest.approx(0.0065)
        assert m.slippage_bps == pytest.approx(12.0)
    finally:
        monkeypatch.delenv("BACKTEST_COMMISSION_PER_SHARE", raising=False)
        monkeypatch.delenv("BACKTEST_SLIPPAGE_BPS", raising=False)
        importlib.reload(tc)


# ── Integration: costs lower NET but never change exit behaviour ───────────────

def test_costs_reduce_net_but_not_exit_reasons():
    import strategy_backtest as sb

    free = CostModel(commission_per_share=0.0, commission_min=0.0, slippage_bps=0.0)
    universe = sb._universe("all")
    r_free = sb.simulate(universe, "2023-08-01", "2024-08-01", costs=free)
    r_cost = sb.simulate(universe, "2023-08-01", "2024-08-01")  # default costs

    # Invariant 2: the exit RULES fire identically — costs never move a stop.
    assert r_free["exit_reason_counts"] == r_cost["exit_reason_counts"]

    # Invariant 1: costs are real and charged. The default run reports a positive
    # cost total and a NET P&L below its own GROSS P&L; the free run reports zero.
    assert r_free["summary"]["total_trading_costs"] == 0.0
    assert r_cost["summary"]["total_trading_costs"] > 0.0
    assert r_cost["summary"]["net_pnl"] < r_cost["summary"]["total_pnl"]
    assert r_free["summary"]["net_pnl"] == r_free["summary"]["total_pnl"]


def test_per_trade_records_expose_cost_fields():
    import strategy_backtest as sb

    res = sb.simulate(sb._universe("all"), "2023-08-01", "2024-08-01")
    assert res["trades"], "expected at least one trade in this window"
    t = res["trades"][0]
    for key in ("commission", "slippage_cost", "net_profit_loss", "profit_loss"):
        assert key in t
    # net = gross − commission − slippage, per record
    assert t["net_profit_loss"] == pytest.approx(
        t["profit_loss"] - t["commission"] - t["slippage_cost"], abs=0.02)
