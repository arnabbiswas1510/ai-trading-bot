# Anchor the account total to IBKR NetLiquidation, not own_cash + positions

- **Date:** 2026-09-28
- **Status:** Accepted
- **Related:** [decisions/2026-09-03_ibkr-sourced-position-values.md](2026-09-03_ibkr-sourced-position-values.md), [decisions/2026-09-04_ibkr-first-live-pricing.md](2026-09-04_ibkr-first-live-pricing.md), [decisions/2026-07-22_margin-safe-cash-functions.md](2026-07-22_margin-safe-cash-functions.md)

## Context

The operator reported that IBKR showed an account value (NetLiquidation) of
**$94,712.78** while the bot's dashboard reported a total of **$111,478.60** —
built from a cash line of **$93,259.06** (which matched IBKR's displayed *Settled
Cash* exactly) plus an invested amount of **$18,219.54**. The overstatement was
**$16,765.82**.

Investigation of live Supabase data:

- `account_balances` (2026-09-28) stored `ibkr_cash_balance = 93,259.06`,
  `ibkr_positions_value = 18,219.54`, `ibkr_total_value = 111,478.60`,
  `ibkr_own_cash = 93,259.06`, `ibkr_margin_loan = 0`.
- `portfolio_positions` held exactly **one real, IBKR-synced position**: CDNA,
  283 shares, market value $18,219.54, `ibkr_synced_at` earlier that day. The
  position was correct — not phantom, not stale.

So the position side was right. The defect was on the total: `reconcile_with_ibkr()`
computed the account total by **reconstruction** —

```python
cash_balance = own_cash            # = get_own_cash() = IBKR TotalCashValue
net_liq      = cash_balance + pos_value
```

`get_own_cash()` reads IBKR's `TotalCashValue`. At the time of the snapshot that
tag read $93,259.06 — the same as *Settled Cash* — because the CDNA purchase had
**not yet settled** (US equities settle T+1). During the settlement window the
cash committed to the purchase still appears in `TotalCashValue`, while the
purchased shares *already* carry a market value in `ib.portfolio()`. Adding the
two therefore counts the ~$16.8k tied up in CDNA **twice** — once as cash, once
as the position.

IBKR's own `NetLiquidation` does not have this problem: it nets the pending
settlement, which is why the broker showed $94,712.78 and the reconstruction did
not.

## Decision

Stop reconstructing the account total. `reconcile_with_ibkr()` now:

1. Reads IBKR's authoritative `NetLiquidation` tag via the existing
   `get_net_liquidation()` and stores it as `ibkr_total_value`.
2. Derives the displayed cash line as `ibkr_cash_balance = net_liq − pos_value`,
   so **cash + positions == NetLiquidation exactly**, regardless of settlement
   state.
3. Preserves the raw `TotalCashValue` untouched in `ibkr_own_cash` for margin
   diagnostics (the margin-loan detection in `get_own_cash()`/`buying.py` is
   unchanged and still reads `TotalCashValue` live).

Guards:

- If the derived cash would be **negative** (only possible if `pos_value` is
  inflated by a position valued off stale cost basis because IBKR has no mark for
  it — which should have been reconciled away), the authoritative total is kept
  but the cash line falls back to raw `own_cash`, and a warning is logged.
- If the `NetLiquidation` tag is momentarily **unavailable** (returns 0), the
  sync falls back to the old reconstruction (`own_cash + pos_value`) so the
  balance write never blocks. This may transiently overstate during an unsettled
  purchase, but a missing balance write is worse — the dashboard and exit sizing
  both depend on it.

The dashboard (`backend/main.py get_portfolio()`) is unchanged: it already reads
`ibkr_cash_balance` as the cash line and adds per-position IBKR market values.
Because the stored cash is now `net_liq − pos_value` and the positions are the
same IBKR marks, the dashboard's `cash + positions` now equals IBKR's
NetLiquidation.

## Consequences

- The dashboard total will match IBKR to the cent on the next reconcile cycle
  after deploy (reconcile runs every ~15 min during market hours). No manual data
  fix is needed; the buggy row is overwritten.
- `ibkr_cash_balance` changes meaning slightly: it is now the **cash component of
  net liquidation** (`net_liq − positions`), not the raw `TotalCashValue`. During
  an unsettled purchase this is *lower* than Settled Cash — correctly so, because
  the money is committed to the position. The raw figure remains available in
  `ibkr_own_cash`.
- No consumer relied on `ibkr_cash_balance == TotalCashValue`: the only reader is
  the dashboard, and all margin-safety gating reads IBKR live via `get_own_cash()`,
  not the stored column.
- No schema change (all columns already exist), no migration.

## Tests

`tests/test_reconcile.py`:
- `test_case4_total_anchors_to_ibkr_net_liquidation` reproduces the CDNA incident
  (own_cash $93,259.06, positions $18,219.54, NetLiquidation $94,712.78) and
  asserts the stored total is $94,712.78 and cash is $76,493.24, with
  cash + positions == total.
- `test_case4_falls_back_to_reconstruction_without_net_liq_tag` asserts the
  fallback path when `get_net_liquidation()` returns 0.
