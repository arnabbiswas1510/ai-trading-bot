# Daily unfilled-slot alert spammed because its dedup latch could not be written (RLS)

- **Date:** 2026-10-01
- **Status:** Accepted
- **Related:** `decisions/2026-09-28_unfilled-slot-daily-alert.md`,
  `decisions/2026-09-18_agent-logs-rls-blocked-writes.md`

## Context

The execution agent sends one "UNFILLED SLOTS" Telegram summary per ET day when
the book has idle slots. `buying.run_market_open_buys` runs every 15 minutes,
~26 times a session, so the once-a-day promise depends entirely on a persistent
dedup latch. That latch is a row in the Supabase `daily_notifications` table
(`report_type='unfilled_slots'`, one row per ET date), added by
`migrations/20260928_add_daily_notifications.sql`.

The operator reported receiving the summary repeatedly — once per cycle — instead
of once per day.

## What was actually wrong

Probed live on 2026-10-01 with the agent's own key (a publishable
`sb_publishable_...` key):

```
SELECT ... FROM daily_notifications   -> 200, zero rows
UPSERT ... INTO daily_notifications   -> 42501 "new row violates row-level
                                         security policy for table
                                         daily_notifications"
```

The table has row-level security **enabled** but **no policy** that applies to
the publishable/anon role the agent authenticates with. Every other table in this
database uses a policy with no `TO` clause (so it applies to PUBLIC, including the
publishable key); `daily_notifications` had none.

The consequence in `buying.maybe_report_unfilled_slots`:

1. `_slot_report_already_sent()` runs a `SELECT`, which under RLS returns 200 with
   zero rows — read as "nothing sent today", not "access denied".
2. The summary is sent.
3. `_slot_report_mark_sent()` runs the `UPSERT`, which is refused (`42501`) and
   swallowed as non-fatal.
4. The latch never persists, so the next 15-minute cycle repeats from step 1.

This is the identical failure `agent_logs` hit on 2026-09-18
(`decisions/2026-09-18_agent-logs-rls-blocked-writes.md`): a denied `SELECT`
under RLS is indistinguishable from an empty table from the read side, so the
`SELECT`-based guard could not catch it. Only an explicit write probe separates
"new day" from "write denied".

## Decision

Two complementary changes.

1. **Fix the root cause — add the missing policy.**
   `migrations/20261001_daily_notifications_rls.sql` enables RLS (idempotent) and
   creates `daily_notifications_full_access` with `FOR ALL USING (true) WITH
   CHECK (true)` and **no `TO` clause**, matching every other table here. The
   table holds only `report_type`/`report_date`/`sent_at`/`detail` — regenerable
   operational state, never trading data or a credential — so widening read
   access to the publishable key exposes nothing that matters.

2. **Add an in-process backstop so a denied DB write can never spam again.**
   `buying.maybe_report_unfilled_slots` now latches the ET date in a module
   global (`_slot_report_latched_date`) the moment a summary is delivered,
   regardless of whether the persistent write succeeded, and checks it first.
   This guarantees at most one summary per running process per ET day even if the
   migration has not yet been applied, or if any future persistence failure
   recurs. The in-memory latch resets on container restart and on date rollover —
   exactly the "start of day" cadence intended. It is a backstop, not a
   replacement: the DB latch is still what dedups across a mid-day restart once
   the policy is in place.

## Why the backstop as well as the migration

Migrations here are applied by hand in the Supabase SQL Editor, so there is a
window between shipping this change and the operator running the SQL. The
in-process latch closes the spam in that window immediately, and makes the
feature robust against this entire failure class (a missing or dropped policy)
rather than this one instance of it. Defence in depth matched the agent_logs
precedent's spirit without a second key or config.

## Consequences

- The operator receives one unfilled-slots summary per day, as designed, as soon
  as this ships — before the SQL is applied (in-process latch) and permanently
  across restarts once it is (DB latch).
- `daily_notifications` becomes readable by the publishable key. Accepted: it is
  operational metadata, and it matches the one-rule-for-every-table convention.
- A regression test (`tests/test_unfilled_slots.py::
  test_no_duplicate_within_process_when_db_latch_write_denied`) reproduces the
  denied-write condition and asserts two cycles in one process send exactly one
  summary. The mock gained a `slot_report_write_denied` switch; an autouse
  conftest fixture resets the module latch between tests.
