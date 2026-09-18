"""Exit decision logic: WHERE a position should exit, and WHETHER it may.

Extracted verbatim from execution_agent.py on 2026-09-18. This module answers one
question -- given a position and a price, what is the exit level? -- and owns the
tunable constants behind that answer, together with the measured evidence that
justifies each one.

Deliberately PURE. Nothing here places an order, reads Supabase or touches IBKR;
those remain in execution_agent.py. The split is what lets these rules be read,
tested and replayed without a brokerage connection.

Constants live here rather than in config.py because config.py is reserved for
values shared ACROSS containers (MAX_POSITIONS, STOP_LOSS_PCT). These are
execution-agent-local, and co-locating them with the logic they govern keeps each
threshold next to the replay result that chose it.

See decisions/2026-09-18_execution-agent-split.md.
"""

import os
import datetime

from config import MAX_LOSS_PCT, STOP_LOSS_PCT

# ── Dynamic trailing stop tightening tiers ───────────────────────────────────
# Lever 1 (profit): unrealized gain % → trail %.
#
# Live-trade review showed a persistent pattern: modest winners were making new
# highs and then round-tripping a large share of the open profit before the sell
# rules reacted. On the 20 closed trades available on 2026-08-20, the 9 winners
# gave back $8,071 from their high-water marks before exit (avg $897, median
# 4.03% below the peak at sale). A simple HWM profit-lock beat the current exits:
# arm once the trade is up +5%, then cap give-back to 1.5% from the peak.
#
# This is intentionally aggressive. The rule is not trying to protect +20% to
# +50% leaders; it is trying to stop 4-9% winners from decaying into 0-4% exits.
# If the tightened screener later starts producing true power-hold leaders, this
# ladder must be revisited together with POWER_HOLD. Until then, bank the first
# leg rather than hoping a modest winner becomes an outlier.
#
# Entries are (threshold, trail_pct), listed highest-threshold-first.
TRAIL_PROFIT_TIERS: list[tuple[float, float]] = [
    ( 5.0, 0.015),   # ≥ 5% gain  → 1.5% trail from HWM
    ( 0.0, None),    # < 5%       → no change (base STOP_LOSS_PCT applies)
]
# The time lever that used to sit here (TRAIL_TIME_TIERS) is retired — see
# docs/retired_code.md. Tightening a stop purely because time has passed
# penalises a position for still working.

