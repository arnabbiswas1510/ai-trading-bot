"""backend/backtester.py (the dashboard backtester) must exit with the LIVE rules.

Option A (decisions/2026-09-29_backtester-option-a-live-exits.md) replaced the
retired 7%-trailing-stop + EMA-21 exit with the real exit engine via
daily_exit_sim.resolve_position_day. This test drives run_backtest over crafted
OFFLINE data (FMP monkeypatched, no network) and asserts:

  * a trade closes with a LIVE exit reason (Prove-It / trailing ladder / hard
    stop / scale-out), and
  * NO trade ever carries the RETIRED reasons ("EMA-21 Exit", or a "7% from
    peak" trailing stop).

The scenario forces a Prove-It Phase 1 exit — a rule that did not exist in the
old backtester at all — so a pass proves the live engine is really wired in.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import backtester  # noqa: E402
from fmp_client import FMPClient  # noqa: E402

WARMUP = 230


def _range_bound_frame(dates) -> pd.DataFrame:
    """A ticker that oscillates in [90,100] for WARMUP days (so no 20-day-high
    breakout and flat volume ⇒ no early buy), then ONE breakout day, then a
    same-day crash that trips the tight day-0 Prove-It Phase 1 band."""
    n = len(dates)
    rng = np.random.default_rng(7)
    close = 95 + rng.uniform(-3, 3, n)          # ~95, inside [92,98]
    high = np.minimum(close + 1.5, 100.0)       # highs capped at 100
    low = close - 1.5
    open_ = close.copy()
    vol = np.full(n, 100_000.0)                 # flat ⇒ volume gate never trips

    b = WARMUP                                  # the breakout day
    close[b], high[b], low[b], open_[b] = 109.0, 110.0, 104.0, 101.0
    vol[b] = 300_000.0                          # 3× surge ⇒ breakout qualifies

    s = b + 1                                   # next day: buy at open, then crash
    open_[s], high[s], low[s], close[s] = 109.0, 109.5, 101.0, 102.0
    vol[s] = 120_000.0

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


def _uptrend_frame(dates) -> pd.DataFrame:
    """A steadily rising index so the SPY market filter stays bullish."""
    n = len(dates)
    close = np.linspace(400, 460, n)
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
         "Volume": np.full(n, 1_000_000.0)},
        index=dates,
    )


def test_dashboard_backtester_uses_live_exit_engine(monkeypatch):
    dates = pd.bdate_range("2023-01-02", periods=WARMUP + 10)
    ticker_df = _range_bound_frame(dates)
    spy_df = _uptrend_frame(dates)

    def fake_prices(self, symbol, start_date, end_date):
        df = spy_df if symbol == "^GSPC" else ticker_df
        return df.loc[start_date:end_date]

    monkeypatch.setattr(FMPClient, "is_configured", lambda self: True)
    monkeypatch.setattr(FMPClient, "get_historical_prices", fake_prices)

    start = dates[0].strftime("%Y-%m-%d")
    end = dates[-1].strftime("%Y-%m-%d")
    result = backtester.run_backtest(
        tickers=["TEST"], start_date_str=start, end_date_str=end,
        initial_capital=100_000.0, max_positions=5,
    )

    trades = result["trades"]
    assert trades, "expected at least one closed trade"

    reasons = [t["exit_reason"] for t in trades]
    # No RETIRED reason survives anywhere.
    for r in reasons:
        assert "EMA" not in r, f"retired EMA-21 exit fired: {r!r}"
        assert "% from peak)" not in r or "Trailing stop" in r, (
            f"retired 7% trailing-stop string fired: {r!r}")

    # A LIVE reason is present — ideally the Prove-It Phase 1 band the scenario forces.
    live_prefixes = ("Prove-It Stop", "Trailing stop", "Hard stop", "Partial scale-out")
    assert any(r.startswith(live_prefixes) for r in reasons), reasons
    assert any(r.startswith("Prove-It Stop") for r in reasons), (
        f"expected the forced Prove-It exit; got {reasons}")
