# decisions/

Architectural Decision Records (ADRs) for the AI Trading Bot.

Each file captures **why** a design choice was made, not just **what** was
changed. This folder is ingested by graphify so decisions are linked to the
code nodes they produced.

## Naming convention

```
YYYY-MM-DD_short-slug.md
```

## When to add a file

- Any change to core trading logic (buy gates, sell logic, stops, screening)
- Any schema migration that reflects a data model decision
- Any removal of a feature (capture what was removed and why)
- Any significant refactor (capture what problem it solved)

Do NOT add files for: bug fixes with obvious root causes, test additions,
UI tweaks, or dependency bumps.

## Template

```markdown
# Decision: <title>

**Date:** YYYY-MM-DD
**Commit:** `abc1234`
**Status:** Implemented | Superseded by <link> | Reverted

## Problem
What was wrong or missing.

## Decision
What was decided and the key details.

## Why
Rationale — especially threshold values and tradeoffs.

## Files changed
- file.py — what changed
```