# ── The Prove-It Stop ─────────────────────────────────────────────────────────
# ONE question governs every loss-cutting exit: has this position ever CLOSED
# above the price we paid?
#
#   PHASE 1 — unproven. The breakout has not confirmed. Anchor to ENTRY.
#             Day 0:  1.0% below entry   (a breakout that fails on day one is
#                                         wrong immediately and cheaply)
#             Day 1+: 3.0% below entry   (a confirmed-but-slow name needs room
#                                         to shake out before it works)
#
#   PHASE 2 — proven. It closed above entry, so it earned patience. Anchor to
#             the PEAK.
#             peak gain >= 2.0%: floor at 1.0% BELOW entry — a trade that went
#                                green is never allowed to become a real loss
#             gain      >= 5.0%: 1.5% trail from the high water mark
#                                (TRAIL_PROFIT_TIERS, unchanged)
#
# WHY THIS REPLACES FIVE RULES
# The kill-switch, Thesis Stop, Early Dollar Stop, EMA-21 exit and Plateau exit
# were five different answers to two questions this asks once. Each carried its
# own window, its own anchor and its own threshold, and they raced each other:
# the Thesis Stop and Early Dollar Stop never fired ONCE in 30 closed trades
# because the kill-switch always got there first — but the kill-switch stopped
# looking after day 0, which is precisely how NBIX (-$2,261), DELL (-$1,283),
# RSI (-$1,390) and HWM (-$1,463) were allowed to run.
#
# EVIDENCE (5-minute replay of all 30 closed trades, reproducing live mechanics:
# 15-minute checks, arm_exit() 0.6% trail, 3.25h deadline)
#     what actually happened      -$6,548
#     rules shipped before this   -$4,069
#     Prove-It                    +$5,410   <- zero winners cut short
# Worst single loss falls from -$2,002 to -$1,140, and the -$1,140 is APH, an
# overnight gap that no stop of any kind can prevent. Every intraday bleed is
# cut small: NBIX -$2,261 -> -$230, CDNA -$1,539 -> +$256, RSI -$1,390 -> -$197.
#
# WHY PHASE 1 WIDENS AFTER DAY 0 RATHER THAN TIGHTENING
# Counter-intuitive but measured. Holding the tight 1.0% band through day 1 costs
# roughly $1,500-2,000 in winner damage: CPAY closed -2.24% on day 1 and low
# -2.88%, then ran to +8.95%. Day 0 is the only day on which the failing and
# working populations separate cleanly.
#
# WHY THE PHASE 2 FLOOR SITS 1% BELOW ENTRY, NOT AT IT
# An exact-breakeven floor flushes any position that pokes green and immediately
# retests entry. CPAY did exactly that on day 4 (high +3.60%, low -0.41%) and an
# at-entry floor sold it for $0, forfeiting +$1,189. One percent of slack is the
# difference between the floor protecting winners and clipping them: it turns
# CPAY into +$1,907 while still catching FRO and CDNA.
#
# See decisions/2026-09-04_prove-it-stop.md.
PROVE_IT_ENABLED           = os.getenv("PROVE_IT_ENABLED", "true").lower() == "true"
# Phase 1 — entry-anchored, applied while the position is unproven.
PROVE_IT_P1_DAY0_PCT       = float(os.getenv("PROVE_IT_P1_DAY0_PCT",       0.01))  # 1.0%
PROVE_IT_P1_LATER_PCT      = float(os.getenv("PROVE_IT_P1_LATER_PCT",      0.03))  # 3.0%
PROVE_IT_P1_DAY0_LAST_DAY  = int(os.getenv("PROVE_IT_P1_DAY0_LAST_DAY",       0))
# Phase 2 — peak gain that arms the give-back floor, and where the floor sits
# relative to entry (negative = below entry).
PROVE_IT_P2_ARM_GAIN_PCT   = float(os.getenv("PROVE_IT_P2_ARM_GAIN_PCT",   0.02))  # +2.0%
PROVE_IT_P2_FLOOR_PCT      = float(os.getenv("PROVE_IT_P2_FLOOR_PCT",     -0.01))  # -1.0%
# How far BELOW the Phase 1 trigger the resting IBKR stop is parked.
#
# Phase 1 is enforced by the bot: on the 15-minute cycle it arms a tight 0.6%
# trailing exit (arm_exit()) rather than selling at what is often a local trough.
# The replay shows that armed exit beats an immediate market sell by roughly
# $600 across the sample, so the bot must get first refusal.
#
# But the bot only looks every 15 minutes and cannot act at all when it is down
# or the market gaps. So a GTC order rests at the broker one slack-width below
# the same level: wide enough that it never front-runs the armed exit, tight
# enough to cap an overnight gap. Belt and braces, in that order.
PROVE_IT_BACKSTOP_SLACK_PCT = float(os.getenv("PROVE_IT_BACKSTOP_SLACK_PCT", 0.01))

