"""Unit tests for exit_shadow.compute_exit_shadows — the side-effect-free shadow
evaluation of the Q1 arm@+3% and Q2 5% give-back trail candidates."""

import exit_shadow
from exit_shadow import compute_exit_shadows, Q1_SHADOW_ARM_GAIN_PCT, Q2_SHADOW_TRAIL_PCT
from exit_rules import PROVE_IT_P2_FLOOR_PCT


def _pos(ticker="TEST", proven=True, hwm=None):
    # prove_it_is_proven() reads the position's `closed_above_entry` latch;
    # mirror how the live agent marks a position that has closed above entry.
    return {"ticker": ticker, "closed_above_entry": proven, "hwm_price": hwm}


def test_unproven_both_candidates_hold_phase1():
    # Not proven → Q1 mirrors live Phase 1, Q2 holds (no give-back trail yet).
    pos = _pos(proven=False)
    out = compute_exit_shadows(
        pos, buy_price=100.0, current_price=98.0, hwm_price=100.0,
        peak_pct=-2.0, live_level=None, live_phase="phase1", days_held=2)
    assert out["q1_state"] == "phase1(same-as-live)"
    assert out["q2_would_exit"] is False
    assert out["q2_state"] == "phase1(hold)"


def test_q1_holds_where_live_arms_in_2_to_3_window():
    # Proven, peak +2.5% (between live +2% arm and Q1 +3% arm): live is armed and
    # watching the -1% floor; Q1 is NOT yet armed and holds. This is the sole
    # divergence window for Q1.
    pos = _pos(proven=True)
    floor = round(100.0 * (1.0 + PROVE_IT_P2_FLOOR_PCT), 2)  # 99.00
    out = compute_exit_shadows(
        pos, buy_price=100.0, current_price=99.0, hwm_price=102.5,
        peak_pct=2.5, live_level=floor, live_phase="phase2", days_held=5)
    assert out["live_would_exit"] is True          # live floor touched
    assert out["q1_would_exit"] is False           # Q1 not armed at +3% yet
    assert out["q1_state"] == "phase2-unarmed(+3%)"
    assert out["q1_diverges"] is True


def test_q1_matches_live_above_3pct_peak():
    # Proven, peak +4% (above Q1 arm): both watch the same -1% floor.
    pos = _pos(proven=True)
    floor = round(100.0 * (1.0 + PROVE_IT_P2_FLOOR_PCT), 2)
    out = compute_exit_shadows(
        pos, buy_price=100.0, current_price=99.0, hwm_price=104.0,
        peak_pct=4.0, live_level=floor, live_phase="phase2", days_held=6)
    assert out["q1_would_exit"] is True
    assert out["q1_level"] == floor
    assert out["q1_state"] == "phase2-armed(+3%)"
    assert out["q1_diverges"] is False


def test_q2_trail_holds_a_shallow_dip_live_cuts():
    # Proven winner, HWM 110, price 108 (down 1.8% from peak). Live 1.5% lock has
    # fired (level 108.35 say); Q2 5% trail level is 104.50 → holds.
    pos = _pos(proven=True)
    out = compute_exit_shadows(
        pos, buy_price=100.0, current_price=108.0, hwm_price=110.0,
        peak_pct=10.0, live_level=108.35, live_phase="phase2", days_held=8)
    assert out["live_would_exit"] is True
    assert out["q2_level"] == round(110.0 * (1.0 - Q2_SHADOW_TRAIL_PCT), 2)  # 104.50
    assert out["q2_would_exit"] is False
    assert out["q2_diverges"] is True


def test_q2_trail_exits_on_deep_giveback():
    # Same winner but price 104 (down 5.45% from the 110 peak) → below the 104.50
    # trail level → Q2 exits.
    pos = _pos(proven=True)
    out = compute_exit_shadows(
        pos, buy_price=100.0, current_price=104.0, hwm_price=110.0,
        peak_pct=10.0, live_level=108.35, live_phase="phase2", days_held=9)
    assert out["q2_would_exit"] is True


def test_pure_no_mutation_of_position():
    # The function must not mutate its inputs (it is used mid-monitor-cycle).
    pos = _pos(proven=True)
    before = dict(pos)
    compute_exit_shadows(
        pos, buy_price=100.0, current_price=99.0, hwm_price=104.0,
        peak_pct=4.0, live_level=99.0, live_phase="phase2", days_held=6)
    assert pos == before


def test_candidate_constants_differ_from_live():
    # Guard against someone accidentally setting the shadow to the live values,
    # which would make the log measure nothing.
    assert Q1_SHADOW_ARM_GAIN_PCT == 0.03   # live is 0.02
    assert Q2_SHADOW_TRAIL_PCT == 0.05      # live profit-lock is 0.015
