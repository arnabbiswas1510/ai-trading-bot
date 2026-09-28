# Archive entry-decision provenance onto closed trades (`trade_history`)

- **Date:** 2026-09-28
- **Status:** Accepted
- **Migration:** `migrations/20260928_add_entry_scores_to_trade_history.sql`
- **Related:** `decisions/2026-09-18_entry-quality-open-question.md`,
  register entry `ai-value-ab-replay`

## Context

Every candidate the bot buys is graded twice at entry: the technical CAN SLIM
**quality score**, and the **AI rating** (`ai_evaluator.py`, 1–100) that maps to
a letter grade (A ≥70 / B ≥55 / C ≥50 / D <50-veto) and a **+5/+15 score bonus**.
Those four values —

```
entry_quality_score   technical quality score (0-100)
entry_ai_rating       raw AI rating 1-100
entry_ai_grade        the AI letter grade A/B/C/D
entry_final_score     quality_score + AI grade bonus (what the 5 slots rank on)
```

— are written onto the `portfolio_positions` row at buy time
(`buying.py` / `force_buy.py`), and that row is **deleted when the trade closes**.
None of them reach `trade_history`. So the realised return of every closed trade
(`percent_return`, `profit_loss`) carries no durable record of how good the
screener or the AI thought the setup was.

This is not hypothetical. On 2026-09-28 the review *"is the AI actually helping
pick winners?"* could only recover `entry_final_score` for **35 of 67** closed
trades — via the separate `breakout_learnings` table — and the raw AI grade for
**none** of them. The analysis had to **infer** the AI's contribution from the
skip/veto log and the composite score rather than correlate `entry_ai_grade`
against `percent_return` directly. The honest conclusion ("no evidence the AI is
helping, but the measurement setup can't isolate it") was itself a symptom of the
missing data.

## Decision

Copy all four entry-provenance columns onto the `trade_history` row at close, so
they outlive the deleted position and can be correlated against the trade's real
outcome.

- New helper `entry_provenance(pos)` in `trade_history.py` copies only the keys
  actually present on the position row, so a sparse row (e.g. the
  `pos_row=None` default on `execute_sell`) never writes `NULL` over a value.
  `None` means *not captured*; `0` is a real score and is preserved.
- Every one of the four sell paths that builds a `trade_log` now merges it:
  `execute_sell` and `execute_scale_out` (`selling.py`), the reconcile/manual-close
  path (`reconciliation.py`), and `handle_mock_sell` (`orders.py`).
- The one bare `execute_sell` call site (`monitoring.py:106`, the Armed-Exit
  deadline force-sell) now passes `pos_row=pos`; every other call site already
  did. Without that fix the most common exit path would have archived nothing.
- Migration adds the four columns to `trade_history` (mirrors the
  `portfolio_positions` shape), idempotent per the migration rules.

`profit_loss` / `percent_return` remain the authoritative **outcome** columns;
these are the authoritative **entry** columns. Existing closed trades keep `NULL`
provenance — this is forward-looking, not a backfill.

## Why this matters: the AI on/off A/B replay

This archival is the **precondition** for the only clean test of whether the AI
adds value — an A/B replay. The AI is always on today, so there is no control
group: every trade has already been touched by the AI bonus and veto, and a win
cannot be attributed to the AI or to the technical screen. The A/B replays the
*same* logged candidates twice:

- **Arm A (AI on):** apply the +5/+15 grade bonus and the grade-D veto, then rank
  and fill 5 slots as live.
- **Arm B (AI off):** identical candidates, AI bonus stripped and veto ignored,
  ranked purely on `quality_score`.

The only difference is the AI, so the P&L difference **is** the AI's contribution,
in dollars. That replay needs closed trades that carry the archived grade, which
did not exist until this change. It is registered as `ai-value-ab-replay` and
gated on trade count, not run now.

Two honest limits, recorded so the future review states them: (1) it is a
counterfactual replay over *logged* candidates (`trigger_decisions` is young —
287 rows, 5 real buys), and (2) the exit rules compress nearly all outcomes into
±3% over 0–3 days, so entry selection has little room to express skill regardless
of how the arms rank — see `decisions/2026-09-18_entry-quality-open-question.md`.

## Consequences

- Every trade closed from now on can have its AI grade correlated with its real
  return directly, with no inference.
- No behavioural change: these columns are written, never read by any trading
  rule. Buy gates, sell rules and sizing are untouched.
- `entry_provenance` is defensive by construction, so a position row missing the
  columns (closed before the buy-side write existed) simply archives fewer keys.
