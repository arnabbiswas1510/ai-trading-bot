# Slot count, and establishing the backtest's noise floor

- **Date:** 2026-08-04
- **Status:** Accepted (analysis; no parameter changed)

## Context

The user asked two questions: were all backtests run at a portfolio size of 4,
and would a larger portfolio be better?

The first question exposed two defects in the harness and forced a
re-verification of the session's two largest decisions. The second turned out to
be unanswerable from CAGR alone, which led to measuring how much of any of this
is signal.

## Harness defects found and fixed

**1. Not all backtests modelled slots.** The per-trade harness
(`breakout_bt.py` / `ablation.py`) assumed unlimited capital. The decisions to
disable the Intraday Loss Minimiser and to widen the profit ladder — the two
largest changes of the session — were made on that harness. Only the later work
(plateau exit, cooling-off, stop width) used the 4-slot simulation.

**2. The minimiser was never implemented in the 4-slot harness.** It accepted a
`minimiser` config key and silently ignored it. This was caught because toggling
it produced byte-identical results.

**3. Slots were filled in arbitrary order.** The live buy loop sorts triggers by
`final_score` descending; the simulation took whatever appeared first. This
penalised small portfolios, which should take the *best* N candidates, not an
arbitrary N. A quality proxy (volume confirmation, pivot proximity, 12-week RS)
now ranks candidates before they compete for slots.

Defect 3 materially changed a previously reported result: an earlier claim that
2 slots collapsed the growth universe to +3.2% CAGR was an artifact. With ranking
it is +20.5%.

## Re-verification: both major decisions hold under the slot constraint

4-slot CAGR, growth universe, bootstrapped over 2,000 resamples of the trade
sequence:

| config | CAGR | 90% CI |
|---|---|---|
| **shipped** | **+27.7%** | +4.1 .. +59.3 |
| minimiser ON (rejected earlier) | +6.0% | -12.2 .. +29.2 |
| tight ladder (rejected earlier) | +7.9% | -8.5 .. +27.4 |

Both rejections are confirmed with the slot constraint modelled and the harness
bug fixed. These are large, well-separated effects.

## The noise floor — the most important result here

The same bootstrap shows the 90% CI on a single config's CAGR spans roughly
**±30 percentage points**. A paired comparison of two similar configs:

> EMA-exit off minus shipped: median **+2.1pp**, 90% CI **[-35.6, +40.4]**,
> P(better) = **54%** — a coin flip.

**Any difference below roughly 10pp of CAGR in this harness is not
distinguishable from noise.** That retroactively qualifies several earlier
results:

| Result | Gap | Verdict |
|---|---|---|
| Minimiser off | ~22pp | Real |
| Wide vs tight ladder | ~20pp | Real |
| Base stop 7% vs 10% | ~14pp | Probably real |
| Stop 10% vs 12% | ~10pp | Borderline |
| Cooling-off 1 vs 7 days | ~4pp | **Noise** — kept, harmless, but unproven |
| EMA exit variants | ~3pp | **Noise** — not acted on |
| Slot count, by CAGR | ~7pp | **Noise** |

Conclusions must come from effects that are large, monotonic, or consistent
across both universes and all sub-periods — not from picking a sweep maximum.

## Slot count

CAGR cannot separate slot counts (all within noise). Maximum drawdown can: it is
smooth and monotonic, which is the signature of a real effect.

Growth universe, final config, score-ranked entry:

| slots | CAGR | max DD | CAGR/DD | avg slots used | capital deployed |
|---|---|---|---|---|---|
| 2 | +20.5% | 32.9% | 0.62 | 1.42 | 71% |
| 3 | +25.4% | 26.9% | 0.94 | 2.06 | 69% |
| **4 (current)** | +27.7% | 21.1% | 1.31 | 2.65 | 66% |
| 5 | +27.0% | 18.2% | 1.49 | 3.24 | 65% |
| 6 | +25.2% | 16.8% | 1.50 | 3.72 | 62% |
| 7 | +26.3% | 16.4% | 1.61 | 4.26 | 61% |
| 8 | +25.2% | 16.2% | 1.56 | 4.70 | 59% |
| 10 | +24.4% | 15.4% | 1.59 | 5.48 | 55% |

**Increasing portfolio size does not increase returns — it reduces drawdown.**
CAGR is flat within noise from 3 to 10 slots, while drawdown falls monotonically
from 32.9% to 15.4%. Risk-adjusted return (CAGR/DD) improves up to about 7 slots
and then flattens.

The reason more slots do not raise returns: **only ~66% of capital is deployed at
4 slots, and adding slots slightly lowers that**. Each position is 1/N of
capital, so more slots means smaller positions, and the extra slots are often
empty. Trigger supply, not slot count, is the binding constraint.

**Recommendation: keep 4 for maximum return; move to 5-6 to cut drawdown roughly
a quarter at no measurable CAGR cost.** Left unchanged pending a user decision,
since "aggressive" plausibly means accepting the drawdown.

## Notable observations, not acted on

- **The EMA-21 exit is the only exit with negative average P/L** (-1.84% growth,
  -1.95% broad, on 17-34% of trades), while the new plateau exit is the best
  (+3.74% / +3.76%). Removing the EMA exit nevertheless failed the bootstrap
  (P=54%), so it is **not** changed. Worth revisiting with more data.
- **The 8-week power-hold rule never fires** in any simulation on either
  universe; toggling it changes nothing. It is currently inert.
- ~34% of capital sits idle. Raising deployment is a larger opportunity than any
  exit parameter, and points back at trigger supply and entry selection.

## Follow-up

1. Decide slot count (return vs drawdown trade-off above).
2. Treat 10pp as the minimum meaningful CAGR difference in this harness. Prefer
   drawdown and exit-mix P/L, which are far more stable statistics.
3. Investigate why power-hold never triggers.
4. Idle capital and entry selection remain the largest untapped levers.
