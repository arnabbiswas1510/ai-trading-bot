"""
test_momentum_screener.py — Tests for momentum screener two-pass logic.

Covers:
  - check_technical_breakout_with_thresholds() with configurable thresholds
  - Pass 1 uses primary thresholds (vol=1.40, prox=0.98)
  - Pass 2 uses relaxed thresholds (vol=1.20, prox=0.95) — only when Pass 1 insufficient
  - Screener skips entirely when daily_triggers already ≥ MAX_POSITIONS
  - Pass 1 tickers not re-checked in Pass 2
  - Relaxed fundamentals: 10% EPS (vs 18%), 3 inst holders (vs 5)
"""

import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, AsyncMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_ohlcv_data
import momentum_screener


# ── Helper ────────────────────────────────────────────────────────────────────

def _mock_fmp_response(data: list, status: int = 200) -> MagicMock:
    res = MagicMock()
    res.status_code = status
    res.json.return_value = data
    return res


def _breakout_with_thresholds(vol_surge_min: float, pivot_proximity: float,
                               base_price: float = 95.0,
                               current_volume: int = 1_600_000,
                               current_close: float = 97.0,
                               rolling_high: float = 100.0) -> dict | None:
    """
    Calls check_technical_breakout_with_thresholds() with synthetic data.

    Defaults: base_price=95.0, current_close=97.0, rolling_high=100.0.
    This ensures close > SMA-50 (~95) AND close is 3% below rolling high
    (97/100 = 97% proximity — between Pass 1 threshold 98% and Pass 2 threshold 95%).
    """
    data = make_ohlcv_data(
        n_days=100,
        base_price=base_price,
        base_volume=1_000_000,
        current_close=current_close,
        current_volume=current_volume,
        rolling_high=rolling_high,
    )
    mock_response = _mock_fmp_response(data)

    with patch("momentum_screener.requests.get", return_value=mock_response):
        return momentum_screener.check_technical_breakout_with_thresholds(
            "TEST", vol_surge_min, pivot_proximity
        )


# ── Threshold correctness ─────────────────────────────────────────────────────

class TestThresholdConfiguration:

    def test_pass1_primary_vol_surge_threshold_1_40(self):
        """Pass 1 uses vol_surge_min=1.40 — a 1.30x raw surge fails (computed ratio < 1.40)."""
        # base_price=95, close=97, rolling_high=100 (97% proximity ≥ 98%? No, 97<98 — also fails proximity)
        # Use rolling_high=97 so proximity=100%, only volume gate is the constraint.
        result = _breakout_with_thresholds(
            vol_surge_min=1.40,
            pivot_proximity=0.98,
            base_price=95.0,
            current_close=97.0,
            rolling_high=97.0,   # proximity = 100% — passes
            current_volume=1_300_000,   # 1.3x raw → computed ratio ~1.26 < 1.40 → fail
        )
        assert result is None

    def test_pass1_primary_vol_surge_passes_at_threshold(self):
        """Pass 1: clearly above 1.40 surge threshold → passes."""
        # Use 1.60x raw volume, which yields computed ratio ~1.55 ≥ 1.40
        result = _breakout_with_thresholds(
            vol_surge_min=1.40,
            pivot_proximity=0.98,
            base_price=95.0,
            current_close=97.0,
            rolling_high=97.0,   # 97/97 = 100% proximity – passes
            current_volume=1_600_000,  # ~1.55x computed ratio ≥ 1.40
        )
        assert result is not None

    def test_pass2_relaxed_vol_surge_threshold_1_20(self):
        """
        Pass 2 uses MOMENTUM_VOLUME_SURGE_MIN=1.20 — a 1.30x raw surge should fail
        Pass 1 (1.40) but pass Pass 2 (1.20).
        """
        # Use rolling_high = current_close (100% proximity) to isolate the volume gate
        base_price = 95.0
        close = 97.0
        high = 97.0   # 100% proximity — always passes proximity gate

        result_pass1 = _breakout_with_thresholds(
            vol_surge_min=1.40, pivot_proximity=0.98,
            base_price=base_price, current_close=close,
            rolling_high=high, current_volume=1_300_000,
        )
        result_pass2 = _breakout_with_thresholds(
            vol_surge_min=1.20, pivot_proximity=0.95,
            base_price=base_price, current_close=close,
            rolling_high=high, current_volume=1_300_000,
        )
        assert result_pass1 is None,  "1.30x raw volume should FAIL Pass 1 (1.40 threshold)"
        assert result_pass2 is not None, "1.30x raw volume should PASS Pass 2 (1.20 threshold)"

    def test_pass2_relaxed_pivot_proximity_0_95(self):
        """
        Pass 2 allows proximity ≥ 0.95 (within 5% of 52w high).
        A stock 3% below high fails Pass 1 (0.98) but passes Pass 2 (0.95).
        """
        # close=97, rolling_high=100 → 97% proximity
        # Pass 1 (0.98): 97% < 98% → fail
        # Pass 2 (0.95): 97% ≥ 95% → pass
        # base_price must be < close=97 so close > SMA-50
        result_pass1 = _breakout_with_thresholds(
            vol_surge_min=1.40, pivot_proximity=0.98,
            base_price=93.0, current_close=97.0,
            rolling_high=100.0, current_volume=1_600_000,
        )
        result_pass2 = _breakout_with_thresholds(
            vol_surge_min=1.20, pivot_proximity=0.95,
            base_price=93.0, current_close=97.0,
            rolling_high=100.0, current_volume=1_600_000,
        )
        assert result_pass1 is None, "3% below high should fail Pass 1 (2% tolerance)"
        assert result_pass2 is not None, "3% below high should pass Pass 2 (5% tolerance)"


