# Reconcile `buy_price` against IBKR `averageCost`, and sum the dashboard headline from the rows

- **Date:** 2026-09-09
- **Status:** **Superseded in part** by
  [`2026-09-10_lot-basis-and-broker-aware-cooling-off.md`](2026-09-10_lot-basis-and-broker-aware-cooling-off.md)

> **⚠️ Erratum (2026-09-10) — the NTRA premise below is backwards. Do not cite
> the cost-basis reasoning or any NTRA figure on this page.**
>
> This ADR assumes the stored `buy_price = $317.43` was corrupt and IBKR's
> `averageCost = $331.70` was the truth. IBKR's own TradeConfirm record proves
> the reverse: **$317.4295 was the actual 8/31 10:32 execution price**, and
> `$331.70` was never a traded price at all — it is above NTRA's entire trading
> range that day. IBKR reported it because the symbol had been round-tripped
> twice already, and after a round trip `averageCost` returns
> `(total buys + commissions − total sell proceeds) / remaining shares`, which
> buries earlier realised losses in the surviving lot.
>
> Consequences for this page:
>
> - **"IBKR's `averageCost` is the authoritative, commission-inclusive cost
>   basis" is false** whenever the symbol has been round-tripped. Decision 2
>   below has been narrowed to a guarded fallback; the lot is now priced from its
>   own BOT fills.
> - **NTRA was not "genuinely −$230.28 (−1.1%)".** It was a **+$220.85 gross /
>   +$218.43 net winner**. Adopting `$331.70` is what later produced the phantom
>   −$649.65 closed loss.
> - The *dashboard* half of this ADR — summing the headline card from the same
>   rows it displays (Decision 1) — **still stands**. It was correct for reasons
>   independent of which cost basis is right.
>
> The body below is left unedited as the record of what was believed at the time.

## Context

The dashboard's *Unrealized profit* card read **+$1,005.10** while the five open
positions' P&L rows summed to only **+$127.85**. The two numbers were computed
from different cost bases in `backend/main.py`:

- **Card** (`unrealized_pnl`): `Σ(IBKR market_value − shares × stored buy_price)`.
- **Rows** (`pos['pnl']`): IBKR's own `unrealizedPNL`, computed against IBKR's
  **average cost**.

For four of the five holdings the two agreed within commissions. **NTRA** did
not: it was stored at `buy_price = $317.43` while IBKR's `averageCost` was
`$331.70` — a ~$14.27/share, ~$871 gap that was the entire discrepancy. NTRA was
genuinely **−$230.28 (−1.1%)** against IBKR's cost, but the card credited it as
**+$640 (+3.3%)** off the stale local price. The live gateway confirmed
`avgCost = 331.70` for `U12941651`.

The wrong `buy_price` did more than mis-report P&L. Every exit rule anchors on
`buy_price`: the Prove-It Phase-1 band, the Phase-2 give-back floor, the static
hard stop, and the `highest_unrealized_pct` / `closed_above_entry` latches. With
a `buy_price` ~4.5% too low, the bot believed NTRA was a proven +4.2% winner
(`closed_above_entry = true`, `highest_unrealized_pct = 4.22`) when its all-time
high ($330.81) had never actually cleared cost. It was pricing and protecting a
fiction.

Root cause of the drift: at buy time the agent records `buy_price =
trade.orderStatus.avgFillPrice`, read immediately after submission. That value
can be captured before all child fills settle, so it can lag IBKR's final
commission-inclusive average cost. Only NTRA drifted across 35 trades, so this
is rare — but silent and corrupting when it happens.

## Decision

1. **The dashboard headline sums the rows.** `unrealized_pnl` in
   `get_dashboard()` is now `sum(pos['pnl'] for pos in updated_positions)` — the
   exact per-row P&L already shown, which prefers IBKR's `unrealizedPNL` and only
   falls back to a cost-basis computation when there is no broker mark. The
   headline can no longer disagree with the sum of its parts.

2. **`buy_price` is reconciled against IBKR's `averageCost`.** In
   `reconcile_with_ibkr()` Case 3 (positions present in both IBKR and Supabase),
   when `abs(averageCost − buy_price) / averageCost > BUY_PRICE_DRIFT_TOLERANCE`
   (default 1%), reconcile:
   - overwrites `buy_price` with IBKR's `averageCost` (the authoritative,
     commission-inclusive basis — the same source Case 2 already trusts for
     manually-discovered positions);
   - resets `highest_unrealized_pct` to `max(0, (hwm_price / averageCost − 1))`
     and `closed_above_entry` to `hwm_price > averageCost`, conservatively, so
     both re-derive off the true basis (the EOD latch re-confirms
     `closed_above_entry`; the monitor loop re-ratchets the peak);
   - sends a Telegram alert.

   The hard stop and trail are recomputed from `buy_price` every monitor cycle,
   so they self-heal without any broker-order surgery.

3. **A new tunable `BUY_PRICE_DRIFT_TOLERANCE` (default `0.01`)** gates the
   guard. 1% is well outside normal commission/rounding noise — the other four
   holdings agreed within ~0.05%.

4. **`_ibkr_avg_cost()` helper** reads `averageCost` (PortfolioItem) or `avgCost`
   (Position, the multi-account `positions()` fallback), preferring the former
   and only consulting the latter when it is genuinely absent. Case 2's manual
   discovery path now uses it too, fixing a latent `AttributeError` on
   multi-account logins where `ib_map` is built from `positions()` objects.

The live NTRA row was corrected in Supabase immediately
(`buy_price = 331.70`, peak `0.0`, `closed_above_entry = false`) so the deployed
fix and the dashboard did not have to wait for a reconcile cycle.

## Consequences

- The headline P&L is now internally consistent and honest: it reflects IBKR's
  cost basis, the same basis orders fill against.
- A future fill mis-capture self-corrects within one reconcile cycle and pages a
  human, instead of silently distorting P&L and exit decisions indefinitely.
- The reset of `closed_above_entry` / `highest_unrealized_pct` on correction is
  conservative: a genuinely-proven position whose `buy_price` drifted up would
  briefly drop to Phase 1 (a *tighter* stop) until the EOD latch re-confirms it.
  Acceptable — the guard only fires on a >1% data error.
- Not addressed here: the order-time capture itself. Trusting `averageCost` at
  reconcile is the durable backstop; tightening `avgFillPrice` capture at buy
  time is a possible follow-up if drift recurs.

## Alternatives considered

- **Only fix the card (sum the rows), leave `buy_price` wrong.** Rejected: the
  headline would be right but the bot would still price and protect NTRA off a
  fiction. The exit-rule corruption was the more dangerous half.
- **Correct NTRA by hand and move on.** Rejected: it would recur silently. The
  reconcile guard makes the class of bug self-healing.
- **Re-place NTRA's resting IBKR stops manually to the corrected basis.**
  Unnecessary: the monitor loop recomputes `desired_hard` and the trail from
  `buy_price` + `highest_unrealized_pct` every cycle and re-syncs the broker
  orders, so correcting the DB fields is sufficient.
