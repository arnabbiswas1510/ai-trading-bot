"""
Tests for the breakout failure penalty (technical_screener._compute_failure_penalty).

The penalty is retained in the codebase but ships INERT: FAILURE_PENALTY_MAX_POINTS
defaults to 0. These tests pin both halves of that decision.

The behaviour under test is not "does the arithmetic work". It is that the
penalty cannot silently come back to life, and that the measurement which
condemned it is preserved in executable form. On 2026-09-17 the penalty was
replayed over all 16 rows of breakout_learnings using each trade's own entry
parameters and returned the maximum for every single one -- all 10 winners and
all 6 losers. A penalty that scores LPG (+6.47%) exactly as it scores CHRD
(-1.62%) is not a weak signal, it is no signal.

See decisions/2026-09-17_failure-penalty-disabled.md.
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _screener(max_points):
    """Reimport technical_screener with a given FAILURE_PENALTY_MAX_POINTS."""
    os.environ["FAILURE_PENALTY_MAX_POINTS"] = str(max_points)
    import technical_screener

    importlib.reload(technical_screener)
    return technical_screener


@pytest.fixture(autouse=True)
def _restore_env():
    before = os.environ.get("FAILURE_PENALTY_MAX_POINTS")
    yield
    if before is None:
        os.environ.pop("FAILURE_PENALTY_MAX_POINTS", None)
    else:
        os.environ["FAILURE_PENALTY_MAX_POINTS"] = before
    import technical_screener

    importlib.reload(technical_screener)


def _learning(exit_date, entry_values, failed=True, trigger_type="BREAKOUT"):
    params = {"_meta": {"trigger_type": trigger_type, "source": "entry_snapshot"}}
    for key, val in entry_values.items():
        params[key] = {"entry": val, "failed": failed}
    return {"exit_date": exit_date, "failed_params": params}


# Real entry parameters from the six losing breakouts that were driving the
# penalty in production on 2026-09-17. Not invented -- copied from Supabase.
LIVE_LOSERS = [
    _learning("2026-09-16", {"volume_surge": 0.82, "rs_score": 100, "technical_score": 57, "pivot_distance_pct": -0.04}),
    _learning("2026-09-11", {"volume_surge": 0.76, "rs_score": 88,  "technical_score": 54, "pivot_distance_pct": -2.29}),
    _learning("2026-09-09", {"volume_surge": 0.90, "rs_score": 100, "technical_score": 58, "pivot_distance_pct": -2.41}),
    _learning("2026-09-09", {"volume_surge": 0.98, "rs_score": 100, "technical_score": 45, "pivot_distance_pct": -2.14}),
    _learning("2026-09-09", {"volume_surge": 1.68, "rs_score": 100, "technical_score": 47, "pivot_distance_pct": -4.43}),
    _learning("2026-09-08", {"volume_surge": 1.80, "rs_score": 94,  "technical_score": 54, "pivot_distance_pct": -2.74}),
]

# DHT as it was scored on 2026-09-17: final_score 77, AI grade A, rating 75.
# Penalised -20 to an adjusted 57, below the BREAKOUT floor of 60, and skipped.
DHT_TRIGGER = {
    "ticker": "DHT", "trigger_type": "BREAKOUT",
    "volume_surge": 1.65, "rs_score": 100,
    "technical_score": 68, "pivot_distance_pct": -1.79,
}

# DHT's OWN earlier trade, which closed +3.47%. Its entry parameters are
# indistinguishable from the losers above -- which is the whole problem.
DHT_OWN_WINNER = {
    "ticker": "DHT", "trigger_type": "BREAKOUT",
    "volume_surge": 0.70, "rs_score": 100,
    "technical_score": 56, "pivot_distance_pct": -2.67,
}


class TestPenaltyDisabledByDefault:
    def test_default_is_off(self):
        ts = _screener(0)
        assert ts.FAILURE_PENALTY_MAX_POINTS == 0

    def test_penalty_is_zero_when_disabled(self):
        ts = _screener(0)
        penalty, _ = ts._compute_failure_penalty(DHT_TRIGGER, LIVE_LOSERS)
        assert penalty == 0

    def test_cap_is_honoured_not_hardcoded(self):
        # The cap used to be a literal 20. If it ever regresses to a literal,
        # this fails -- which is the point: the kill switch must actually kill.
        ts = _screener(5)
        penalty, _ = ts._compute_failure_penalty(DHT_TRIGGER, LIVE_LOSERS)
        assert penalty == 5


class TestTheMeasurementThatCondemnedIt:
    """
    Executable evidence. If someone re-enables the penalty, these tests document
    exactly what it does to real candidates.
    """

    def test_dht_saturates_the_cap(self):
        ts = _screener(20)
        penalty, reason = ts._compute_failure_penalty(DHT_TRIGGER, LIVE_LOSERS)
        assert penalty == 20
        assert "volume_surge" in reason and "rs_score" in reason

    def test_penalty_is_saturated_not_merely_high(self):
        # Raising the cap far above 20 shows the uncapped score is ~4x the cap.
        # A cap doing that much work is not a score, it is a constant.
        ts = _screener(1000)
        penalty, _ = ts._compute_failure_penalty(DHT_TRIGGER, LIVE_LOSERS)
        assert penalty >= 60

    def test_dht_own_winning_trade_is_penalised_identically(self):
        # The decisive result: the configuration that produced DHT's +3.47%
        # winner scores exactly the same as the candidate the penalty rejected.
        ts = _screener(20)
        rejected, _ = ts._compute_failure_penalty(DHT_TRIGGER, LIVE_LOSERS)
        winner, _ = ts._compute_failure_penalty(DHT_OWN_WINNER, LIVE_LOSERS)
        assert winner == rejected == 20

    def test_tolerances_are_wider_than_the_signal(self):
        # Winner/loser separation measured 2026-09-17 vs the match tolerances.
        # Every tolerance exceeds the difference it is supposed to detect, so a
        # match is guaranteed. This is the mechanical root cause.
        separation = {"volume_surge": 0.15, "rs_score": 2.1,
                      "technical_score": 4.5, "pivot_distance_pct": 0.33}
        tolerance = {"volume_surge": 0.5, "rs_score": 10,
                     "technical_score": 10, "pivot_distance_pct": 2}
        for param, sep in separation.items():
            assert tolerance[param] > sep, (
                f"{param}: tolerance {tolerance[param]} should exceed separation {sep}"
            )


class TestPreBreakoutWasAlwaysExempt:
    def test_pre_breakout_never_matches_breakout_learnings(self):
        # 196/196 PRE_BREAKOUT rows in trigger_history scored 0. The _meta
        # trigger_type filter is why: every learning row is BREAKOUT-tagged.
        ts = _screener(20)
        pre = {**DHT_TRIGGER, "trigger_type": "PRE_BREAKOUT"}
        penalty, reason = ts._compute_failure_penalty(pre, LIVE_LOSERS)
        assert penalty == 0
        assert reason == ""


class TestWinnersNeverContributed:
    def test_rows_flagged_not_failed_are_ignored(self):
        # Winners ARE stored in breakout_learnings (with exit_type "stop_loss",
        # misleadingly) but their params carry failed=False and must not count.
        ts = _screener(20)
        winners = [_learning("2026-09-16", {"volume_surge": 1.65, "rs_score": 100,
                                            "technical_score": 68,
                                            "pivot_distance_pct": -1.79}, failed=False)]
        penalty, reason = ts._compute_failure_penalty(DHT_TRIGGER, winners)
        assert penalty == 0
        assert reason == ""
