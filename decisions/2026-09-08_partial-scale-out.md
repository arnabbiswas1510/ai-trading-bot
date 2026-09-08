# Partial Scale-Out — book a third of a winner at +4%

**Date:** 2026-09-08
**Status:** Accepted — PROVISIONAL (tuned on 33 closed trades; tracked for
revisit in `decisions/provisional_decisions.json`, id `scaleout-4pct-33pct`)

---

## Context

### The winner→loser pathway

A recurring, expensive failure mode is a position that runs green, then fades
back through entry before any stop fires — a trade that *was* a winner is booked
as a loss. GNK peaked at roughly +2.85% and closed −$221 after being +$442
intraday the day before; NTRA followed the same shape. The give-back is real
money that was, at one point, ours.

Every earlier attempt to fix this moved the **stop LEVEL** — a tighter trail, a
ratchet behind the peak. The 2026-09-07 `exit_rule_replay --ratchet` sweep
showed why that cannot work on this book: a stop level is *symmetric*. Tightening
it to save a fader by $X clips the genuine winners — the fat right tail the CAN
SLIM expectancy depends on — by more than $X. On a book carried by a few large
winners, any symmetric level change is net-negative.

### The insight

The fix must change **quantity, not level**. Selling *part* of the position at a
gain is asymmetric: the booked profit is a realised gain a later fade cannot
erase, and because the stop on the *remaining* shares is left untouched, the
winners are not clipped at all. The give-back is reduced on the shares we sold;
the upside is preserved on the shares we kept.

## Decision

When an open position's **peak** gain (`highest_unrealized_pct`) first reaches
**`SCALE_OUT_TRIGGER_PCT` = +4%**, sell **`SCALE_OUT_FRACTION` = 33%** of the
shares at market. The remaining ~67% keeps riding the **unchanged** Prove-It
stop. The rule fires **exactly once** per position (a `scaled_out` flag), is
suppressed for **power-held** leaders (an O'Neil 8-week leader is precisely what
we do not want to trim), and never runs on OCA-managed, already-armed, or
**Prove-It-triggered** positions. It is evaluated *after* the Prove-It firing
check, so a mandatory give-back exit always takes precedence: a winner that has
already faded to its floor exits in full rather than being trimmed and left for
another 15-minute cycle.

Freed capital is **not** immediately redeployed. It stays as reserve until a full
slot opens, then redeploys via the normal `available_cash / remaining_slots`
sizing (option a, chosen by the operator). Slots are counted by ticker existence
(`len(holdings)`), so a scaled position still occupies one of the
`MAX_POSITIONS` slots — scale-out never creates a sixth name.

### Why +4% / 33%

Chosen on the `exit_rule_replay --scale` sweep over **33 closed trades** (deltas
vs the shipped Prove-It stop):

| Config | Net vs shipped | Harmed | Faders helped |
|---|---|---|---|
| +5% / 33% | +$54 (tie) | — | fewer |
| **+4% / 33%** | **−$64 (free)** | **6 (lowest)** | **3** (LPG +$317, SGHC +$514, CDNA +$275) |
| +3% / 33% | negative | higher | more, but reintroduces the winner-clip tax |
| breakeven-remainder | negative | higher | reintroduces the clip |

+4%/33% is **net-free** (−$64 is deep inside noise), has the **lowest** harmed
count, and its benefit is **spread over 3 trades** rather than carried by one.
33% over 50% because the replay understates runner upside (its bars stop at each
trade's actual sell date), so over-booking has a hidden cost.

## Consequences / known limitations

- It rescues **only the faders that peak ≥ +4%**. GNK (+2.85%) and FRO (+3.91%)
  peak below the trigger and are **not** helped — those are an *entry-quality*
  problem (the screener admitting setups that never run), tracked separately and
  currently blocked on a data wall (`trigger_decisions` too sparse).
- A partial sell books its own `trade_history` row. Its P&L is realised
  immediately; the buy commission stays attributed to the final close so the
  total commission across the partial row + close row equals the real fees
  exactly (no double count).
- `reconcile_with_ibkr()` excludes the scale-out SLD fill from the final close's
  weighted-average price and commission via the new `scaled_out_at` timestamp
  (passed as the existing `since` filter). Without this, summing all SLD fills
  for the ticker would blend the scale-out price into the close. **Tier-1
  (`ibkr_fills`) and Tier-2 (`reqExecutions`) close-price archival both apply the
  `scaled_out_at` cutoff.** The Tier-3 Flex-query fallback
  (`fetch_trade_confirms_for_ticker`) does **not** filter by `scaled_out_at` and
  would blend the two SLD fills — but it only runs when Tiers 1 and 2 both return
  nothing, which for a scaled position is a remote edge (the closing fills are in
  the durable `ibkr_fills` table). Accepted as a known limitation.

### Order-execution safety

The partial sell is **cancel-first**, matching `execute_sell()`: the full-size
protective bracket is cancelled *before* the market order so the resting
trailing/hard legs can never fire alongside it and oversell the position into a
short. The residual exposure is a few seconds of an unprotected **long** during
the fill — a far more benign failure mode than an accidental short, and reachable
only by an implausible instantaneous move (the bracket sat ~7% away). An earlier
fill-first design was rejected in review precisely because leaving the full
bracket live during the sale created that oversell/short race.

Protection is **always** restored — a right-sized bracket on success, the
original full-size bracket on any abort (no-fill, exception). Sold quantity and
price are taken from the order's own `trade.fills`, never from an `ib.portfolio()`
delta, so a concurrent fill (a manual sell, a late bracket execution) can never be
mis-attributed to the scale-out. The remainder's bracket is placed **before** any
Supabase write, and the `scaled_out` latch is set **before** the `trade_history`
insert, so neither a DB failure nor a partial fill can leave the position reduced-
and-unprotected or able to re-fire.

## Provisional status

This is tuned on 33 trades and is logged in the Provisional Decision Register.
The monthly `decision_review.yml` job will open a tracking issue once ≥50 closed
trades exist (and not before 2026-11-20). The review must re-answer: is +4% still
the right trigger or is it CPAY/LPG-shaped; is the benefit still spread over ≥3
trades; has the winner-clip grown; would 25% or 50% now win.

## Alternatives rejected

- **Tighter trailing stop / peak ratchet** — symmetric, net-negative on a
  winner-carried book (the `--ratchet` sweep).
- **Immediate redeployment of freed cash** — would force a sixth concurrent bet
  or an early rotation; the operator chose reserve-until-slot-opens.
- **Scale at +3%** — helps more faders but reintroduces the winner-clip tax.
