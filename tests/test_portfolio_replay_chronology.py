"""Offline regressions for opening-only information and historical replay timing."""
from datetime import date, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "research"))

import backtester
from cooling_off import compute_cooled_map
import port_sim
import research.strategy_backtest as strategy
from trade_costs import CostModel


@pytest.mark.parametrize("engine", ["research", "dashboard"])
@pytest.mark.parametrize("held_open,expected_alloc", [(50.0, 375.0), (float("nan"), 500.0)])
def test_opening_size_cannot_see_todays_close(monkeypatch, engine, held_open, expected_alloc):
    dates = pd.bdate_range("2025-01-02", periods=208)
    frames = {}
    for symbol, signal in [("A", 205), ("B", 206)]:
        frame = pd.DataFrame(
            {"Open": 90.0, "High": 91.0, "Low": 89.0,
             "Close": 90.0, "Volume": 1000.0}, index=dates)
        frame.loc[dates[signal], ["High", "Close", "Volume"]] = [102.0, 101.0, 5000.0]
        frame.loc[dates[signal + 1], ["Open", "High", "Low", "Close"]] = [100, 101, 99, 100]
        frames[symbol] = frame
    frames["A"].loc[dates[207], "Open"] = held_open
    market = pd.DataFrame(
        {"Open": range(100, 308), "High": range(101, 309),
         "Low": range(99, 307), "Close": range(100, 308),
         "Volume": 1000.0}, index=dates)
    costs = CostModel(commission_per_share=0, commission_min=0, slippage_bps=0)
    mod = strategy if engine == "research" else backtester
    monkeypatch.setattr(mod, "resolve_position_day", lambda *args: SimpleNamespace(
        scale_shares=0, exit_price=None))
    original_new_position = mod.new_position
    buys = []

    def record_buy(ticker, shares, buy_price, buy_date, alloc, cfg):
        buys.append((ticker, shares, alloc))
        return original_new_position(ticker, shares, buy_price, buy_date, alloc, cfg)

    monkeypatch.setattr(mod, "new_position", record_buy)
    if engine == "research":
        def fake_daily(symbol):
            frame = market if symbol == "SPY" else frames[symbol]
            return [dict(date=dt.strftime("%Y-%m-%d"),
                         **{k.lower(): float(v) for k, v in row.items()})
                    for dt, row in frame.iterrows()]
        monkeypatch.setattr(strategy.bardata, "daily", fake_daily)
    else:
        monkeypatch.setattr(backtester.FMPClient, "is_configured", lambda self: True)

        def fake_prices(self, symbol, start, end):
            frame = market if symbol == "^GSPC" else frames[symbol]
            return frame.loc[start:end].copy()

        monkeypatch.setattr(backtester.FMPClient, "get_historical_prices", fake_prices)

    observed = []
    for future_close in [20.0, 200.0]:
        frames["A"].loc[dates[207], "Close"] = future_close
        buys.clear()
        start, end = dates[204].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")
        if engine == "research":
            strategy.simulate(["A", "B"], start, end, 1000, 2, costs=costs)
        else:
            backtester.run_backtest(["A", "B"], start, end,
                                    initial_capital=1000, max_positions=2, costs=costs)
        observed.append(next(buy for buy in buys if buy[0] == "B"))
    assert observed[0] == observed[1]
    assert observed[0][1] == int(expected_alloc // 100)
    assert observed[0][2] == pytest.approx(expected_alloc)


def _historical_inputs():
    days = ["2026-09-24", "2026-09-25", "2026-09-28"]
    bars = {s: [dict(date=d, open=100.0, high=100.0, low=99.0, close=100.0)
                for d in days] for s in ["A", "B"]}
    emas = {s: [None] * len(days) for s in bars}
    dix = {s: {d: i for i, d in enumerate(days)} for s in bars}
    cfg = dict(slots=1, power_hold=False, base_stop=0.1, ema=False,
               profit_tiers=[], time_tiers=[])
    return days, bars, emas, dix, cfg


def test_historical_entries_do_not_recycle_later_exit_slots():
    days, bars, emas, dix, cfg = _historical_inputs()
    bars["A"][1]["low"] = 80
    bars["B"][2]["low"] = 80
    sig = {days[0]: [("A", 0, 1)], days[1]: [("B", 1, 1)], days[2]: [("B", 2, 1)]}
    closed, occupancy = port_sim.simulate(cfg, sig, bars, emas, dix, days, track=True)
    assert [(hold, day) for _, hold, _, day in closed] == [(1, 1), (0, 2)]
    assert occupancy == [1, 0, 0]


def test_historical_entry_day_risk_is_not_skipped():
    days, bars, emas, dix, cfg = _historical_inputs()
    bars["A"][0]["low"] = 80
    closed = port_sim.simulate(cfg, {days[0]: [("A", 0, 1)]}, bars, emas, dix, days)
    assert len(closed) == 1
    assert closed[0][1:] == (0, "trail", 0)
    assert closed[0][0] == pytest.approx(-10)


def test_historical_missing_entry_bar_is_not_replaced_by_another_date():
    days, bars, emas, dix, cfg = _historical_inputs()
    del dix["A"][days[1]]
    bars["A"][2]["low"] = 80
    closed = port_sim.simulate(cfg, {days[1]: [("A", 0, 1)]}, bars, emas, dix, days)
    assert closed == []


class _LedgerClient:
    def __init__(self, sold_on, pnl):
        self.row = dict(ticker="A", sell_date=sold_on.isoformat(),
                        net_profit_loss=pnl, sell_reason="test")

    def table(self, name):
        row = self.row

        class Query:
            cutoff = ""

            def select(self, *_args): return self
            def order(self, *_args, **_kwargs): return self
            def eq(self, *_args): return self

            def gte(self, _field, cutoff):
                self.cutoff = cutoff
                return self

            def execute(self):
                rows = [row] if name == "trade_history" and row["sell_date"] >= self.cutoff else []
                return SimpleNamespace(data=rows)

        return Query()


@pytest.mark.parametrize("pnl", [-1, 0, 1])
@pytest.mark.parametrize("cool", [0, 1, 3, 7])
@pytest.mark.parametrize("age", [0, 1, 2, 3, 4, 7, 8])
def test_reason_aware_matches_live_inclusive_calendar_cutoff(pnl, cool, age):
    friday = date(2026, 9, 25)
    today = friday + timedelta(days=age)
    live_blocked = "A" in compute_cooled_map(_LedgerClient(friday, pnl), today, cool)
    assert port_sim._is_cooled(pnl, friday, today, cool, True, age) == live_blocked


def test_historical_blanket_zero_remains_deliberately_disabled():
    today = date(2026, 9, 25)
    assert not port_sim._is_cooled(-1, today, today, 0, False, 0)
    assert port_sim._is_cooled(-1, today, today, 0, True, 0)
    assert port_sim._is_cooled(1, today, today, 0, True, 0)


def test_reason_aware_reentry_on_monday_is_blocked_but_tuesday_is_allowed():
    days = ["2026-09-25", "2026-09-28", "2026-09-29"]
    bars = {"A": [dict(date=d, open=100.0, high=100.0, low=80.0, close=90.0)
                  for d in days]}
    dix = {"A": {d: i for i, d in enumerate(days)}}
    cfg = dict(slots=1, power_hold=False, base_stop=0.1, ema=False,
               profit_tiers=[], time_tiers=[], cool=3, cool_reason_aware=True)
    sig = {d: [("A", i, 1)] for i, d in enumerate(days)}
    closed = port_sim.simulate(cfg, sig, bars, {"A": [None] * 3}, dix, days)
    assert [r[3] for r in closed] == [0, 2]
