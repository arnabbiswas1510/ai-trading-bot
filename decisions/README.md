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

## Amending an existing ADR

The **body is append-only.** Never rewrite a decision, its context or its numbers
to match what is true now — that destroys the record of what was believed at the
time, which is the entire value of the file.

The **`Status:` header is not history.** It is a claim about the present, and it
goes stale silently: a superseded ADR reads perfectly, so nothing warns the next
reader that its thresholds are dead. Keep it current.

When a later change affects an existing ADR, edit only the header and add a dated
block quote below it stating what is now false and what still holds:

- **Superseded by `<link>`** — the decision was replaced outright.
- **Superseded in part by `<link>`** — some of it still holds. Say precisely
  which parts, so the surviving reasoning stays citable.
- **Accepted — with one documented exception** — a later change violated an
  invariant this ADR established. Defend the exception here, and record what
  would turn it into a genuine regression.
- **Erratum** — a number published here was later shown to be wrong. Mark it
  explicitly **do not cite** and give the corrected figure. A wrong benchmark
  left quotable is worse than no benchmark.

Finding these is part of the mandatory pre-commit grep described in `AGENTS.md`:
`decisions/` is **not** excluded from that search.
