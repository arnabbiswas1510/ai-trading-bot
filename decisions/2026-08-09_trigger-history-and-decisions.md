# ADR: Point-in-time trigger archive and buy/skip decision log

**Date:** 2026-08-09
**Status:** Accepted
**Relates to:** 2026-08-09 (watchlist history), 2026-08-09 (thesis stop + its correction)

---

## Context

`technical_screener.py` truncates the entire `daily_triggers` table on every run:

```python
client.table("daily_triggers").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()
```

At the time of writing the live table held **nine rows** — a single day.

This is the same pattern as `watchlist`, but the loss is worse. Each morning the
screener emits N triggers and at most a few are bought (`MAX_POSITIONS = 4`).
`trade_history` therefore contains only candidates that were **already judged
good**. That is selection on the dependent variable: outcomes are observed only
for the subset that passed every gate.

**The rejected candidates are the control group, and they were being deleted.**

A trigger row is also far richer than a watchlist row — it carries the entire
decision input: `final_score`, `ai_rating`, `ai_grade`, `quality_score`,
`technical_score`, `rs_score`, `liquidity_score`, `sentiment_score`, `atr_pct`,
`est_days_to_target`, `trigger_type`, `failure_penalty`, and the AI's
`score_rationale` in prose.

Worse, every rejection in `run_market_open_buys()` was a `print()` followed by
`continue`. Nothing was persisted, so even the *reason* a candidate was passed
over was unrecoverable.

## Decision

Two append-only tables, plus a new `trigger_audit.py` module.

### `trigger_history` — what the screener saw

One immutable row per `(triggered_at, ticker, trigger_type)`.

**Written at truncate time, capturing the OUTGOING rows, not the incoming ones.**
This is the subtle part and it is easy to get backwards. `ai_evaluator.py:119`
updates `daily_triggers` with scores *after* `technical_screener` inserts.
Archiving the incoming rows would therefore store `NULL` `ai_rating` /
`final_score` / `score_rationale` — silently defeating the entire purpose. By
truncate time the outgoing rows are fully enriched *and* have already been acted
upon by the buy loop.

### `trigger_decisions` — what the bot did, and why

One row per `(decision_date, ticker, trigger_type)`, recording `decision`
(BOUGHT/SKIPPED), a stable `reason_code`, prose detail, and the scores **as
evaluated at that moment**.

Kept separate from `trigger_history` because a trigger can be re-evaluated on
several days within `TRIGGER_LOOKBACK_DAYS` and receive a different verdict each
day (skipped for slots on Monday, bought on Tuesday). The decision date is
therefore part of the key.

Scores are **snapshotted rather than joined**, because the trigger row may be
re-scored on a later run, which would silently rewrite history.

### The `is_capacity` flag

Reason codes are split into quality rejections (`AI_VETO`, `SCORE_FLOOR`,
`NO_AI_SCORE`, `BELOW_PIVOT`, `EXTENDED_ABOVE_PIVOT`) and capacity rejections
(`SLOTS_FULL`, `INSUFFICIENT_CASH`, `SHARES_ZERO`), with a boolean column so
analysis can separate them without parsing prose.

This distinction is essential and easy to lose: a name skipped for lack of a
slot says **nothing** about the quality model, but **everything** about the cost
of `MAX_POSITIONS`. Conflating them would corrupt both analyses.

## What this makes answerable

1. **Does `final_score` predict forward return?** Currently unanswerable —
   outcomes exist only for high scores that were bought, so the relationship is
   range-restricted. The AI evaluator costs money and is presently unvalidated.
2. **Is the D-grade AI veto correct?** Vetoed names are never bought, so never
   measured.
3. **What does `MAX_POSITIONS = 4` cost?** Triggers skipped purely for slots are
   now recorded in bulk, both pre-cycle and mid-cycle.
4. **Is the score floor set at the right level?** `SCORE_FLOOR` rejections are
   the near-miss population needed to test it.
5. **Does `PRE_BREAKOUT` convert better than `BREAKOUT`?**

Item 1 also repairs a specific known gap: the earlier min-score-gate backtest
returned null, but was flagged as having limited external validity precisely
because the simulation used a proxy score rather than the live `final_score`,
whose history did not exist. It will exist now.

## Consequences

**Positive**
- The counterfactual is preserved; the control group exists for the first time.
- Rejection *reasons* are queryable, so gate behaviour can be audited rather
  than inferred from logs.
- Zero change to live behaviour: `daily_triggers` semantics are untouched, all
  audit writes are non-fatal, and no control flow was altered — every call sits
  immediately before an existing `continue`/`break`.

**Negative / risks**
- ~13 new call sites inside live trading code. Mitigated by every write being
  wrapped and non-fatal, and by integration tests that drive the real
  `run_market_open_buys()` and assert on what reached the audit module. Those
  tests were mutation-verified: deleting the `AI_VETO` and `SCORE_FLOOR` calls
  fails the suite.
- **Forward-only.** Repairs nothing retroactively.
- Outcomes are not stored here. Linking a trigger to a forward return requires
  joining price data after the fact; that analysis is not yet built.
- Unbounded growth, deliberately — pruning would recreate the problem this
  exists to solve. At tens of rows per day it is negligible.

## Files

- `trigger_audit.py` (new) — `save_trigger_history()`,
  `record_trigger_decision()`, `record_decisions_bulk()`, reason-code constants
- `migrations/add_trigger_history.sql` — both tables, indexes, seed from
  `daily_triggers`
- `technical_screener.py` — SELECT existing rows, archive, then truncate
- `execution_agent.py` — decision logging at every gate and on successful fill
- `tests/test_trigger_audit.py` — 26 tests, including source-level guards that
  the archive precedes the truncate and is fed from a SELECT
