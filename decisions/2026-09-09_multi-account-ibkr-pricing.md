# Multi-account IBKR pricing: reqPnLSingle fallback + strict target-account scoping

- **Date:** 2026-09-09
- **Status:** Accepted

## Context

Every open position was showing `FMP estimate — not broker` on the dashboard,
and the execution agent logged `IBKR mark unavailable — FMP fallback` for all
five holdings on every 15-minute cycle. Exit rules (trailing stop, Prove-It, the
give-back floor) were therefore being evaluated on FMP prices, not IBKR marks —
the exact decision-vs-fill mismatch the IBKR-first design
(`2026-09-04_ibkr-first-live-pricing.md`) exists to prevent.

Diagnosis on the live box found the cause, and it was **not** a data-farm or
subscription problem:

- `ib.managedAccounts()` returned **two** accounts — the live trading account
  `U12941651` and a second, near-empty account `U13359115` (NetLiq $19.36) that
  had been linked to the same login.
- `ib_insync.connectAsync` starts the legacy `reqAccountUpdates` stream — the one
  that emits `updatePortfolio` and so populates `ib.portfolio()` with
  `marketPrice` — **only when exactly one account is managed**. With two
  accounts it calls `reqAccountUpdatesMulti` instead, which delivers account
  *values* but no per-position marks.
- Result: `ib.portfolio()` was permanently empty. `build_ibkr_price_map()` read
  it directly and returned `{}`, so `get_position_price()` fell to FMP for every
  ticker and `reconcile_with_ibkr()` wrote no `current_price` / `market_value` /
  `ibkr_synced_at`, leaving the dashboard on its labelled FMP fallback.
- An explicit `ib.reqAccountUpdates(U12941651)` was tried and **timed out**
  waiting for `accountDownloadEnd`: IBKR does not serve the single-account
  portfolio subscription for a multi-account login. So "just call
  reqAccountUpdates" is not a fix.

Even the `account_balances.ibkr_positions_value` figure was FMP-derived, because
it is computed via the same `get_position_price()` path — only `own_cash` (from
`accountValues()`, which *is* served per-account) was a true broker number.

## Decision

Price the target account's positions from **`reqPnLSingle`**, which IBKR computes
server-side per account (like `NetLiquidation`), needs no market-data line, and
works under a multi-account login. `marketPrice` is derived as `value / shares`.

`build_ibkr_price_map(ib)` now resolves marks in this order, always scoped to the
one configured account (`IBKR_ACCOUNT` / `get_ibkr_account`):

1. **Fast path** — `ib.portfolio()` filtered to the target account. This still
   works, and is cheapest, for single-account logins.
2. **Fallback** — `reqPnLSingle` per target-account position when the fast path
   yields no usable mark (the multi-account case). Subscriptions are read once
   and cancelled.

Results are TTL-cached (`_IBKR_PRICE_MAP_CACHE`, 15 s) so the several builders
that run each cycle (monitor, balance sync, reconcile, ad-hoc exit checks) make
one broker round-trip rather than many.

**The second account is ignored everywhere, by requirement.** Holdings are now
read through `ibkr_target_positions(ib)` — `ib.positions()` filtered to the
target account — which, unlike `ib.portfolio()`, IBKR *does* serve for
multi-account logins. This replaced the raw `ib.portfolio()` reads in:

- `execute_sell()` sell confirmation — previously an empty `portfolio()` would
  read as "position gone" and could delete a Supabase row after a **rejected**
  sell. Now it confirms against `ib.positions()` for the target account.
- `execute_scale_out()` share-count check (`_ibkr_qty`).
- `reconcile_with_ibkr()` the post-close double-check and the valuation write
  (now fed from `build_ibkr_price_map`).

## Consequences

- Live positions are priced from real IBKR marks again on both the agent and the
  dashboard; the `FMP estimate — not broker` label clears once the fix is
  deployed and a reconcile cycle runs during market hours.
- The bot is now robust to extra accounts appearing under the login: it prices
  and trades strictly the configured account and never reads, prices, or reports
  on any other.
- `reqPnLSingle` adds a bounded per-cycle round-trip (subscribe → read → cancel)
  only when the portfolio fast path is unavailable; the TTL cache keeps it to one
  build per cycle.
- Removing the second account from the login would restore the `ib.portfolio()`
  fast path automatically — but is no longer required for correctness.

## Alternatives rejected

- **`ib.reqAccountUpdates(target)` on connect** — times out under a multi-account
  login; the subscription is not served.
- **`reqTickers` / `reqMktData`** — needs a market-data line and can block when
  the data farm is down; this is precisely what the codebase already avoids.
- **Requiring the operator to unlink the second account** — brittle operationally
  and leaves the bot silently mispricing the moment any account is added.
