# Price the lot from its own fills, and make cooling-off broker-aware

**Date:** 2026-09-10
**Status:** Accepted — with one decision **not yet in effect in production**
(see the erratum below) — supersedes in part
`decisions/2026-09-09_buy-price-drift-guard.md`

> **⚠️ Erratum (2026-09-15) — Decision 4 describes an intent, not the live state.
> `migrations/backfill_ntra_round_trips.sql` has NEVER been applied to the
> production Supabase.**
>
> Verified against live `trade_history` on 2026-09-15: NTRA still exists as **one
> contaminated composite row** — 61 shares, `buy_price = 331.70`, sold 9/10 at
> 321.05, `net_profit_loss = −651.07`. The three separate round trips this ADR
> says were restored (RT1 −$706.66, RT2 −$162.84, RT3 **+$218.43**) are **not**
> in the table. RT1 and RT2 remain absent entirely.
>
> What is still true: the *code* half of this ADR (Decisions 1–3) **is deployed
> and provably working.** The execution-agent image running in production
> (built 2026-09-13) contains `lot_buy_basis_from_fills()`,
> `has_prior_round_trip()` and the `ibkr_fills` cooling-off lookup, and every
> one of the 8 trades opened since `ibkr_fills` began recording reconciles to
> its own BOT fills to within 0.02%. The contamination mechanism is closed.
>
> What is still false until the migration runs:
>
> - The phantom **−$651.07** NTRA loss is still the single worst-looking recent
>   trade in the book, and it was really a **+$218.43 winner**.
> - `breakout_learnings` carries a matching `exit_type = stop_loss`, `pnl_pct =
>   −3.21` lesson for NTRA with `rs_score`, `volume_surge`, `technical_score`
>   and `pivot_distance_pct` all flagged `failed: true`. The closed-loop
>   learning of `decisions/2026-08-22_closed-loop-learning-from-trade-history.md`
>   is therefore penalising the entry criteria of a **winning** trade.
> - Every replay/backtest figure involving NTRA remains contaminated, including
>   the `--cliff` sweep that rejected a Phase 2 unarmed floor.
>
> **Open question 2 below is now answered, and the answer is worse than the
> question assumed.** It asked how many other closed trades carry a basis
> corrupted by the same mechanism. It is **unanswerable for 35 of the 43 closed
> trades**: `ibkr_fills` contains no row earlier than **2026-09-08**, so no
> independent record of the opening fills exists for anything bought before that
> date — which is every trade in the all-time top-10 loss list (NBIX, CDNA, HWM,
> RSI, FRO, DELL, APH, OII, FROG, PTGX). Those basis figures cannot be verified,
> only trusted. Only the 8 post-09-08 trades are provably clean.
>
> The body below is left unedited as the record of what was decided.

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
`migrations/backfill_ntra_round_trips.sql` splits the contaminated row into the
three real round trips. The aggregate is preserved **exactly** — this changes
which trade made or lost the money, not how much:

> **NOT YET APPLIED IN PRODUCTION as of 2026-09-15 — see the erratum at the top
> of this file. The table below is the intended end state, not the live ledger.**

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
