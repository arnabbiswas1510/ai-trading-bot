# Keep the test suite importable with the root requirements alone

- **Date:** 2026-09-05
- **Status:** Accepted

## Context

The Daily Screener workflow runs the entire pytest suite *before* it executes
any screener step:

```
pip install -r requirements.txt      # root requirements ONLY
python -m pytest tests/ -v           # <-- gate
python tv_api_screener.py            # fundamental scan
python technical_screener.py         # breakout scan
python ai_evaluator.py               # AI grading
```

The repository has two dependency manifests. `requirements.txt` at the root
covers the execution agent and the screeners. `backend/requirements.txt` adds
the web stack — FastAPI, uvicorn, yfinance — and is installed only into the
`trading-bot` image. CI installs the root one.

On 2026-09-04 `tests/test_dashboard_pricing.py` was added to cover
`resolve_position_price()`, a pure function that had been placed in
`backend/main.py`. The test imported it from there. `backend/main.py` begins with
`from fastapi import FastAPI`, so on CI the import raised `ModuleNotFoundError`
at **collection** time.

Collection errors abort the whole run. The screener steps never executed, and
the failure surfaced only as a generic "Daily Screener FAILED" Telegram alert.
The suite passed locally, because a local machine has the backend requirements
installed too.

Two properties made this worse than an ordinary broken test:

1. **The blast radius is unrelated to the change.** A dashboard-pricing test
   took down the fundamental scan, the breakout scan and the AI evaluation. The
   trading pipeline is gated on a test suite that has nothing to do with it.
2. **It cannot be caught locally by running the tests.** The failure is a
   property of the *environment*, and the developer environment is a superset of
   CI's. Running the suite before pushing gives a green result and false
   confidence.

## Decision

**Tests may import from `backend/` only where the module is importable with the
root `requirements.txt` alone.**

Concretely:

- Pure logic that needs test coverage moves into a module with no third-party
  module-scope imports. `backend/pricing.py` is the first of these and holds
  `resolve_position_price()`; `backend/main.py` imports it and stays the FastAPI
  layer.
- `tests/test_ci_import_hygiene.py` enforces it. For every `tests/test_*.py` it
  parses module-scope imports, resolves any that name a `backend/` module, and
  fails if that module imports FastAPI, uvicorn or yfinance at module scope. It
  also asserts those packages are still absent from the root requirements — so
  if the constraint is ever genuinely relaxed, that is a deliberate edit here
  rather than a silent weakening — and that `backend/pricing.py` stays
  standard-library-only.

Verified as a real guard: reverting the import to `backend.main` makes it fail,
and a `sitecustomize.py` that hides the three packages reproduces the original
CI failure exactly and confirms the fix under 563 tests.

## Alternatives rejected

- **Add FastAPI to the root `requirements.txt`.** Fixes the symptom by making
  every CI run and both runtime images install a web framework they do not use.
  It also concedes that trading code may depend on the web stack, which is the
  opposite of the container separation the project already maintains.
- **`pytest.importorskip("fastapi")` in the test.** The test would then skip
  silently on CI — the coverage would exist locally and evaporate exactly where
  it is being relied on as a gate. Worse than failing loudly.
- **Stop running the suite before the screeners.** The gate is valuable: it has
  caught real regressions before they reached a live scan. The problem is not
  the gate, it is that a test could break it for a reason unrelated to trading.
- **Move the test out of `tests/`.** Hides it from the gate and from every other
  runner too.

## Consequences

**Positive**

- A test-only mistake can no longer take down the day's screening run.
- The rule is machine-checked rather than remembered, and the failure message
  names the fix.
- Pure pricing logic now sits in a module with no framework coupling, which is
  where it belonged anyway.

**Negative / accepted**

- One extra module (`backend/pricing.py`) and a small indirection in
  `backend/main.py`.
- The guard checks one level of indirection, not a full transitive closure. That
  covers the realistic case — a test importing a backend module that pulls in
  the web stack — without an import graph walk. A deeper chain could still slip
  through; the `WEB_ONLY_PACKAGES` premise test is what would catch the drift.
- `tests/` remains a gate on the trading pipeline. This decision reduces the
  chance a test breaks it, but does not decouple them. Splitting the workflow so
  the screeners run on their own suite is the larger fix, deliberately not taken
  here.

## Files

- `backend/pricing.py` — new; `resolve_position_price()` moved here.
- `backend/main.py` — imports it; endpoint otherwise unchanged.
- `tests/test_dashboard_pricing.py` — imports `pricing`, not `main`.
- `tests/test_ci_import_hygiene.py` — new; enforces the rule.
- `docs/configuration.md` — records the two-manifest constraint.
