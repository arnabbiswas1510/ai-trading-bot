"""
exit_core.py — the PURE per-cycle exit decision shared by the live monitor and
the backtester.

WHY THIS MODULE EXISTS (backtest fidelity, Phase 2)
───────────────────────────────────────────────────
This is the exit twin of ``decision_core.py``. For a backtest to be trustworthy
enough to size real money against, its EXITS must match what the live bot does —
not an approximation. The live money path and both backtesters now decide exits
through this same code:

  • ``monitoring.monitor_portfolio_intraday`` — the LIVE money path;
  • ``backend/backtester.py`` and ``research/strategy_backtest.py`` — both resolve
    exits via the shared ``daily_exit_sim`` module, which imports and calls THIS
    ``exit_core`` / ``exit_rules`` (Option A, 2026-09-29). No re-implemented
    7%-trail / EMA-21 rules remain in either backtester.

The remaining outlier is ``research/exit_rule_replay.py``, which still MIRRORS the
``PROVE_IT_*`` constants in its own code for its real-trade replay.

This module extracts the live per-cycle exit
VERDICT — armed-exit deadline, power-hold widening, the Prove-It Stop firing
check, partial scale-out, and the trailing/hard-stop resolution — into a single
**pure function with no I/O**. It takes plain data (a position dict, the runtime
scalars the live path computes, and a config snapshot) and returns an
``ExitDecision``. It never touches IBKR, Supabase, Telegram, or the clock.

All of the exit MATH already lives in ``exit_rules.py`` (extracted Stage 1,
2026-09-18). This module only orchestrates those pure functions in the exact
order ``monitoring.py`` calls them, so it is parity-by-construction with the live
math and only reproduces the live ORDERING. That ordering is contractual: the
Prove-It firing check runs BEFORE scale-out (so a position through its give-back
floor exits in full rather than being trimmed), and the armed-exit deadline check
short-circuits everything.

WHAT STAYS OUT (deliberately)
─────────────────────────────
Everything with a side effect or an external dependency remains in
``monitoring.py``: fetching the live IBKR price, computing trading/calendar days,
the exit-shadow log write, the hwm/highest_unrealized_pct persistence, arming the
exit, executing the sell/scale-out, cancelling and re-placing the OCA bracket, and
the notifications. ``monitoring.py`` computes those inputs, will ask this module
for the verdict, then performs the I/O the verdict implies. (The live delegation
is gated on the orchestrator-split safety window — a quiet book — and lands in a
later patch; this module is written and proven equal against the golden monitor
book first.)

THE DECISION SEQUENCE (order is contractual — mirrors monitoring.py L88–345)
────────────────────────────────────────────────────────────────────────────
  1. armed-exit deadline   → SELL_DEADLINE if held past ARMED_EXIT_DEADLINE_HOURS,
                             else AWAIT_ARMED   (short-circuits the rest)
  2. power-hold active?     → suppresses discretionary exits, widens the trail
  3. Prove-It stop level    → ARM_PROVE_IT if price is at/through the level
  4. partial scale-out      → SCALE_OUT the first time PEAK gain crosses the trigger
  5. trail + hard-stop      → HOLD, carrying the resolved trail % and hard price
"""

from __future__ import annotations

from dataclasses import dataclass

import exit_rules as er


# ── Actions ─────────────────────────────────────────────────────────────────────
SELL_DEADLINE = "SELL_DEADLINE"   # armed exit passed its deadline — force sell now
AWAIT_ARMED   = "AWAIT_ARMED"     # armed, still inside deadline — let the trail work
ARM_PROVE_IT  = "ARM_PROVE_IT"    # price at/through the Prove-It level — arm a tight exit
SCALE_OUT     = "SCALE_OUT"       # first peak past the scale trigger — book a fraction
HOLD          = "HOLD"            # no exit; carry the resolved trail/hard-stop to place


@dataclass
class ExitDecision:
    """The verdict for one position this cycle.

    ``action`` selects the branch ``monitoring.py`` takes. The remaining fields
    carry the resolved values the live path would act on, so the caller performs
    the I/O without recomputing anything (and so tests can assert the numbers).
    """
    action: str
    reason: str = ""
    # Prove-It resolution
    prove_it_level: float | None = None
    prove_it_phase: str = ""
    # Runtime scalars the live loop also derives (echoed for the caller/tests)
    unrealized_pct: float = 0.0
    highest_unrealized_pct: float = 0.0
    power_held: bool = False
    sell_state: str | None = None
    # Scale-out
    scale_shares: int = 0
    # Trail + hard-stop resolution (HOLD path)
    new_trail_pct: float | None = None
    desired_hard: float = 0.0
    hard_changed: bool = False

    @property
    def is_terminal(self) -> bool:
        """True when the verdict ends this position's processing for the cycle
        (mirrors the ``continue`` statements in the live loop)."""
        return self.action in (SELL_DEADLINE, AWAIT_ARMED, ARM_PROVE_IT, SCALE_OUT)


