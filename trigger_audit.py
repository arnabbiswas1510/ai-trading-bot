"""
trigger_audit.py

Point-in-time archive of breakout triggers and the buy/skip decisions made
against them.

WHY THIS EXISTS
---------------
Both `daily_triggers` and `watchlist` are current-state tables that are
truncated on every screener run. For triggers this destroys the single most
valuable research asset the bot produces: the **counterfactual**.

Each morning the screener emits N triggers and at most a few are bought (4
slots). `trade_history` therefore only ever contains candidates that were
already judged good — selection on the dependent variable. The rejected
candidates, which are the control group, are deleted.

Without them these questions cannot be answered at all:

  * Does `final_score` predict forward return? Outcomes are only observed for
    high scores that were bought, so the relationship is range-restricted.
  * Is the D-grade AI veto correct? Vetoed names are never bought, never measured.
  * What does MAX_POSITIONS=4 cost? Needs triggers skipped purely for slots.
  * Does PRE_BREAKOUT convert better than BREAKOUT?

Two tables, because they answer different questions and arrive at different
times:

  `trigger_history`   — what the screener saw, one row per (triggered_at,
                        ticker, trigger_type). Written at truncate time, which
                        captures the PREVIOUS run's rows: by then ai_evaluator
                        has written back scores (ai_evaluator.py:119 updates
                        daily_triggers after technical_screener inserts) and
                        execution_agent has already acted on them. Archiving the
                        incoming rows instead would capture NULL scores.

  `trigger_decisions` — what the bot did about it and why, one row per
                        (decision_date, ticker, trigger_type). A trigger can be
                        re-evaluated on several days within TRIGGER_LOOKBACK_DAYS
                        and get a different verdict each day, so the decision
                        date is part of the key.

Every write here is NON-FATAL by design. This is a research feature and must
never be capable of interrupting live screening or live trading.
"""

from __future__ import annotations

import datetime

# ── Decision reason codes ─────────────────────────────────────────────────────
# Stable identifiers so analysis can group without parsing prose. The
# distinction that matters most is between gate rejections (AI_VETO,
# SCORE_FLOOR) which test the quality model, and capacity rejections
# (SLOTS_FULL, INSUFFICIENT_CASH) which test position sizing instead.
BOUGHT               = "BOUGHT"
ALREADY_HELD         = "ALREADY_HELD"
COOLING_OFF          = "COOLING_OFF"
AI_VETO              = "AI_VETO"
NO_AI_SCORE          = "NO_AI_SCORE"
SCORE_FLOOR          = "SCORE_FLOOR"
SLOTS_FULL           = "SLOTS_FULL"
INSUFFICIENT_CASH    = "INSUFFICIENT_CASH"
NO_PRICE             = "NO_PRICE"
EXTENDED_ABOVE_PIVOT = "EXTENDED_ABOVE_PIVOT"
BELOW_PIVOT          = "BELOW_PIVOT"
SHARES_ZERO          = "SHARES_ZERO"
BUY_FAILED           = "BUY_FAILED"
LOOP_HALTED          = "LOOP_HALTED"

# Rejections caused by capacity rather than by candidate quality. Analysis must
# separate these: a name skipped for lack of a slot says nothing about the
# quality model, but everything about the cost of MAX_POSITIONS.
CAPACITY_CODES = {SLOTS_FULL, INSUFFICIENT_CASH, SHARES_ZERO}

