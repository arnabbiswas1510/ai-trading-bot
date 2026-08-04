# Decision: Repository-Wide Dead Code Cleanup

## Problem

A thorough dead-code scan (vulture + manual cross-reference of every
candidate against actual call-sites) turned up a substantial amount of
unreferenced code accumulated over the project's history: stale one-off
incident-response scripts, a scratch duplicate of the execution agent, raw
debug output committed to the repo, and several functions/constants left
behind after features were built but never wired in (or after their call
sites were refactored away).

Leaving this in place increases the surface area the graph and future
contributors have to reason about, and risks someone accidentally
resurrecting an incident-specific script (e.g. the TRV/OII partial-sell
scripts) against the wrong ticker/position.

## Decision

### Files removed entirely (2,339 lines, zero references anywhere in the
codebase, docs, workflows, or Dockerfiles)

- `downloaded_agent.py` — stale 1,336-line duplicate/old snapshot of
  `execution_agent.py`, never imported by anything.
- `test_market_order.py` — manual IBKR connectivity debug script, not part
  of the pytest suite.
- `topup_nbix.py` — one-off emergency script for the NBIX position top-up
  incident (2026-07-15); already executed, incident closed.
- `partial_sell_trv.py` — one-off emergency script for the TRV oversized-buy
  incident; already executed, incident closed.
- `partial_sell_oii.py` — one-off emergency script for the OII oversized-buy
  incident; already executed, incident closed.
- `write_prebreakout_tests.py` — helper used once to generate
  `tests/test_pre_breakout.py`; no longer needed once that file existed.
- `rootcause.txt`, `v4.txt` — raw pasted terminal/debug output committed by
  mistake; not code, not documentation.
- `debug_telegram_msg.py` — manual Telegram send debug utility, unreferenced
  by any code path.

### Dead code removed from active files

- **`execution_agent.py`**: unused `import math`; unused `PLATEAU_DAYS`
  constant (plateau rotation is driven by `hwm_date`/days-since-HWM logic
  elsewhere, not this constant); unused functions `get_fresh_triggers_today()`,
  `_compute_3day_avg_close()`, `_compute_param_drift()`,
  `check_consolidation_floor_break()`, `_generate_analysis_reason()`,
  `_set_rotation_recommendation()`, and `has_bought_today()` (its caller was
  already refactored to rely on `run_market_open_buys()` being idempotent
  instead — a comment at the old call site literally said
  "has_bought_today removed", but the function definition itself had been
  left behind).
- **`ai_evaluator.py`**: unused `AI_VETO_THRESHOLD` constant and the entire
  `evaluate_held_position()` function — a held-position AI re-evaluation
  pipeline that was built (referencing `_compute_param_drift()`, also
  removed above) but never called from `execution_agent.py`'s EOD analysis
  as its docstring claimed.
- **`telegram_notifier.py`**: unused `notify_power_hold()` method — leftover
  from the Power Hold Rule feature, superseded by later plateau-rotation
  simplification work (see `decisions/2026-06-20_plateau-rotation-2-rule-simplification.md`).
- **`technical_screener.py`**: unused local `volume_today`; unused
  `PRE_BREAKOUT_SCORE_BOOST` module constant (its own copy —
  `ai_evaluator.py` independently reads the same env var, so removing the
  unused copy here does not affect behavior).
- **`backend/database.py`**: unused `get_position()`, `buy_position()`,
  `sell_position()`, `get_historical_triggers()` — a legacy manual-trade
  Web UI flow (writing `profit_target` / `is_power_hold`, both already
  slated for removal per `migrations/add_hwm_date.sql`) fully superseded by
  the IBKR-driven execution agent. The active app uses `get_positions()`
  (plural) instead.

### Bug found and fixed while investigating (tightly coupled — not a
separate task)