@dataclass
class ExitConfig:
    """Immutable snapshot of every threshold the exit decision reads.

    Built from the live ``execution_agent`` module via ``config_from_module`` so
    production uses production constants, while a backtest can construct its own
    to sweep them. Passing a snapshot (not the live module) keeps the decision
    pure and its inputs explicit. The Prove-It band/floor/tier constants are NOT
    snapshotted here — they are read inside ``exit_rules`` at call time, which is
    the single source both the live path and this module already share.
    """
    armed_exit_deadline_hours: float
    stop_loss_pct: float
    power_hold_trail_pct: float
    scale_out_enabled: bool
    scale_out_trigger_pct: float
    scale_out_fraction: float
    prove_it_p2_floor_pct: float


def config_from_module(ea) -> ExitConfig:
    """Snapshot the live exit thresholds off the execution_agent module."""
    return ExitConfig(
        armed_exit_deadline_hours=ea.ARMED_EXIT_DEADLINE_HOURS,
        stop_loss_pct=ea.STOP_LOSS_PCT,
        power_hold_trail_pct=ea.POWER_HOLD_TRAIL_PCT,
        scale_out_enabled=ea.SCALE_OUT_ENABLED,
        scale_out_trigger_pct=ea.SCALE_OUT_TRIGGER_PCT,
        scale_out_fraction=ea.SCALE_OUT_FRACTION,
        prove_it_p2_floor_pct=ea.PROVE_IT_P2_FLOOR_PCT,
    )


# ── Runtime context ─────────────────────────────────────────────────────────────
@dataclass
class ExitContext:
    """The per-cycle scalars ``monitoring.py`` computes from I/O and clock, passed
    IN so this module stays pure.

    ``hours_armed`` is only read when ``pos['exit_armed']`` is truthy.
    ``power_hold_armed`` reflects the side-effecting ``maybe_arm_power_hold`` call
    in the live loop: the live power-hold flag is
    ``maybe_arm_power_hold(...) or is_power_hold_active(...)``. This module always
    computes the pure ``is_power_hold_active`` half itself and ORs it with this
    flag, so a caller that has just armed power-hold reports it here.
    """
    current_price: float
    days_held: int
    calendar_days: int
    hours_armed: float = 0.0
    power_hold_armed: bool = False


def _armed_deadline_reason(pos: dict, hours_armed: float) -> str:
    return (
        f"Armed Exit Deadline — {pos.get('exit_armed_reason', 'armed exit')} "
        f"not stopped out after {hours_armed:.2f}h, forcing sell"
    )


def _prove_it_reason(phase: str, days_held: int, unrealized_pct: float,
                     highest_unrealized_pct: float, prove_it_level: float,
                     cfg: ExitConfig) -> str:
    if phase == "phase1":
        band_pct = er.prove_it_p1_threshold_pct(days_held) * 100.0
        return (
            f"Prove-It Stop (Phase 1 — unproven) — Day {days_held}, "
            f"never closed above entry and price "
            f"{unrealized_pct:.2f}% <= -{band_pct:.1f}% of entry "
            f"(${prove_it_level:.2f})"
        )
    return (
        f"Prove-It Stop (Phase 2 — give-back floor) — Day {days_held}, "
        f"peak +{highest_unrealized_pct:.2f}% gave back to "
        f"{unrealized_pct:.2f}%, at or below the "
        f"{cfg.prove_it_p2_floor_pct * 100:+.1f}% floor "
        f"(${prove_it_level:.2f}). A green trade does not become a loss."
    )


