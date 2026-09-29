"""
decision_core.py — the PURE entry-decision logic shared by the live bot and the
backtester.

WHY THIS MODULE EXISTS (backtest fidelity, Phase 1)
───────────────────────────────────────────────────
For a backtest to be trustworthy enough to size real money against, it must make
the *same* entry decisions the live bot makes — not an approximation. Before this
module existed, ``buying.run_market_open_buys`` (live) and ``backend/backtester.py``
(research) were two independent implementations of "which breakout do we buy and
how big", and they had already drifted: the backtester still modelled retired exit
rules and a reduced set of entry gates. Any profitability number it produced was
an answer about a strategy the bot no longer runs.

This module extracts the decision — ranking, the per-trigger gate ladder, and
position sizing — into **pure functions with no I/O**. They take plain data
(trigger dicts, current holdings, cash, price, a config snapshot) and return a
``Decision``. They never touch IBKR, Supabase, Telegram, or the clock. That makes
them:

  • callable identically by the live path and the backtester → parity by
    construction, not by discipline;
  • unit-testable without mocking a broker;
  • the single place a gate's threshold or ordering can change.

WHAT STAYS OUT (deliberately)
─────────────────────────────
Everything with a side effect or an external dependency remains in ``buying.py``:
fetching live cash / price from IBKR, computing trading-days-to-earnings from the
NYSE calendar, placing the order, waiting for the fill, writing the position and
the audit rows, and sending notifications. ``buying.py`` computes those inputs,
asks this module for the verdict, then performs the I/O the verdict implies.

THE GATE LADDER (order is contractual — the live audit trail depends on it)
──────────────────────────────────────────────────────────────────────────
Per trigger, in ``final_score``-descending order:
  1. already-held            → SKIP ALREADY_HELD
  2. cooling-off             → SKIP COOLING_OFF
  3. AI D-grade veto         → SKIP AI_VETO
  4. earnings blackout       → SKIP EARNINGS_IMMINENT
  5. no AI score (NULL)      → SKIP NO_AI_SCORE   (fail closed)
  6. score < floor           → SKIP SCORE_FLOOR
  --- sizing computed by caller (needs live cash) ---
  7. capacity reached        → HALT (bulk SLOTS_FULL from here to end)
  8. cash < MIN_POSITION     → SKIP INSUFFICIENT_CASH
  9. volume surge (BREAKOUT) → SKIP SCORE_FLOOR (vol variant)
 10. pre-breakout pivot dist → SKIP BELOW_PIVOT
  --- price fetched by caller (needs IBKR) ---
 11. extended above pivot    → SKIP EXTENDED_ABOVE_PIVOT
 12. below pivot (breakdown) → SKIP BELOW_PIVOT
 13. shares <= 0             → SKIP SHARES_ZERO
     otherwise              → BUY (shares)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import trigger_audit as ta

# ── Decision verbs ────────────────────────────────────────────────────────────
BUY = "BUY"
SKIP = "SKIP"
PROCEED = "PROCEED"          # eligible so far — caller continues the ladder
HALT_CAPACITY = "HALT"       # book full mid-cycle — caller breaks + bulk-sweeps


@dataclass
class Decision:
    """The verdict for one trigger at one point in the ladder.

    ``audit`` carries the exact keyword arguments the live path forwards to
    ``trigger_audit.record_trigger_decision`` so the research trail is unchanged
    by the extraction. ``verb`` is the audit verb string ("SKIPPED"/"BOUGHT"),
    distinct from ``action`` which drives control flow.
    """
    action: str                              # BUY | SKIP | PROCEED | HALT_CAPACITY
    reason_code: str = ""                    # trigger_audit.* code
    detail: str = ""
    shares: int = 0
    position_size: float = 0.0
    audit: dict = field(default_factory=dict)

    @property
    def verb(self) -> str:
        return "BOUGHT" if self.action == BUY else "SKIPPED"


@dataclass
class DecisionConfig:
    """Immutable snapshot of every threshold the entry decision reads.

    Built from the live ``execution_agent`` module via ``config_from_module`` so
    production uses production constants, while a backtest can construct its own
    to sweep them. Passing a snapshot (not the live module) keeps the decision
    functions pure and their inputs explicit.
    """
    max_positions: int
    min_trigger_score: float
    min_pre_breakout_score: float
    min_relaxed_trigger_score: float
    min_vol_surge_gate: float
    max_pivot_extension: float
    max_pivot_breakdown: float
    max_pre_breakout_pivot_dist: float
    min_position_size: float
    price_safety_reserve: float
    earnings_blackout_trading_days: int


def config_from_module(ea) -> DecisionConfig:
    """Snapshot the live thresholds off the execution_agent module."""
    return DecisionConfig(
        max_positions=ea.MAX_POSITIONS,
        min_trigger_score=ea.MIN_TRIGGER_SCORE,
        min_pre_breakout_score=ea.MIN_PRE_BREAKOUT_SCORE,
        min_relaxed_trigger_score=ea.MIN_RELAXED_TRIGGER_SCORE,
        min_vol_surge_gate=ea.MIN_VOL_SURGE_GATE,
        max_pivot_extension=ea.MAX_PIVOT_EXTENSION,
        max_pivot_breakdown=ea.MAX_PIVOT_BREAKDOWN,
        max_pre_breakout_pivot_dist=ea.MAX_PRE_BREAKOUT_PIVOT_DIST,
        min_position_size=ea.MIN_POSITION_SIZE,
        price_safety_reserve=ea.PRICE_SAFETY_RESERVE,
        earnings_blackout_trading_days=ea.EARNINGS_BLACKOUT_TRADING_DAYS,
    )


# ── Ranking ───────────────────────────────────────────────────────────────────
def trigger_sort_key(trigger: dict):
    """The live ranking key: final_score, then quality_score, then ai_rating, 0.

    Mirrors buying.run_market_open_buys' sort exactly, including the ``or``
    fallthrough that treats 0 / None identically.
    """
    return (trigger.get("final_score")
            or trigger.get("quality_score")
            or trigger.get("ai_rating")
            or 0)


def rank_triggers(triggers: list[dict]) -> list[dict]:
    """Return triggers sorted highest-conviction first (descending)."""
    return sorted(triggers, key=trigger_sort_key, reverse=True)


# ── Score helpers ─────────────────────────────────────────────────────────────
def candidate_score_of(trigger: dict):
    """The score the floor is applied to: adjusted_score if present, else
    final_score. Returns None when the trigger was never AI-rated (fail closed)."""
    adj = trigger.get("adjusted_score")
    return adj if adj is not None else trigger.get("final_score")


def min_score_for(trigger_type: str, cfg: DecisionConfig) -> float:
    if trigger_type == "PRE_BREAKOUT_RELAXED":
        return cfg.min_relaxed_trigger_score
    if trigger_type == "PRE_BREAKOUT":
        return max(cfg.min_trigger_score, cfg.min_pre_breakout_score)
    return cfg.min_trigger_score


# ── Phase A: price-independent eligibility (gates 1–6) ────────────────────────
def evaluate_eligibility(trigger: dict, *, held_tickers, cooled_map,
                         days_to_earnings, cfg: DecisionConfig) -> Decision:
    """Gates 1–6. Returns a SKIP Decision, or PROCEED when the trigger is still
    eligible and the caller should compute sizing.

    ``held_tickers``     : container of tickers currently in the portfolio.
    ``cooled_map``        : {ticker: reason} from reason-aware cooling-off, or {}.
    ``days_to_earnings``  : trading days to next earnings, or None (fail open).
    """
    ticker = trigger["ticker"]

    # 1. already held
    if ticker in held_tickers:
        return Decision(SKIP, ta.ALREADY_HELD, "Already an open position")

    # 2. cooling-off (reason-aware; caller precomputes the map)
    cool_reason = cooled_map.get(ticker)
    if cool_reason:
        return Decision(SKIP, ta.COOLING_OFF,
                        f"Reason-aware cooling-off: {cool_reason}")

    # 3. AI D-grade veto
    if trigger.get("ai_grade") == "D":
        return Decision(SKIP, ta.AI_VETO, "D-grade, conviction < 30")

    # 4. earnings blackout (None fails OPEN)
    if (days_to_earnings is not None
            and days_to_earnings <= cfg.earnings_blackout_trading_days):
        return Decision(
            SKIP, ta.EARNINGS_IMMINENT,
            f"earnings in {days_to_earnings} trading day(s) "
            f"(<= {cfg.earnings_blackout_trading_days}-day blackout)")

    # 5. no AI score → fail closed
    candidate_score = candidate_score_of(trigger)
    if candidate_score is None:
        return Decision(SKIP, ta.NO_AI_SCORE,
                        "final_score NULL — not rated by ai_evaluator.py")

    # 6. score floor
    trigger_type = str(trigger.get("trigger_type") or "BREAKOUT")
    min_score = min_score_for(trigger_type, cfg)
    if float(candidate_score) < float(min_score):
        return Decision(
            SKIP, ta.SCORE_FLOOR,
            f"score {candidate_score} < floor {min_score}",
            audit={"candidate_score": float(candidate_score),
                   "min_score": float(min_score)})

    return Decision(PROCEED)


# ── Position sizing ───────────────────────────────────────────────────────────
def equity_capped_position_size(available_cash: float, remaining_slots: int,
                                equity: float, max_positions: int) -> float:
    """Dollar allocation for one new position, never exceeding an equal-weight
    share of total account equity (equity / max_positions).

    Base rule: free cash spread over the still-open slots. Hard ceiling: one
    equal-weight slot of the whole account, added after the 2026-09-21 oversizing
    incident where a nearly-full book funnelled all free cash into a single slot.
    When equity <= 0 it is treated as UNKNOWN and the ceiling is skipped (a zero
    cap would block every buy). See
    decisions/2026-09-21_equity-capped-position-size.md.
    """
    remaining_slots = max(1, int(remaining_slots))
    base = max(0.0, float(available_cash)) / remaining_slots
    if equity and equity > 0 and max_positions and max_positions > 0:
        equity_cap = float(equity) / int(max_positions)
        return min(base, equity_cap)
    return base


# ── Phase B: capacity, cash and market-confirmation gates (7–10) ──────────────
def evaluate_capacity(stock_held_count: int, cfg: DecisionConfig) -> Decision:
    """Gate 7. HALT_CAPACITY when the book is already full mid-cycle; the caller
    breaks the loop and bulk-records SLOTS_FULL for the remaining triggers."""
    if stock_held_count >= cfg.max_positions:
        return Decision(
            HALT_CAPACITY, ta.SLOTS_FULL,
            f"Capacity reached mid-cycle at {stock_held_count}/{cfg.max_positions}")
    return Decision(PROCEED)


def evaluate_cash(available_cash: float, remaining_slots: int,
                  cfg: DecisionConfig) -> Decision:
    """Gate 8. SKIP INSUFFICIENT_CASH when free cash is below the floor."""
    if available_cash < cfg.min_position_size:
        return Decision(
            SKIP, ta.INSUFFICIENT_CASH,
            f"available ${available_cash:,.0f} < floor ${cfg.min_position_size:,.0f}",
            audit={"available_cash": available_cash,
                   "slots_free": remaining_slots})
    return Decision(PROCEED)


def evaluate_market_gates(trigger: dict, cfg: DecisionConfig) -> Decision:
    """Gates 9–10, price-independent market-confirmation checks.

    9. Hard volume-surge gate — BREAKOUT rows only (the column is a volume
       CONTRACTION ratio for PRE_BREAKOUT rows, where lower is better, so gating
       it would invert the selection).
    10. PRE_BREAKOUT distance-below-52W-high gate.
    """
    trigger_type = str(trigger.get("trigger_type") or "BREAKOUT")

    # 9. volume surge (confirmed breakouts only)
    trigger_vol_surge = float(trigger.get("volume_surge") or 0)
    if trigger_type == "BREAKOUT" and trigger_vol_surge < cfg.min_vol_surge_gate:
        return Decision(
            SKIP, ta.SCORE_FLOOR,
            f"vol_surge {trigger_vol_surge:.2f}x < MIN_VOL_SURGE_GATE "
            f"{cfg.min_vol_surge_gate:.2f}x")

    # 10. pre-breakout pivot distance
    if trigger_type in ("PRE_BREAKOUT", "PRE_BREAKOUT_RELAXED"):
        stored_pivot_dist = trigger.get("pivot_distance_pct")
        if stored_pivot_dist is not None:
            pivot_dist = float(stored_pivot_dist)
            if pivot_dist < -(cfg.max_pre_breakout_pivot_dist * 100):
                return Decision(
                    SKIP, ta.BELOW_PIVOT,
                    f"PRE_BREAKOUT {abs(pivot_dist):.1f}% below 52W pivot "
                    f"(max {cfg.max_pre_breakout_pivot_dist * 100:.0f}%)")
    return Decision(PROCEED)


# ── Phase C: price-dependent gates and final sizing (11–13) ───────────────────
def evaluate_price_gates(trigger: dict, current_price: float, pivot_price: float,
                         position_size: float, cfg: DecisionConfig) -> Decision:
    """Gates 11–13. Returns a BUY Decision with the share count, or a SKIP.

    ``current_price`` : the price the caller obtained (IBKR delayed, else prev
                        close). ``pivot_price`` : the trigger's close_price.
    """
    extension_pct = ((current_price - pivot_price) / pivot_price
                     if pivot_price > 0 else 0.0)

    # 11. extended above the buy zone (ceiling)
    if extension_pct > cfg.max_pivot_extension:
        return Decision(
            SKIP, ta.EXTENDED_ABOVE_PIVOT,
            f"{extension_pct * 100:.1f}% above pivot ${pivot_price:.2f} "
            f"(max {cfg.max_pivot_extension * 100:.0f}%)",
            audit={"price": current_price, "extension_pct": extension_pct})

    # 12. collapsed below the pivot (failed breakout — floor)
    if extension_pct < -cfg.max_pivot_breakdown:
        return Decision(
            SKIP, ta.BELOW_PIVOT,
            f"{abs(extension_pct) * 100:.1f}% below pivot ${pivot_price:.2f}",
            audit={"price": current_price, "extension_pct": extension_pct})

    # 13. share count after the flat safety reserve
    shares = int((position_size - cfg.price_safety_reserve) / current_price)
    if shares <= 0:
        return Decision(
            SKIP, ta.SHARES_ZERO,
            f"price ${current_price:.2f} too high for position size "
            f"${position_size:,.0f}",
            audit={"price": current_price, "shares": 0})

    return Decision(
        BUY, ta.BOUGHT, "", shares=shares, position_size=position_size,
        audit={"price": current_price, "extension_pct": extension_pct})
