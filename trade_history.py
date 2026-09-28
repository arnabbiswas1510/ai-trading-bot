"""Trade-history persistence + exit-context formatting, extracted from
execution_agent.py (see decisions/2026-09-27_execution-agent-modular-split.md).

Standalone: references no patched siblings, so no ``ea.`` indirection is needed.
execution_agent re-exports these via ``from trade_history import (...)`` so any
``execution_agent.insert_trade_history`` patch keeps resolving.
"""

# Longest reason the code will ever emit, regardless of schema. This is a
# runaway guard, not a schema limit -- it exists so a malformed position row can
# never generate an unbounded string.
SELL_REASON_RUNAWAY_LIMIT = 1000

# Fallback width used only when the database rejects the full reason. Matches
# the pre-2026-09-18 varchar(200) cap.
SELL_REASON_LEGACY_LIMIT = 200


def _clamp_reason(text: str, limit: int) -> str:
    """Trim a reason string to `limit` chars, preferring a comma boundary.

    Cutting mid-number produces a string that looks like a real value but is
    not ("trigger $18" from "$181.88"), which is worse than an obvious
    truncation, so the cut is taken at the last comma-separated part that fits.
    """
    if text is None or len(text) <= limit:
        return text
    head = text[: limit - 1]
    cut = head.rfind(", ")
    if cut > limit // 2:            # keep a sane amount of the string
        return text[:cut] + "…"
    return head + "…"


def insert_trade_history(client, trade_log: dict):
    """Insert a trade_history row, never letting a long reason lose the trade.

    Every caller DELETEs the position from portfolio_positions before inserting
    here, and that delete has already committed by the time this runs. Postgres
    raises on a varchar overflow rather than truncating, so an over-long reason
    would abort the insert *after* the position was destroyed -- losing the
    trade record entirely. A diagnostic string must never be able to do that.

    trade_history.sell_reason/buy_reason were widened to `text` by
    migrations/20260918_widen_sell_reason.sql. This retry makes the agent
    correct whether or not that migration has been applied yet, so deployment
    ordering cannot cause data loss.
    """
    for key in ("sell_reason", "buy_reason"):
        if trade_log.get(key):
            trade_log[key] = _clamp_reason(trade_log[key], SELL_REASON_RUNAWAY_LIMIT)
    try:
        return client.table("trade_history").insert(trade_log).execute()
    except Exception as first_error:
        over = {k: v for k, v in trade_log.items()
                if k in ("sell_reason", "buy_reason")
                and isinstance(v, str) and len(v) > SELL_REASON_LEGACY_LIMIT}
        if not over:
            raise
        retry = dict(trade_log)
        for key, value in over.items():
            retry[key] = _clamp_reason(value, SELL_REASON_LEGACY_LIMIT)
        print(f"⚠️  trade_history insert rejected ({first_error}); retrying with "
              f"{'/'.join(sorted(over))} truncated to {SELL_REASON_LEGACY_LIMIT} "
              f"chars. Apply migrations/20260918_widen_sell_reason.sql to keep "
              f"the full exit context.")
        return client.table("trade_history").insert(retry).execute()


# The entry-decision provenance captured on the portfolio_positions row at buy
# time (buying.py / force_buy.py). These four columns record WHAT THE SCREENER
# AND THE AI THOUGHT of the setup at the moment it was bought:
#   entry_quality_score  the technical CAN SLIM quality score (0-100)
#   entry_ai_rating      the raw AI rating 1-100 (ai_evaluator.py)
#   entry_ai_grade       the AI letter grade A/B/C/D the rating mapped to
#   entry_final_score    quality_score + the AI grade bonus, the value slots rank on
_ENTRY_PROVENANCE_KEYS = (
    "entry_quality_score",
    "entry_ai_rating",
    "entry_ai_grade",
    "entry_final_score",
)


