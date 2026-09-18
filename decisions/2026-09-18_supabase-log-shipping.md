# Ship noteworthy log lines to Supabase

**Date:** 2026-09-18
**Status:** Accepted

## Context

On 2026-09-18 the bot executed five buys and one broker-side close and sent **no
Telegram alerts** for any of them. Patch 056 added delivery-health counters so
the *next* such outage is visible, but it could not explain this one, because
the evidence that would have explained it — the agent's own stdout — was
unreachable.

The production host is a DietPi box on a home LAN at `192.168.1.2`. Reading its
logs requires either being on that network or SSHing into it. From a corporate
network neither is possible: outbound SSH to the home router is blocked, and
probing empirically showed the corporate proxy also blocks the operator's
dynamic-DNS hostname, `api.telegram.org` and the usual paste sites. The block is
**egress-side**, on the network the question is being asked from, so no amount
of port forwarding or reverse proxying at the home end can fix it.

So the diagnosis had to wait until the operator was physically home. For a rule
that silently stops announcing live trades, that is far too slow.

## Decision

The `execution-agent` ships a **filtered, redacted** subset of its log lines to a
new Supabase table, `agent_logs`, once per monitoring cycle.

Supabase was chosen because it is already reachable from every network that
matters, already credentialed, already in the architecture, and already the
place operational state lives. Nothing new is exposed.

Three properties define the design:

**Shipping is opt-in per line, never opt-out.** Only lines containing one of
`TeeLogger.SHIP_MARKERS` — `[TELEGRAM-FAIL]`, `CRITICAL`, `Traceback`, `❌`, `⚠️`
— are shipped. Full stdout is both far too chatty and full of position sizes,
cash balances and account detail that has no business leaving the host. What
ships is the set of lines that indicate something went wrong.

**Redaction happens in `TeeLogger`, not at the call sites.** The code that writes
a log line has no idea the text may be transmitted, so the tee is the only place
that can be responsible for stripping secrets. `TeeLogger.redact()` removes IBKR
account numbers, Telegram bot tokens, Supabase JWTs and generic `key=value`
secrets before a line is even placed in the buffer, so the in-memory buffer never
holds an unredacted credential either.

**It cannot harm trading.** The buffer is a `deque` with `maxlen=500`, which is
the entire memory guarantee under a failure storm. `flush_logs_to_supabase()`
never raises, and its call site in the monitor cycle is additionally wrapped. A
`_shipping` re-entrancy guard is held across the insert, so a Supabase failure
that logs an error cannot buffer that error and re-ship it forever — the exact
loop this design invites. The capture call in `write()` runs *after* the file
write and is itself wrapped, so a bug in shipping cannot cost us the durable
local log, which remains the primary record.

Retention is 14 days (`AGENT_LOG_RETENTION_DAYS`), purged by the agent at most
once per day. Without it this is the one table in the system that would grow
without bound.

## Alternatives rejected

**Expose the host's log directory over HTTP.** Rejected on security grounds
independent of the firewall. That host runs `ib-gateway` with
`READ_ONLY_API=no`, meaning it holds live order-submission rights; inbound
exposure is a very poor trade for log visibility. The logs also carry the IBKR
account number, positions and cash. And it would not have worked anyway: the
block is egress-side.

**A tunnel (ngrok/Tailscale).** `ngrok.io` currently resolves and reaches
ngrok's edge from the corporate network, so this would work *today*. It is
rejected as a primary mechanism because it depends on a specific hostname
remaining unblocked by a proxy policy nobody here controls, and because it again
means exposing a host with live trading rights. It remains available as an
ad-hoc fallback for a deep investigation.

**Ship everything to a logging service.** More capability than the problem needs,
a new vendor, a new credential on the trading host, and a new egress dependency
that could itself be blocked.

**Telegram as the log channel.** It is the channel whose failure we are trying to
diagnose. It cannot be its own diagnostic.

## Consequences

- An alert-channel failure, unhandled exception or order error on the production
  host becomes readable from anywhere with Supabase access, within one 15-minute
  cycle.
- `agent_logs` is deliberately **not** in `supabase_backup.TABLES`. Backing it up
  would preserve indefinitely the very rows the retention policy exists to
  delete. It is listed in the new `NOT_BACKED_UP` map with that reason, so the
  backup-completeness test still forces every new table to be *classified*
  rather than silently omitted.
- Lines are buffered in memory between cycles, so a container restart loses at
  most one cycle's worth. The local file keeps them regardless; only the remote
  copy is lossy. This is accepted — durability lives in the file, reachability
  lives in Supabase.
- If the `agent_logs` migration has not been applied, the insert fails, the
  error is written straight to the real stdout, and the agent carries on.
  `schema_guard` reports the table as ADVISORY, never blocking buys.
- Under a sustained failure storm, lines are dropped oldest-first and the drop
  count is shipped as its own `WARN` row, so a truncated view never reads as a
  complete one.

## Follow-up

This does not explain the 2026-09-18 outage. That still requires the
`docker logs execution-agent` grep the operator has yet to run. What this
guarantees is that the *next* one does not depend on where anybody is sitting.
