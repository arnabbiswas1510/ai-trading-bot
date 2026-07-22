# Decision: Separate Container Architecture (execution-agent vs trading-bot)

**Date:** Project inception
**Commit:** Initial architecture
**Status:** In force — do not collapse

## Decision

Maintain strict separation between two Docker containers:

1. **`execution-agent`** (`execution_agent.py`) — brokerage write access,
   order placement, position monitoring, risk enforcement
2. **`trading-bot`** — FastAPI backend + React dashboard, read-only views

## Rationale

**Isolated Failure Domain:** Risk monitoring (7% stop-loss, profit targets,
trailing stops) is mission-critical. It must not crash if the web dashboard,
API endpoints, or database clients experience issues. A bug in the dashboard
cannot take down order management.

**Security:** Only the `execution-agent` container has IB Gateway write access.
The `trading-bot`'s credentials footprint is limited to read-only Supabase
queries and FMP pricing — no brokerage credentials.

**Single Responsibility:** Cleaner Dockerfiles, focused dependency sets, easier
isolated testing. The agent can be restarted independently without affecting
the dashboard.

## Constraints this imposes

- Agent and bot communicate only through Supabase (shared DB) — no direct
  inter-container API calls.
- All state that the dashboard needs to display must be written to Supabase by
  the agent, not computed on the fly from IBKR.
- Tests for execution logic go in `tests/` at repo root, not inside `backend/`.

## Network setup

- IB Gateway binds internally to `127.0.0.1:4002` inside its container.
- `socat` TCP tunnel exposed on port `4004` → mapped to host port `4002`.
- Execution agent connects to `ib-gateway:4004`.
- `READ_ONLY_API=no` env var required in IB Gateway config to allow order placement.
