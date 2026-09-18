# Ship the full agent log, not just the error lines

**Date:** 2026-09-18
**Status:** Accepted
**Supersedes in part:** `decisions/2026-09-18_supabase-log-shipping.md`

## Context

`decisions/2026-09-18_supabase-log-shipping.md` established *that* the agent
ships its logs to Supabase, and why Supabase rather than an exposed port or a
tunnel. That reasoning stands unchanged.

What it got wrong was *how much*. It shipped only lines matching
`TeeLogger.SHIP_MARKERS` — `[TELEGRAM-FAIL]`, `CRITICAL`, `Traceback`, `❌`,
`⚠️` — on the argument that full stdout is too chatty and carries position and
cash detail that should stay on the host.

That filter answers exactly one question: *is the alert channel dead?* It is
close to useless for the question actually being asked from work, which is
*what was the agent doing when it decided that?* A stack trace without the
twenty lines that preceded it explains nothing. The filter discarded precisely
the context that makes the error interpretable, and it discarded it at capture
time, where the decision is irreversible.

The volume argument also did not survive being checked. There are ~125 `print()`
sites across the four cycle functions (`reconcile_with_ibkr` 43,
`run_market_open_buys` 38, `monitor_portfolio_intraday` 31,
`process_exit_requests` 13). At 26 market-hours cycles plus off-hours checks,
that is roughly 4–5k lines/day. With tiered retention that settles at ~16–20k
rows, on the order of 5 MB — against a 500 MB budget. The chattiness was assumed,
not measured.

The privacy argument was also mis-aimed. Positions and cash already live in
Supabase, in `portfolio_positions` and `account_balances`. The thing that must
never leave the host is **credentials**, and that is a redaction problem, not a
filtering problem.

## Decision

Ship every log line by default. Control volume with mechanisms that preserve
context instead of discarding it.

**Tiered retention.** `level` now includes `TRADE` and `INFO` alongside the
severity levels. `INFO`/`TRADE` are purged after `AGENT_LOG_INFO_RETENTION_DAYS`
(3); everything `WARN` and above keeps `AGENT_LOG_RETENTION_DAYS` (14). Routine
chatter is the bulk of the volume and the first to lose its value — nobody
debugs a healthy cycle from three weeks ago — while the lines a post-mortem
needs keep the long window. This is what makes the firehose affordable.

**Consecutive-line dedup.** Identical adjacent lines collapse into one row
carrying `repeat_count`. A stuck retry loop can emit the same line thousands of
times; without this, one bug fills the retention window. Only *adjacent* lines
collapse, so interleaved events remain distinct rows.

**A hard row ceiling.** `AGENT_LOG_MAX_ROWS` (250,000) is enforced regardless of
age, because age-based retention cannot bound a burst that happens between two
sweeps. The sweep now runs hourly rather than daily for the same reason.

**Session and sequence.** Every row carries `session_id` (one container run) and
a monotonic `seq`. Without them, interleaved lines from a restart loop are
indistinguishable from one long session — which is the exact shape a
crash-restart bug takes — and lines sharing a timestamp have no recoverable
order.

**Batched inserts.** A drained buffer goes out in 500-row chunks. One
20,000-row insert would exceed PostgREST's body limit and lose the whole
drain — precisely the backlog most worth keeping. A partial failure reports how
many rows were written rather than claiming none were.

**Flush on every path, not just the market-open cycle.** Off-hours cycles,
both main-loop exception handlers, `KeyboardInterrupt`, and a `SIGTERM` handler
all flush. Overnight is when the IBKR daily logoff, the autoheal restart and the
6am health check happen — the events most likely to be broken by morning and
least likely to be watched live. `docker stop` sends SIGTERM, whose default
disposition kills Python *without* running `atexit`, so without an explicit
handler the last cycle's logs are lost in exactly the scenario most worth
reading.

**Redaction becomes load-bearing.** It always ran, but it now protects every
line rather than the handful that matched a marker. It remains `TeeLogger`'s
sole responsibility, because the code writing a log line has no idea the text
may be transmitted.

`AGENT_LOG_SHIP_ALL=false` restores the old marker-only behaviour. It is kept as
an escape hatch so a volume problem can be resolved by an env var rather than a
deploy.

## Two bugs this surfaced

Both were found by tests, and both are worth recording because neither is
obvious from reading the diff.

**The shutdown hook must not be registered at import.** Registering
`atexit`/`SIGTERM` handlers at module import meant *any* process that imported
`execution_agent` attempted a Supabase round trip on exit and printed a warning
into its own output. `tests/test_max_positions_config.py` imports the module in
a subprocess and parses its stdout as JSON; twelve of its cases began failing
with `JSONDecodeError`. The hooks are now installed by `main_loop()`, which is
the precise scope of "the agent is actually running".

**Shipping diagnostics belong on stderr.** The failure message was written to
the real *stdout* to bypass the tee. Same consequence: it corrupts the output of
any caller parsing stdout. It now goes to `sys.__stderr__`. A regression test
asserts this directly — the first version of that test used `capsys` and was
**vacuous**, because `capsys` does not intercept `sys.__stdout__`.

A third, smaller one: a failure in the *retention sweep* was reported as "could
not ship log lines", which would send whoever read it hunting a delivery bug
that did not exist. Purge failures are now reported separately and state that
the rows were shipped.

## Consequences

- Debugging from anywhere: `SELECT ... FROM agent_logs WHERE session_id = ?
  ORDER BY seq` replays a container run in emission order.
- Steady state ~16–20k rows (~5 MB). Worst case at the row ceiling is ~60 MB.
  Both comfortable against Supabase's 500 MB tier.
- A burst is now visible rather than silently truncated: dropped lines are
  counted and the count is shipped as its own `WARN` row, naming the local log
  file as the complete record.
- The local file remains the primary, complete record. Supabase is the
  *reachable* copy and is lossy by design — a restart can cost one cycle's
  buffer.
- `migrations/20260918_expand_agent_logs.sql` is idempotent and safe in either
  order: it creates the table if `20260918_add_agent_logs.sql` was never
  applied, and adds only what is missing if it was.

## What this does not change

The choice of Supabase, and the rejection of exposing the host or running a
tunnel, are unchanged — see the superseded ADR. That host runs `ib-gateway`
with `READ_ONLY_API=no` and holds live order-submission rights; inbound exposure
remains a poor trade for log visibility.

This still does not explain the 2026-09-18 alert-channel outage. It makes the
next one readable from wherever the question is being asked.