def evaluate_exit(pos: dict, ctx: ExitContext, cfg: ExitConfig) -> ExitDecision:
    """Return the live per-cycle exit verdict for one position — pure, no I/O.

    Reproduces ``monitor_portfolio_intraday``'s decision sequence (L88–345) by
    orchestrating the ``exit_rules`` primitives in the exact live order.
    """
    buy_price = float(pos["buy_price"])
    shares = int(pos["shares"])
    current_price = ctx.current_price
    pos_stop_loss_pct = float(pos.get("stop_loss_pct") or cfg.stop_loss_pct)

    # 1. ── Armed Trailing Exit deadline check ───────────────────────────────────
    # Short-circuits everything: an armed position is either forced out at the
    # deadline or left alone to let its tight trail work.
    if pos.get("exit_armed"):
        if ctx.hours_armed >= cfg.armed_exit_deadline_hours:
            return ExitDecision(
                action=SELL_DEADLINE,
                reason=_armed_deadline_reason(pos, ctx.hours_armed),
                sell_state=er.sell_state_code(pos, "", False, 0.0),
            )
        return ExitDecision(
            action=AWAIT_ARMED,
            sell_state=er.sell_state_code(pos, "", False, 0.0),
        )

    # 2. ── Unrealized / peak, then power-hold ───────────────────────────────────
    unrealized_pct = round(((current_price / buy_price) - 1.0) * 100.0, 4)
    prev_highest = float(pos.get("highest_unrealized_pct") or 0.0)
    highest_unrealized_pct = max(prev_highest, unrealized_pct)

    power_held = ctx.power_hold_armed or er.is_power_hold_active(pos, ctx.calendar_days)

    # 3. ── The Prove-It Stop ─────────────────────────────────────────────────────
    if power_held:
        prove_it_level, prove_it_phase = None, "power-hold"
    else:
        prove_it_level, prove_it_phase = er.prove_it_stop_level(
            pos, buy_price, ctx.days_held, highest_unrealized_pct
        )

    sell_state = er.sell_state_code(pos, prove_it_phase, power_held,
                                    highest_unrealized_pct)

    if (prove_it_level is not None
            and current_price <= prove_it_level
            and not pos.get("exit_armed")):
        return ExitDecision(
            action=ARM_PROVE_IT,
            reason=_prove_it_reason(prove_it_phase, ctx.days_held, unrealized_pct,
                                    highest_unrealized_pct, prove_it_level, cfg),
            prove_it_level=prove_it_level,
            prove_it_phase=prove_it_phase,
            unrealized_pct=unrealized_pct,
            highest_unrealized_pct=highest_unrealized_pct,
            power_held=power_held,
            sell_state=sell_state,
        )

    # 4. ── Partial Scale-Out ─────────────────────────────────────────────────────
    # AFTER the Prove-It firing check so a position through its floor exits in full
    # rather than being trimmed. Only a winner still above the floor reaches here.
    if (cfg.scale_out_enabled
            and "scaled_out" in pos          # migration applied — flag can persist
            and not power_held
            and not pos.get("scaled_out")
            and highest_unrealized_pct >= cfg.scale_out_trigger_pct * 100.0):
        scale_shares = int(shares * cfg.scale_out_fraction)
        if scale_shares >= 1 and (shares - scale_shares) >= 1:
            return ExitDecision(
                action=SCALE_OUT,
                prove_it_level=prove_it_level,
                prove_it_phase=prove_it_phase,
                unrealized_pct=unrealized_pct,
                highest_unrealized_pct=highest_unrealized_pct,
                power_held=power_held,
                sell_state=sell_state,
                scale_shares=scale_shares,
            )

    # 5. ── Trail tightening + static hard-stop ratchet → HOLD ────────────────────
    if power_held:
        new_trail_pct = (
            cfg.power_hold_trail_pct
            if pos_stop_loss_pct < cfg.power_hold_trail_pct
            else None
        )
    else:
        new_trail_pct = er._compute_dynamic_trail_pct(
            unrealized_pct, ctx.calendar_days, pos_stop_loss_pct,
            prove_it_pct=er.prove_it_trail_pct(
                prove_it_level, current_price, prove_it_phase
            ),
        )

    desired_hard = er.hard_stop_price(pos, buy_price, highest_unrealized_pct,
                                      power_held, ctx.days_held)
    stored_hard = float(pos.get("hard_stop_price") or 0.0)
    desired_hard = er.safe_hard_stop(desired_hard, current_price, stored_hard)

    _unproven = not er.prove_it_is_proven(pos, highest_unrealized_pct)
    if power_held or _unproven:
        hard_changed = abs(desired_hard - stored_hard) >= 0.01
    else:
        hard_changed = desired_hard > stored_hard + 0.005   # ratchet up only

    return ExitDecision(
        action=HOLD,
        prove_it_level=prove_it_level,
        prove_it_phase=prove_it_phase,
        unrealized_pct=unrealized_pct,
        highest_unrealized_pct=highest_unrealized_pct,
        power_held=power_held,
        sell_state=sell_state,
        new_trail_pct=new_trail_pct,
        desired_hard=desired_hard,
        hard_changed=hard_changed,
    )
