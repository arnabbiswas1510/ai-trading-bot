# Smart OCA Managed Exit — a queue-driven exit the agent places itself

Date: 2026-08-18
Status: Accepted

## Context

Exiting a named position *intelligently* — riding a bounce rather than dumping
at whatever the current print happens to be — was only possible by hand, via
`force_sell.py` or `managed_exit.py`. Both carry the same operational tax:

> Sells must use `clientId=1`, the session that placed the buys, or IBKR's cash
> account logic treats them as opening a short. The execution-agent must
> therefore be **stopped** before either script can run.

Stopping the agent to manage one exit leaves *every other position unmonitored*
— no trailing stop maintenance, no thesis stop, no dollar stop, no kill-switch.
The tool built to reduce risk on one name removes protection from the other
three. That trade is never worth making, so in practice these tools went unused
and positions were either dumped at market or left to the automated ladder.

The immediate trigger was DELL: 46 shares at $496.04, trading at $468.61
(−$1,261.78). Its live GTC trailing stop was 12% from a high-water mark of
$499.23, meaning the real exposed downside was **$439.32, or −$2,609** — far
worse than the loss actually being contemplated. Something had to bound it, and
that something could not require taking the agent offline.

## Decision

Exits become **data**. A row in a new `exit_requests` table expresses the
intent; the execution-agent — which already holds `clientId=1` — drains the
queue on its normal 15-minute cycle and places an IBKR **OCA pair**:

| Leg | Order | Purpose |
|---|---|---|
| Upper | `LMT` SELL at a recovery target | the optimistic exit |
| Lower | `TRAIL` SELL | ratchets up behind any bounce |

One cancels the other (`ocaType=1`).

### The lower leg is a trailing stop, not a static stop

This is the load-bearing choice. With a static stop, a position that rallies
most of the way to the limit and then fades still exits at the original stop
price — the entire move is handed back. A trail follows the advance and banks
whatever the bounce actually delivered. That retained upside is the *only*
thing that makes waiting for the upper leg better than selling now; a static
stop removes it and leaves nothing but the downside.

### Requests store intent, not prices

Requests are usually queued outside market hours. A row carrying
`limit_price = 489.89` written at 22:00 is stale by 09:30. Storing the mode
(`BREAKEVEN`, `PCT_FROM_ENTRY`, `ABS`, …) lets the agent resolve the real price
at placement time. `ATR_AUTO` likewise scales the trail to the stock's own
volatility, reusing the reasoning already proven in `managed_exit.py`: a 1%
trail on a name with a 7% average true range fires on the first tick of
ordinary noise, cancels the upper leg, and reproduces "sell now" with extra
steps.

### A separate table, not columns on `portfolio_positions`

`portfolio_positions` rows are **deleted** when a position sells. Columns there
would destroy the record of what was requested at exactly the moment it became
worth reviewing. `exit_requests` outlives the position and is the audit trail.

### Drained every cycle, not just at the open

The agent already loops every 15 minutes. "First thing in the morning" is
simply the case where the request was queued overnight. An exit decision made
at 11:00 should act at 11:00.

### Placement waits for the tape to settle

Legs are not placed at 09:30. The opening auction has the widest spreads and
the wildest prints of the session, so a limit computed off a 09:30 tick is
computed off noise. Placement begins at `OCA_EXIT_SETTLE_MINUTE` (09:45 ET) for
requests queued before the open; intraday requests are placed immediately.

### The automated ladder is suspended for managed tickers

Every automated exit rule (thesis stop, early dollar stop, intraday minimiser,
EMA-21 exit, kill-switch) funnels into `execute_sell()` or `arm_exit()`, and
**both cancel all open SELL orders for the ticker** — which would silently
destroy the OCA. While a request is `PLACED`, those rules skip the position
entirely.

Because that suspension removes the normal safety net, two software backstops
are mandatory and enforced by the agent every cycle:

- **Hard floor** — market-exit if price falls `hard_floor_pct` below the
  placement price. Without it a name that gaps down and keeps sliding sits
  unsold behind an unreachable limit.
- **Expiry** — market-exit after `expires_after_days` trading days. An OCA can
  otherwise sit unfilled indefinitely while the position bleeds.

`get_oca_managed_tickers()` re-asserts `status == 'PLACED'` in Python even
though the query already filters server-side. Suspension is the one failure
mode that leaves a position with *no stop at all*, so it fails closed: any row
that is not unambiguously a `PLACED` exit request can never suspend the ladder.

### `ocaType=1` (CANCEL_WITH_BLOCK)

Not cosmetic. In a cash account two unblocked SELL orders for the same shares
are liable to be rejected as exceeding the position, and a partial fill on one
leg must reduce its sibling rather than leave a naked short.

## Consequences

**Positive**
- Managed exits no longer require stopping the agent; the portfolio is never
  unmonitored.
- Worst case on a managed position is bounded by the floor, not by a 12%
  trail from a stale high-water mark.
- Full audit trail of what was requested, what was placed, and how it resolved.
- The queue is a table, so the dashboard can offer a "Smart Exit" button that
  simply inserts a row — no SSH, nothing to stop.

**Negative**
- Suspending the automated ladder concentrates responsibility in the floor and
  expiry settings. A carelessly wide `--floor` is now genuinely dangerous.
- An OCA is a bet, not a fix. For DELL it converts a certain −$1,262 into a
  bounded range of roughly −$283 to −$1,801. It removes the −$2,609 tail; it
  does not make a failed breakout a good hold.
- Two GTC orders per managed position consume IBKR order slots.

**Neutral**
- `force_sell.py` and `managed_exit.py` are unchanged and remain the right
  tools for a true emergency, when waiting for the next 15-minute cycle is
  itself the risk.

## DELL — the first request

| | |
|---|---|
| Position | 46 sh @ $496.04 (cost $22,817.84) |
| At decision | $468.61 → −$1,261.78 (−5.53%) |
| Prior worst case | $439.32 (12% trail from HWM $499.23) → **−$2,609** |
| Upper leg | $489.89 — today's high, a level the market actually printed → −$283 |
| Lower leg | 2.5% trail (~$456.89) → −$1,801 |

Breakeven ($496.04) was rejected as the upper leg: it needs a +5.85% round trip
on a breakout already marked `FAIL` that never closed above entry. $489.89 is a
tested level from the same session and roughly doubles the odds of filling.

2.5% was chosen over 2.0% because DELL's daily range is ~6.7% of price
(ATR 7.6%); a 2% trail sits inside that noise and would likely fire before the
limit had any chance.

## References

- `migrations/add_exit_requests.sql`
- `execution_agent.py` — `process_exit_requests()`, `place_oca_exit()`,
  `get_oca_managed_tickers()`
- `request_exit.py` — the queueing CLI
- `tests/test_oca_managed_exit.py`
- `docs/sell_logic.md`, `docs/configuration.md`
- Prior art on ATR-scaled trails: `managed_exit.py`
