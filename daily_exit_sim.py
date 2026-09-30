"""
daily_exit_sim.py — the LIVE exit engine, resolved once per DAILY OHLC bar.

WHY THIS MODULE EXISTS
----------------------
Two backtesters share current exit-rule primitives:

  * ``research/strategy_backtest.py`` — the offline research backtester over the
    committed ``benchmark_data/`` dataset, and
  * ``backend/backtester.py``        — the dashboard/web backtester the operator
    runs from the UI over FMP history.

If each re-implemented the exits they would silently drift from production and
from each other. So the daily-bar exit resolution lives here ONCE, driving the
real ``exit_core``/``exit_rules`` code, and both consumers import it. A change to
a live exit rule changes every backtest automatically.

This module is import-safe: it reads only environment variables (never raises at
import) and imports only ``exit_core`` and ``exit_rules`` (which in turn import
only ``config`` + stdlib). That is what lets it be COPY'd into the backend image
alongside ``config.py``/``exit_rules.py``/``exit_core.py`` without touching any
live-trading code path — see ``decisions/2026-09-29_backtester-option-a-live-exits.md``.

FIDELITY
--------
Sharing primitives is not full execution parity. Daily sequencing can change
which rule fires and relative profitability, not just fill price. The armed
trail/deadline, broker anchor resets, intraday observations and EOD latch are
approximated here. Calling this function every five minutes would also advance
its day counter and latch a "close" every five minutes; it is NOT an intraday
adapter. Use research/live_rule_replay.py for chronological recorded-input replay.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import exit_core as ec
import exit_rules as er


# ── Exit config — the live thresholds exit_core reads ────────────────────────────
def build_exit_config() -> ec.ExitConfig:
    """The live exit thresholds, as a pure snapshot for ``exit_core``.

    ``exit_rules`` already re-exports the Prove-It / power-hold / trail constants
    (it reads them from the same env vars the live agent does). The four values
    that live on ``execution_agent`` instead — the armed-exit deadline and the
    three scale-out knobs — are read here from the SAME env vars with the SAME
    defaults, so ``.env`` remains the single operational switch, exactly as
    ``config.py`` documents for the backend split. This never re-declares a
    number ``exit_rules`` already owns.
    """
    return ec.ExitConfig(
        armed_exit_deadline_hours=float(os.getenv("ARMED_EXIT_DEADLINE_HOURS", 3.25)),
        stop_loss_pct=er.STOP_LOSS_PCT,
        power_hold_trail_pct=er.POWER_HOLD_TRAIL_PCT,
        scale_out_enabled=os.getenv("SCALE_OUT_ENABLED", "true").lower() == "true",
        scale_out_trigger_pct=float(os.getenv("SCALE_OUT_TRIGGER_PCT", 0.04)),
        scale_out_fraction=float(os.getenv("SCALE_OUT_FRACTION", 0.33)),
        prove_it_p2_floor_pct=er.PROVE_IT_P2_FLOOR_PCT,
    )


# ── Minimal OHLC bar the exit engine reads ───────────────────────────────────────
@dataclass
class DayBar:
    """The four prices ``resolve_position_day`` needs from one daily candle.

    Any object exposing ``.open/.high/.low/.close`` works (the research
    backtester passes its richer ``Bar``, which is a superset); this is the
    lightweight adapter the dashboard backtester builds from a pandas row.
    """
    open: float
    high: float
    low: float
    close: float


# ── Position state — live-shaped so exit_rules/exit_core read it unchanged ────────
def new_position(ticker: str, shares: int, buy_price: float, buy_date: str,
                 alloc: float, cfg: ec.ExitConfig) -> dict:
    """A backtest position carrying every field the live exit path reads.

    ``closed_above_entry`` is the Prove-It phase latch; ``highest_unrealized_pct``
    and ``hwm_price`` drive the trail, scale-out and Phase-2 arming;
    ``stop_loss_pct`` is the position's BASE trail fraction (the live per-position
    ATR base; the backtest has no ATR so it defaults to config STOP_LOSS_PCT and
    the profit ladder tightens off it each day); ``power_hold``/``scaled_out``
    latch once. ``days_held`` is trading days since entry (0 on the entry day,
    matching ``trading_days_between``).
    """
    pos = {
        "ticker": ticker,
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "alloc": alloc,
        "peak_price": buy_price,
        "hwm_price": buy_price,
        "highest_unrealized_pct": 0.0,
        "closed_above_entry": False,
        "scaled_out": False,
        "power_hold": False,
        "stop_loss_pct": cfg.stop_loss_pct,   # resting base trail fraction
        "days_held": 0,
        "exit_armed": False,                  # daily bars resolve arms same-day
    }
    # Place the initial resting hard stop exactly as the live OCA bracket would.
    desired = er.hard_stop_price(pos, buy_price, 0.0, power_held=False, days_held=0)
    pos["hard_stop_price"] = er.safe_hard_stop(desired, buy_price, 0.0)
    return pos


@dataclass
class DayResult:
    """What resolving one position for one day produced."""
    exit_price: float | None = None
    exit_reason: str | None = None
    scale_shares: int = 0
    scale_price: float | None = None


def _ladder_trail_pct(peak_pct: float, base_pct: float) -> float:
    """The trailing-stop fraction the LIVE profit ladder rests at for a given
    peak gain, off the position's base.

    Mirrors the profit lever in ``exit_rules._compute_dynamic_trail_pct``: walk
    the LIVE ``TRAIL_PROFIT_TIERS`` table (highest threshold first); the first
    tier the PEAK gain has crossed sets the fraction, and a ``None`` tier means
    "no change — the base applies". One-way tightening is automatic because the
    peak is monotone. The table itself is the single live source, so a change to
    ``exit_rules.TRAIL_PROFIT_TIERS`` changes this backtest.
    """
    for threshold, pct in er.TRAIL_PROFIT_TIERS:
        if peak_pct >= threshold:
            return pct if pct is not None else base_pct
    return base_pct


def resolve_position_day(pos: dict, bar, calendar_days: int,
                         cfg: ec.ExitConfig) -> DayResult:
    """Resolve one position against one DAILY bar using the live exit engine.

    ``bar`` need only expose ``.open/.high/.low/.close`` (see ``DayBar``).

    The daily sequence mirrors how the live system splits work between the bot's
    poll and the broker's resting orders, and every resting LEVEL is computed by a
    live ``exit_rules`` function (never a re-derived threshold):

      A. Three resting downside LEVELS — computed off the peak/HWM as of the START
         of the day (yesterday's close), so a wide-range day cannot trip its own
         trail — are resolved against the day's LOW:
           • the Prove-It level      — ``er.prove_it_stop_level`` (Phase 1 band /
                                        Phase 2 give-back floor);
           • the profit-ladder trail — ``hwm × (1 − _ladder_trail_pct(peak))``,
                                        the ladder off the live ``TRAIL_PROFIT_TIERS``;
           • the static hard stop     — ``er.hard_stop_price`` + ``er.safe_hard_stop``.
         The HIGHEST level at/above the low fills first; a bar that OPENS through
         it fills at the open (pessimistic). Levels are computed directly rather
         than carried as a re-anchored %, so the Phase 2 floor sits exactly where
         ``exit_rules`` says (it does not drift with the HWM).
      B. If nothing stopped out, today's HIGH is folded into the peak/HWM and
         power-hold latch — for tomorrow's levels and today's close decision.
      C. The CLOSE runs ``exit_core.evaluate_exit`` to latch ``closed_above_entry``
         and take the partial scale-out — the live per-cycle verdict for survivors.

    This function advances a daily clock. Do not call it once per intraday bar;
    a chronological replay must separate broker quotes, bot cycles and EOD.
    """
    buy = float(pos["buy_price"])
    days_held = int(pos["days_held"])
    base_pct = float(pos.get("stop_loss_pct") or cfg.stop_loss_pct)
    o, h, l, c = bar.open, bar.high, bar.low, bar.close

    # ── A. Resting downside LEVELS use the START-OF-DAY peak (NO look-ahead) ──────
    # The trail and Phase-2 arming that protect the position DURING the day were
    # placed off the peak seen through YESTERDAY's close. Folding today's HIGH in
    # before checking today's LOW would let a wide-range day trip its own trail —
    # the price cannot be known to have hit the high before the low within one
    # daily bar. Today's high is folded in at the END, for tomorrow.
    peak_prev = float(pos["highest_unrealized_pct"])
    power_held = bool(pos.get("power_hold")) or er.is_power_hold_active(pos, calendar_days)

    if power_held:
        proveit_level, phase = None, "power-hold"
        ladder_pct = cfg.power_hold_trail_pct
    else:
        proveit_level, phase = er.prove_it_stop_level(pos, buy, days_held, peak_prev)
        ladder_pct = _ladder_trail_pct(peak_prev, base_pct)

    trail_level = float(pos["hwm_price"]) * (1.0 - ladder_pct)
    desired_hard = er.hard_stop_price(pos, buy, peak_prev, power_held, days_held)
    # safe_hard_stop refuses to place a stop at/above the market. A resting order
    # can only fill this bar if it sat below the day's open.
    hard_level = desired_hard if desired_hard < o else 0.0

    downside: list[tuple[float, str]] = []
    if proveit_level is not None and proveit_level > 0:
        label = ("Prove-It Stop (Phase 1 band)" if phase == "phase1"
                 else "Prove-It Stop (Phase 2 give-back floor)")
        downside.append((proveit_level, label))
    if trail_level > 0:
        downside.append((trail_level, f"Trailing stop {ladder_pct * 100:.1f}% from peak"))
    if hard_level > 0:
        downside.append((hard_level, "Hard stop (static)"))

    triggered = [(lvl, rsn) for lvl, rsn in downside if l <= lvl]
    if triggered:
        lvl, rsn = max(triggered, key=lambda x: x[0])   # highest level fills first
        fill = o if o < lvl else lvl                     # gap-through → fill at open
        return DayResult(exit_price=fill, exit_reason=rsn)

    # ── B. Survived the day — NOW fold in today's HIGH (peak/HWM/power-hold) ──────
    high_unreal = (h / buy - 1.0) * 100.0
    peak = max(peak_prev, high_unreal)
    pos["highest_unrealized_pct"] = peak
    pos["hwm_price"] = max(float(pos["hwm_price"]), h)
    pos["peak_price"] = pos["hwm_price"]
    if er.is_power_hold_active(pos, calendar_days):
        pos["power_hold"] = True   # latch, mirrors maybe_arm_power_hold

    # ── C. EOD housekeeping at the CLOSE via the live decision ───────────────────
    if c > buy:
        pos["closed_above_entry"] = True
    ctx = ec.ExitContext(
        current_price=c, days_held=days_held, calendar_days=calendar_days,
        power_hold_armed=bool(pos.get("power_hold")),
    )
    decision = ec.evaluate_exit(pos, ctx, cfg)

    result = DayResult()
    if decision.action == ec.SCALE_OUT and not pos["scaled_out"] and decision.scale_shares >= 1:
        pos["scaled_out"] = True
        pos["shares"] = int(pos["shares"]) - int(decision.scale_shares)
        result.scale_shares = int(decision.scale_shares)
        result.scale_price = c

    pos["days_held"] = days_held + 1   # survived the day; advance the trading-day counter
    return result