# ── Smart OCA Managed Exit (queue-driven, see migrations/20260818_add_exit_requests.sql) ─
# A row in `exit_requests` asks the agent to exit a named position via an IBKR
# OCA pair rather than a market dump:
#     upper leg = LMT sell at an optimistic recovery target
#     lower leg = TRAIL sell that ratchets up behind any bounce
# One cancels the other. The agent drains the queue every monitoring cycle, so a
# request made at 11:00 acts at 11:00 — "first thing in the morning" is just the
# special case where the request was queued overnight.
#
# The legs are NOT placed at 09:30. The opening auction has the widest spreads
# and the wildest prints of the session; a limit computed off a 09:30 tick is
# computed off noise. We wait until the tape settles.
OCA_EXIT_ENABLED          = os.getenv("OCA_EXIT_ENABLED", "true").lower() == "true"
OCA_EXIT_SETTLE_MINUTE    = int(os.getenv("OCA_EXIT_SETTLE_MINUTE", 45))   # place from 09:45 ET
# Trail sizing for stop_mode='ATR_AUTO'. A trail tighter than the stock's own
# noise fires on the first random wiggle, which just reproduces "sell now" with
# extra steps and forfeits the upper leg entirely.
OCA_EXIT_ATR_FRACTION     = float(os.getenv("OCA_EXIT_ATR_FRACTION",     0.33))
OCA_EXIT_MIN_TRAIL_PCT    = float(os.getenv("OCA_EXIT_MIN_TRAIL_PCT",    0.015))  # 1.5%
OCA_EXIT_MAX_TRAIL_PCT    = float(os.getenv("OCA_EXIT_MAX_TRAIL_PCT",    0.040))  # 4.0%
OCA_EXIT_DEFAULT_ATR_PCT  = float(os.getenv("OCA_EXIT_DEFAULT_ATR_PCT",  3.0))
# Upper-leg sizing for limit_mode='ATR_AUTO' — the default for a bare insert.
#
# BREAKEVEN was the original default, but it anchors the target to the ENTRY
# price, so the bounce required is proportional to how much the position is
# already down: a name 5.5% underwater needs a 5.9% rally before the leg can
# fill, which is exactly when you least want to wait. ATR_AUTO anchors to the
# CURRENT price instead, so entry drops out of the maths and the target is
# always about half a day's move away — reachable regardless of the loss, and
# self-scaling to each stock's own volatility.
#
# Clamped at both ends: a very quiet name would otherwise get a target inside
# the spread, and a very volatile one a target no realistic bounce reaches.
OCA_EXIT_UPPER_ATR_FRACTION = float(os.getenv("OCA_EXIT_UPPER_ATR_FRACTION", 0.50))
OCA_EXIT_MIN_UPPER_PCT    = float(os.getenv("OCA_EXIT_MIN_UPPER_PCT",    0.0075))  # 0.75%
OCA_EXIT_MAX_UPPER_PCT    = float(os.getenv("OCA_EXIT_MAX_UPPER_PCT",    0.050))   # 5.0%
# Backstop applied in software each cycle: an OCA can sit unfilled indefinitely
# while the position bleeds, so bound both the price and the time.
OCA_EXIT_DEFAULT_FLOOR_PCT = float(os.getenv("OCA_EXIT_DEFAULT_FLOOR_PCT", 0.05))  # 5% below placement
OCA_EXIT_DEFAULT_EXPIRY_DAYS = int(os.getenv("OCA_EXIT_DEFAULT_EXPIRY_DAYS", 3))

# Route the *discretionary* Day 7+ exits through the Smart OCA queue instead of
# selling at market on whichever 15-minute tick happened to notice.
#
# Scoped to Day 7+ non-urgent rules ON PURPOSE. The Prove-It Stop
# (kill-switch, dollar stop, thesis stop) keep arm_exit(): a placed OCA
# suspends the automated ladder for up to OCA_EXIT_DEFAULT_EXPIRY_DAYS, which
# is exactly the wrong trade for a position that is actively failing.
# See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.
SMART_EXIT_FOR_RULES = os.getenv("SMART_EXIT_FOR_RULES", "true").lower() == "true"

