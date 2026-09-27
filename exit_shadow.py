"""
exit_shadow.py — side-effect-free shadow evaluation of candidate exit rules.

Records, every monitor cycle, what two register-tracked exit CANDIDATES would
have done to each open position — without ever touching a live order:

  • Q1 arm-timing: Phase 2 arms at +3% peak instead of the live +2%.
    Register: exit-parameters-proveit (the PROVE_IT_P2_ARM_GAIN_PCT question).
  • Q2 give-back trail: a 5% trail from the high-water mark instead of the live
    1.5% profit-lock. Register: ladder-width-runon.

WHY A LIVE SHADOW, AND WHAT IT CAN / CANNOT MEASURE
---------------------------------------------------
The backtest (research/exit_rule_replay.py) can only see 5-minute bars and only
one bull-tape regime. A live shadow adds the two things the replay cannot:

  1. Sub-5-minute WICK behaviour — whether a tighter rule fires on an intraday
     spike-down that then recovers, a cost invisible to a 5-minute replay.
  2. OUT-OF-REGIME behaviour, as trades accumulate across different tapes.

But a live shadow has one hard limit that must not be forgotten: it can only
observe divergence UP TO the real exit, because once the live rule sells there
are no shares left to watch. So it captures the *wick / timing* side of these
rules (a looser trail riding out a dip the live rule cut; an earlier/later arm),
but NOT the run-on upside of holding a winner PAST the live exit — that stays
harness-only (--runon). The register entries say so explicitly.

This module is PURE: it reads position state and returns a dict. It never places,
cancels or modifies an order, and never writes to the database. The caller
(monitor_portfolio_intraday) does the insert, wrapped so a failure here can never
disturb trading.
"""

from exit_rules import prove_it_is_proven, PROVE_IT_P2_FLOOR_PCT

# Candidate values under measurement. Deliberately NOT the live constants — these
# are the alternatives the register is asking us to observe. Live stays at
# PROVE_IT_P2_ARM_GAIN_PCT = 0.02 and a 1.5% profit-lock.
Q1_SHADOW_ARM_GAIN_PCT = 0.03   # vs live +2%
Q2_SHADOW_TRAIL_PCT    = 0.05   # vs live 1.5% profit-lock from the peak


def _live_would_exit(current_price: float, live_level):
    """Would the LIVE Prove-It rule fire this cycle? (rule-level, independent of
    whether the position already armed on a prior cycle)."""
    return live_level is not None and current_price <= float(live_level)


def _q1_arm3(pos: dict, buy_price: float, current_price: float,
             peak_pct: float, live_would_exit: bool, live_level,
             live_phase: str) -> tuple:
    """Q1: Phase 2 arms at +3% instead of +2%. Identical to live in Phase 1 and
    once peak >= +3%; the ONLY divergence is the proven, peak in [+2%, +3%)
    window, where live has armed the give-back floor and Q1 has not yet."""
    if live_phase == "phase1" or not prove_it_is_proven(pos, peak_pct):
        return live_would_exit, live_level, "phase1(same-as-live)"
    if peak_pct < Q1_SHADOW_ARM_GAIN_PCT * 100.0:
        # Proven but below the +3% arm: Q1 is NOT armed, so no floor — it holds
        # where live (armed at +2%) is watching a floor.
        return False, None, "phase2-unarmed(+3%)"
    floor = round(buy_price * (1.0 + PROVE_IT_P2_FLOOR_PCT), 2)
    return (current_price <= floor), floor, "phase2-armed(+3%)"


def _q2_trail5(pos: dict, current_price: float, hwm_price: float,
               peak_pct: float) -> tuple:
    """Q2: a 5% give-back trail from the high-water mark, for proven positions.
    Looser than the live 1.5% profit-lock, so it rides out dips the live rule
    cuts. No breakeven floor (the replay showed flooring the remainder clips
    winners)."""
    if not prove_it_is_proven(pos, peak_pct) or hwm_price <= 0:
        return False, None, "phase1(hold)"
    level = round(hwm_price * (1.0 - Q2_SHADOW_TRAIL_PCT), 2)
    return (current_price <= level), level, "phase2-trail5"


def compute_exit_shadows(pos: dict, buy_price: float, current_price: float,
                         hwm_price: float, peak_pct: float,
                         live_level, live_phase: str, days_held: int) -> dict:
    """Return a flat dict describing the live rule and the two shadow candidates
    this cycle, ready to be inserted as one exit_shadow_log row.

    `live_level` / `live_phase` are prove_it_stop_level()'s output for this
    position this cycle (level may be None). `peak_pct` is
    highest_unrealized_pct; `hwm_price` is the stored high-water-mark price.
    """
    current_pct = ((current_price - buy_price) / buy_price * 100.0
                   if buy_price > 0 else 0.0)
    live_exit = _live_would_exit(current_price, live_level)

    q1_exit, q1_level, q1_state = _q1_arm3(
        pos, buy_price, current_price, peak_pct, live_exit, live_level, live_phase)
    q2_exit, q2_level, q2_state = _q2_trail5(
        pos, current_price, hwm_price, peak_pct)

    return {
        "ticker":          pos.get("ticker"),
        "days_held":       int(days_held),
        "price":           round(float(current_price), 4),
        "current_pct":     round(float(current_pct), 4),
        "peak_pct":        round(float(peak_pct), 4),
        "live_would_exit": bool(live_exit),
        "live_level":      round(float(live_level), 4) if live_level is not None else None,
        "live_phase":      live_phase,
        "q1_would_exit":   bool(q1_exit),
        "q1_level":        round(float(q1_level), 4) if q1_level is not None else None,
        "q1_state":        q1_state,
        "q2_would_exit":   bool(q2_exit),
        "q2_level":        round(float(q2_level), 4) if q2_level is not None else None,
        "q2_state":        q2_state,
        # Convenience flags for the review query — the divergences are the whole
        # point: cases where a candidate would act differently from live.
        "q1_diverges":     bool(q1_exit) != bool(live_exit),
        "q2_diverges":     bool(q2_exit) != bool(live_exit),
    }
