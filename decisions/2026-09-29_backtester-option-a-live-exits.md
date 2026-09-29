# Option A: the dashboard backtester exits with the LIVE engine

- **Date:** 2026-09-29
- **Status:** Accepted
- **Supersedes in part:** `decisions/2026-09-29_backtester-exit-core-adoption.md`
  (which built the research backtester "Option B" and named this as the follow-up)

## Context

`research/strategy_backtest.py` (Option B, shipped 2026-09-29) resolves every
exit by calling the live `exit_core`/`exit_rules`, so its exits are byte-for-byte
production. But the **dashboard** backtester the operator actually clicks —
`backend/backtester.py` — still carried its OWN re-implementation of the exits: a
fixed **7% trailing-stop-from-peak** plus a **close-below-EMA-21×0.99** exit. That
is the strategy the bot ran *before the Prove-It Stop shipped on 2026-09-04*. Two
separate exit code paths cannot stay in sync, so the web button silently answered
questions about a strategy the bot no longer runs.

The obstacle was structural, not logical. The web image is built
`COPY backend/ ./backend/` (root `Dockerfile`) and deliberately does **not**
contain the root modules `config.py`, `exit_rules.py`, `exit_core.py` — the split
`config.py`'s own docstring documents. So `backend/backtester.py` physically could
not `import exit_core` the way the research tool does. Closing the gap requires
bringing those modules into the image, which touches the live dashboard build.
That is why it was sequenced as a separate, deployment-touching change ("Option
A") after the zero-risk research tool ("Option B").

## Decision

1. **Extract the daily-bar exit engine into one shared root module,
   `daily_exit_sim.py`.** It holds `build_exit_config`, `new_position`,
   `DayResult`, `_ladder_trail_pct`, `resolve_position_day` and a minimal `DayBar`
   adapter — the exact code that was in `research/strategy_backtest.py`. It imports
   only `exit_core` and `exit_rules` (which import only `config` + stdlib) and
   reads env vars at call time, so it is import-safe.

2. **`research/strategy_backtest.py` now imports these from `daily_exit_sim`**
   (re-exporting the public names so its tests are unchanged) instead of defining
   them locally. There is now exactly one daily-bar exit engine in the repo.

3. **`backend/backtester.py` imports the same module** and replaces its exit block
   with `resolve_position_day` per position per day, and its buy record with
   `new_position`. The retired 7%-trail + EMA-21 exit and the constants
   `DEFAULT_STOP_LOSS_PCT` / `DEFAULT_EXIT_BUFFER` are deleted (logged in
   `docs/retired_code.md`).

4. **The root `Dockerfile` COPYs `config.py exit_rules.py exit_core.py
   daily_exit_sim.py` into `./backend/`** so the web container carries them.
   `tests/test_web_image_completeness.py` walks `backtester`'s import closure and
   fails if any root dependency is dropped from that COPY line — the same guard
   `tests/test_agent_image_completeness.py` gives the agent image.

The `stop_loss_pct` and `profit_target_pct` API parameters remain accepted for
frontend compatibility but are now **ignored for exits** (the live engine uses the
config `STOP_LOSS_PCT` as the trail base). The entry scan and the coarse
`SPY > EMA-21` market filter are **unchanged** — this change is scoped to exits.
Entry parity against `decision_core` remains a separate follow-up.

## Why this is safe for the live dashboard

- `config.py` is import-safe: every value is `os.getenv(name, default)`, no
  raises, no side effects at import.
- `exit_rules.py` imports only `os`, `datetime`, and two constants from `config`;
  `exit_core.py` imports only `exit_rules`; `daily_exit_sim.py` imports only those
  two. None open a socket, read a file, or touch IBKR/Supabase/the clock.
- These modules are used by the backtester only. No live money path imports
  `daily_exit_sim` or `backend/backtester.py`.
- A boot-time import failure is caught by `tests/test_web_image_completeness.py`
  before it can reach production.

## Fidelity (unchanged from Option B)

Daily bars give **exit-rule parity**, not exact fill-price fidelity: the live
loss rules `arm_exit()` a 0.6% IBKR trail that resolves intraday, on a 15-minute
poll, and neither commission nor slippage is modelled. `resolve_position_day` is
bar-granularity agnostic by design — a 5-minute loop drops in with no change to
the `exit_core`/`exit_rules` calls. Trust it for RELATIVE questions (which rule
fires, how often, does a change help or hurt), not for a precise +EV/−EV dollar
verdict. The register work-item `intraday-fmp-exit-fidelity` tracks the intraday
upgrade, gated on live usage showing it is needed.

## Consequences

- The dashboard and research backtesters now run **identical** exit code; a change
  to any live exit rule changes both automatically. The whole class of
  "backtester answered about a retired strategy" error is closed on the exit side.
- The web image is slightly larger (four small root modules) and the Dockerfile
  now has an explicit root-module COPY line guarded by a completeness test.
- Remaining fidelity work is unchanged: entry parity (`decision_core`), 5-minute
  bars, and cost/slippage modelling — the backtest-fidelity roadmap items #1
  (entry side), #2 and #3.

## Verification

- `tests/test_backtester_live_exits.py` — drives `run_backtest` over crafted
  offline data (FMP monkeypatched) and asserts a **Prove-It** exit fires and no
  trade carries the retired "EMA-21 Exit" or "7% from peak" strings.
- `tests/test_web_image_completeness.py` — the Dockerfile carries every root
  module `backtester` imports.
- `tests/test_strategy_backtest.py` — the 8 rule-parity tests still pass against
  the now-shared `daily_exit_sim` code.
- Full suite: 1069 passed (2 pre-existing unrelated failures: the
  `daily_notifications` backup gap and `research/market_gate_bt.py`'s
  `date.today()`).
