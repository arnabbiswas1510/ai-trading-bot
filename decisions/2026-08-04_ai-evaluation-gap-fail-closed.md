# 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers

## Status

Accepted.

## Context

Investigating why the bot was losing money surfaced a silent data-integrity bug
in the screening pipeline.

On 2026-08-03 the `daily_triggers` table held 33 rows, but only **8** had a
`final_score`. The other 25 had NULL `final_score`, `ai_rating`, `ai_grade`,
`liquidity_score`, `sentiment_score` and `score_rationale` — they had never been
AI-evaluated.

The pattern of which rows survived is diagnostic:

```
rows  1-6   AMZN BMY DXCM IMAX CDNA SHIP   <- rated
rows  7-31  JPM GE HWM VLO ... LOB          <- 25 consecutive, all dropped
rows 32-33  ECO LPG                         <- rated
```

`ai_evaluator.py` sent **all 33 tickers in a single `gpt-4o-mini` prompt**,
requesting per-ticker JSON with 2-3 sentence rationales. The model returned only
the head and tail of the list and silently dropped everything in between — the
well-known "lost in the middle" long-context attention failure. Nothing in the
prompt required an entry per ticker, and nothing validated the response was
complete. The only handling was a `continue` with a warning log.

The workflow ordering (`technical_screener.py` -> `ai_evaluator.py`) was correct
and no cap limited how many triggers were sent, so this was purely the LLM call.

The consequence was worse than missing data, because `run_market_open_buys()`
**failed open**:

```python
candidate_score = adjusted_score or final_score or quality_score or ai_rating
```

`quality_score` is written by the technical screener, so a trigger the AI never
saw was not rejected — it was silently judged on **price/volume patterns alone**,
bypassing every guardrail the AI layer exists to enforce:

- sub-$15 price cap (rating capped at 45)
- average volume < 500k penalty (-20)
- small-cap penalty (-15)
- slow-mover ATR cap (rating capped at 35 when +25% is unreachable in 60 days)
- sentiment and negative-news screening

On 2026-08-03 that made three un-vetted tickers buyable: AMP (76), GEO (74) and
JPM (66) — an asset manager, a prison REIT and a bank, none of which resemble
CAN SLIM growth candidates. `trade_history` does not retain `entry_final_score`,
so we cannot prove past losers entered this way, but GE, HWM and TTWO all appear
in the AI-skipped set, and HWM was the single worst closed trade (-6.25%).

## Decision

Four changes, in order of safety-criticality.

### 1. Fail closed on un-vetted triggers (`execution_agent.py`)

Removed the `quality_score` / `ai_rating` fallback from the buy-gate score
resolution. A trigger with no `adjusted_score` and no `final_score` has not been
AI-vetted and is now **skipped**, with an explicit log line. `adjusted_score`
still takes precedence over `final_score` as before.

Refusing to trade on incomplete information is strictly better than trading on a
partial signal. A missed entry costs nothing; an un-vetted entry bypasses every
risk control we deliberately built.

### 2. Batch the AI calls (`ai_evaluator.py`)

Triggers are evaluated in batches of `AI_BATCH_SIZE` (default 8, env-tunable)
instead of one giant prompt, keeping every ticker inside the model's reliable
attention window.

### 3. Demand completeness in the prompt

Each prompt now names the exact tickers required and states that the response
must contain one entry per ticker, explicitly instructing the model that a low
rating is preferable to an omission and that it must not return "only the best
candidates".

### 4. Validate and retry, then alert

`evaluate_triggers()` verifies every requested ticker came back, retries the
stragglers (`AI_BATCH_RETRIES`, default 1), and reports whatever is still
missing. Response keys are normalised for case/whitespace so formatting drift is
not mistaken for a dropped ticker. A batch that fails outright no longer aborts
the whole run — the remaining batches still complete. Persistent gaps raise a
Telegram alert via `notify_exception()`, since the execution agent now fails
closed and would otherwise skip those names silently.

## Consequences

- Simulated against the real 33-ticker payload with a deliberately flaky model
  that drops middle entries: **33/33 rated** (previously 8/33).
- More OpenAI requests per run (~5 instead of 1 for 33 triggers) on a cheap
  model — a negligible cost for correct data.
- The bot may now buy *less* until the evaluator is confirmed healthy in
  production, because un-vetted triggers are skipped rather than waved through.
  This is intended.
- Missing ratings are now loud (Telegram) instead of silent.

`tests/conftest.py`'s `make_trigger()` now defaults `final_score=75` to represent
a normally-vetted trigger; tests pass `final_score=None` explicitly to exercise
the fail-closed path.

## Files changed

- `execution_agent.py` — fail closed in the buy-gate score floor.
- `ai_evaluator.py` — `AI_BATCH_SIZE`/`AI_BATCH_RETRIES`; extracted
  `_format_trigger_block()`, `build_prompt()`, `call_ai_batch()`; new
  `evaluate_triggers()` batching/validation/retry driver; missing-rating alert.
- `tests/test_ai_evaluator_batching.py` — new: batching, middle-drop recovery,
  persistent-miss reporting, partial API failure, key-drift tolerance, prompt
  completeness.
- `tests/test_buy_gates.py` — `TestAIRatingSorting` replaced by
  `TestTriggerRankingAndVetting` (the old test asserted the removed
  `ai_rating` fallback); added un-vetted-skip and `adjusted_score` precedence
  cases.
- `tests/conftest.py`, `tests/test_buy_fill_verification.py` — fixtures updated
  to supply a vetted `final_score`.

All 192 tests pass (was 185).

## Not addressed here

This fixes entry *data integrity* only. The separate findings from the same
investigation — a 0.49:1 payoff ratio driven by over-tight exits
(`TRAIL_TIME_TIERS`, `INTRADAY_PULLBACK_PCT`, `EARLY_LOSS_STOP_PCT`) and a
`revenue growth > 0%` screen that admits non-CAN SLIM names — remain open.