# ── O'Neil 8-Week Hold Rule ───────────────────────────────────────────────────
# From "How to Make Money in Stocks": a stock that gains 20%+ within 3 weeks of a
# proper breakout is behaving like a genuine market leader and should be held for
# at least 8 weeks rather than trimmed on the first wobble.
#
# This is the mechanism that would let a position become the outsized winner
# CAN SLIM expectancy depends on. While a position is in its power-hold window we
# suppress the DISCRETIONARY exits (Prove-It Stop, Rank & Replace) AND widen the
# trailing stop to POWER_HOLD_TRAIL_PCT (see below). The trailing stop is never
# removed — it remains the disaster backstop, so this bounds opportunity cost,
# never risk.
#
# TRIGGER LOWERED 20% -> 10% (2026-09-04). At +20% the rule was unreachable in
# practice: it never armed once across 30 closed trades, because it never armed
# once across ANY closed trade. The realised winner distribution tops out well
# below the level the rule was calibrated for — the +20%-in-3-weeks leader it was
# built to protect is a population this screener has not yet produced.
#
# A rule that cannot fire protects nothing. 10% sits inside the observed
# distribution (MPC +6.4%, LPG +7.0%, CPAY +9.0% peak) without being trivially
# easy to reach, so it can begin to bind on the genuinely strong names while
# still requiring roughly double the peak of a typical winner.
#
# ⚠️ UNVALIDATED AS AN OPTIMUM, AND PROVABLY INERT AS SHIPPED. Measured
# 2026-09-18 with a run-on replay window (research/exit_rule_replay.py --runon):
# 13 of 50 closed trades reached +10% within 21 days of entry, but the bot was
# still holding exactly ONE of them. The earlier claim that no trade ever
# reached +10% was an artefact of a harness that truncated price history at the
# realised exit and so could not see a stock's path after we sold it.
#
# The rule therefore does not fail for want of +10% names. It fails because the
# +5% ladder rung below clamps the trail to 1.5% and sells at roughly half the
# trigger — exactly what the note at TRAIL_PROFIT_TIERS predicted. Confirmed by
# replay: power hold at +10% is byte-identical to shipped at every trail width.
# Lowering this number alone will NOT fix that; the ladder would still sell
# first. The two must be retuned together or not at all, and not before slot
# opportunity cost is modelled — see
# decisions/2026-09-18_runon-window-winners-run.md.
POWER_HOLD_ENABLED        = os.getenv("POWER_HOLD_ENABLED", "true").lower() == "true"
POWER_HOLD_GAIN_PCT       = float(os.getenv("POWER_HOLD_GAIN_PCT", 10.0))
POWER_HOLD_TRIGGER_DAYS   = int(os.getenv("POWER_HOLD_TRIGGER_DAYS", 21))   # 3 weeks
POWER_HOLD_DURATION_DAYS  = int(os.getenv("POWER_HOLD_DURATION_DAYS", 56))  # 8 weeks
# Trail width applied WHILE a position is power-held, replacing the profit ladder.
#
# Without this the rule was self-defeating: TRAIL_PROFIT_TIERS tightens the trail
# well below the gain that arms the power hold — under the ladder in force at the
# time, to 6.5% at the then-current +20% trigger — so the ladder strangled the
# leaders the rule exists to protect. Instrumenting the
# backtest showed the rule armed on 9% (growth) / 6% (broad) of trades and then
# *100% of those still exited on the trailing stop*, making it inert.
#
# The current HWM profit lock makes this worse, not better: it clamps to 1.5% from
# the peak at only +5% gain, so by the time a position reaches POWER_HOLD_GAIN_PCT
# it is already on the tightest rung. Bypassing the ladder while power-held is
# therefore load-bearing — see decisions/2026-08-20_hwm-profit-lock-first-leg.md.
#
# Widening the trail while power-held recovers the intended behaviour. The effect
# is large, monotonic in the trail width, and consistent across both universes
# (growth +27.0% -> +66.3% CAGR, broad +27.4% -> +44.5% at 0.30). Crucially it
# does NOT increase risk: the rule only arms after a position is already well up,
# so the worst trade is unchanged at -10% and max drawdown is flat (17.6% / 14.5%).
# NOTE: those figures were measured with the +20% trigger. The move to +10% widens
# the trail on a weaker class of position and is NOT covered by that backtest.
# 0.30 is chosen over removing the stop entirely (+76.6% / +48.8%) to retain a
# disaster backstop, since the upside rests on very few trades.
POWER_HOLD_TRAIL_PCT      = float(os.getenv("POWER_HOLD_TRAIL_PCT", 0.30))