# ── Two-pass flow logic ───────────────────────────────────────────────────────

class TestTwoPassFlowLogic:

    def test_screener_skips_entirely_when_daily_triggers_full(self):
        """
        Screener must exit immediately when count_daily_triggers_today() >= MAX_POSITIONS.
        This avoids redundant work when primary triggers already fill all slots.
        """
        with patch("momentum_screener.count_daily_triggers_today", return_value=4), \
             patch("momentum_screener.MAX_POSITIONS", 4), \
             patch("momentum_screener.write_momentum_triggers") as mock_write:
            import asyncio
            asyncio.run(momentum_screener.main())

        mock_write.assert_not_called()

    def test_pass2_only_runs_when_pass1_insufficient(self):
        """
        Pass 2 must only run if Pass 1 found fewer results than needed.
        Mock Pass 1 to yield enough results → Pass 2 never runs.
        """
        # When pass1_results >= needed, write immediately and return.
        # We verify write_momentum_triggers is called ONCE with pass1 results only.
        pass1_trigger = {
            "ticker": "NVDA",
            "close_price": 102.0,
            "volume_surge": 1.6,
            "sma_50": 100.0,
            "rolling_high_52w": 102.0,
            "pivot_distance_pct": 0.0,
        }
        data = make_ohlcv_data(n_days=100, base_price=100.0, base_volume=1_000_000,
                               current_close=102.0, current_volume=1_600_000,
                               rolling_high=102.0)
        mock_response = _mock_fmp_response(data)

        with patch("momentum_screener.count_daily_triggers_today", return_value=3), \
             patch("momentum_screener.MAX_POSITIONS", 4), \
             patch("momentum_screener.get_sp500_tickers", new=AsyncMock(return_value=["NVDA"])), \
             patch("momentum_screener.passes_relaxed_fundamentals", new=AsyncMock(return_value=True)), \
             patch("momentum_screener.requests.get", return_value=mock_response), \
             patch("momentum_screener.write_momentum_triggers") as mock_write:
            import asyncio
            asyncio.run(momentum_screener.main())

        # write should be called once with pass1 results (no pass2 needed)
        mock_write.assert_called_once()

    def test_pass1_tickers_excluded_from_pass2(self):
        """
        Tickers found in Pass 1 must NOT be re-checked in Pass 2.
        Tests the `if ticker in pass1_tickers: continue` guard.
        """
        pass1_tickers_found = []
        pass2_checked_tickers = []

        original_fn = momentum_screener.check_technical_breakout_with_thresholds

        call_count = [0]
        def mock_breakout(ticker, vol_surge_min, pivot_proximity):
            call_count[0] += 1
            if vol_surge_min == 1.40:  # Pass 1
                pass1_tickers_found.append(ticker)
                return {"ticker": ticker, "close_price": 102.0, "volume_surge": 1.6,
                        "sma_50": 100.0, "rolling_high_52w": 102.0, "pivot_distance_pct": 0.0}
            else:  # Pass 2
                pass2_checked_tickers.append(ticker)
                return None

        with patch("momentum_screener.count_daily_triggers_today", return_value=2), \
             patch("momentum_screener.MAX_POSITIONS", 4), \
             patch("momentum_screener.get_sp500_tickers", new=AsyncMock(return_value=["NVDA", "AAPL"])), \
             patch("momentum_screener.passes_relaxed_fundamentals", new=AsyncMock(return_value=True)), \
             patch("momentum_screener.check_technical_breakout_with_thresholds", side_effect=mock_breakout), \
             patch("momentum_screener.write_momentum_triggers"):
            import asyncio
            asyncio.run(momentum_screener.main())

        # NVDA and AAPL were in Pass 1 results → should not appear in Pass 2 checks
        for ticker in pass1_tickers_found:
            assert ticker not in pass2_checked_tickers, (
                f"{ticker} was found in Pass 1 but re-checked in Pass 2 — duplicate!"
            )


# ── Relaxed fundamental thresholds ───────────────────────────────────────────

class TestRelaxedFundamentals:

    def test_momentum_eps_threshold_is_10pct_not_18pct(self):
        """
        Momentum uses MOMENTUM_MIN_Q_EPS_GROWTH=0.10 (10%), not CANSLIM's 18%.
        A ticker with 12% EPS growth should pass momentum but fail primary.
        """
        # Verify the module-level constant
        assert momentum_screener.MOMENTUM_MIN_Q_EPS_GROWTH == 0.10, (
            f"MOMENTUM_MIN_Q_EPS_GROWTH should be 0.10, got {momentum_screener.MOMENTUM_MIN_Q_EPS_GROWTH}"
        )
        # And it should be strictly less than the primary CANSLIM threshold
        primary_threshold = 0.18
        assert momentum_screener.MOMENTUM_MIN_Q_EPS_GROWTH < primary_threshold

    def test_momentum_inst_holder_threshold_is_3_not_5(self):
        """Momentum requires only 3 institutional holders vs CANSLIM's 5."""
        assert momentum_screener.MOMENTUM_MIN_INST_HOLDERS == 3
        assert momentum_screener.MOMENTUM_MIN_INST_HOLDERS < 5  # less strict than CANSLIM