_HISTORY_COLUMNS = (
    "ticker", "close_price", "volume_surge", "sma_50", "rolling_high_52w",
    "pivot_distance_pct", "triggered_at", "retention_period", "ai_rating",
    "quality_score", "ai_grade", "final_score", "avg_volume_50",
    "technical_score", "rs_score", "liquidity_score", "sentiment_score",
    "score_rationale", "atr_pct", "est_days_to_target", "adjusted_score",
    "failure_penalty", "penalty_reason", "trigger_type",
)


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def save_trigger_history(client, triggers, archived_at=None):
    """Archive `daily_triggers` rows to the append-only `trigger_history`.

    MUST be called BEFORE the truncate in technical_screener.py; afterwards
    there is nothing left to archive.

    Pass the rows CURRENTLY in the table (the previous run's), not the incoming
    ones — the previous rows carry the AI scores and have been acted upon.

    Idempotent via upsert on (triggered_at, ticker, trigger_type), so a re-run
    or an unchanged multi-day trigger overwrites rather than duplicating.
    """
    if not triggers:
        print("[*] No triggers to archive in trigger_history.")
        return 0

    archived = archived_at or datetime.datetime.now(datetime.timezone.utc).isoformat()

    payload = []
    for t in triggers:
        if not t.get("ticker"):
            continue
        row = {k: t.get(k) for k in _HISTORY_COLUMNS}
        # Both are primary key components and must never be null.
        row["trigger_type"] = t.get("trigger_type") or "BREAKOUT"
        row["triggered_at"] = t.get("triggered_at") or _today()
        row["archived_at"] = archived
        payload.append(row)

    return _upsert(client, "trigger_history", payload,
                   "triggered_at,ticker,trigger_type",
                   "migrations/add_trigger_history.sql")


def record_trigger_decision(client, trigger, decision, reason_code, detail=None,
                            decision_date=None, **context):
    """Record one buy/skip verdict against one trigger.

    `decision` is BOUGHT or SKIPPED; `reason_code` is one of the constants above.
    Non-fatal: never raises, so a failure here cannot interrupt a buy cycle.
    """
    return record_decisions_bulk(client, [trigger], decision, reason_code,
                                 detail=detail, decision_date=decision_date,
                                 **context)


def record_decisions_bulk(client, triggers, decision, reason_code, detail=None,
                          decision_date=None, **context):
    """Record the same verdict against many triggers.

    Used when the portfolio is already full at the start of a cycle: every
    trigger is then skipped for SLOTS_FULL, and those rows are exactly what
    measures the opportunity cost of the position cap.
    """
    if not triggers:
        return 0

    when = decision_date or _today()

    payload = []
    for t in triggers:
        ticker = t.get("ticker")
        if not ticker:
            continue
        payload.append({
            "decision_date":   when,
            "ticker":          ticker,
            "trigger_type":    t.get("trigger_type") or "BREAKOUT",
            "triggered_at":    t.get("triggered_at"),
            "decision":        decision,
            "reason_code":     reason_code,
            "reason_detail":   detail,
            "is_capacity":     reason_code in CAPACITY_CODES,
            # Snapshot the score AS EVALUATED. The row in trigger_history may be
            # re-scored later, so the decision must carry its own copy or the
            # score/outcome link is lost.
            "final_score":     t.get("final_score"),
            "adjusted_score":  t.get("adjusted_score"),
            "ai_grade":        t.get("ai_grade"),
            "quality_score":   t.get("quality_score"),
            "candidate_score": context.get("candidate_score"),
            "min_score":       context.get("min_score"),
            "price":           context.get("price"),
            "extension_pct":   context.get("extension_pct"),
            "slots_free":      context.get("slots_free"),
            "available_cash":  context.get("available_cash"),
            "shares":          context.get("shares"),
        })

    return _upsert(client, "trigger_decisions", payload,
                   "decision_date,ticker,trigger_type",
                   "migrations/add_trigger_history.sql")


def _upsert(client, table, payload, on_conflict, migration_hint):
    """Chunked, idempotent, non-fatal upsert."""
    if not payload:
        return 0
    written = 0
    try:
        for i in range(0, len(payload), 100):
            chunk = payload[i:i + 100]
            client.table(table).upsert(chunk, on_conflict=on_conflict).execute()
            written += len(chunk)
    except Exception as e:
        msg = str(e)
        if table in msg or "PGRST" in msg or "42P01" in msg:
            print(f"[!] {table} table missing — run {migration_hint}. Continuing.")
        else:
            print(f"[!] Could not write {table} (non-fatal): {e}")
    return written
