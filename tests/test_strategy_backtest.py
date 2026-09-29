"""
Tests for research/strategy_backtest.py — the daily-bar strategy backtest whose
EXITS are the live exit_core / exit_rules code.

These tests exist to prove one thing the roadmap cares about above all else: the
backtester now fires the LIVE exit rules (Prove-It Phase 1 band, Phase 2 give-back
floor, dynamic trailing ladder, partial scale-out) and NOT the retired 7%-trail +
EMA-21 rules the old backend/backtester.py modelled. The engine under test is
``resolve_position_day``, which drives a single position against a single daily
bar; testing it directly isolates the exit logic from the portfolio/data plumbing.

Teeth: ``test_phase1_band_value_drives_the_exit`` mutates the Phase 1 band
constant and shows the day-0 exit disappears — so these tests are wired to the
real ``exit_rules`` constants, not asserting a hard-coded number.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exit_rules as er
import research.strategy_backtest as sb


@pytest.fixture
def cfg():
    return sb.build_exit_config()


def _bar(o, h, l, c, date="2026-06-01"):
    return sb.Bar(date=date, open=o, high=h, low=l, close=c, volume=1_000_000)


def _pos(cfg, buy=100.0, shares=100, date="2026-06-01"):
    return sb.new_position("X", shares, buy, date, alloc=buy * shares, cfg=cfg)


# ── Prove-It Phase 1 (the retired rules never had this) ──────────────────────────
def test_phase1_day0_band_fires_at_one_percent(cfg):
    pos = _pos(cfg)
    # Day 0, drops 1.5% intraday — through the 1% day-0 band, above the 7% disaster.
    res = sb.resolve_position_day(pos, _bar(100.0, 100.0, 98.5, 99.0), calendar_days=0, cfg=cfg)
    assert res.exit_price is not None
    assert "Phase 1" in res.exit_reason
    # Fills AT the entry-anchored band (entry * (1 - 1%)), not a 7%-from-peak trail.
    assert res.exit_price == pytest.approx(100.0 * (1 - er.PROVE_IT_P1_DAY0_PCT), abs=0.01)


def test_benign_up_day_holds_and_latches_proven(cfg):
    pos = _pos(cfg)
    res = sb.resolve_position_day(pos, _bar(100.0, 101.0, 99.7, 100.8), calendar_days=0, cfg=cfg)
    assert res.exit_price is None
    assert pos["closed_above_entry"] is True          # close 100.8 > entry
    assert pos["days_held"] == 1                       # advanced for tomorrow


def test_phase1_band_value_drives_the_exit(cfg, monkeypatch):
    """Teeth: widen the band to 20% and the day-0 exit must vanish."""
    monkeypatch.setattr(er, "PROVE_IT_P1_DAY0_PCT", 0.20)
    pos = _pos(cfg)
    res = sb.resolve_position_day(pos, _bar(100.0, 100.0, 98.5, 99.0), calendar_days=0, cfg=cfg)
    assert res.exit_price is None                      # 98.5 is inside a 20% band


# ── Prove-It Phase 2 give-back floor ─────────────────────────────────────────────
def test_phase2_giveback_floor_fires_after_arming(cfg):
    pos = _pos(cfg)
    # Day 0: closes green and peaks +3% → proven AND armed (>= +2%).
    r0 = sb.resolve_position_day(pos, _bar(100.0, 103.0, 100.0, 102.0), calendar_days=0, cfg=cfg)
    assert r0.exit_price is None
    assert pos["closed_above_entry"] is True
    assert pos["highest_unrealized_pct"] == pytest.approx(3.0, abs=0.01)
    # Day 1: gives back to the -1% floor (entry * 0.99).
    r1 = sb.resolve_position_day(pos, _bar(101.0, 101.0, 98.9, 99.0), calendar_days=1, cfg=cfg)
    assert r1.exit_price is not None
    assert "Phase 2" in r1.exit_reason
    assert r1.exit_price == pytest.approx(100.0 * (1 + er.PROVE_IT_P2_FLOOR_PCT), abs=0.01)


# ── Partial scale-out ────────────────────────────────────────────────────────────
def test_scale_out_books_a_partial_at_trigger(cfg):
    pos = _pos(cfg, shares=100)
    # Peaks +5% (>= +4% scale trigger), closes green, no downside hit.
    res = sb.resolve_position_day(pos, _bar(100.0, 105.0, 100.0, 104.5), calendar_days=0, cfg=cfg)
    assert res.exit_price is None                       # scale-out is not a full exit
    assert res.scale_shares == int(100 * cfg.scale_out_fraction)
    assert pos["scaled_out"] is True
    assert pos["shares"] == 100 - res.scale_shares      # remainder still held


# ── Trailing ladder tightens (dynamic trail, not a fixed 7%) ─────────────────────
def test_trail_ladder_tightens_after_five_percent(cfg):
    pos = _pos(cfg)
    # Day 0: peak +6% arms the tight 1.5% ladder rung; closes green, no downside.
    r0 = sb.resolve_position_day(pos, _bar(100.0, 106.0, 100.0, 105.5), calendar_days=0, cfg=cfg)
    assert r0.exit_price is None
    # Day 1: pulls back only ~1.6% off the 106 peak — a 10% base trail (95.4) would
    # NOT fire, but the tightened 1.5% ladder (106 * 0.985 = 104.41) must.
    r1 = sb.resolve_position_day(pos, _bar(105.0, 105.0, 104.3, 104.4), calendar_days=1, cfg=cfg)
    assert r1.exit_price is not None
    assert "Trailing stop" in r1.exit_reason
    assert r1.exit_price == pytest.approx(106.0 * (1 - 0.015), abs=0.02)


# ── Gap-through fill is pessimistic ──────────────────────────────────────────────
def test_gap_through_open_fills_at_open_not_level(cfg):
    pos = _pos(cfg)
    # Opens at 97 — already through the 1% day-0 band (99). Fill at the worse open.
    res = sb.resolve_position_day(pos, _bar(97.0, 97.5, 96.0, 96.5), calendar_days=0, cfg=cfg)
    assert res.exit_price == pytest.approx(97.0, abs=0.01)


# ── End-to-end smoke test on the committed dataset ───────────────────────────────
def test_simulate_uses_only_live_exit_reasons():
    res = sb.simulate(["NVDA", "AAPL", "MSFT", "AVGO", "META"],
                      start="2024-01-01", end="2025-06-30", max_positions=3)
    assert res["summary"]["closed_trades"] > 0
    allowed = {"prove_it_phase1", "prove_it_phase2", "trailing_ladder",
               "hard_stop", "scale_out", "other"}
    assert set(res["exit_reason_counts"]).issubset(allowed)
    # The retired rules must NOT appear.
    for t in res["trades"]:
        assert "EMA" not in t["exit_reason"]
        assert "7%" not in t["exit_reason"]
