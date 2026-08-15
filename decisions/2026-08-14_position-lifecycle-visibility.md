# 2026-08-14 — Surface every risk rule's live state in the dashboard

## Status
Accepted

## Context

Two losses this month (NBIX −$2,226, DELL open) came from the same failure: a risk
rule that looked healthy was in fact unable to fire, and there was no way to see
that from the dashboard. In both cases the Thesis Stop had been exempted by an
intraday poke above entry — invisible everywhere except in the agent's stdout.

Reviewing `DashboardView.jsx` while fixing that turned up a second, worse problem.
The expanded position panel was describing rules that no longer exist:

| Displayed | Reality |
|---|---|
| "Day 7+: Mandatory Time-Stop if gain < 2.0%" | rule removed |
| "Break-Even Stop — Inactive (Need +5.0% peak)" | rule removed |
| `STOP_LOSS_PCT = 0.07  // 7% trailing stop` | base is 10%, ATR-scaled to a 12% cap |
| `PLATEAU_DAYS = 7` | plateau fires after 10 stale trading days |
| "Tier 3 auto-rotate fires at day 7" | Rank & Replace, margin-gated, day 7+ |

Meanwhile it showed **nothing at all** about the Early Loss Kill-switch, the Thesis
Stop, the follow-through latch, the Day-3 verdict, Power Hold or the Armed Exit —
that is, every rule that actually decides whether a position lives or dies. A
dashboard that confidently describes rules that do not exist while omitting the ones
that do is worse than no dashboard: it manufactures false confidence.

## Decision

Add a **Risk Rule Ladder**: one row per rule, per position, showing which of the nine
lifecycle rules apply right now, what state each is in, its exact trigger price and
the distance to it.

**A single mirrored rule engine, not inline JSX.** `frontend/src/lib/positionRules.js`
is a pure module holding the agent's thresholds and the same evaluation order as
`docs/sell_logic.md`. Both the compact table cell and the expanded panel call it, so
they cannot disagree. The stale content above existed precisely because thresholds
were scattered across three inline `if` ladders that nobody updated together.

**Seven states, not a boolean.** "Rule on/off" cannot express the thing that cost
real money. The states are ARMED, TRIGGERED, DEGRADED, WATCH, ACTIVE, PENDING,
SUPPRESSED, EXPIRED, OFF.

`DEGRADED` is the point of the exercise: the rule exists, the window is open, and it
still cannot protect the position. Today all four holdings would render the Thesis
Stop as healthy; with this change DELL renders **orange, "DISARMED by an intraday
poke above entry"**, naming the missing `closed_above_entry` column and the migration
that fixes it. This is the visual counterpart to `schema_guard.py` — the guard blocks
new buys, the ladder explains why to a human.

**Colour semantics are fixed and legended.** Red = acting or about to act. Orange =
cannot protect you. Amber = close to its trigger. Green = live and clear. Blue =
deliberately off. Grey = not yet armed or already past. A collapsible legend inside
each ladder states these, so the colours are not folklore.

**Real estate.** The compact cell replaces the old "Plateau Days" column rather than
adding a twelfth: plateau days are now one row of the ladder with a progress bar, so
a dedicated column was redundant. The cell shows only states needing attention
(`✓ all rules nominal` otherwise) with the full nine-rule breakdown in the hover
tooltip and the complete ladder on row expansion — the collapsible pattern already
used for the Entry Conviction card.

**Day counts are recomputed client-side.** `days_held` in Supabase is a snapshot
written at the last cycle; the agent recomputes it live via `trading_days_between()`
every cycle. The UI does the same. Rendering the stored value would mis-state which
window a position is in — DELL's stored `days_held` is 0 while it is genuinely on
day 1.

## Consequences

**Positive**
- The NBIX/DELL failure mode is now visible at a glance instead of requiring a
  forensic session.
- Every threshold shown is traceable to one constant block that can be diffed
  against `execution_agent.py`.
- Removing rules from the agent in future will no longer silently leave zombie
  descriptions in the UI, because there is one place to delete them from.

**Negative / risks**
- **The constants are a mirror, and mirrors drift.** `RULES_CONFIG` duplicates values
  that live in `execution_agent.py`. This is the same class of bug being fixed. It is
  accepted because the alternative — an endpoint that evaluates rules server-side —
  requires the backend to import the agent's module graph (ib_insync and all), which
  the container split exists specifically to prevent. Mitigations: every value carries
  a comment naming its source, the module header states it is a mirror, and
  `verify-build.mjs` now fails the Docker build if the ladder is missing from the
  bundle.
- The rule engine reads env-var *defaults*. A prod override (e.g. `STALE_EXIT_DAYS`)
  would not be reflected. No position currently relies on an override.
- Three rules (EMA-21, Day-3 verdict, Rank & Replace) can only report a window and a
  state, not a price, because the API does not carry EMA-21 or a live trigger list.
  The ladder says so rather than inventing a level.

## Correctness issues found and fixed while implementing

1. **Rank & Replace would have shown a false TRIGGERED on 3 of 4 positions.** The
   agent gates the entire rule on `len(positions) >= MAX_POSITIONS`; with 4 of 5 slots
   filled it never runs, because a free slot means the trigger is *bought*, not
   swapped. The engine now reports `SUPPRESSED — Idle, book not full (4/5 slots)`.
   The comparison is also strictly `>` the margin, not `>=`.
2. **`is_power_hold` never existed.** `backend/database.py` read
   `row.get("is_power_hold")` against a column actually named `power_hold`, so Power
   Hold read as `False` for every position, always.
3. **Live RS never rendered.** The panel read `pos.rs_score`; the API supplies
   `live_rs_score`.
4. **Thesis Stop past day 5.** Reporting DEGRADED for a position whose window has
   closed is alarming without being actionable — the latch is irrelevant once the rule
   cannot fire. The expiry check now precedes the latch check, so only DELL (the one
   position genuinely in the window) is flagged.

## Files

- `frontend/src/lib/positionRules.js` — **new**; `RULES_CONFIG`, `STATE`, `STATE_META`,
  `evaluatePositionRules()`, `rulesTooltip()`
- `frontend/src/components/DashboardView.jsx` — `LifecycleCell`, `RiskLadder`,
  `rulesFor()`; stale Trail Stop / Time-Stop / Break-Even cards removed
- `backend/database.py::get_positions()` — exposes the lifecycle fields
- `frontend/scripts/verify-build.mjs` — 9 new fingerprints
- `docs/sell_logic.md` — Dashboard section

## Follow-up

If a third consumer of these thresholds appears, move the evaluation server-side by
extracting the constants from `execution_agent.py` into a small dependency-free module
that both the agent and the backend can import. Not worth it for two consumers.
