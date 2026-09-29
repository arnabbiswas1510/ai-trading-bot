"""
test_exit_core.py — unit + parity tests for the pure exit-decision module.

Two layers:

  1. UNIT — each branch of ``exit_core.evaluate_exit`` pinned directly on plain
     position dicts: armed-deadline force-sell, armed-await, Prove-It Phase 1 and
     Phase 2 firing, partial scale-out, the HOLD trail/hard-stop resolution, and
     the power-hold widening. These document the contract and give fast, no-mock
     coverage of the ladder ordering.

  2. PARITY — the SAME scripted multi-regime book that
     ``tests/test_exit_path_golden.py`` runs through the LIVE
     ``monitor_portfolio_intraday`` is also run through ``exit_core``, and the
     two are asserted to agree action-for-action. This is what makes ``exit_core``
     safe to delegate to later: it cannot silently drift from the live monitor,
     because a change to either that breaks agreement fails this test.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import execution_agent as ea
import exit_core as ec
from tests.conftest import make_position
from tests.golden_log import record_monitor


CFG = ec.config_from_module(ea)


def _ctx(pos, current_price, *, now=None, hours_armed=0.0, power_hold_armed=False):
    """Build an ExitContext the way monitoring.py derives it from the clock."""
    tz = ZoneInfo("America/New_York")
    now = now or datetime.datetime(2026, 6, 17, 11, 30, tzinfo=tz)
    today = now.date()
    buy_d = datetime.datetime.fromisoformat(
        pos["buy_date"].replace("Z", "+00:00")
    ).date()
    return ec.ExitContext(
        current_price=current_price,
        days_held=ea.trading_days_between(buy_d, today),
        calendar_days=(today - buy_d).days,
        hours_armed=hours_armed,
        power_hold_armed=power_hold_armed,
    )


# ── 1. Armed-exit deadline short-circuit ─────────────────────────────────────────
def test_armed_past_deadline_forces_sell():
    pos = make_position("ARMD", buy_price=100.0, days_ago=1)
    pos["exit_armed"] = True
    pos["exit_armed_reason"] = "Prove-It Stop (Phase 1)"
    d = ec.evaluate_exit(pos, _ctx(pos, 96.0, hours_armed=4.0), CFG)
    assert d.action == ec.SELL_DEADLINE
    assert d.is_terminal
    assert "Armed Exit Deadline" in d.reason


def test_armed_within_deadline_awaits():
    pos = make_position("ARMD", buy_price=100.0, days_ago=1)
    pos["exit_armed"] = True
    d = ec.evaluate_exit(pos, _ctx(pos, 96.0, hours_armed=0.5), CFG)
    assert d.action == ec.AWAIT_ARMED
    assert d.is_terminal


def test_armed_check_precedes_everything():
    # Even a position that would otherwise scale out stays in the armed branch.
    pos = make_position("ARMD", buy_price=100.0, days_ago=10,
                        highest_unrealized_pct=9.0)
    pos["exit_armed"] = True
    pos["scaled_out"] = False
    d = ec.evaluate_exit(pos, _ctx(pos, 108.0, hours_armed=0.1), CFG)
    assert d.action == ec.AWAIT_ARMED


# ── 2. Prove-It Stop firing ──────────────────────────────────────────────────────
def test_phase1_fires_when_below_band():
    # Unproven, day 2, ~-4% -> below the widened Phase-1 band -> arm.
    pos = make_position("LOSR", buy_price=100.0, days_ago=2,
                        highest_unrealized_pct=0.0)
    d = ec.evaluate_exit(pos, _ctx(pos, 96.0), CFG)
    assert d.action == ec.ARM_PROVE_IT
    assert d.prove_it_phase == "phase1"
    assert "Phase 1" in d.reason
    assert d.is_terminal


def test_phase1_holds_when_above_band():
    pos = make_position("HOLDR", buy_price=100.0, days_ago=2,
                        highest_unrealized_pct=0.0)
    d = ec.evaluate_exit(pos, _ctx(pos, 99.5), CFG)
    assert d.action == ec.HOLD


def test_phase2_floor_fires_on_giveback():
    # Proven (peaked green above the arm gain), now given back below the floor.
    pos = make_position("PRVN", buy_price=100.0, days_ago=8,
                        highest_unrealized_pct=6.0)
    pos["closed_above_entry"] = True
    # Floor is entry*(1+PROVE_IT_P2_FLOOR_PCT); price just under it fires.
    floor = 100.0 * (1.0 + ea.PROVE_IT_P2_FLOOR_PCT)
    d = ec.evaluate_exit(pos, _ctx(pos, floor - 0.5), CFG)
    assert d.action == ec.ARM_PROVE_IT
    assert d.prove_it_phase == "phase2"
    assert "Phase 2" in d.reason


def test_phase2_holds_above_floor():
    pos = make_position("PRVN", buy_price=100.0, days_ago=8,
                        highest_unrealized_pct=6.0)
    pos["closed_above_entry"] = True
    d = ec.evaluate_exit(pos, _ctx(pos, 104.0), CFG)
    assert d.action == ec.HOLD


# ── 3. Partial scale-out ─────────────────────────────────────────────────────────
def test_scale_out_first_time_peak_crosses_trigger():
    pos = make_position("SCAL", buy_price=100.0, days_ago=6, shares=100,
                        highest_unrealized_pct=5.0)
    pos["closed_above_entry"] = True
    pos["scaled_out"] = False
    d = ec.evaluate_exit(pos, _ctx(pos, 104.5), CFG)
    assert d.action == ec.SCALE_OUT
    assert d.scale_shares == int(100 * ea.SCALE_OUT_FRACTION)
    assert d.is_terminal


def test_no_scale_out_if_already_scaled():
    pos = make_position("SCAL", buy_price=100.0, days_ago=6, shares=100,
                        highest_unrealized_pct=5.0)
    pos["closed_above_entry"] = True
    pos["scaled_out"] = True
    d = ec.evaluate_exit(pos, _ctx(pos, 104.5), CFG)
    assert d.action == ec.HOLD


def test_prove_it_fires_before_scale_out():
    # A winner that has ALSO given back below its floor exits in full, not scales.
    pos = make_position("BOTH", buy_price=100.0, days_ago=8, shares=100,
                        highest_unrealized_pct=6.0)
    pos["closed_above_entry"] = True
    pos["scaled_out"] = False
    floor = 100.0 * (1.0 + ea.PROVE_IT_P2_FLOOR_PCT)
    d = ec.evaluate_exit(pos, _ctx(pos, floor - 0.5), CFG)
    assert d.action == ec.ARM_PROVE_IT


# ── 4. HOLD resolution: trail + hard-stop ────────────────────────────────────────
def test_hold_carries_hard_stop():
    # Unproven, price -0.5% -> above the day-0 1% band -> holds, and the static
    # hard-stop leg is resolved for placement.
    pos = make_position("HELD", buy_price=100.0, days_ago=8,
                        highest_unrealized_pct=0.0)
    d = ec.evaluate_exit(pos, _ctx(pos, 99.5), CFG)
    assert d.action == ec.HOLD
    assert d.desired_hard > 0.0


def test_power_hold_widens_trail():
    pos = make_position("PWR", buy_price=100.0, days_ago=20, shares=100,
                        highest_unrealized_pct=15.0, stop_loss_pct=0.05)
    pos["power_hold"] = True
    pos["closed_above_entry"] = True
    d = ec.evaluate_exit(pos, _ctx(pos, 112.0), CFG)
    assert d.action == ec.HOLD
    assert d.power_held is True
    # Trail widened toward the power-hold trail from the tighter 5% currently set.
    assert d.new_trail_pct == ea.POWER_HOLD_TRAIL_PCT


# ── 5. PARITY: exit_core agrees with the live monitor on the golden book ──────────
def _scripted_book():
    positions = [
        make_position("LOSERAA", buy_price=100.0, days_ago=2, shares=100,
                      highest_unrealized_pct=0.0),
        make_position("WINNERB", buy_price=100.0, days_ago=10, shares=100,
                      highest_unrealized_pct=9.0),
        make_position("PROVENC", buy_price=100.0, days_ago=15, shares=100,
                      highest_unrealized_pct=6.0),
    ]
    live = {"LOSERAA": 96.0, "WINNERB": 108.0, "PROVENC": 104.0}
    return positions, live


def _monitor_kinds_by_ticker():
    """Run the LIVE monitor over the scripted book; return {ticker: {kinds}}."""
    positions, live = _scripted_book()
    with record_monitor(positions, live) as rec:
        rows = rec.rows()
    by_ticker: dict[str, set] = {}
    for r in rows:
        by_ticker.setdefault(r.get("ticker"), set()).add(r["kind"])
    return by_ticker


def test_exit_core_matches_live_monitor_on_golden_book():
    """The pure core's verdict must imply the SAME money-path action the live
    monitor emitted for each scripted position — the anti-drift guarantee."""
    positions, live = _scripted_book()
    live_kinds = _monitor_kinds_by_ticker()

    for pos in positions:
        ticker = pos["ticker"]
        d = ec.evaluate_exit(pos, _ctx(pos, live[ticker]), CFG)
        kinds = live_kinds.get(ticker, set())

        if d.action == ec.ARM_PROVE_IT:
            assert "arm_exit" in kinds, (
                f"{ticker}: core armed but live monitor did not (live={kinds})")
        elif d.action == ec.SELL_DEADLINE:
            assert "execute_sell" in kinds, (
                f"{ticker}: core force-sold but live did not (live={kinds})")
        elif d.action == ec.SCALE_OUT:
            assert "execute_scale_out" in kinds, (
                f"{ticker}: core scaled but live did not (live={kinds})")
        elif d.action == ec.HOLD:
            assert not (kinds & {"arm_exit", "execute_sell", "execute_scale_out"}), (
                f"{ticker}: core held but live took an exit action (live={kinds})")


def test_golden_book_exercises_arm_and_hold():
    """Guard the parity test is non-vacuous: the book must produce at least one
    ARM and one HOLD, or the assertions above would pass while proving little."""
    positions, live = _scripted_book()
    actions = {
        pos["ticker"]: ec.evaluate_exit(pos, _ctx(pos, live[pos["ticker"]]), CFG).action
        for pos in positions
    }
    assert ec.ARM_PROVE_IT in actions.values()
    assert ec.HOLD in actions.values()
