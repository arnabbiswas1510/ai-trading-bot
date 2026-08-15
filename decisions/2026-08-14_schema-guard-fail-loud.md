# 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing

## Status
Accepted

## Context

`decisions/2026-08-13_reconcile-supabase-schema-drift.md` recorded that seven
migrations believed applied were not. Three columns were absent from the live
Supabase project:

- `portfolio_positions.closed_above_entry` — the Thesis Stop's follow-through latch
- `portfolio_positions.hwm_rs_score`, `highest_rs_score` — Rule 1 (RS Decay) anchors

Nothing failed. Nothing alerted. Both rules read a missing column as `None` and
degraded into a fallback path, so two shipped risk controls sat inert in
production with no signal in the logs or Telegram.

The cost was real. NBIX and DELL both poked above entry intraday and **never
closed above it**, so the Thesis Stop's fallback — which accepts any intraday
evidence of trading above entry — permanently exempted both. NBIX ran from
−2.9% to −11.5% (≈ $1,450 of avoidable loss on a 145-share position) with no
rule able to act. Rule 1 never fired once.

The generalisable defect is not the missing column. It is that **a degraded risk
control is indistinguishable from a healthy one at runtime.** Fixing the
migration fixes today's instance; it does nothing about the next one.

## Decision

Add `schema_guard.py`, a startup and per-cycle schema assertion, and gate
`run_market_open_buys()` on it.

**Degrade, do not die.** The obvious response — abort at boot — is wrong for a
trading daemon. Exiting stops position monitoring, trailing-stop maintenance and
exits, which is strictly more dangerous than running with one rule impaired. The
guard instead mirrors the existing margin-loan block:

- **CRITICAL** (a column a live risk rule reads): refuse to open **new**
  positions; continue monitoring and exiting existing ones. Taking on fresh risk
  while the controls meant to protect it are impaired is the thing worth
  preventing.
- **ADVISORY** (`trigger_history`, `trigger_decisions`, `watchlist_history` —
  analytics archives): warn only, never block trading.

Supporting properties:

- **Self-healing.** The probe is a handful of `LIMIT 1` queries, so it re-runs
  every buy cycle. Applying the migration clears the block without restarting the
  container.
- **Alert once, not every cycle.** One Telegram alert when degradation is
  detected and one when it is resolved. A 15-minute repeating alert would be
  ignored within a day.
- **A monitoring concern must never become a trading outage.** If the probe
  itself raises (network, auth), the cycle proceeds and logs the failure.
- **Notifier failures are swallowed** — Telegram being down must not block a
  trading decision.

## Consequences

**Positive**
- The 2026-08-13 class of drift becomes loud and self-limiting: it is announced,
  and it stops new capital being committed under impaired controls.
- Boot-time output states plainly whether every risk-rule column is present, so a
  deploy that lands ahead of its migration is visible immediately.

**Negative / risks**
- A false positive (transient API error mistaken for a missing column) would
  block buys for a cycle. Mitigated by treating probe *exceptions* as pass, and
  by re-checking every cycle so a transient fault self-clears in 15 minutes.
- One more pre-flight block on the buy path. Ordered first, before the margin
  check, because it is the cheapest and most fundamental precondition.
- The CRITICAL/ADVISORY split is a judgement call and must be maintained by hand
  as new columns are added. `schema_guard.CRITICAL_COLUMNS` carries a reason
  string per column to keep that judgement explicit.

## Related change: the migration backfill was itself unsafe

While implementing this, the backfill in
`migrations/2026-08-13_apply_missing_migrations.sql` was found to reproduce the
very defect it repairs:

```sql
UPDATE portfolio_positions SET closed_above_entry = TRUE
 WHERE COALESCE(highest_unrealized_pct, 0) > 0;   -- WRONG
```

`highest_unrealized_pct` is computed from the **live intraday price** in
`monitor_portfolio_intraday()`, so it records pokes, not closes. DELL
(`highest_unrealized_pct = 0.6431`, never closed above entry) would have been
latched `TRUE` and permanently exempted from the Thesis Stop by the very script
meant to restore it.

The backfill now only pre-sets the latch for positions already past the thesis
window (`days_held > 5`), where it cannot affect behaviour, and leaves in-window
positions `FALSE` so the close-based EOD latch establishes the truth within one
session. Leaving an in-window position `FALSE` is safe because the Thesis Stop
additionally requires price ≤ −1× ATR below entry, which a working position is
not.

## Evidence for preferring the close latch

`research/latch_bt.py` (new) compares follow-through definitions under the paired
stationary-block bootstrap, 2000 reps, in both mandated universes:

| Comparison | PASS | BROAD |
|---|---|---|
| close latch vs poke fallback | **+9.92pp, CI [+3.35, +18.40], P=100%** | −5.06pp, CI [−13.47, +1.79], n.s. |

Significant benefit in one universe, no evidence of harm in the other — the same
evidentiary shape that justified shipping the Thesis Stop itself. The close latch
is not a new rule; it is the shipped design, and this ADR's contribution is
ensuring it is actually *in effect*.

Deliberately **not** shipped: removing the latch entirely (best in PASS at
+27.76pp but contradicted in BROAD — the curve-fitting signature ADR 2026-08-04
warns about) and capping the ATR-normalised threshold for high-ATR names like
DELL (moves ~1 trade; far too thin to act on).

## Files

- `schema_guard.py` — CRITICAL/ADVISORY definitions, `check_schema()`, `SchemaReport`
- `execution_agent.py` — `assert_schema_ok()`; buy-path block; boot-time report
- `Dockerfile.agent` — packages `schema_guard.py` (omitting it would reproduce the
  `trigger_audit` crash loop of 2026-08-13)
- `migrations/2026-08-13_apply_missing_migrations.sql` — corrected backfill
- `research/latch_bt.py`, `research/thesis_bt.py` — latch-definition evidence
- `tests/test_schema_guard.py` — 15 tests
- `docs/buy_logic.md`, `docs/sell_logic.md` — updated per the Doc Sync Rule

## Follow-up

`schema_guard` covers columns that gate risk rules. It does not verify column
*types* or the presence of indexes. Worth extending if a future migration changes
a type rather than adding a column.