def hard_stop_price(pos: dict, buy_price: float,
                    highest_unrealized_pct: float,
                    power_held: bool = False,
                    days_held: int = 0) -> float:
    """
    The absolute price a STATIC broker-side hard stop should rest at right now.

    This is the disconnect-proof floor. Unlike the trailing stop — which freezes
    at its last-placed % when the bot drops and then only trails from the HWM —
    the hard stop is a static STP that does not move on its own, so a bot outage
    cannot let a position bleed past it. It is placed in an OCA group with the
    trailing stop (see place_protective_stops); whichever fills first cancels the
    other.

    Three levels, entry-anchored in every case:

      • PHASE 1 (unproven only): the Prove-It band, one backstop-slack wider —
        entry * (1 - p1_pct(days_held)) * (1 - PROVE_IT_BACKSTOP_SLACK_PCT).
        This leg USED to be carried by the trailing order, which ratcheted its
        anchor up with price and converted a loss cap into a profit-taker; see
        decisions/2026-09-18_phase1-static-backstop.md. It is static here, so it
        cannot chase the HWM. Proven-but-unarmed positions are NOT included —
        that is the separately-rejected `p2_unarmed_keeps_p1` hypothesis.
      • Proven AND armed (peak >= +2%): the give-back floor, one backstop-slack
        wider than the bot's own stop — entry * (1 + PROVE_IT_P2_FLOOR_PCT)
        * (1 - PROVE_IT_BACKSTOP_SLACK_PCT) ~= entry * 0.98. A proven green
        trade's floor becomes broker-GUARANTEED, not dependent on the bot being
        online to re-pin the trail.
      • Never looser than the disaster floor, entry * (1 - MAX_LOSS_PCT). The
        2026-09-07 --basetrail replay showed a 7% always-on base is free in
        normal operation (the Prove-It floor fires first) while capping the
        worst case.

    Because every level is entry-anchored and static, none can chase the HWM up
    and clip a winner — which is exactly why the replay let us tighten it for
    free where a 5% *trailing* base could not.

    The Phase 1 level WIDENS once, from the day-0 band to the day-1+ band,
    because the band itself widens by design ("a confirmed-but-slow name needs
    room to shake out"). That is the single documented exception to the caller's
    ratchet-up-only rule, and it applies only while unproven — see
    monitor_portfolio_intraday(). Once proven the floor only ever rises.

    Under power-hold the tight floor is suppressed back to the disaster level so
    the widened trail (POWER_HOLD_TRAIL_PCT) can actually let the leader run —
    the same single exception the trailing stop already makes.
    """
    disaster = buy_price * (1.0 - MAX_LOSS_PCT)
    if power_held or buy_price <= 0:
        return round(disaster, 2)
    if (PROVE_IT_ENABLED
            and prove_it_is_proven(pos, highest_unrealized_pct)
            and highest_unrealized_pct >= PROVE_IT_P2_ARM_GAIN_PCT * 100.0):
        floor = buy_price * (1.0 + PROVE_IT_P2_FLOOR_PCT)
        armed_floor = floor * (1.0 - PROVE_IT_BACKSTOP_SLACK_PCT)
        return round(max(disaster, armed_floor), 2)
    if PROVE_IT_ENABLED and not prove_it_is_proven(pos, highest_unrealized_pct):
        # PHASE 1 ONLY — unproven. The entry-anchored band, set one
        # backstop-slack below the level the bot itself polls, so the resting
        # order is a genuine backstop and cannot fire before the bot does.
        #
        # Deliberately NOT extended to proven-but-unarmed positions. Giving that
        # window the Phase 1 band is the `p2_unarmed_keeps_p1` hypothesis, which
        # was measured on 2026-09-10 and REJECTED (-$1,691, entirely DXCM); see
        # decisions/2026-09-10_prove-it-unarmed-window-measured-not-closed.md.
        # That window keeps the disaster floor until the re-run owed on the
        # post-backfill sample says otherwise.
        band = buy_price * (1.0 - prove_it_p1_threshold_pct(days_held))
        p1_backstop = band * (1.0 - PROVE_IT_BACKSTOP_SLACK_PCT)
        return round(max(disaster, p1_backstop), 2)
    return round(disaster, 2)

def safe_hard_stop(desired: float, current_price: float,
                   stored: float) -> float:
    """
    The hard-stop price it is safe to actually PLACE right now.

    A SELL stop resting at or above the market triggers immediately and sells at
    market. So a raise that lands above current price is not protection — it is
    an instant liquidation at whatever the book happens to be.

    This is not hypothetical. The Phase 1 floor moved from the disaster level
    (entry -7%) to the entry-anchored band (entry -2% / -4%) in
    decisions/2026-09-18_phase1-static-backstop.md. Any open position already
    trading between those two levels would have had a stop placed ABOVE it on
    the very next monitor cycle and been sold on the spot.

    When the desired level is unreachable, keep whatever is already resting and
    let the bot-side exit act instead — the same rule prove_it_trail_pct() uses
    when its level is already through the price.
    """
    if current_price <= 0:
        return stored
    if desired >= current_price:
        return stored
    return desired


def _position_atr_pct(pos: dict) -> tuple[float, str]:
    """
    The ATR percent both OCA legs are sized from, with its provenance.

    Note this is `entry_atr_pct` — the ATR recorded when the position was
    opened, not today's. It is the only ATR the position row carries. For a
    name whose volatility has since expanded this sizes both legs slightly
    tight; the hard floor and expiry backstops bound that.
    """
    atr_pct = pos.get("entry_atr_pct")
    if not atr_pct or float(atr_pct) <= 0:
        return OCA_EXIT_DEFAULT_ATR_PCT, "default (no ATR on record)"
    return float(atr_pct), "entry_atr_pct"

