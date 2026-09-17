# Price the lot from its own fills, and make cooling-off broker-aware

**Date:** 2026-09-10
**Status:** Accepted — **fully in effect as of 2026-09-15** — supersedes in part
`decisions/2026-09-09_buy-price-drift-guard.md`

> **Resolution (2026-09-15) — Decision 4 is now applied. The data half of this
> ADR is live.**
>
> An earlier erratum on this date recorded that
> `migrations/20260910_backfill_ntra_round_trips.sql` had never been run against
> production. It has now been applied, together with the `breakout_learnings`
> repair it implied. Live `trade_history` now holds three NTRA rows for the
> 8/26–9/10 sequence:
>
> | id | shares | buy | sell | net |
> |---|---|---|---|---|
> | 59 | 40 | 338.4300 | 320.8203 | −$706.66 |
> | 60 | 61 | 320.4900 | 317.8600 | −$162.84 |
> | 53 | 61 | 317.4295 | 321.0500 | **+$218.43** |
>
> The aggregate is preserved at −$651.07, exactly as this ADR specified; only the
> attribution changed. The phantom "worst recent loss" is gone and RT3 is booked
> as the winner it was.
>
> The `breakout_learnings` row for the 8/31 lot has been corrected in the same
> pass: `pnl_pct` −3.21 → **+1.14**, and `rs_score`, `volume_surge`,
> `technical_score` and `pivot_distance_pct` all flipped from `failed: true` to
> `failed: false`. This mattered more than it looks — the screener's Phase 2
> penalty was active (13 learning rows against a `LEARNING_MIN_ROWS` of 3) and
> the row sat inside the 30-day window, carrying the maximum **3× weight**. It
> was penalising the exact high-RS breakout profile the strategy is built to buy.
>
> The code half (Decisions 1–3) was already deployed and verified working in the
> 2026-09-13 image; that is unchanged.
## Context

On 2026-09-10 the bot appeared to close NTRA at a **−$649.65 loss**, well outside
the ~$300 per-trade loss the operator expects. The stored row said 61 shares
bought at `$331.70` on 8/31 and sold at `$321.05` on 9/10, having never once
closed above entry — a textbook failed breakout bled out over six days.

That reading was wrong in every particular.

IBKR's own TradeConfirm record shows NTRA was round-tripped **three times**, not
once:

| When (ET) | Side | Qty | Price |
|---|---|---|---|
| 8/26 11:32 | BUY | 40 | 338.4300 |
| 8/31 09:35 | SELL | 40 | 320.8203 |
| 8/31 09:46 | BUY | 61 | 320.4900 |
| 8/31 10:26 | SELL | 61 | 317.8600 |
| **8/31 10:32** | **BUY** | **61** | **317.4295** |
| 9/10 09:30 | SELL | 61 | 321.0500 |

The 10:32 buy matches the stored `buy_date` (14:32:06Z) to the second. **The lot
cost $317.4295, and the trade was a +$220.85 gross / +$218.43 net winner.**

Two independent defects combined to hide this.

### Defect 1 — `averageCost` is not a lot cost basis after a round trip

`reconcile_with_ibkr()` treated IBKR's `averageCost` as the authoritative,
commission-inclusive cost of the open position. After a same-symbol round trip
it is not. For NTRA, IBKR reported `averageCost = 331.70`, which is exactly:

```
(total buys + commissions − total sell proceeds) / remaining shares
= (52,450.29 + 3.00 − 32,219.59) / 61
= 331.70
```

— the two earlier realised losses folded into the surviving lot's basis.
`$331.70` sits **above NTRA's entire 8/31 trading range (~316–325.65)**, so no
fill could ever have occurred there. The drift guard adopted it anyway,
overwriting the true `317.43`.

The damage was not merely cosmetic. `buy_price` anchors the Prove-It band, the
give-back floor and the hard stop, so for six days every exit rule priced off a
basis 4.5% above reality, and the position was judged "never proven" when it had
in fact been green.

### Defect 2 — cooling-off could not see broker-side sells

