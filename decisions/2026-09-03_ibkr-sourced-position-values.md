# Source dashboard position values from IBKR, not FMP

- **Date:** 2026-09-03
- **Status:** Accepted

## Context

The dashboard valued every open position as:

```
shares (Supabase)  x  price (live FMP quote)
```

Three defects followed from that.

**It never matched the broker.** `shares` came from our own ledger and `price`
from a third-party vendor, so neither factor was IBKR's. Any share-count drift
(partial fills, manual TWS adjustments) silently propagated into the reported
market value, and IBKR's own average cost — which diverges from `buy_price`
after a partial fill — was never used at all.

**It mixed two vintages of data in one total.** `/portfolio` adds position value
to `ibkr_cash_balance`, which the execution agent refreshes once per 15-minute
cycle. Adding a live FMP quote to a 15-minute-old broker cash figure produces a
number that was never true at any single instant.

**It hid staleness.** FMP returns a quote after the close, so the dashboard
always rendered a fresh-looking price with no indication of when — or whether —
the broker had last agreed with it. The failure mode was invisible.

A migration describing the fix (`migrations/add_ibkr_position_values.sql`) was
drafted on 2026-08-24 but never committed, and the ADR it referenced was never
written. No code read or wrote the columns; `backend/database.update_position_price()`
had been a no-op stub since the FMP path was introduced. This ADR completes the
change.

## Decision

Persist IBKR's own valuation and render exactly that.

### Agent side

`reconcile_with_ibkr()` calls a new `_sync_ibkr_position_values()` after the
Case 3 share-count correction, writing four columns per position from the
`ib.portfolio()` `PortfolioItem`:

| Column | Source |
|---|---|
| `current_price` | `PortfolioItem.marketPrice` |
| `market_value` | `PortfolioItem.marketValue` |
| `unrealized_pnl` | `PortfolioItem.unrealizedPNL` |
| `ibkr_synced_at` | Wall clock at write, America/New_York |

`market_value` is **stored, not recomputed** as `shares x price`. IBKR is the
authority on both factors; recomputing would reintroduce the drift the column
exists to eliminate.

This deliberately does **not** use `ib.reqTickers()`, which blocks indefinitely
when the ushmds data farm is down — the hazard recorded in commit `83cf3a4`.
`portfolio()` reads the account update stream and is unaffected.

Positions arriving via the `positions()` fallback carry no `marketPrice` and are
**skipped**, not written with a derived price. A stale broker mark is
recoverable; a fabricated one is indistinguishable from a real one.

### Web side

`/portfolio` no longer reads any price from FMP. It uses `current_price` when
`ibkr_synced_at` is set, and otherwise falls back to **cost basis**, labelling
the row `price_source: COST_BASIS`. The FMP call survives only to resolve a
company name for tickers absent from the current watchlist snapshot; its `price`
field is explicitly ignored.

`get_positions()` no longer coerces a NULL `current_price` to `buy_price`. That
coercion made "broker marked it at cost" and "never synced" identical, which is
precisely the ambiguity being removed.

The dashboard renders `IBKR as of HH:MM` beneath each price, or
`Cost basis — not synced`.

### After hours

The agent only reconciles between 09:30 and 16:00 ET, so outside those hours the
displayed mark is the closing one and the timestamp says so. This is intended.
Substituting a fresher third-party quote to avoid an old timestamp would restore
the original defect in a less detectable form.

## Consequences

**Positive**

- Position values, market values and unrealized P&L match the brokerage exactly.
- Cash and positions in the portfolio total now share one vintage.
- Staleness is visible rather than disguised as a live price.
- One fewer FMP request path on the critical dashboard render.

**Negative / accepted**

- Prices do not move after 16:00 ET. This is a reporting change, not a
  behavioural one — no sell rule reads these columns.
- Until `migrations/add_ibkr_position_values.sql` is applied, every position
  renders at cost basis. The write degrades gracefully on PGRST204 and warns
  once per process rather than every cycle.
- A position opened between two reconcile cycles shows cost basis briefly.

**Not addressed.** The execution agent's own sell rules continue to use
`get_live_price()` (FMP, with an IBKR delayed-price fallback). The agent and the
dashboard can therefore still disagree intraday. Unifying them is a larger change
and is deliberately out of scope here.

## Follow-up

1. Apply `migrations/add_ibkr_position_values.sql` in the Supabase SQL editor.
2. Confirm the dashboard shows `IBKR as of HH:MM` after the next agent cycle.
