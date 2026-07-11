"""
tests/test_score_components.py

Unit tests for the new 5-component scoring functions:
  - compute_liquidity_score (scoring.py)
  - compute_rs_score (scoring.py)
  - compute_final_score (scoring.py)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import directly from scoring.py — pure functions, no external deps
from scoring import compute_liquidity_score, compute_rs_score, compute_final_score



# ══════════════════════════════════════════════════════════════════════════════
# compute_liquidity_score
# ══════════════════════════════════════════════════════════════════════════════

class TestLiquidityScore:

    # ── Price tier ────────────────────────────────────────────────────────────
    def test_high_price_large_cap_high_volume(self):
        """NVDA-like: $750, 42M avg vol, Large -> max score"""
        score = compute_liquidity_score(750.0, 42_000_000, "Large")
        assert score == 100

    def test_mid_price_mid_cap_mid_volume(self):
        """Mid-tier stock: $35, 800K vol, Mid"""
        score = compute_liquidity_score(35.0, 800_000, "Mid")
        # price=30, vol=20, size=12 -> 62
        assert score == 62

    def test_low_price_small_cap_low_volume_sghc_like(self):
        """SGHC-like: $8, 180K vol, Small -> very low"""
        score = compute_liquidity_score(8.0, 180_000, "Small")
        # price=0, vol=0, size=4 -> 4
        assert score == 4

    def test_price_exactly_at_15_boundary(self):
        """$15 exact -> price tier = 20"""
        score = compute_liquidity_score(15.0, 500_000, "Mid")
        # price=20, vol=20, size=12 -> 52
        assert score == 52

    def test_price_just_below_15(self):
        """$14.99 -> price tier = 10"""
        score = compute_liquidity_score(14.99, 500_000, "Mid")
        # price=10, vol=20, size=12 -> 42
        assert score == 42

    def test_price_at_50_boundary(self):
        """$50 exactly -> price tier = 40"""
        score = compute_liquidity_score(50.0, 2_000_000, "Large")
        # price=40, vol=40, size=20 -> 100
        assert score == 100

    def test_price_just_below_50(self):
        """$49.99 -> price tier = 30"""
        score = compute_liquidity_score(49.99, 2_000_000, "Large")
        # price=30, vol=40, size=20 -> 90
        assert score == 90

    # ── Volume tier ───────────────────────────────────────────────────────────
    def test_volume_exactly_at_1m(self):
        score = compute_liquidity_score(50.0, 1_000_000, "Large")
        # price=40, vol=30, size=20 -> 90
        assert score == 90

    def test_volume_at_200k(self):
        score = compute_liquidity_score(50.0, 200_000, "Large")
        # price=40, vol=10, size=20 -> 70
        assert score == 70

    def test_volume_below_200k(self):
        score = compute_liquidity_score(50.0, 199_999, "Large")
        # price=40, vol=0, size=20 -> 60
        assert score == 60

    # ── Company size ──────────────────────────────────────────────────────────
    def test_unknown_company_size_defaults_to_8(self):
        score = compute_liquidity_score(50.0, 2_000_000, "Unknown")
        # price=40, vol=40, size=8 -> 88
        assert score == 88

    def test_none_company_size_defaults_to_8(self):
        score = compute_liquidity_score(50.0, 2_000_000, None)
        # price=40, vol=40, size=8 -> 88
        assert score == 88

    def test_small_company_size(self):
        score = compute_liquidity_score(50.0, 2_000_000, "Small")
        # price=40, vol=40, size=4 -> 84
        assert score == 84

    # ── Score is always in valid range ────────────────────────────────────────
    def test_score_always_non_negative(self):
        score = compute_liquidity_score(0.01, 0, "Small")
        assert score >= 0

    def test_score_always_at_most_100(self):
        score = compute_liquidity_score(9999.0, 999_999_999, "Large")
        assert score <= 100


# ══════════════════════════════════════════════════════════════════════════════
# compute_rs_score
# ══════════════════════════════════════════════════════════════════════════════

class TestRsScore:

    def test_strong_outperformer(self):
        """Stock +20%, SPY +5% -> excess +15% -> 100"""
        assert compute_rs_score(20.0, 5.0) == 100

    def test_excess_exactly_10(self):
        """Excess exactly 10% -> 100"""
        assert compute_rs_score(15.0, 5.0) == 100

    def test_excess_5_percent(self):
        """Excess 5% -> 50 + 5*5 = 75"""
        assert compute_rs_score(10.0, 5.0) == 75

    def test_excess_zero_neutral(self):
        """Same return as SPY -> 50"""
        assert compute_rs_score(5.0, 5.0) == 50

    def test_lagging_5_percent(self):
        """Excess -5% -> 50 + (-5)*5 = 25"""
        assert compute_rs_score(0.0, 5.0) == 25

    def test_lagging_10_percent(self):
        """Excess exactly -10% -> max(0, 50-50) = 0"""
        assert compute_rs_score(-5.0, 5.0) == 0

    def test_severe_underperformer(self):
        """Excess << -10% -> 0"""
        assert compute_rs_score(-30.0, 5.0) == 0

    def test_both_zero(self):
        """Both flat (e.g. on a holiday or data issue) -> neutral 50"""
        assert compute_rs_score(0.0, 0.0) == 50


# ══════════════════════════════════════════════════════════════════════════════
# compute_final_score
# ══════════════════════════════════════════════════════════════════════════════

class TestFinalScore:

    def test_perfect_scores(self):
        """All 100 -> final 100"""
        assert compute_final_score(100, 100, 100, 100, 100) == 100

    def test_zero_scores(self):
        """All 0 -> final 0"""
        assert compute_final_score(0, 0, 0, 0, 0) == 0

    def test_nvda_like(self):
        """NVDA-like scores -> should be around 80"""
        score = compute_final_score(72, 95, 82, 78, 85)
        assert 75 <= score <= 85, f"Expected ~80, got {score}"

    def test_sghc_like(self):
        """SGHC-like scores -> should be around 40-50"""
        score = compute_final_score(66, 14, 35, 40, 30)
        assert 30 <= score <= 50, f"Expected ~40, got {score}"

    def test_weights_sum_correctly(self):
        """Weighted formula: tech=100, rest=0 -> score = 30"""
        assert compute_final_score(100, 0, 0, 0, 0) == 30

    def test_liquidity_weight(self):
        """liq=100, rest=0 -> score = 25"""
        assert compute_final_score(0, 100, 0, 0, 0) == 25

    def test_output_clamped_to_100(self):
        """Score cannot exceed 100"""
        assert compute_final_score(200, 200, 200, 200, 200) == 100

    def test_output_clamped_to_0(self):
        """Score cannot go below 0"""
        assert compute_final_score(-100, -100, -100, -100, -100) == 0
