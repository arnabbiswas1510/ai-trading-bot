# Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those

## Problem

The Smart OCA Managed Exit was reachable only by hand (`request_exit.py` or a
row in `exit_requests`). Every *automated* sell rule still called
`execute_sell()` — a plain `MarketOrder` — so the bot sold at whichever price
the 15-minute cycle happened to notice.

For rules that fire on an emergency that is defensible. For rules that fire
because a position is *boring* it is not: the plateau exit's own trigger
condition is "nothing has happened for 10 trading days", and it then demanded
an immediate fill as though the building were burning.

The request was to use the smart sell for **every** sell scenario. That turns
out to be wrong for three of them, so this ADR records the boundary as much as
the change.

## Decision

Add `enqueue_smart_exit()` — rules write the same `exit_requests` row a human
would write, rather than calling `place_oca_exit()` directly. One placement
path, one audit trail, one set of backstops, and every rule inherits ATR_AUTO
sizing, the hard floor and the expiry for free.

Routed through the queue (`SMART_EXIT_FOR_RULES`, default on):

| Rule | Day | Why it qualifies |
|---|---|---|
| EMA-21 support breach | 7+ | EOD signal on a position past its consolidation window |
| Plateau exit | 7+ | Trigger is literally "no new high in 10 days" |
| Intraday Loss Minimiser | 7+ | Past the armed-exit scope; not an emergency |
| Intraday Minimiser fallback | 7+ | Same |

### Left alone deliberately

**Day 0–6 loss cutters — kill-switch, dollar stop, thesis stop — keep
`arm_exit()`.** A *placed* OCA suspends the automated ladder for up to
`OCA_EXIT_DEFAULT_EXPIRY_DAYS` (3), leaving only the 5% floor. For a position
that is actively failing in its first week that is the wrong trade: those rules
exist to cut fast, and handing them a 3-day instrument inverts their purpose.
Placement is also deferred to `OCA_EXIT_SETTLE_MINUTE`, so a kill-switch firing
at 14:00 would wait until the *next morning*. `arm_exit()` already rides a
bounce via a 0.6% trail and force-sells after `ARMED_EXIT_DEADLINE_HOURS`,
which is the correct shape for an urgent exit.

**Rank & Replace keeps its market sell.** It is the least urgent rule in the
ladder and by that logic the best candidate — but it is a *swap*: the sell
exists only to fund the named replacement buy on the next line
(`if sold: run_market_open_buys(ib)`). An OCA may take 3 days, so routing it
through the queue decouples the halves — cash stays tied up, the slot stays
occupied, and the trigger being rotated into is long gone by the time the sell
completes. A better exit price is not worth losing the entry it was taken for.

**The backstops stay market orders.** The floor/expiry handler inside
`process_exit_requests()`, and the `arm_exit` deadline, are the backstops *for*
the smart mechanisms. A backstop that can itself fail to fill is not a
backstop — making these smart is infinite regress.

## Consequences

- Non-urgent exits get an ATR-sized limit target instead of a market print, and
  a trail that keeps working if the target is never reached.
- **A queued rule exit suspends the ladder for that ticker** once `PLACED`. The
  floor and expiry are what bound the risk; this is the same exposure a manual
  request already carried, now reachable automatically.
- `process_exit_requests()` is called a **second time** after
  `monitor_portfolio_intraday()`. Without it a rule that fired at 10:00 would
  sit `PENDING` until 10:15 before its OCA went out. It is idempotent.
- Enqueue failure (migration not applied, unexpected error) returns `False` and
  the caller falls back to `execute_sell()`. **A triggered sell rule must never
  execute nothing** — that is the one outcome worse than a market sell.
- A duplicate insert (unique partial index `idx_exit_requests_one_active`) is
  treated as *success*: a request already owns that exit, and market-selling on
  top of a live OCA would cancel the legs it just placed.
- `requested_by` distinguishes `manual` from `auto:plateau`, `auto:ema21`,
  `auto:intraday_minimiser`, so the audit trail shows which rule asked.
- Set `SMART_EXIT_FOR_RULES=false` to restore market selling everywhere.

## Guard

`TestEnqueueSmartExit` covers the payload, the duplicate-is-success rule and
both fallback paths. `TestSmartExitRuleScoping` asserts the boundary directly —
that the Day 0–6 cutters still call `arm_exit()`, that Rank & Replace and the
backstops never enqueue — so the scoping cannot be widened by accident.
