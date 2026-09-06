# Fail loudly when the database is unreachable

**Date:** 2026-09-06
**Status:** Accepted

## Context

A DietPi reboot left every Docker container without an external DNS resolver.
The host resolves only via Tailscale MagicDNS (`100.100.100.100`), and Docker
bakes the upstream resolver list into a container's embedded resolver at
*create* time — so containers created before `tailscaled` was ready came up
with `NO EXTERNAL NAMESERVERS DEFINED`. That root cause is addressed separately
by pinning `dns:` in `docker-compose.yml`.

This ADR is about the *second* failure, which is the more dangerous one.

With Supabase unreachable, the dashboard did not show an error. It showed:

```
Invested:  $0        Cash: $100,000
Positions: 0         Trades: 0
```

That is a plausible, internally consistent, entirely fictional account state.
It is indistinguishable from a fully liquidated portfolio. In reality five
positions worth $94,807 and thirty closed trades were sitting safely in
Supabase the whole time.

The cause was a swallowed exception. `get_positions()` and
`get_trade_history()` both ended in:

```python
except Exception as e:
    print(f"Error getting positions from Supabase: {e}")
    return []
```

`[]` conflates two completely different facts: **"the broker holds no
positions"** and **"we could not find out."** Every layer above then behaved
correctly given a wrong input — the summary arithmetic is right, the renderer
is right, the $100,000 is the untouched `initial_balance` with nothing
subtracted from it. Nothing was broken except the premise.

The frontend compounded it: `if (portfolioRes.ok)` with no `else`, so a failed
fetch left `portfolioData` at its initial value and rendered anyway.

The market was closed when this happened. Had it been open, there would have
been no signal that the execution agent — which failed identically — was blind
to its own triggers and unable to record its own fills.

## Decision

**A failure to read is raised, never returned as data.**

1. `backend/database.py` defines `DataSourceUnavailable`. `get_positions()` and
   `get_trade_history()` raise it (chaining the original cause) instead of
   returning `[]`.
2. `/api/portfolio` and `/api/trades` map it to **HTTP 503**, distinct from the
   500 used for genuine application errors, so the client can tell "the
   database is unreachable" from "the server has a bug".
3. The frontend treats a non-OK response from either endpoint as fatal and
   **replaces the entire view** with an error panel plus a Retry button.

Point 3 is deliberate. A warning banner *above* a $100,000 dashboard is still a
dashboard showing $100,000, and the numbers are the hazard. They must not
render at all.

An empty database is still legitimate and still returns `[]`. The fix
distinguishes unreachable from empty; it does not turn "no open positions" into
an error. `tests/test_datasource_unavailable.py` pins both halves of that.

## Consequences

- An unreachable Supabase is now immediately visible instead of silently
  fabricating a clean slate.
- The 503 is machine-readable, so monitoring can alert on it.
- Three other accessors (`get_daily_triggers`, `get_cash_flows`,
  `get_account_balances`) still use the `return []` pattern. They are not
  fixed here: none of them can produce a *plausible but false account state*,
  which is the specific hazard being addressed. Converting them is worthwhile
  but is a wider change with more call sites, and is tracked as follow-up
  rather than smuggled into this one.
- The execution agent is untouched. It does not use these accessors, so
  trading behaviour cannot be affected by this change.

## Alternatives rejected

**Return a `stale: true` flag and keep rendering.** Rejected: it preserves the
exact hazard — real-looking numbers on screen — and relies on the operator
noticing a badge.

**Cache the last good response and show that.** Rejected: stale positions
during market hours are worse than no positions, because they invite action.
Age would have to be displayed prominently anyway, at which point showing the
error is simpler and safer.

**Fix only the DNS.** Rejected: DNS was one trigger. Any Supabase outage, key
rotation, RLS change or network partition reproduces the same silent lie.
