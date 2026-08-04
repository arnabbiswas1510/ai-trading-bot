# Plateau exit: optimise capital velocity, not per-trade expectancy

- **Date:** 2026-08-04
- **Status:** Accepted
- **Builds on:** `2026-08-04_tune-exits-on-breakout-population.md`

## Context

The previous ADR disabled the Intraday Loss Minimiser, which raised average hold
from ~10 to ~18 days. It also flagged widening `STOP_LOSS_PCT` from 7% to 10% as
the best-supported remaining lever.

The user declined the stop widening, with a specific rationale:

> My goal is to beat the market and ensure maximum profitability of the bot in
> the long run as a swing trader. I don't want to indefinitely hold stocks that
> have plateaued. I would rather rotate and buy them again when they are
> breaking out.

This exposed a measurement error running through all the previous exit analysis:
**every conclusion was based on per-trade expectancy, which implicitly assumes
unlimited capital.** The bot has 4 slots. When capital rather than ideas is the
binding constraint, the objective is return per unit of capital-*time*, and a
position that stops advancing has a real cost that per-trade expectancy cannot
see.

It also exposed a coverage gap. A position that plateaus *sideways* — above its
trailing stop and above EMA-21 — trips **no exit at all**:

| Exit | Reacts to | Fires on a sideways plateau? |
|---|---|---|
| Trailing stop | a drop from peak | No |
| MA (EMA-21) exit | a breakdown | No |
| Intraday Loss Minimiser | a pullback | No — now disabled anyway |
| Rank & Replace | a better trigger | Only if portfolio is full, Day 7+, **and verdict == PASS** |

Rank & Replace was the only rotation path and it is heavily gated. Disabling the
minimiser removed the one exit that reliably cleared stalled positions, so this
gap was actively widened by the previous commit.

## Method

Two measurements, because the first was insufficient:

1. **Per-trade** (previous methodology). Exit if no new high in N days.
2. **Portfolio-level**: a chronological 4-slot simulation. Signals compete for
   free slots; a slot occupied by a stalled position is unavailable to a fresh
   breakout. Returns compounded at 1/4 capital per trade to give CAGR.

Run over 3 years on both universes, and split into three sub-periods.

## Findings

### Per-trade analysis says plateau exits are harmful

Broad universe, 2,314 entries:

| Stale exit | expectancy | avg hold | expectancy/day |
|---|---|---|---|
| off | **+1.01%** | 18d | 0.056 |
| 10 days | +0.87% | 14d | 0.062 |
| 5 days | +0.59% | 9d | **0.066** |

Expectancy falls monotonically as the exit tightens — but return *per day* rises.
The two metrics disagree, and with 4 slots the second is the relevant one.

### Portfolio-level analysis reverses the conclusion

CAGR by period. GROWTH is the screener-passing universe — the population the bot
actually trades.

| Config | BROAD full | BROAD worst period | GROWTH full | GROWTH worst period |
|---|---|---|---|---|
| off | +10.1% | -8.9% | +15.9% | +5.2% |
| stale 5d | **+20.8%** | -3.8% | +15.5% | **-4.2%** |
| stale 8d | +7.5% | -7.0% | +19.0% | +2.3% |
| **stale 10d** | +16.1% | **-1.2%** | **+20.9%** | **+13.7%** |
| stale 12d | +6.5% | -2.9% | +23.1% | +10.1% |
| stale 15d | +10.4% | -12.1% | +17.2% | +10.1% |

**10 days beats no-exit in all three GROWTH sub-periods** (+24.0/+17.7/+13.7 vs
+17.7/+13.1/+5.2) and has the best worst-period result on both universes.

### 5 days was an overfit trap

5 days maximised BROAD (+20.8% vs +10.1%) and would have been the obvious pick
from a single universe. On GROWTH it is *worse than no plateau exit at all*
(+15.5% vs +15.9%) and turns a sub-period negative. BROAD results are also
non-monotonic (5d +20.8, 8d +7.5, 10d +16.1) — a noise signature.

GROWTH by contrast is a smooth plateau across 8-15 days (+19.0 / +20.9 / +23.1 /
+17.2), so 10 is not a knife edge. Chose 10 over the 12 that maximised the full
period, because 10 had the better worst period.

## Decision

Add a **Plateau Exit**: sell at EOD when a position has gone
`STALE_EXIT_DAYS` (10) **trading** days without a new high water mark.

- Gated to Day 7+ so it cannot fire during breakout consolidation.
- Suppressed by the 8-week power-hold rule — a leader that has run 20%+ is
  exactly what should be left alone.
- Counts trading days via the existing `trading_days_between()` so a long
  weekend cannot advance the stall counter.
- Reuses `hwm_date`, which already ratchets in the monitor loop. No migration.
- `STALE_EXIT_ENABLED=false` to disable.

`STOP_LOSS_PCT` stays at **7%** per the user's decision. This is coherent: a
tight stop plus fast rotation is a higher-turnover posture, and widening the stop
would have pushed holds the other way.

## Consequences

**Positive**

- Closes a real gap: sideways plateaus previously tripped no exit.
- Optimises the metric that matches the constraint (4 slots), not the one that
  assumes unlimited capital.
- Unlike Rank & Replace it is not gated on `verdict == PASS`, so FAIL-verdict
  positions — which lost their main exit when the minimiser was disabled — are
  reachable again.
- Partly offsets the hold-time increase from disabling the minimiser.

**Negative / accepted risks**

- Will sometimes sell a position that was merely resting before continuing. That
  is the direct cause of the per-trade expectancy drop (+1.01% -> +0.87%), and it
  is accepted because the freed slot earns more elsewhere. The design intent is
  that such names are simply re-bought on their next breakout.
- Higher turnover means more commissions, which the simulation ignores. At ~14d
  average hold this is small but not zero.
- The 4-slot simulation still ignores position sizing, AI scoring and the
  ranking of competing same-day triggers, so CAGR figures are directional.
- BROAD results are noisy enough that this rests mainly on GROWTH; GROWTH carries
  survivorship bias. The safeguard used was requiring improvement in *every*
  sub-period rather than only in aggregate.

## Follow-up

1. Rank & Replace still skips positions whose Day 3 verdict was FAIL
   (`verdict_rr != "PASS": continue`), which is backwards — FAIL positions are
   the best rotation candidates. The plateau exit reaches them, so this is no
   longer urgent, but the gate is still wrong.
2. Re-measure once real fills exist: the plateau exit should show up as shorter
   holds and more trades, and hold-time should be tracked as a health metric.
3. Entry *timing* remains the largest open problem (previous ADR, finding 4).
