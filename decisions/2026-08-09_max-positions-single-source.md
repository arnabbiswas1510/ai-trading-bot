# Make MAX_POSITIONS a single env-driven constant

Date: 2026-08-09
Status: Accepted
Relates to: `2026-08-04_power-hold-trail-and-five-slots.md`

## Context

ADR 2026-08-04 moved the portfolio from 4 concurrent positions to 5. That
decision was applied in `execution_agent.py` and nowhere else:

| declaration site | value before this change |
|---|---|
| `execution_agent.py:152` | 5 (decision applied) |
| `technical_screener.py:55` | 4 |
| `force_buy.py:56` | 4 |
| `backend/backtester.py:25` | 4 |
| `.env.template:27` | `MAX_POSITIONS=4` |

This was not cosmetic. `technical_screener.py` uses *its own* MAX_POSITIONS as
the target of the daily candidate waterfall — the comment above it reads
"Target = MAX_POSITIONS so the waterfall always aims to fill the portfolio" —
and gates filter relaxation on `len(active_triggers) < MAX_POSITIONS` (L691).
At 4 it stopped producing candidates once it had four, while the agent had five
slots to fill. **The fifth slot was structurally starved**, and nothing failed
loudly; the portfolio simply sat at four.

`.env.template` was worse: anything deployed from it sets `MAX_POSITIONS=4`
globally, which overrides the agent's default and reverts the five-slot decision
entirely, silently.

The root cause is that a duplicated constant is not a constant — it is N
constants that happen to agree until someone changes one.

## Decision

Introduce `config.py` as the single source of truth. Root modules
(`execution_agent`, `force_buy`, `technical_screener`) import from it. Slot
count is changed by editing `.env` and restarting — **never** by editing code.

> **Superseded in part by `2026-08-09_stop-loss-cooloff-single-source.md`.**
> This ADR missed a fourth root module: `rotate_positions.py` carried its own
> `MAX_POSITIONS = 4`. It was found later, during an unrelated audit, and is
> fixed there along with the same drift in `STOP_LOSS_PCT` and
> `COOLING_OFF_DAYS`. The omission is left visible rather than edited away — an
> ADR that claims to have centralised a constant, while one copy survives, is
> exactly the failure this pattern exists to prevent, and it recurred within a
> day of being written.

`backend/` is exempt from the import: it ships as its own image (`Dockerfile`
copies only `backend/`), so `config.py` does not exist in that container.
`backtester.py` reads the same env var with the same default instead, so `.env`
remains the single operational switch even though the import cannot be shared.

`Dockerfile.agent` was updated to `COPY config.py` — without it the agent
container would crash on import at startup. A test asserts this, because the
failure would only appear on deploy.

The `backtester` default moves 4 → 5. This does change the baseline for future
backtests, which is the point: a backtest simulating a different portfolio shape
than production answers a question nobody asked. Historical numbers in earlier
ADRs were produced at 4 slots and are labelled as such there.

## Consequences

- Changing slot count is now a one-line `.env` edit affecting every module at
  once, including the backtester.
- The screener will now produce up to 5 candidates, so the fifth slot can
  actually be filled — this is a **live behaviour change** the first time the
  screener runs after deploy.
- Anyone deploying from `.env.template` gets 5, matching the ADR.

## Verification

- `tests/test_max_positions_config.py` (8 tests): all modules agree on the
  default; the default is 5; values 3/4/6/7 propagate to every module; no root
  module re-reads the env locally; `Dockerfile.agent` ships `config.py`.
- Mutation test: restoring `technical_screener.py`'s local
  `os.environ.get("MAX_POSITIONS", 4)` — the exact original bug — fails 2 tests.
- Full suite 336 passing.

### Note on test isolation

The first version of these tests used `importlib.reload()` and broke 3 unrelated
tests in `test_reconcile.py` and `test_short_fix_and_new_logic.py`: reloading
`execution_agent` rebinds module-level objects that other suites already hold
references to. Resolution is now done in a subprocess. Recorded because the
failure mode is non-obvious and order-dependent.