def resolve_oca_trail_pct(pos: dict, stop_mode: str, stop_value) -> tuple[float, str]:
    """
    Resolves the OCA lower leg's trailing percent.

    'ATR_AUTO' scales the trail to the stock's own volatility. This matters more
    than it looks: a 1% trail on a name with a 7% average true range fires on
    the first tick of ordinary noise, which cancels the upper leg and turns the
    whole OCA into an expensive market order.
    """
    if stop_mode == "TRAIL_PCT" and stop_value:
        return float(stop_value) / 100.0, f"fixed {float(stop_value):.2f}%"

    atr_pct, source = _position_atr_pct(pos)

    raw = (atr_pct / 100.0) * OCA_EXIT_ATR_FRACTION
    trail = max(OCA_EXIT_MIN_TRAIL_PCT, min(OCA_EXIT_MAX_TRAIL_PCT, raw))
    note = f"auto: {OCA_EXIT_ATR_FRACTION:.0%} of {atr_pct:.2f}% ATR ({source})"
    if abs(trail - raw) > 1e-9:
        note += f", clamped to {trail*100:.2f}%"
    return trail, note

def resolve_oca_limit_price(pos: dict, limit_mode: str, limit_value,
                            ref_price: float, limit_cap=None) -> float | None:
    """
    Resolves the OCA upper leg's limit price from stored *intent*.

    Requests are frequently queued outside market hours, so a literal price
    captured at request time would be stale by the time it is placed. Only
    'ABS' pins an absolute price; everything else is resolved here against the
    live reference price or the position's entry.

    'ATR_AUTO' is the default and the one to reach for on a force sell: it
    targets the current price plus a fraction of the stock's ATR, so the leg is
    reachable within about half a session no matter how far underwater the
    position is. BREAKEVEN, by contrast, demands a bounce proportional to the
    loss already taken.

    limit_cap is the ceiling on the resolved target, and exists because
    PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the
    open means the better the gap, the greedier the target becomes, so it never
    takes the gift it was waiting for. Capping at (typically) breakeven turns
    "sell 4.5% above wherever it opens" into "sell 4.5% above the open, but
    never hold out for more than breakeven" — which is what an exit plan
    actually wants. A capped target that lands below the market is fine: a SELL
    limit cannot fill under its limit price, so it simply fills at the better
    prevailing bid.
    """
    entry = float(pos.get("buy_price") or 0)
    mode = (limit_mode or "ATR_AUTO").upper()

    if mode == "NONE":
        return None
    elif mode == "ATR_AUTO":
        # Anchored to the CURRENT price, not the entry, so the target stays
        # reachable no matter how far underwater the position is. See the
        # OCA_EXIT_UPPER_ATR_FRACTION comment for why this is the default.
        if not ref_price:
            return None
        atr_pct, _ = _position_atr_pct(pos)
        raw  = (atr_pct / 100.0) * OCA_EXIT_UPPER_ATR_FRACTION
        frac = max(OCA_EXIT_MIN_UPPER_PCT, min(OCA_EXIT_MAX_UPPER_PCT, raw))
        price = ref_price * (1 + frac)
    elif mode == "ABS":
        price = float(limit_value) if limit_value else None
    elif mode == "BREAKEVEN":
        price = entry or None
    elif mode == "PCT_FROM_ENTRY":
        price = entry * (1 + float(limit_value or 0) / 100.0) if entry else None
    elif mode == "PCT_FROM_PRICE":
        price = ref_price * (1 + float(limit_value or 0) / 100.0) if ref_price else None
    else:
        return None

    if price and limit_cap and float(limit_cap) > 0:
        price = min(price, float(limit_cap))
    return price

def prove_it_is_proven(pos: dict, highest_unrealized_pct: float = 0.0) -> bool:
    """
    Has this position ever CLOSED above the price we paid?

    This single question selects the Prove-It phase, so it is the most
    load-bearing predicate in the exit ladder. `closed_above_entry` is latched
    True by the EOD block the first time a close prints above entry and is never
    cleared afterwards — a breakout confirms only once.

    Fails SAFE. A missing column (migration not yet applied) reads as None, which
    must never be treated as "unproven": that would apply the tight Phase 1 band
    to a working position. When the latch is unavailable, every available sign
    that the position has traded above entry counts as proof, which is
    deliberately more generous than the close-based latch it stands in for.
    """
    latch = pos.get("closed_above_entry")
    if latch is not None:
        return bool(latch)
    try:
        buy_price = float(pos.get("buy_price") or 0)
    except (TypeError, ValueError):
        return True
    if buy_price <= 0:
        return True
    return (
        highest_unrealized_pct > 0
        or float(pos.get("hwm_price") or 0) > buy_price
        or float(pos.get("intraday_high_today") or 0) > buy_price
    )

