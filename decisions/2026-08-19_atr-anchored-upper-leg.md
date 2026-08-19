# Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price

## Problem

The Smart OCA Managed Exit defaulted `limit_mode` to `BREAKEVEN`, which resolves
the upper leg to the position's **entry price**. That makes the bounce required
to fill proportional to the loss already taken:

| Position | Entry | Price | Breakeven target | Bounce needed |
|---|---|---|---|---|
| LPG | 49.88 | 49.41 | 49.88 | **+0.94%** (workable) |
| DELL | 496.04 | 468.61 | 496.04 | **+5.85%** (unreachable) |

The incentive is backwards: **the deeper underwater, the further away the
target, the less likely it fills, the longer the position bleeds.** On DELL the
upper leg could never realistically fill, so the OCA degenerated into "trail
down, or sit until the 3-day expiry and market out" — exactly the
bounce-dependence the queue was built to avoid.

The requirement driving this change was to be able to write:

```sql
INSERT INTO exit_requests (ticker) VALUES ('LPG');
```

and get a smart exit that **completes**, without the operator reasoning about
limit modes or supplying prices.

## Decision

Add `limit_mode='ATR_AUTO'` and make it the default, at both the schema and the
CLI:

```
upper = current_price × (1 + clamp(0.50 × ATR%, 0.75%, 5.0%))
lower = trail at        clamp(0.33 × ATR%, 1.50%, 4.0%)      (already existed)
```

Entry price drops out of the maths entirely, so the target is always roughly
half a session's move away and scales to each stock's own volatility:

| Position | ATR | Upper | Bounce needed | Trail |
|---|---|---|---|---|
| LPG | 4.0% | 50.40 | +2.0% | 1.50% |
| DELL | 7.6% | 486.41 | +3.8% | 2.51% |
| quiet name | 0.5% | +0.75% (clamped up) | — | 1.50% |
| wild name | 20% | +5.0% (clamped down) | — | 4.00% |

The upper fraction (0.50) is deliberately larger than the trail fraction (0.33),
so the optimistic leg always sits further from the market than the protective
one — the asymmetry the operator asked for, preserved at every ATR.

Both clamps are load-bearing: without the floor a very quiet name gets a target
inside the bid/ask spread; without the ceiling a very volatile name gets a
target no realistic bounce reaches, which reintroduces the original bug.

### Polling, not LISTEN/NOTIFY

The queue is drained by the agent's existing 15-minute cycle. A trigger-based
`LISTEN/NOTIFY` was considered and **rejected**: PostgreSQL notifications are
fire-and-forget, so any notification raised while the agent is disconnected
(restart, redeploy, dropped pooler connection) is lost permanently. A poller
would still be required as a backstop, so the trigger would add a failure mode
without removing anything.

Polling costs one indexed query per cycle (~96/day) against a partial index on
`status`, on a connection the agent already holds. The real cost is latency —
up to 15 minutes — and the remedy for that is a shorter queue-only poll
interval, not a different mechanism.

## Consequences

- A bare insert now produces a self-sizing OCA that resolves regardless of how
  far underwater the position is.
- `BREAKEVEN` remains available for the genuinely optimistic case, and
  `request_exit.py` now prints a warning showing the bounce it requires whenever
  the entry sits above the current price.
- `MARKET` mode (`--now`) is unchanged and remains the correct choice for "get
  me out now" — ATR_AUTO is "get me out well, and soon".
- The ATR used is `entry_atr_pct`, recorded at entry rather than today. For a
  name whose volatility has since expanded this sizes both legs slightly tight;
  the hard floor and expiry backstops bound that exposure.
- Existing `PENDING` rows are deliberately **not** rewritten by the migration: a
  queued `BREAKEVEN` may have been an explicit choice. Cancel and re-queue to
  move one onto the new default.

## Guard

`tests/test_oca_managed_exit.py::TestAtrAutoUpperLeg` pins the properties that
matter: the target ignores entry price entirely, is always nearer than breakeven
on a loser, is clamped at both ends, falls back to the default ATR when none is
recorded, applies when no mode is stored at all, and never crosses the trail at
any ATR from 1% to 12%.
