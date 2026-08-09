# Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py

**Date:** 2026-08-09
**Status:** Accepted

## Context

`config.py` was introduced (ADR 2026-08-09_max-positions-single-source) to hold
`MAX_POSITIONS` after that constant was found declared independently in four modules,
with the 4→5 decision applied in only one of them.

While auditing environment variables for the documentation rewrite, the **same bug class**
turned up twice more:

| Constant | `execution_agent.py` | `force_buy.py` | `force_sell.py` | `rotate_positions.py` |
|---|---|---|---|---|
| `STOP_LOSS_PCT` | `0.10` | `0.07` | `0.07` | `0.07` |
| `COOLING_OFF_DAYS` | `7` | `3` | — | `3` |

`rotate_positions.py` also carried its own stray `MAX_POSITIONS = 4`, which the previous
ADR missed.

This is materially worse than the `MAX_POSITIONS` drift, because `force_buy.py` and
`rotate_positions.py` **place real orders**. A position opened through the manual path was
given a 7% protective stop, while the identical position opened by the agent got 10%.
The stop the operator saw depended on which script happened to open the trade — with no
indication anywhere that the two differed.

The 10% figure is not arbitrary: it is the floor of the ATR-scaled band
(`max(0.10, min(0.12, 2.5 × entry_atr_pct))`) established when the trailing stop was made
volatility-aware. A 7% stop sits inside typical breakout noise for these names and is
precisely the premature-exit failure mode the ATR work was done to eliminate.

Likewise `COOLING_OFF_DAYS`: the agent waits 7 sessions before re-entering a name it sold,
while a manual rotation would re-enter after 3 — potentially straight back into the position
the agent had just exited on a valid signal.

## Decision

Move `STOP_LOSS_PCT` and `COOLING_OFF_DAYS` into `config.py` alongside `MAX_POSITIONS`, and
import them in all five modules. The agent's values win, because the agent is the module
whose values were derived from backtests:

```python
STOP_LOSS_PCT    = 0.10   # ATR band floor
COOLING_OFF_DAYS = 7
```

Each constant carries an inline comment stating where its value came from, so a future
change has to confront the evidence rather than just the number.

## Consequences

**Behaviour changes in production.** This is not a pure refactor:

- `force_buy.py` now attaches a **10%** protective stop instead of 7%. Manually opened
  positions will sit further from their stop and are less likely to be shaken out on noise.
- `rotate_positions.py` now respects a **7-session** cooling-off instead of 3, and a
  **5-slot** book instead of 4.
- `force_sell.py` uses the 10% figure where it references the stop.

Existing open positions are unaffected — stops already lodged at IBKR are not recalculated
by this change.

**Deployment:** `Dockerfile.agent` already copies `config.py` (added in the previous ADR),
but a rebuild is still required if the running image predates it:

```
docker compose up -d --build
```

Without the rebuild the manual scripts will fail on import rather than silently using stale
values — a loud failure, which is the correct behaviour.

## Guard

`tests/test_max_positions_config.py` was extended from 8 to 14 tests. It now asserts, for
each of the three constants, that **no module declares its own copy** — the test greps the
source of all five modules for an independent assignment and fails if one reappears.

The tests run the import in a **subprocess**. An earlier draft used `importlib.reload()`,
which rebound module-level objects that other test modules held references to and broke
three unrelated suites. Reloading a module under test is not isolation.

This guard is the actual deliverable. Centralising the two constants fixes today's drift;
the test is what prevents the third instance.

## Alternatives considered

**Leave the manual scripts alone** — they are operator tools, run deliberately. Rejected:
the operator running `force_buy.py` has no reason to expect a different risk parameter than
the agent uses, and nothing surfaced the difference. An undocumented divergence in a
live-order path is a latent incident.

**Load everything from `.env` only, with no defaults.** Rejected: it makes a missing
variable a runtime failure in the middle of the trading day, and the deployed `.env` is not
visible from the development machine. Defaults in one file, overridable by environment, keeps
the fallback correct.
