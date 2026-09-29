"""
test_decision_core.py — unit tests for the PURE entry-decision module.

These exercise decision_core directly with plain dicts (no broker, no DB, no
clock), pinning each gate's verdict and the exact reason codes / share math. They
are the fine-grained complement to tests/test_buy_decision_golden.py, which pins
the same logic as exercised THROUGH the live buying.run_market_open_buys path.
Together they are the parity contract the backtester (Phase 2+) must satisfy.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import decision_core as dc          # noqa: E402
import trigger_audit as ta          # noqa: E402


CFG = dc.DecisionConfig(
    max_positions=5,
    min_trigger_score=60,
    min_pre_breakout_score=65,
    min_relaxed_trigger_score=58,
    min_vol_surge_gate=0.75,
    max_pivot_extension=0.05,
    max_pivot_breakdown=0.02,
    max_pre_breakout_pivot_dist=0.05,
    min_position_size=5000.0,
    price_safety_reserve=1000.0,
    earnings_blackout_trading_days=3,
)


def _trig(ticker="AAA", **kw):
    base = {
        "ticker": ticker,
        "close_price": 100.0,
        "volume_surge": 2.0,
        "pivot_distance_pct": -0.5,
        "final_score": 80,
        "trigger_type": "BREAKOUT",
    }
    base.update(kw)
    return base


# ── Ranking ───────────────────────────────────────────────────────────────────
class TestRanking:
    def test_orders_by_final_score_desc(self):
        trigs = [_trig("LOW", final_score=60), _trig("HIGH", final_score=99),
                 _trig("MID", final_score=80)]
        assert [t["ticker"] for t in dc.rank_triggers(trigs)] == ["HIGH", "MID", "LOW"]

    def test_falls_through_to_quality_then_ai_rating(self):
        a = {"ticker": "Q", "final_score": None, "quality_score": 70}
        b = {"ticker": "R", "final_score": None, "quality_score": None, "ai_rating": 90}
        c = {"ticker": "Z", "final_score": None, "quality_score": None, "ai_rating": None}
        ranked = [t["ticker"] for t in dc.rank_triggers([c, a, b])]
        assert ranked == ["R", "Q", "Z"]


# ── Eligibility ladder (gates 1–6) ────────────────────────────────────────────
class TestEligibility:
    def _elig(self, trig, held=(), cooled=None, dte=None):
        return dc.evaluate_eligibility(
            trig, held_tickers=set(held), cooled_map=cooled or {},
            days_to_earnings=dte, cfg=CFG)

    def test_already_held(self):
        d = self._elig(_trig("AAA"), held=["AAA"])
        assert d.action == dc.SKIP and d.reason_code == ta.ALREADY_HELD

    def test_cooling_off(self):
        d = self._elig(_trig("AAA"), cooled={"AAA": "sold at a loss today"})
        assert d.action == dc.SKIP and d.reason_code == ta.COOLING_OFF

    def test_ai_veto_d_grade(self):
        d = self._elig(_trig("AAA", ai_grade="D"))
        assert d.action == dc.SKIP and d.reason_code == ta.AI_VETO

    def test_earnings_blackout_fires_at_threshold(self):
        d = self._elig(_trig("AAA"), dte=3)
        assert d.action == dc.SKIP and d.reason_code == ta.EARNINGS_IMMINENT

    def test_earnings_none_fails_open(self):
        d = self._elig(_trig("AAA"), dte=None)
        assert d.action == dc.PROCEED

    def test_earnings_beyond_window_proceeds(self):
        d = self._elig(_trig("AAA"), dte=4)
        assert d.action == dc.PROCEED

    def test_no_ai_score_fails_closed(self):
        d = self._elig(_trig("AAA", final_score=None))
        assert d.action == dc.SKIP and d.reason_code == ta.NO_AI_SCORE

    def test_adjusted_score_overrides_final(self):
        # adjusted_score present and below floor → SCORE_FLOOR even if final_score high
        d = self._elig(_trig("AAA", final_score=99, adjusted_score=50))
        assert d.action == dc.SKIP and d.reason_code == ta.SCORE_FLOOR
        assert d.audit["candidate_score"] == 50.0

    def test_score_floor_breakout(self):
        d = self._elig(_trig("AAA", final_score=59))
        assert d.action == dc.SKIP and d.reason_code == ta.SCORE_FLOOR

    def test_pre_breakout_uses_higher_floor(self):
        # PRE_BREAKOUT floor = max(60, 65) = 65; a 62 passes BREAKOUT but not PRE
        d = self._elig(_trig("AAA", final_score=62, trigger_type="PRE_BREAKOUT"))
        assert d.action == dc.SKIP and d.reason_code == ta.SCORE_FLOOR

    def test_clean_breakout_proceeds(self):
        assert self._elig(_trig("AAA", final_score=80)).action == dc.PROCEED

    def test_ladder_order_held_beats_veto(self):
        # A held D-grade name must report ALREADY_HELD (gate 1 before gate 3).
        d = self._elig(_trig("AAA", ai_grade="D"), held=["AAA"])
        assert d.reason_code == ta.ALREADY_HELD


# ── Capacity / cash (gates 7–8) ───────────────────────────────────────────────
class TestCapacityCash:
    def test_capacity_halts_when_full(self):
        d = dc.evaluate_capacity(5, CFG)
        assert d.action == dc.HALT_CAPACITY and d.reason_code == ta.SLOTS_FULL

    def test_capacity_proceeds_with_room(self):
        assert dc.evaluate_capacity(4, CFG).action == dc.PROCEED

    def test_insufficient_cash(self):
        d = dc.evaluate_cash(4999.0, 1, CFG)
        assert d.action == dc.SKIP and d.reason_code == ta.INSUFFICIENT_CASH
        assert d.audit["available_cash"] == 4999.0

    def test_cash_at_floor_proceeds(self):
        assert dc.evaluate_cash(5000.0, 1, CFG).action == dc.PROCEED


# ── Market gates (9–10) ───────────────────────────────────────────────────────
class TestMarketGates:
    def test_breakout_low_volume_skipped(self):
        d = dc.evaluate_market_gates(_trig(volume_surge=0.5), CFG)
        assert d.action == dc.SKIP and d.reason_code == ta.SCORE_FLOOR

    def test_breakout_ok_volume_proceeds(self):
        assert dc.evaluate_market_gates(_trig(volume_surge=1.5), CFG).action == dc.PROCEED

    def test_pre_breakout_ignores_volume_gate(self):
        # low volume_surge is a GOOD contraction ratio for PRE_BREAKOUT → not gated
        d = dc.evaluate_market_gates(
            _trig(trigger_type="PRE_BREAKOUT", volume_surge=0.4,
                  pivot_distance_pct=-1.0), CFG)
        assert d.action == dc.PROCEED

    def test_pre_breakout_too_far_below_pivot(self):
        d = dc.evaluate_market_gates(
            _trig(trigger_type="PRE_BREAKOUT", volume_surge=0.4,
                  pivot_distance_pct=-6.0), CFG)  # 6% > 5% max
        assert d.action == dc.SKIP and d.reason_code == ta.BELOW_PIVOT


# ── Price gates (11–13) ───────────────────────────────────────────────────────
class TestPriceGates:
    def test_extended_above_pivot(self):
        d = dc.evaluate_price_gates(_trig(), current_price=106.0,
                                    pivot_price=100.0, position_size=20_000.0, cfg=CFG)
        assert d.action == dc.SKIP and d.reason_code == ta.EXTENDED_ABOVE_PIVOT

    def test_below_pivot_breakdown(self):
        d = dc.evaluate_price_gates(_trig(), current_price=97.0,
                                    pivot_price=100.0, position_size=20_000.0, cfg=CFG)
        assert d.action == dc.SKIP and d.reason_code == ta.BELOW_PIVOT

    def test_shares_zero_when_price_too_high(self):
        # position 20k, reserve 1k → 19k / 100000 price = 0 shares
        d = dc.evaluate_price_gates(_trig(), current_price=100_000.0,
                                    pivot_price=100_000.0, position_size=20_000.0, cfg=CFG)
        assert d.action == dc.SKIP and d.reason_code == ta.SHARES_ZERO

    def test_buy_returns_exact_share_count(self):
        # (20000 - 1000) / 100 = 190 shares
        d = dc.evaluate_price_gates(_trig(), current_price=100.0,
                                    pivot_price=100.0, position_size=20_000.0, cfg=CFG)
        assert d.action == dc.BUY and d.shares == 190
        assert d.reason_code == ta.BOUGHT

    def test_within_zone_slightly_above_pivot_buys(self):
        d = dc.evaluate_price_gates(_trig(), current_price=104.0,
                                    pivot_price=100.0, position_size=20_000.0, cfg=CFG)
        assert d.action == dc.BUY and d.shares == int(19_000.0 / 104.0)


# ── Sizing ────────────────────────────────────────────────────────────────────
class TestSizing:
    def test_equity_cap_binds(self):
        # cash 100k / 2 slots = 50k uncapped; cap = 100k/5 = 20k
        assert dc.equity_capped_position_size(100_000, 2, 100_000, 5) == 20_000

    def test_base_rule_when_cap_not_binding(self):
        # cash 30k / 3 slots = 10k; cap = 100k/5 = 20k → base wins
        assert dc.equity_capped_position_size(30_000, 3, 100_000, 5) == 10_000

    def test_zero_equity_skips_cap(self):
        assert dc.equity_capped_position_size(50_000, 1, 0, 5) == 50_000

    def test_slots_floored_at_one(self):
        assert dc.equity_capped_position_size(10_000, 0, 0, 5) == 10_000
