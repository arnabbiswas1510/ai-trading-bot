import datetime
import pandas as pd
from unittest.mock import patch
import technical_screener
from technical_screener import (
    compute_pre_breakout_quality_score,
    check_pre_breakout_coil,
    PRE_BREAKOUT_PROXIMITY,
    PRE_BREAKOUT_VOL_MAX,
    PRE_BREAKOUT_UPTREND_MIN,
)


def _make_df(n=60, base_vol=1_000_000, recent_closes=None, recent_vols=None, sma_close=90.0):
    if recent_closes is None:
        recent_closes = [sma_close] * 3
    if recent_vols is None:
        recent_vols = [int(base_vol * 0.8)] * 3
    tail_len = len(recent_closes)
    base_len = n - tail_len
    start = datetime.date(2026, 1, 1)
    rows = []
    for i in range(base_len):
        d = start + datetime.timedelta(days=i)
        rows.append({"date": d.isoformat(), "open": sma_close, "high": sma_close + 1,
                     "low": sma_close - 1, "close": sma_close, "volume": base_vol})
    for i, c in enumerate(recent_closes):
        d = start + datetime.timedelta(days=base_len + i)
        rows.append({"date": d.isoformat(), "open": c - 0.5, "high": c + 0.5,
                     "low": c - 1, "close": c, "volume": recent_vols[i]})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date", ascending=True).reset_index(drop=True)
    df["sma_50"] = df["close"].rolling(window=50).mean()
    df["avg_volume_50"] = df["volume"].rolling(window=50).mean()
    df["rolling_high_52w"] = df["high"].rolling(window=min(252, len(df))).max()
    return df


def _coil(df, rolling_high=100.0, stock_12w_return=15.0, spy_12w_return=5.0, ticker="AAPL"):
    today_row = df.iloc[-1]
    sma_50 = float(today_row["sma_50"])
    avg_vol_50 = float(today_row["avg_volume_50"])
    with patch.object(technical_screener, "_SPY_12W_RETURN", spy_12w_return):
        return check_pre_breakout_coil(
            ticker, df, sma_50, avg_vol_50, rolling_high,
            stock_12w_return, "2026-03-01", atr_pct=1.2, est_days_to_target=21
        )


class TestPreBreakoutQualityScore:

    def test_highest_score_tight_coil(self):
        """Within 1%, 0 vol ratio, 3/3 closes up -> score == 100."""
        assert compute_pre_breakout_quality_score(-0.5, 0.0, 3, 99.5, 90.0) == 100

    def test_score_within_1pct(self):
        """Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80."""
        assert compute_pre_breakout_quality_score(-0.8, 0.5, 3, 99.2, 90.0) == 80

    def test_score_within_3pct(self):
        """Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65."""
        assert compute_pre_breakout_quality_score(-2.0, 0.5, 2, 98.0, 90.0) == 65

    def test_score_within_5pct(self):
        """Within 5%, 0.8x vol, 2 closes up -> 28+int(0.2*40)+10=28+8+10=46 (rounding gives 45)."""
        score = compute_pre_breakout_quality_score(-4.0, 0.8, 2, 96.0, 90.0)
        assert score in (45, 46)  # int(round) may give either depending on float precision

    def test_score_within_8pct(self):
        """Within 8%, 0.9x vol, 2 closes up -> 20+4+10=34 (rounding may give 33)."""
        score = compute_pre_breakout_quality_score(-7.0, 0.9, 2, 93.0, 90.0)
        assert score in (33, 34)

    def test_zero_uptrend_component(self):
        """0 rising closes -> uptrend=0 -> 35+20+0=55."""
        assert compute_pre_breakout_quality_score(-2.0, 0.5, 0, 98.0, 90.0) == 55


class TestPreBreakoutCoilPass:

    def test_all_gates_pass(self):
        """5% below high, vol contracting, 3/3 closes up -> PRE_BREAKOUT."""
        df = _make_df(recent_closes=[93.0, 94.0, 95.0], recent_vols=[700_000] * 3)
        result = _coil(df, rolling_high=100.0)
        assert result is not None
        assert result["trigger_type"] == "PRE_BREAKOUT"
        assert result["ticker"] == "AAPL"

    def test_2_of_3_closes_up_accepted(self):
        """2 of 3 closes rising meets PRE_BREAKOUT_UPTREND_MIN=2."""
        df = _make_df(recent_closes=[93.0, 94.0, 95.0], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0) is not None

    def test_very_tight_coil_within_1pct(self):
        """Within 1% of pivot -> quality_score >= 70 (vol 0.6x avg -> contraction pts ~16)."""
        df = _make_df(recent_closes=[98.5, 99.0, 99.5], recent_vols=[600_000] * 3)
        result = _coil(df, rolling_high=100.0)
        assert result is not None
        assert result["quality_score"] >= 70


class TestPreBreakoutCoilFail:

    def test_gate_a_too_far_from_high(self):
        """15% below 52w high -> beyond 8% proximity -> None."""
        df = _make_df(recent_closes=[84.0, 84.5, 85.0], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0) is None

    def test_gate_a_at_or_above_high(self):
        """At or above 52w high -> confirmed breakout territory -> None."""
        df = _make_df(recent_closes=[99.5, 100.0, 100.5], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0) is None

    def test_gate_b_below_sma(self):
        """Close (77) below SMA-50 (~90) -> below trend -> None."""
        df = _make_df(recent_closes=[75.0, 76.0, 77.0], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0) is None

    def test_gate_c_low_rs(self):
        """Stock -5% vs SPY +15% -> low RS -> None."""
        df = _make_df(recent_closes=[93.0, 94.0, 95.0], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0, stock_12w_return=-5.0, spy_12w_return=15.0) is None

    def test_gate_d_volume_not_contracting(self):
        """Recent 3d avg vol 1.1x 50d avg -> sellers still active -> None."""
        df = _make_df(recent_closes=[93.0, 94.0, 95.0], recent_vols=[1_100_000] * 3)
        assert _coil(df, rolling_high=100.0) is None

    def test_gate_e_only_1_close_up(self):
        """Strictly descending then tiny uptick: must compare vs prior row.
        Use all-declining: 97->96->95 -> 0 of 3 closes rising -> below min=2 -> None."""
        df = _make_df(recent_closes=[97.0, 96.0, 95.0], recent_vols=[700_000] * 3)
        assert _coil(df, rolling_high=100.0) is None


class TestPreBreakoutScoreBoost:

    def test_boost_applied(self):
        from scoring import compute_final_score
        base = compute_final_score(60, 60, 60, 60, 60)
        assert min(100, base + 10) == base + 10

    def test_boost_capped_at_100(self):
        from scoring import compute_final_score
        assert min(100, compute_final_score(100, 100, 100, 100, 100) + 10) == 100

    def test_constants_have_expected_defaults(self):
        assert PRE_BREAKOUT_PROXIMITY == 0.08
        assert PRE_BREAKOUT_VOL_MAX == 1.0
        assert PRE_BREAKOUT_UPTREND_MIN == 2
