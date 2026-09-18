# Register preconditions: separating "is it time?" from "is it safe?"

**Date:** 2026-09-18
**Status:** Accepted

## Context

The Provisional Decision Register (`decisions/provisional_decisions.json` +
`research/decision_review.py` + `.github/workflows/decision_review.yml`) was built
on 2026-09-17 to remove the "we forgot" failure mode for **tuned parameters**. Its
one question was *is this due yet?*, answered from a closed-trade count and a date
floor — both facts about the world outside the bot.

That framing broke down the same week. On 2026-09-18 the orchestrator split was
deferred (see `decisions/2026-09-18_execution-agent-split.md`) not because it was
too early in calendar terms, but because **the bot was in the wrong state**: five
positions had filled that morning, all at day 0, sitting in the tightest Prove-It
Phase 1 band — and `monitor_portfolio_intraday`, the largest thing the split would
touch, is the code enforcing their stops.

There was nowhere to put that. The register could express "not before 2026-10-06"
but not "and only when the book is quiet". The deferral therefore lived in a chat
log, which is precisely the prose-tracking the register exists to abolish.

The request that prompted this was explicit: notify me on the day, **check the
state of the bot, and confirm it still makes sense to action** before starting.

## Decision

Model the two questions separately, because they are separate.

- **`revisit`** remains the **trigger**: has enough time passed, or enough trades
  accumulated, that this is worth looking at? Unchanged.
- **`preconditions`** is new and is the **safety gate**: given live portfolio
  state *today*, is acting actually sensible? Two predicates, both optional and
  conjunctive:
  - `max_open_positions` — `0` demands a flat book.
  - `min_position_age_days` — measured against the **youngest** open position.

A second field, `kind` (`parameter` | `work-item`), makes deferred engineering
work a first-class citizen rather than a parameter-shaped lie. Both fields are
optional and absent means "no constraint", so all five pre-existing entries
behave exactly as before.

`research/decision_review.py` gains `portfolio_state()`, which reads
`portfolio_positions` from Supabase, and `check_preconditions()`, kept as a
function distinct from `is_due()`.

The orchestrator split is registered as the first `work-item`: due 2026-10-06,
actionable at ≤2 open positions with the youngest ≥7 days old.

### Why *youngest*, not average or oldest

A single day-0 position puts the book in the tight Phase 1 band regardless of how
long the other four have been held. Any aggregate that can be dragged down by
four old positions would report "quiet" during exactly the window the gate exists
to protect.

### Why a blocked item still opens an issue

The tempting design is to suppress a due-but-blocked entry until it clears. That
reintroduces the original failure mode through the back door: an item could be
blocked for months and never surface, and nobody would know it was waiting. So a
blocked entry is reported and the issue is opened, with the blocker named and an
explicit instruction not to start. The issue stays open until the state clears.

The corollary matters more than the rule: **do not weaken a precondition to make
an issue closable.** A gate that is relaxed the moment it fires is not a gate.

### Why `None` age is not zero

`youngest_position_age_days` is `None` for a flat book. Read as `0` it would mean
"a brand-new position exists" — inverting the check and blocking work precisely
when it is safest. It is handled explicitly and pinned by a test. An unparseable
`buy_date` is likewise skipped rather than defaulted, since defaulting to `0`
would let one malformed row block a work item permanently.

## Consequences

- Deferred work is now machine-tracked with its safety conditions, and cannot be
  lost to a scrolled-away chat log.
- Reviews arrive with live bot state attached, so the "does this still make
  sense?" judgement is made against facts rather than recollection.
- The gate can stall indefinitely if the book never quietens. This is intended —
  a stalled work item is visible in every run's output and in an open issue,
  which is strictly better than one silently started at a bad moment.
- The cron is monthly, so a precondition clearing mid-month is not noticed until
  the next run. Running `python3 research/decision_review.py --insecure` locally
  answers it immediately; a tighter cadence was not worth the noise.
- `tests/test_decision_review.py` (15 tests) pins both gates, their
  independence, the flat-book case, and the registered orchestrator entry.

## Alternatives rejected

- **Folding preconditions into `revisit`.** Conflates "it is time" with "it is
  safe" and loses the ability to report *blocked*, which is the most useful state.
- **A separate work-items file.** Two alarms to maintain, two things to forget.
  The register already had the cron, the issue-opening and the Telegram path.
- **Free-form predicate strings evaluated at runtime.** Flexible and unauditable.
  Named fields can be reviewed in a diff.

## See also

- `decisions/2026-09-18_execution-agent-split.md` — the deferral that motivated this
- `decisions/2026-09-17_exit-review-automation.md` — the original register
- `docs/tech_debt_and_requirements_tracker.md` — TD-005
- `AGENTS.md` › Provisional Decision Register