The 10:26 sell and the 10:32 re-buy are six minutes apart. `COOLING_OFF_DAYS` is
7, so this should have been impossible. The gate queried `trade_history`, which
is written by the bot's *own* sell path; the 10:26 exit never landed there, so
the gate saw nothing and waved the re-entry through. Two of the three round
trips are absent from `trade_history` entirely, which is why their losses had
nowhere to go but onto the surviving row.

## Decision

**1. The cost basis of a position comes from the BOT fills that opened it.**
`lot_buy_basis_from_fills()` walks `ibkr_fills` backwards from the position's
`buy_date`, consuming only as many fills as the position holds, and returns
their share-weighted average. A ticker bought, sold and bought again therefore
prices off the **last** entry alone. It returns `None` — never a partial average
— when the fills cannot account for the full share count.

**2. `averageCost` is demoted to a guarded fallback.** It is used only when no
BOT fills cover the lot *and* `has_prior_round_trip()` finds no earlier sell for
that symbol. When the symbol has been round-tripped and fills are missing,
reconcile **refuses to touch `buy_price`** and logs why. Keeping a possibly-stale
recorded price beats adopting a provably fictitious one.

**3. Cooling-off consults `ibkr_fills` as well as `trade_history`.** The
real-time fill hook writes `ibkr_fills` the instant IBKR reports an execution, so
it sees exits regardless of whether the bot's own sell path ran. Applied in both
`run_market_open_buys()` and `rotate_positions.py`.

**4. The NTRA ledger is repaired.**
`migrations/20260910_backfill_ntra_round_trips.sql` splits the contaminated row into the
three real round trips. The aggregate is preserved **exactly** — this changes
which trade made or lost the money, not how much:

> **APPLIED IN PRODUCTION 2026-09-15.** The table below is the live ledger:
> rows id 59, id 60 and id 53 respectively. See the resolution note at the top
> of this file.

| Round trip | Shares | Entry → Exit | Net |
|---|---|---|---|
| 8/26 → 8/31 | 40 | 338.4300 → 320.8203 | −$706.66 |
| 8/31 → 8/31 | 61 | 320.4900 → 317.8600 | −$162.84 |
| 8/31 → 9/10 | 61 | 317.4295 → 321.0500 | **+$218.43** |
| | | **Sum** | **−$651.07** |

−$651.07 is precisely the `net_profit_loss` previously stored on the single row.

## Consequences

- **No exit-rule threshold was changed.** The NTRA "loss" was an accounting
  artefact; the Prove-It band never misfired. Tightening stops in response would
  have taxed winners to fix a bug that was not in the exit logic. The replay
  evidence is unambiguous that stop tightening costs more than it saves.
- **Backtests built on `trade_history` were contaminated.** Any per-trade figure
  involving a round-tripped symbol is suspect; see the erratum below.
- `averageCost` remains the basis source for the common case (no round trip,
  no fills recorded), so positions opened before `ibkr_fills` existed still
  reconcile.
- The churn itself — selling and re-buying the same name inside an hour — is now
  blocked at the gate, but the *reason* the 10:26 sell fired is not addressed
  here and is left open.

## Erratum — figures published in the 2026-09-09 drift-guard ADR

`decisions/2026-09-09_buy-price-drift-guard.md` was written on the belief that
NTRA's stored `317.43` was the corrupted value and IBKR's `331.70` the truth. It
is now proven to be the reverse. That ADR's reasoning, its worked NTRA example
and its claim that `averageCost` is "the authoritative, commission-inclusive cost
basis" **must not be cited**. Its `Status:` has been updated accordingly; the
body is left intact as the record of what was believed at the time.

## Open questions

- Why did the 10:26 exit fire ~40 minutes after entry? The re-entry is now
  blocked, but the exit that preceded it is unexplained.
- ~~How many other closed trades have a `buy_price` contaminated by the same
  mechanism?~~ **Answered 2026-09-15, unfavourably: it cannot be determined.**
  `ibkr_fills` has no row before 2026-09-08, so 35 of 43 closed trades — every
  trade in the all-time top-10 loss list — have no independent record of their
  opening fills to check against. The 8 trades since are all clean (max drift
  0.02%). Treat any pre-09-08 per-trade figure as unverifiable, including every
  number the exit-parameter replay derives from them.
