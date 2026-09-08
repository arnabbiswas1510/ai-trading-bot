# Sell-state transition notifications

**Date:** 2026-09-08
**Status:** Accepted

---

## Context

Every 15-minute cycle, `monitor_portfolio_intraday()` places each open position
under exactly one **governing exit regime** — the rule that currently decides how
it will be sold. Three of the most meaningful regime changes happened **silently**:

- **Unproven → Proven** — the breakout closed above entry for the first time, so
  the Prove-It stop moves from anchoring at *entry* (Phase 1) to guarding the
  *give-back floor* (Phase 2). This is the single most important "the trade is
  working" signal and there was no notification for it.
- **Give-back floor arming** — the peak gain topped +2% (`PROVE_IT_P2_ARM_GAIN_PCT`),
  so a green trade is now protected from becoming a loss. Silent.
- **Profit-lock engaging** — the gain reached +5% (`TRAIL_PROFIT_TIERS[0]`), so the
  trailing stop tightened to 1.5% to lock in profit. Silent.

Three other regimes already had their own richer Telegram: an **armed exit**
(`notify_prove_it_stop`), **power hold** arming (`maybe_arm_power_hold`), and a
**partial scale-out** (its own message).

The operator asked for a concise Telegram on *every* change in a position's sell
logic.

## Decision

Add a small **per-position state machine** persisted in
`portfolio_positions.sell_state`. Each cycle the agent computes the single
governing regime (`sell_state_code()`); when it differs from the stored value it
sends one concise Telegram (`notify_sell_state_change()`) and updates the column.

**Regimes**, in precedence order (mirrors the monitor loop):

| Code | Meaning | Announced here? |
|---|---|---|
| `EXITING` | an exit is armed / selling | no — `notify_prove_it_stop` already fires |
| `POWER_HOLD` | O'Neil 8-week leader | no — power-hold arm message already fires |
| `PROFIT_LOCKED` | peak ≥ +5%, trail tightened | **yes** |
| `PROVEN_FLOOR` | proven, peak ≥ +2%, floor armed | **yes** |
| `PROVEN` | closed above entry, peak < +2% | **yes** |
| `UNPROVEN` | never closed above entry | initial state only |

`EXITING` and `POWER_HOLD` are **latched but not re-announced**
(`SELL_STATE_SUPPRESS_NOTIFY`) so they do not double-text against their existing
dedicated messages — but tracking them means *leaving* them (e.g. power hold
expiring back to `PROFIT_LOCKED`) is still a clean, announced transition.

### Why persist the state instead of comparing in memory

The agent restarts (deploys, gateway reconnects). An in-memory "last state" would
re-announce every position's regime on every restart. Latching in Supabase makes
each transition fire **exactly once**, ever.

### Silent first observation

When a position has no prior `sell_state` (just bought, or the migration just
landed), the first cycle records its regime **silently** — an initial state is
not a transition, and the buy was already announced. Only subsequent changes ping.

## Consequences / known limitations

- **Inert until migrated.** The rule is gated on the `sell_state` column
  existing. Before `migrations/add_sell_state_column.sql` is applied, the agent
  logs a one-line notice and skips the notification rather than spamming every
  cycle (it cannot latch, so it cannot detect a *change*).
- **Latch-first ordering.** The column is written *before* the Telegram is sent,
  so a notification failure can never cause a re-fire next cycle.
- **No new tunable.** There is no env toggle; the feature is always on once the
  column exists. Muting is done at the Telegram bot, consistent with the other
  notifications.
- Transitions into `EXITING` / `POWER_HOLD` are intentionally quiet here to avoid
  duplicating their richer dedicated messages.

## Alternatives rejected

- **In-memory last-state** — re-announces everything on every restart.
- **A toggle env var** — unnecessary surface area; the bot itself is the mute
  point, and the other regime messages have no toggle either.
- **Announcing armed / power-hold here too** — would double-text against the
  existing richer messages for those events.
