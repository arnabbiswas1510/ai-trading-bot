# Closed-loop learning from realised outcomes

- **Date:** 2026-08-22
- **Status:** Accepted

## Context

The scoring stack was not operating as a real closed loop:

1. `ai_evaluator.py` consumed `trade_history` only as free-form prompt context.
   No deterministic score penalty was derived from realised outcomes.
2. `technical_screener.py`'s Phase-2 penalty depended on `breakout_learnings`,
   but the table remained empty in production because broker-reconciled exits
   (the dominant trailing-stop path) were not writing learning rows.
3. Even when Phase-2 would activate, it computed `adjusted_score` from
   `final_score` before AI scores existed at that stage, making the penalty base
   unreliable.

Result: losses were not feeding back into next-day ranking in a reliable,
measurable way.

## Decision

Implement a deterministic closed-loop path on top of the existing AI scoring:

1. **Ticker-level history penalty in `ai_evaluator.py`**
   - Build a per-ticker index from recent `trade_history`.
   - Compute a bounded penalty from repeated recent losses and negative average return.
   - Write `adjusted_score = final_score - (failure_penalty + history_penalty)`.
   - Keep `final_score` unchanged as the raw model score; gate buys on `adjusted_score`.

2. **Ensure `breakout_learnings` is written on reconciled exits**
   - Reuse a shared writer for both `execute_sell()` and `reconcile_with_ibkr()`.
   - Persist exit metadata and a failed-parameter snapshot for future penalties.

3. **Fix Phase-2 penalty activation and base score**
   - Make activation threshold configurable (`LEARNING_MIN_ROWS`, default 3).
   - Keep the recency window configurable (`LEARNING_LOOKBACK_DAYS`, default 90).
   - Compute pre-AI `adjusted_score` from technical/quality score at screener time,
     then let `ai_evaluator.py` recompute `adjusted_score` from final AI score.

## Consequences

**Positive**

- Learning now feeds directly into the score used by buy gates.
- Closed-loop behavior is measurable and no longer dependent on prompt-only memory.
- The breakout learning table is populated by the real dominant exit path.

**Risks**

- Ticker-level history penalty can overreact on sparse samples; bounded by
  `HISTORY_LEARNING_MAX_PENALTY`.
- Parameter-level penalties still need data growth to stabilise.

## Docs sync

Runtime behavior and new tunables are reflected in:

- `docs/technical_triggers.md`
- `docs/buy_logic.md`
- `docs/configuration.md`
- `.env.template`