def entry_provenance(pos: dict | None) -> dict:
    """Copy the entry-decision provenance off a portfolio_positions row so it
    survives into trade_history.

    These columns are written at buy time and DELETED with the position row at
    close, so without this copy the only durable record of how the screener and
    the AI graded each *closed* trade is lost. That loss is exactly what blocked
    the 2026-09-28 "is the AI helping pick winners?" review: entry quality could
    only be recovered for 35 of 67 closed trades (via breakout_learnings) and the
    raw AI grade for none of them, forcing the analysis to infer the AI's
    contribution rather than correlate it against realised returns directly.

    Returns only the keys actually present on the row, so a sparse or partial
    position (e.g. the ``pos_row=None`` default on execute_sell) never writes
    NULLs over columns that already hold a value. See
    decisions/2026-09-28_archive-entry-scores-to-trade-history.md.
    """
    if not isinstance(pos, dict):
        return {}
    return {k: pos[k] for k in _ENTRY_PROVENANCE_KEYS
            if pos.get(k) is not None}


def _exit_context_suffix(pos: dict, sell_price: float,
                         broker_trail_fill: bool = False) -> str:
    """
    Build a human- and machine-readable summary of the risk state a position was
    in at the moment it was closed.

    Broker-side exits are discovered after the fact: the GTC trailing order fires
    at IBKR without consulting the agent, so the only record written used to be
    the bare label "Trailing stop (IBKR GTC TRAIL order)". That is true but
    useless for review — it does not say what trail was in force, what peak the
    trail was anchored to, how long the position had been held, or how far it had
    run before it turned. All of those live on the `portfolio_positions` row,
    which is deleted moments later, so they are lost permanently unless captured
    here.

    Returns an empty string when the row carries nothing worth recording, so a
    sparse position never produces a reason string full of dangling em-dashes.

    ── Why the stored HWM cannot be trusted on its own ──────────────────────
    `hwm_price` and `highest_unrealized_pct` are refreshed by the 15-minute
    monitor cycle. A resting IBKR order does not wait for that cycle: it fires
    the instant price touches its trigger. So a position that runs up and turns
    over *between* two cycles is closed against a peak the agent never saw, and
    every number derived from the stored HWM understates what happened.

    That is not a cosmetic gap. On 2026-09-18 four positions (SMTC, TEN, DHT,
    TWLO) were closed by a Phase 1 stop that had ratcheted above entry, and in
    all four the stored HWM produced an "implied trigger" BELOW the entry price
    — a number that reads exactly like a loss cap behaving correctly, while the
    order had in fact fired at or above breakeven. The logging described a
    healthy rule for weeks while the opposite was happening, and diagnosing it
    required re-fetching 5-minute bars, which is impossible from a phone.

    The fill is the one number known exactly. A trailing order fills at (or just
    through) its own trigger, so `sell_price / (1 - trail)` reconstructs the
    anchor the broker was actually using. Where that disagrees with the stored
    HWM, the stored HWM is stale and the reconstruction is reported instead.
    """
    parts: list[str] = []

    hwm = pos.get("hwm_price")
    trail_pct = pos.get("stop_loss_pct")

    try:
        hwm = float(hwm) if hwm is not None else None
    except (TypeError, ValueError):
        hwm = None
    try:
        trail_pct = float(trail_pct) if trail_pct is not None else None
    except (TypeError, ValueError):
        trail_pct = None

    if trail_pct is not None:
        parts.append(f"trail {trail_pct * 100:.2f}%")

    # The anchor the broker's order was actually trailing from, recovered from
    # the fill rather than from the agent's own (cycle-delayed) observation.
    # Only meaningful for a genuinely HWM-relative trail; a floor-pinned stop is
    # detected and reported separately below.
    fill_anchor: float | None = None
    try:
        _sp = float(sell_price) if sell_price else None
    except (TypeError, ValueError):
        _sp = None
    # ONLY valid when the fill came from the trailing order itself. A manual
    # close (or any other exit) fills at a price with no relationship to the
    # trail, so reconstructing an anchor from it would manufacture a "stale HWM"
    # claim out of an unrelated number. Callers that cannot prove the fill was
    # the resting trail order leave this False and get the original behaviour.
    if broker_trail_fill and _sp and trail_pct is not None and 0 < trail_pct < 1:
        fill_anchor = _sp / (1 - trail_pct)

    if hwm is not None and hwm > 0:
        hwm_date = pos.get("hwm_date")
        parts.append(f"HWM ${hwm:.2f}" + (f" set {hwm_date}" if hwm_date else ""))
        if trail_pct is not None:
            # The price the resting order would have been sitting at, IF the trail
            # were anchored on the high-water mark. Labelled "implied" because the
            # agent never observed the broker's actual trigger — it is
            # reconstructed from the trail and the peak we know.
            #
            # But `stop_loss_pct` is not always HWM-anchored. The Prove-It floor
            # lever (prove_it_trail_pct) stores a trail measured from the price at
            # the moment the resting order was last re-placed — IBKR's trailing
            # anchor RESETS on every cancel/re-place — so for a floor-pinned stop
            # hwm*(1-trail) lands ABOVE where the order actually sat. FIVE
            # (2026-09-09) is the proof: HWM $256.09, stored trail 0.18%, giving a
            # bogus "implied trigger $255.63" while the stop in fact fired at
            # $248.85. A real trailing stop can never trigger above its own fill,
            # so when the reconstruction lands above the exit we know the anchor
            # was not the HWM and report the re-anchored floor honestly instead of
            # a fabricated trigger.
            implied = hwm * (1 - trail_pct)
            if sell_price and implied > float(sell_price) + 0.01:
                parts.append(
                    f"floor re-anchored near ${float(sell_price):.2f} "
                    f"(trail not HWM-relative)"
                )
            else:
                # A trailing order cannot fire below its own anchor-derived
                # level. If the fill implies a HIGHER anchor than the peak we
                # recorded, the recorded peak is stale — it was set on an
                # earlier cycle and the position ran further before turning.
                # Publishing `implied` unqualified here is what concealed the
                # 2026-09-18 ratchet: it reports a trigger below entry for an
                # order that fired above it.
                if fill_anchor is not None and fill_anchor > hwm * 1.001:
                    parts.append(
                        f"stored HWM STALE (cycle-delayed) — fill implies peak "
                        f"${fill_anchor:.2f}, actual trigger ${_sp:.2f}"
                    )
                else:
                    parts.append(f"implied trigger ${implied:.2f}")
    elif fill_anchor is not None:
        # No stored peak at all, but the fill still pins the anchor.
        parts.append(
            f"no stored HWM — fill implies peak ${fill_anchor:.2f}, "
            f"actual trigger ${_sp:.2f}"
        )

    # Where the stop actually sat relative to the entry price. This is the single
    # most diagnostic number on a stopped-out trade and it was never recorded: a
    # level ABOVE entry means whatever fired was taking profit, regardless of
    # which rule believed it was capping a loss.
    try:
        _bp = float(pos.get("buy_price")) if pos.get("buy_price") else None
    except (TypeError, ValueError):
        _bp = None
    if _bp and _bp > 0 and _sp:
        parts.append(f"stop sat at entry {(_sp / _bp - 1) * 100:+.2f}%")

    days_held = pos.get("days_held")
    if days_held is not None:
        parts.append(f"day {days_held} of hold")

    peak_pct = pos.get("highest_unrealized_pct")
    if peak_pct is not None:
        try:
            peak_val = float(peak_pct)
            # `highest_unrealized_pct` shares the stored HWM's cycle delay. When
            # the fill implies the position ran further than the agent recorded,
            # report the reconstruction alongside it rather than publishing a
            # peak that is known to be too low. SMTC (2026-09-18) logged "peak
            # +0.27%" against a real peak of +2.66%.
            if _bp and _bp > 0 and fill_anchor is not None:
                implied_peak = (fill_anchor / _bp - 1) * 100
                if implied_peak > peak_val + 0.05:
                    parts.append(
                        f"peak {peak_val:+.2f}% recorded but >={implied_peak:+.2f}% "
                        f"implied by fill"
                    )
                else:
                    parts.append(f"peak {peak_val:+.2f}%")
            else:
                parts.append(f"peak {peak_val:+.2f}%")
        except (TypeError, ValueError):
            pass

    if pos.get("exit_armed"):
        armed_price = pos.get("exit_armed_price")
        armed_bits = "armed"
        if armed_price:
            try:
                armed_bits += f" at ${float(armed_price):.2f}"
            except (TypeError, ValueError):
                pass
        armed_reason = pos.get("exit_armed_reason")
        if armed_reason:
            armed_bits += f" ({armed_reason})"
        parts.append(armed_bits)

    if pos.get("power_hold"):
        parts.append("power hold active")

    if not parts:
        return ""
    return " — " + ", ".join(parts)
