# Fix the self-defeating power-hold rule; move to 5 slots

**Date:** 2026-08-04
**Status:** Accepted
**Supersedes (in part):** `2026-08-04_backtest-noise-floor-and-slot-count.md` (slot count left open there)

## Context

Two open questions from the slot-count analysis:

1. Is 5 or 6 slots better than 4?
2. Can any structural change plausibly beat the market, rather than match it?

Benchmarks over the identical backtest window (2023-07-03 .. 2026-08-03):

| benchmark | CAGR | maxDD | $100K |
|---|---|---|---|
| SPY | +19.0% | 21.4% | $171K |
| QQQ | +22.9% | 25.4% | $189K |
| IWM | +16.1% | 29.2% | $158K |

The bot at 4 slots produced +27.7% (growth) / +23.3% (broad) **gross**. At a
realistic 0.30% round-trip execution cost that falls to +21.5% / +17.8%, and the
break-even cost against SPY is only ~0.5% round trip. So before this change the
bot could not be shown to beat buy-and-hold: its edge sat inside the established
±10pp noise floor.

## Decision 1 — MAX_POSITIONS 4 → 5

| slots | GROWTH CAGR / DD | BROAD CAGR / DD |
|---|---|---|
| 4 | +27.7% / 21.1% | +23.3% / 14.9% |
| 5 | +27.0% / 18.2% | +27.4% / 14.1% |
| 6 | +25.2% / 16.8% | +26.3% / 14.9% |

6 is rejected: it loses on CAGR on **both** universes, and its drawdown advantage
does not replicate (better on growth, worse on broad). 5 wins CAGR on both.

The CAGR and drawdown gaps are **not statistically significant** — a paired
moving-block bootstrap (2,000 resamples, 21-day blocks) puts every confidence
interval across zero (5-vs-4 drawdown: median −1.12pp, CI [−8.34, +4.59],
P=64%). An earlier claim that 5–6 slots cuts drawdown ~25% "at no CAGR cost"
overstated the confidence and is corrected here.

The actual justification is a far more stable statistic — **outlier dependence**:

| | top-10 trades as % of total P/L |
|---|---|
| GROWTH 4 slots | **109%** (without the top 10 the strategy loses money) |
| GROWTH 5 slots | 92% |
| BROAD 4 slots | 98% |
| BROAD 5 slots | 74% |

## Decision 2 — bypass the profit ladder while power-held

`TRAIL_PROFIT_TIERS` tightens the trail to 6.5% at +20% gain. That is *exactly*
the threshold which arms the O'Neil 8-week hold rule. The two rules cancelled:
instrumenting the backtest showed the rule armed on 9% (growth) / 6% (broad) of
trades and then **100% of armed positions still exited on the trailing stop.**
The rule was inert — which is why it "never fired" in earlier simulations.

Introduced `POWER_HOLD_TRAIL_PCT` (default 0.30). While power-held the ladder is
bypassed and the trail widened instead.

| ph trail | GROWTH CAGR / DD | BROAD CAGR / DD |
|---|---|---|
| off (shipped) | +27.0% / 18.2% | +27.4% / 14.1% |
| 0.12 | +34.6% / 18.2% | +30.2% / 14.1% |
| 0.22 | +49.7% / 17.6% | +38.3% / 14.1% |
| **0.30** | **+66.3% / 17.6%** | **+44.5% / 14.5%** |
| no trail | +76.6% / 17.6% | +48.8% / 15.3% |

The effect is large, monotonic in the trail width, and consistent across both
universes. Paired bootstrap for the no-trail variant: growth median +34.8pp
(P=94%), broad median +17.8pp (P=77%) — the strongest result found all session,
though still short of conventional significance on the broad universe.

**Risk is close to flat, but not perfectly unchanged.** The rule only arms
*after* a position is already +20% up within 3 weeks, so the base 10% stop
governs everything before that. Max drawdown is essentially unmoved (growth
18.2% → 17.6%, broad 14.1% → 14.5%). The single-trade tail does widen slightly,
because a power-held name can now peak at +20% and give back 30% of that peak:
worst trade moves from −10% to −12% (growth) / −15% (broad). Expectancy rises
far more than the tail does (+1.64% → +4.15% growth, +1.73% → +3.06% broad).

0.30 was chosen over removing the stop entirely (+76.6% / +48.8%) to retain a
disaster backstop, because the upside is concentrated in very few trades (top-5 =
49% / 62% of total P/L under the no-trail variant).

## Rejected

- **Entry-score selectivity** (take only the top 60th/80th percentile signals):
  worse on both universes (growth +20.9% / +11.6% vs +27.0%), and it starved
  capital (utilisation fell to 42% / 20%). The quality proxy has no predictive
  power; consistent with the earlier finding that breakout *timing* has ~zero
  edge versus random entry and all edge lies in stock *selection*.
- **Removing the profit ladder entirely** (flat 10%): better on growth (+30.8%),
  worse on broad (+23.5% vs +27.4%). Fails the two-universe test.
- **Ladder only above +50%**: same failure mode, larger (+33.1% vs +20.5%).
- **SPY>200MA regime filter**: consistently cuts drawdown (18.2→14.6, 14.1→11.0)
  but costs return on broad (+20.5% vs +27.4%). Not adopted for an
  aggressive-growth mandate; revisit if drawdown becomes the binding constraint.
- **Removing the EMA-21 exit**: still the only negative-P/L exit, and slightly
  better on both universes, but the effect (+1.4 to +2.2pp) stays far inside the
  noise floor. Unchanged, as before.

## Consequences

- `migrations/add_power_hold.sql` is now **required**, not optional. Without the
  column the flag cannot persist, so `is_power_hold_active()` falls back to a
  check bounded by `POWER_HOLD_TRIGGER_DAYS` and the rule silently expires at day
  21 instead of day 56 — discarding most of the benefit above.
- The monitor loop can now *widen* a trailing stop, which it previously never
  did. Notifications distinguish "widened (power hold)" from "tightened".
- Fixed a pre-existing display bug in the same block: the tighten message read
  `pos_stop_loss_pct` *after* it had been reassigned, printing the new value
  twice instead of old → new.

## Expected result

Shipped config (5 slots, power-hold trail 30%) against the benchmarks:

| | CAGR gross | CAGR @0.30% cost, +idle interest | maxDD | $100K → 3.1y |
|---|---|---|---|---|
| bot, growth universe | +66.3% | **+61.0%** | 18.6% | $434K |
| bot, broad universe | +44.5% | **+39.6%** | 16.3% | $280K |
| SPY | +19.0% | — | 21.4% | $171K |
| QQQ | +22.9% | — | 25.4% | $189K |
| IWM | +16.1% | — | 29.2% | $158K |

This is the first configuration whose margin over QQQ exceeds the ~10pp noise
floor on both universes. Treat the absolute figures as optimistic (see fidelity
limits) — the defensible claim is that the *ranking* is now clearly above
buy-and-hold, not that +61% is achievable.

## Fidelity limits

Unchanged and still material: no commissions or slippage, daily bars, no AI
scoring, growth universe carries survivorship/look-ahead bias, and 2023–2026 was
a strong bull market. Treat absolute CAGR as optimistic and rankings as the
trustworthy output. The headline improvement rests on ~20 power-held trades.