- **`rotate_positions.py`** had `from force_buy import _place_buy,
  get_available_cash` — but `force_buy.py` does not define or re-export
  `get_available_cash` (it only imports `get_own_cash` from
  `execution_agent.py`). This import was silently broken
  (`ImportError` on any attempt to import `rotate_positions.py`, e.g. from
  `backend/main.py`'s rotation-approval flow). Since `get_available_cash`
  was never actually referenced inside `rotate_positions.py`, the fix was to
  simply drop it from the import list.
- **`execution_agent.py`'s own `get_available_cash()`** was initially flagged
  as dead (never called anywhere), but a dedicated regression test
  (`tests/test_margin_safety.py::test_deprecated_get_available_cash_delegates_to_get_own_cash`)
  guards it — it's an intentionally-kept deprecated compatibility alias that
  prevents any future call site from silently reverting to reading
  `AvailableFunds` (the root cause of a prior margin-safety incident) instead
  of `get_own_cash()`'s `TotalCashValue`. This one was restored and left in
  place.

## What was verified NOT dead (confirmed via manual call-site tracing, left
untouched)

- `backend/main.py` route handlers — called by FastAPI via `@app.get`/`@app.post`
  decorators, not by direct Python calls.
- `force_buy.py` / `force_sell.py` / `retention_helper.py` — actively used by
  `rotate_positions.py`, `.github/workflows/`, and `Dockerfile.agent`.
- `telegram_notifier.py`'s `profit_target` parameter — explicitly commented
  "kept for backwards-compat; no longer used".
- ib_insync `.tif` / `.orderType` / `.lmtPrice` / `.transmit` "unused
  attribute" warnings from the scanner — false positives; these are read
  internally by the IBKR API, not by our Python code.
- All frontend components (`frontend/src/components/*.jsx`) — every one is
  referenced elsewhere; no dead code found there.

## Follow-up pass: one-off scripts not wired into the main programs

After a user follow-up question ("are all one-off scripts cleaned up?"), did a
second sweep specifically for scripts never invoked by `execution_agent.py`,
`backend/main.py`, `Dockerfile.agent`, `docker-compose.yml`, or
`.github/workflows/*.yml`. Vulture doesn't catch this class of dead code
because it only flags unused *symbols* within a file it scans — a whole file
that calls its own functions under `if __name__ == "__main__":` looks "used"
to it even if no other file ever imports it. Required a manual grep-based
audit of every root-level and `backend/`-level `.py` file's call sites.

Found one more genuinely dead file this pass:
- `backend/debug_screener.py` — a manual, standalone script for exercising
  `backend/screener.py` (`test_screener()` under
  `if __name__ == "__main__":`). Never imported by `backend/main.py`, never
  referenced by any workflow/Dockerfile, and not collected by pytest (lives
  outside `tests/`, doesn't match the `test_*.py` discovery path anyway since
  it's not under `tests/`). Deleted.

Confirmed NOT dead (intentionally kept, verified referenced):
- `run_app.py` — a manual local-dev orchestrator (`python run_app.py` boots
  backend + frontend together). Not called by anything else, but this is
  identical in spirit to `force_buy.py`/`force_sell.py`: a human-invoked
  utility script, not leftover cruft. Kept.
- `restart_and_health_check.py` — invoked by `scripts/restart_6am.sh` (the
  6:00 AM cron restart job that `docker cp`s it into the `execution-agent`
  container and runs it). Kept.
- `tv_api_screener.py`, `technical_screener.py`, `ai_evaluator.py`,
  `flex_query_sync.py` — all directly invoked as steps in
  `.github/workflows/daily_screener.yml` / `ibkr_cash_flow_sync.yml`. Kept.
- `scoring.py`, `telegram_notifier.py`, `force_buy.py` — copied into
  `Dockerfile.agent` and imported by `execution_agent.py`. Kept.

## Files changed

- Deleted: `downloaded_agent.py`, `test_market_order.py`, `topup_nbix.py`,
  `partial_sell_trv.py`, `partial_sell_oii.py`, `write_prebreakout_tests.py`,
  `rootcause.txt`, `v4.txt`, `debug_telegram_msg.py`, `backend/debug_screener.py`.
- Modified: `execution_agent.py`, `ai_evaluator.py`, `telegram_notifier.py`,
  `technical_screener.py`, `backend/database.py`, `rotate_positions.py`
  (import bug fix).
- All 182 existing tests pass unchanged after the cleanup.