def prove_it_p1_threshold_pct(days_held: int) -> float:
    """
    Phase 1 band for a given day of the hold, as a positive fraction below entry.

    Widens after the entry day rather than tightening. A breakout that fails on
    day 0 is wrong immediately; from day 1 the failing and working populations
    overlap, and holding the tight band through day 1 costs far more in clipped
    winners than it saves in cut losers.
    """
    if days_held <= PROVE_IT_P1_DAY0_LAST_DAY:
        return PROVE_IT_P1_DAY0_PCT
    return PROVE_IT_P1_LATER_PCT

def prove_it_stop_level(pos: dict, buy_price: float, days_held: int,
                        highest_unrealized_pct: float) -> tuple[float | None, str]:
    """
    The price at which this position should be protected right now, and which
    phase produced it.

    Phase 1 (unproven) anchors to ENTRY: the breakout has not confirmed, so the
    only meaningful reference is what we paid. Phase 2 (proven) anchors to the
    give-back floor once the peak gain has armed it: the trade went green, so it
    is never allowed to become a real loss. Above +5% the profit ladder in
    TRAIL_PROFIT_TIERS takes over and is tighter than either.

    Returns (None, phase) when no Prove-It level applies — an unarmed Phase 2
    position is governed by the base trailing stop alone.
    """
    if not PROVE_IT_ENABLED or buy_price <= 0:
        return None, "disabled"
    if prove_it_is_proven(pos, highest_unrealized_pct):
        if highest_unrealized_pct < PROVE_IT_P2_ARM_GAIN_PCT * 100.0:
            return None, "phase2-unarmed"
        return buy_price * (1.0 + PROVE_IT_P2_FLOOR_PCT), "phase2"
    return buy_price * (1.0 - prove_it_p1_threshold_pct(days_held)), "phase1"

def prove_it_trail_pct(level: float | None, current_price: float,
                       phase: str) -> float | None:
    """
    Trailing % that parks the resting IBKR stop on `level`.

    IBKR's trailingPercent is measured from the high water mark, and the anchor
    RESETS whenever the order is cancelled and re-placed — which is exactly what
    the tightening block does. So the percentage must be solved against the
    CURRENT price, not a historical peak, or the stop lands somewhere nobody
    intended.

    PHASE 1 RETURNS None, AND MUST CONTINUE TO.
    This function once solved a trail for Phase 1 too, sitting
    PROVE_IT_BACKSTOP_SLACK_PCT behind the band. That was a defect, because the
    order it produces is orderType='TRAIL' and a TRAIL anchor RATCHETS UP with
    the high water mark. The bot's one-way rule then refuses to widen it back,
    so on any position that rallied before fading, a stop written to cap a LOSS
    climbed into profit and fired as a profit-taker. SMTC (2026-09-18) was
    stopped out 23 minutes after entry at +0.76% by an order intended to rest at
    -2.0%. Phase 1 is now carried by the STATIC hard-stop leg instead — see
    hard_stop_price() and decisions/2026-09-18_phase1-static-backstop.md.

    In Phase 2 the resting order IS the mechanism, so it sits exactly on the
    floor. That is safe because the Phase 2 level is itself peak-anchored and
    one-way by design: it is supposed to rise.
    """
    if level is None or current_price <= 0:
        return None
    if phase == "phase1":
        return None
    if level >= current_price:
        # Already at or through the level. Nothing sane to place; the bot-side
        # exit is what acts here.
        return None
    return round(1.0 - (level / current_price), 4)

