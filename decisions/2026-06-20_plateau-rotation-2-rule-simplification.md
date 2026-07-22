# Decision: Plateau Rotation — Simplify from 3-Tier to 2-Rule

**Date:** 2026-06-20 (approx)
**Commit:** `15b379c`
**Status:** Implemented

## Problem

The original 3-tier plateau rotation strategy (Tier 1/2/3 based on days held +
P&L thresholds) had too many parameters, created hard-to-reason edge cases, and
was difficult to test exhaustively. The logic was causing unexpected rotation
of still-performing positions.

## Decision

Simplified to **2 deterministic rules** evaluated at EOD (3:45–4:00 PM ET):

**Rule 1 — Portfolio Full:** Portfolio must be at capacity (5 positions). If
there are open slots, never rotate — just fill them with fresh triggers.

**Rule 2 — Fresh trigger beats incumbent:** A fresh breakout trigger exists in
`daily_triggers` that is NOT already held. The incumbent position must show
signs of decay (configurable score threshold). If both conditions are true,
sell the lowest-score incumbent and buy the fresh trigger.

`hwm_rs_score` column was made **dormant** — the column exists in the schema
but Python no longer writes to it. The field was written inconsistently and
the simpler 2-rule logic doesn't need it.

## Why simpler is better here

The 3-tier system was optimising for a signal (precise decay scoring) that
we don't have clean data for. The 2-rule system is conservative by default
(never rotates unless portfolio is full AND a genuinely better opportunity
exists) and is fully testable with a small number of scenarios.

## Files changed

- `execution_agent.py` — `check_plateau_rotation()` rewrite
- `tests/test_plateau_rotation.py` — 7 tests covering both rules + edge cases
- Note: `hwm_rs_score` column remains in DB schema for potential future use
