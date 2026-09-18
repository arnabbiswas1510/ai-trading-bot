# agent_logs was unwritable for a day, and the guard said it was fine

**Date:** 2026-09-18
**Status:** Accepted

## Context

Log shipping to Supabase shipped earlier the same day
(`decisions/2026-09-18_comprehensive-log-shipping.md`). Its whole purpose is to
make the execution agent's log readable from anywhere, because the production
host at `192.168.1.2` is not reachable from outside the LAN and `docker logs` is
therefore unavailable exactly when it is most needed.

It did not work. `agent_logs` was empty.

Three things had to be untangled to establish why, and the order matters because
the first two both produce the same innocent-looking symptom.

**1. An empty table under RLS is ambiguous.** A `SELECT` denied by row-level
security does not error. It returns HTTP 200 with zero rows. So "the feature has
not shipped anything yet" and "every insert is being rejected" are
indistinguishable from the outside. The only way to separate them is to attempt a
write:

```
POST /rest/v1/agent_logs
401 {"code":"42501","message":"new row violates row-level security policy"}
```

**2. The policy was scoped to a role the agent does not have.**
`migrations/20260918_add_agent_logs.sql` created:

```sql
CREATE POLICY agent_logs_service_role_all ON agent_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

Every other table in the project uses a policy with **no `TO` clause**, which
applies to `PUBLIC`:

```sql
CREATE POLICY "Service role full access" ON ibkr_fills
    FOR ALL USING (true) WITH CHECK (true);
```

The name on those older policies is misleading — they are not service-role
scoped — and that misleading name is most likely what prompted the new table to
be written with an explicit `TO service_role` that looked like it matched
convention while doing something materially different. The bot authenticates
with a single anon-class publishable key (`sb_publishable_…`, `SUPABASE_KEY`),
which is not `service_role`, so it was denied. This was already documented in
`decisions/2026-09-06_commission-accounting.md`; it was not carried across.

**3. The failure was reported to the one place nobody can read.**
`flush_logs_to_supabase()` catches the insert error and writes it to `stderr`,
deliberately bypassing `TeeLogger` to avoid recursing into the buffer it holds a
guard on. That reasoning is sound, but the consequence is that the error is
visible only in `docker logs` on the unreachable host — which is precisely the
problem log shipping exists to solve. The feature failed in the one way it could
not report.

**Why `schema_guard` did not catch it.** `_probe()` issues `SELECT … LIMIT 1`.
Under point 1, that succeeds. The guard reported the table present and healthy
for the entire time not one line was being stored.

## Decision

**Relax the policy to match every other table.** `agent_logs_service_role_all` is
dropped and replaced by `agent_logs_full_access` with no `TO` clause.

The alternative — issuing the agent a service-role key — was rejected. A
service-role key bypasses RLS on *every* table in the project, so to fix logging
it would grant unrestricted write access to `trade_history`,
`portfolio_positions` and `daily_triggers`. That is a far larger change in blast
radius than the problem justifies, and it would leave this project with two key
classes to manage on a host that is already hard to reach.

**The accepted trade-off is explicit:** shipped log lines are now readable by
anyone holding the publishable key, on the same terms as every other table here.
That is acceptable *only* because the log is redacted before it is buffered —
which made the state of the redaction worth checking before relaxing anything.

**It had a hole.** `_REDACTIONS` matched JWTs (`\beyJ…`) and `key=value` pairs
following `apikey=` / `token=`. Supabase's current key format is **not a JWT**,
so a **bare** `sb_secret_…` or `sb_publishable_…` token — not preceded by
`apikey=` — passed through all four patterns untouched. The shape that leaks is
exactly the 401 error text quoted above, which the agent was generating every 15
minutes. A pattern for `sb_(publishable|secret)_…` was added, with three tests
proving it (verified non-vacuous: they fail with the pattern removed).

**And close the blind spot, not just this instance of it.** `schema_guard` gains
`ADVISORY_WRITABLE`, a short list of tables whose entire purpose is to be written
and where a read probe therefore proves nothing. Each is probed with a sentinel
insert and an immediate delete. The finding is reported alongside the other
advisory results and **never blocks buys** — losing logs is a visibility problem,
and blocking trading over it would be a strictly worse failure than the one being
fixed. Cleanup is best-effort: the sentinel carries an epoch `logged_at`, so the
agent's own retention sweep removes any survivor on its next pass.

## Consequences

- `agent_logs` accepts writes from the key the agent actually holds.
- Redaction is now the **primary** control on log contents, not a backstop. Any
  future credential format needs a pattern here before it can appear in a log
  line.
- A table that reads but does not write is now detected in the same cycle rather
  than after a day of silence. The cost is one insert and one delete per buy
  cycle, per listed table — which is why the list is deliberately one entry long.
- `schema_guard`'s report can now be non-empty while the schema is fully healthy.
  `ok` and `degraded` are unchanged, so nothing downstream treats it as a block.
- The older `"Service role full access"` policies keep their misleading name.
  Renaming them is a separate change with no functional effect, and doing it in
  this commit would obscure the one policy change that matters.

## Deployment

The migration must be run by hand in the Supabase SQL Editor — the anon key
cannot alter policies, which is the same restriction that caused the bug. It is
idempotent and ends with a verification query that must return one row with
`roles = {public}`.

If the production host's `SUPABASE_KEY` turns out to be a service-role key, then
writes were working all along and only external visibility was blocked. The
migration is correct either way; it makes the table readable and writable on the
same terms as the rest of the project.