def _compute_dynamic_trail_pct(
    unrealized_pct: float,
    calendar_days: int,
    current_pct: float,
    prove_it_pct: float | None = None,
) -> float | None:
    """
    Returns a tighter trailing stop % if the position has crossed a new tier,
    otherwise returns None (no change needed).

    Two independent levers — the tighter of the two always wins:
      Lever 1 (profit):   unrealized gain % -> TRAIL_PROFIT_TIERS
      Lever 2 (Prove-It): the trail % that pins the resting IBKR stop at the
                          current Prove-It level (see prove_it_trail_pct())

    `calendar_days` is retained for signature stability and for callers that
    still report it; the time lever it fed (TRAIL_TIME_TIERS) is retired — see
    docs/retired_code.md.

    One-way only: result is always strictly less than current_pct.
    Never loosens a stop (a position at 5% trail stays at 5% even if it
    briefly dips below a profit tier threshold).

    That one-way rule is what turns the Prove-It lever into a FIXED floor rather
    than a trail. As price rises, the % needed to keep the stop at the floor
    grows, is looser than what is already placed, and is therefore rejected —
    so the stop stays put. As price falls back toward the floor the required %
    shrinks, is tighter, and is applied — pinning the stop exactly on the floor.
    """
    # Profit lever: find highest threshold the gain has crossed
    profit_trail: float | None = None
    for threshold, pct in TRAIL_PROFIT_TIERS:
        if unrealized_pct >= threshold:
            profit_trail = pct
            break

    candidates = [p for p in (profit_trail, prove_it_pct) if p is not None]
    if not candidates:
        return None

    new_pct = min(candidates)   # tighter of the two levers

    # Compare at the precision IBKR actually places the order. place_trailing_stop
    # submits trailingPercent = round(pct * 100, 2), so any decrease smaller than
    # 0.01% yields a byte-identical resting order. Comparing the raw floats instead
    # let the Prove-It floor lever — which recomputes a slightly different % every
    # cycle as price ticks — clear `new_pct < current_pct` by a sub-basis-point
    # margin on every pass, re-placing the same stop and firing a "4.9% → 4.9%"
    # notification each time (the DHT churn/spam observed 2026-09-07). Only act
    # when the placed value would genuinely change.
    if round(new_pct * 100, 2) < round(current_pct * 100, 2):
        return new_pct
    return None

def is_power_hold_active(pos: dict, calendar_days: int) -> bool:
    """
    O'Neil 8-week hold rule.

    True while a position is inside its protected window: it gained
    POWER_HOLD_GAIN_PCT or more within POWER_HOLD_TRIGGER_DAYS of entry, and is
    still within POWER_HOLD_DURATION_DAYS of entry.

    Callers must use this to suppress DISCRETIONARY exits, and to widen the
    trailing stop to POWER_HOLD_TRAIL_PCT. The trailing stop is never suspended,
    so a protected position can still be stopped out if it genuinely breaks down.

    NOTE: the `power_hold` column must be migrated (migrations/20260804_add_power_hold.sql).
    Without it the flag cannot persist, so the fallback below only holds while
    calendar_days <= POWER_HOLD_TRIGGER_DAYS — the rule would silently expire at
    day 21 instead of day 56, losing most of its intended effect.
    """
    if not POWER_HOLD_ENABLED:
        return False
    if calendar_days > POWER_HOLD_DURATION_DAYS:
        return False

    # The qualifying run must have happened inside the trigger window. Once the
    # flag is set we keep honouring it, so a later pullback cannot cancel it.
    if pos.get("power_hold"):
        return True

    peak_gain = float(pos.get("highest_unrealized_pct") or 0.0)
    return peak_gain >= POWER_HOLD_GAIN_PCT and calendar_days <= POWER_HOLD_TRIGGER_DAYS

def sell_state_code(pos: dict, prove_it_phase: str, power_held: bool,
                    peak_pct: float) -> str | None:
    """The single governing exit regime for a position this cycle.

    Precedence follows the monitor loop: an armed exit governs everything, then
    the power-hold widening, then the profit-lock ladder (peak >= the first
    TRAIL_PROFIT_TIERS threshold), then the Prove-It phase. Returns None when no
    regime applies (Prove-It disabled), so the caller tracks nothing.
    """
    if pos.get("exit_armed"):
        return "EXITING"
    if power_held:
        return "POWER_HOLD"
    if TRAIL_PROFIT_TIERS and peak_pct >= TRAIL_PROFIT_TIERS[0][0]:
        return "PROFIT_LOCKED"
    if prove_it_phase == "phase2":
        return "PROVEN_FLOOR"
    if prove_it_phase == "phase2-unarmed":
        return "PROVEN"
    if prove_it_phase == "phase1":
        return "UNPROVEN"
    return None

def _infer_exit_type(reason: str) -> str:
    """Classify an exit reason into the breakout_learnings exit_type bucket."""
    r_lower = str(reason or "").lower()
    if "rank & replace" in r_lower or "rank and replace" in r_lower:
        return "rank_replace"
    if "time-stop" in r_lower or ("mandatory" in r_lower and "time" in r_lower):
        return "time_stop"
    if "break-even" in r_lower or "hwm break" in r_lower:
        return "break_even"
    if "ema" in r_lower or "moving average" in r_lower:
        return "ma_exit"
    if "hard stop" in r_lower:
        return "hard_stop"
    if "stop" in r_lower:
        return "stop_loss"
    if "rotation" in r_lower or "param" in r_lower or "drift" in r_lower:
        return "rotation"
    return "manual"
