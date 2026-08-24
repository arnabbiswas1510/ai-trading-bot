"""
test_volatility_fit.py — pins the volatility-fit classifier and the redefined
`est_days_to_target` semantics.

Context: the AI evaluator used to rank candidates on `EstDaysTo25%` = 25/ATR, a
monotonic rescaling of ATR, which made the rubric an unbounded PREFERENCE FOR
VOLATILITY. Measured over 2,315 offline breakout signals that preference cost
-9.5pp CAGR with a clean dose-response. The bot has no +25% target at all.

See decisions/2026-08-24_ai-evaluator-volatility-fit.md.
"""
import re
import pathlib

import pytest

from scoring import (
    volatility_fit,
    est_days_to_lock,
    ATR_STOP_CAP_PCT,
    ATR_COMFORT_LOW,
    PROFIT_LOCK_PCT,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── est_days_to_lock ─────────────────────────────────────────────────────────

def test_measures_the_five_percent_lock_not_a_phantom_target():
    # The bot arms its profit lock at +5%; it never holds for +25%.
    assert PROFIT_LOCK_PCT == 5.0
    assert est_days_to_lock(2.5) == 2      # 5.0 / 2.5
    assert est_days_to_lock(1.0) == 5
    assert est_days_to_lock(0.5) == 10


@pytest.mark.parametrize("bad", [0, -1, None, "", "abc"])
def test_sentinel_for_unknown_atr(bad):
    assert est_days_to_lock(bad) == 999


def test_lock_is_non_binding_for_realistic_candidates():
    """The whole point of the retune: at a +5% threshold, velocity stops
    discriminating. Every candidate above ~0.75%/day reaches the lock before the
    day-7 stall rotation, so ranking on speed adds noise, not signal."""
    for atr in (0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0):
        assert est_days_to_lock(atr) <= 7


# ── volatility_fit ───────────────────────────────────────────────────────────

def test_extreme_volatility_is_penalised_not_rewarded():
    """The regression under test. 2.5 x ATR is clamped at 12%, so above 4.8%/day
    the position holds under 2.5 ATR of room. All 12 worst trades in the offline
    portfolio had entry ATR >= 4.25% and clustered at exactly the -12% cap."""
    for atr in (4.81, 5.5, 6.5, 9.0):
        emoji, label, tone = volatility_fit(atr)
        assert tone == "bad", f"ATR {atr}% should be penalised, got {tone}"
        assert "12% stop cap" in label


def test_comfort_band_is_neutral():
    for atr in (1.5, 2.0, 2.5, 3.5, 4.8):
        assert volatility_fit(atr)[2] == "good"


def test_quiet_names_flagged_for_the_rotation_window():
    for atr in (0.4, 0.9, 1.49):
        emoji, label, tone = volatility_fit(atr)
        assert tone == "warn"
        assert "+5% lock" in label


@pytest.mark.parametrize("bad", [0, -3, None, "x"])
def test_unknown_atr_is_not_silently_scored(bad):
    assert volatility_fit(bad)[2] == "unknown"


def test_boundaries_are_exact_and_non_overlapping():
    assert volatility_fit(ATR_STOP_CAP_PCT)[2] == "good"          # 4.8 inclusive
    assert volatility_fit(ATR_STOP_CAP_PCT + 0.01)[2] == "bad"
    assert volatility_fit(ATR_COMFORT_LOW)[2] == "good"           # 1.5 inclusive
    assert volatility_fit(ATR_COMFORT_LOW - 0.01)[2] == "warn"


def test_monotonic_tone_ordering():
    """Tone must be a band, not a ramp: warn -> good -> bad as ATR rises."""
    tones = [volatility_fit(a)[2] for a in
             (0.5, 1.0, 1.5, 2.5, 3.5, 4.8, 5.5, 7.0)]
    assert tones == ["warn", "warn", "good", "good", "good", "good", "bad", "bad"]


# ── the prompt itself ────────────────────────────────────────────────────────

def _prompt_source():
    return (ROOT / "ai_evaluator.py").read_text()


def test_prompt_does_not_promise_a_profit_target_the_bot_never_trades():
    src = _prompt_source()
    assert "hits +25%" not in src
    assert "EstDaysTo25%" not in src
    assert "-7% stop loss" not in src
    assert "before a -7% trailing stop" not in src


def test_prompt_states_the_real_ladder():
    src = _prompt_source()
    assert "NO profit target" in src
    assert "+5%" in src
    # whitespace-insensitive: the prompt is a wrapped triple-quoted string
    flat = re.sub(r"\s+", " ", src)
    assert "1.5% below the high-water mark" in flat
    assert "2.5 x ATR, clamped to a 10%-12% band" in flat


def test_prompt_does_not_reward_raw_speed():
    """The exact failure mode: an unbounded 'faster is better' rubric."""
    src = _prompt_source()
    assert "Do NOT reward raw" in src or "must not be scored as one" in src
    # the old ladder's boost clause must be gone
    assert "boost rating +10-15 pts" not in src


def test_prompt_penalises_the_stop_cap_tail():
    src = _prompt_source()
    assert re.search(r"ATR > 4\.8%/day: reduce rating by 20 pts", src)


def test_screener_no_longer_computes_a_twenty_five_percent_estimate():
    src = (ROOT / "technical_screener.py").read_text()
    assert "25.0 / atr_pct" not in src
    assert src.count("est_days_to_lock(atr_pct)") == 2   # both sites converted


# ── frontend mirror stays in lockstep ────────────────────────────────────────

def test_frontend_mirror_matches_backend_constants():
    js = (ROOT / "frontend/src/lib/volatilityFit.js").read_text()
    assert f"ATR_STOP_CAP_PCT = {ATR_STOP_CAP_PCT}" in js
    assert f"ATR_COMFORT_LOW = {ATR_COMFORT_LOW}" in js
    assert f"PROFIT_LOCK_PCT = {PROFIT_LOCK_PCT}" in js


def test_no_stale_twenty_five_percent_labels_in_the_ui():
    view = (ROOT / "frontend/src/components/BreakoutsView.jsx").read_text()
    assert "days to +25%" not in view
    assert "Fast mover" not in view          # the old speed-is-good framing
    assert "+5% lock" in view
