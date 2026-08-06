# The backtest "noise floor" was mostly a bootstrap bug

- **Date:** 2026-08-05
- **Status:** Accepted (analysis + methodology fix; no runtime parameter changed yet)
- **Supersedes parts of:** `2026-08-04_backtest-noise-floor-and-slot-count.md`
- **Challenges:** `2026-08-04_plateau-exit-capital-velocity.md`

## Context

`2026-08-04_backtest-noise-floor-and-slot-count.md` concluded the harness had a
noise floor of roughly +/-30pp of CAGR, and that **any difference below ~10pp is
indistinguishable from noise**. On that basis the EMA-exit variants (~3pp),
cooling-off period (~4pp) and slot count (~7pp) were classified as noise and
either left unchanged or changed without evidence.

That noise floor was largely an artifact of how the confidence interval was
computed.

## The bug

`boot.py` estimated the CI on the *difference between two configs* like this:

```python
diffs = sorted(cg([random.choice(pl_e) for _ in pl_e])      # arm E
             - cg([random.choice(pl_b) for _ in pl_b])      # arm B, INDEPENDENT draw
               for _ in range(2000))
```

Each arm is resampled with independent randomness, so the statistic has variance
`Var(E) + Var(B)` rather than `Var(E - B)`. The two arms trade largely the same
names, in the same market, over the same three years, so they are strongly
positively correlated and the inflation is severe.

It also resampled **iid over individual trades**, destroying serial correlation,
regime clustering, and the fact that concurrent positions share market shocks.

## The fix

Trade-level pairing is impossible: changing an exit rule changes exit dates, which
changes slot occupancy, which changes which later entries are taken. The arms
genuinely have different trade lists.

They do however share an identical calendar, so we pair on the time axis with a
**stationary block bootstrap** (Politis & Romano 1994):

- Draw circular blocks of trading days with Geometric(1/L) lengths.
- Draw the blocks **once per replicate and apply them to both arms** — same market
  periods, same randomness, genuine pairing.
- Blocks rather than iid trades preserve serial correlation and regime clustering.

Implemented in `research/boot_fixed.py` (`paired_block_bootstrap`), with the
original method kept as `unpaired_trade_bootstrap` for comparison.

## Results

Growth universe (79 names), 4 slots, 208 trades, 3.09 years, 2000 replicates,
L=60 trading days. The shipped-config baseline reproduces the previously published
**+27.7% CAGR exactly**, which validates the harness and the new committed dataset.

| comparison vs shipped | ORIGINAL 90% CI | width | CORRECTED 90% CI | width | P(better) |
|---|---|---|---|---|---|
| plateau (stale) exit OFF | `+12.4 [-25.9, +55.4]` | 81.2pp | `+13.2 [+3.0, +31.1]` | **28.1pp** | **99%** |
| EMA exit OFF | `+3.1 [-35.2, +41.1]` | 76.3pp | `+3.0 [-10.2, +15.2]` | **25.4pp** | 65% |
| minimiser ON @2% | `-21.8 [-55.6, +11.1]` | 66.7pp | `-23.0 [-47.3, -3.7]` | **43.6pp** | 2% |
| tight profit ladder | `-19.1 [-52.4, +9.8]` | 62.2pp | `-20.9 [-47.1, -1.4]` | **45.7pp** | 4% |
| base stop 7% | `-8.8 [-45.7, +26.2]` | 71.9pp | `-9.7 [-34.0, +11.7]` | **45.7pp** | 22% |
| base stop 12% | `+1.2 [-35.9, +40.0]` | 75.9pp | `+1.9 [-10.7, +16.7]` | **27.3pp** | 60% |
| cooling-off 1d | `-9.3 [-42.8, +26.3]` | 69.1pp | `-8.8 [-21.2, -0.8]` | **20.3pp** | 2% |
| 5 slots | `+4.8 [-26.5, +39.0]` | 65.5pp | `+5.1 [-4.6, +16.6]` | **21.2pp** | 78% |
| 6 slots | `+6.8 [-19.9, +35.2]` | 55.0pp | `+7.1 [-3.4, +19.6]` | **23.0pp** | 86% |

Intervals narrow by **1.5x to 4.4x**. Point estimates barely move, confirming the
bug was in the variance, not the mean. Verified stable across block lengths
L = 20 / 60 / 120 and three seeds; signs never flip.

## Conclusions that change

**1. The plateau / stale exit: a large growth-universe effect that FAILS to
replicate.** On the growth universe, removing it is **+13.2pp, 90% CI
[+3.0, +31.1], P(better) = 99%**, stable across all nine block-length x seed
combinations (P = 96-100%). That is the largest single effect the harness has
produced.

It does **not** hold on the broad universe:

| universe | plateau ON | plateau OFF | difference (L=60) | P(better) |
|---|---|---|---|---|
| growth (79 names, 208 trades) | +27.7% | +40.6% | **+13.2pp [+3.0, +31.1]** | **99%** |
| broad (256 names, 186 trades) | +23.2% | +25.3% | +2.4pp [-10.7, +15.7] | 63% |

The broad-universe interval comfortably includes zero at every block length.
Under the standing rule from `2026-08-04_backtest-noise-floor-and-slot-count.md`
— "conclusions must come from effects that are large, monotonic, or consistent
across both universes and all sub-periods" — this **fails the replication test**.

**No change is made to the plateau exit.** The growth universe is the
survivorship-biased one, so a large effect there that vanishes on the broader,
less-biased universe is exactly the signature of an artifact rather than an edge.
This does mean the exit remains adopted on a statistic (per-trade P/L) now known
to be the wrong one, so it is a genuine open question — but the evidence to remove
it is not there.

**2. Cooling-off 7d genuinely beats 1d.** Previously "~4pp -> Noise, kept,
harmless, but unproven". Corrected: **-8.8pp for 1d, CI [-21.2, -0.8]**, excludes
zero, P=2%. No longer unproven.

## Conclusions that hold

- **Minimiser OFF** — `-23.0pp [-47.3, -3.7]`, excludes zero. Correctly called.
- **Wide profit ladder** — tight is `-20.9pp [-47.1, -1.4]`, excludes zero.
- **EMA-21 exit: genuinely inconclusive** — `+3.0pp [-10.2, +15.2]`, P=65%. The
  original ADR's "coin flip" verdict was **right**, and the tighter interval does
  not rescue it. Left in place, still unproven. See the warning below.
- **Slot count** — 5 slots `+5.1pp` (P=78%), 6 slots `+7.1pp` (P=86%). Both lean
  positive but neither interval excludes zero. `MAX_POSITIONS=5` is reasonable and
  is better supported by the monotonic drawdown argument than by CAGR.
- **Base stop 10%** — 7% is `-9.7pp` (P=22%), 12% is `+1.9pp` (P=60%). Keep 10%.

## Warning: universe composition dominates marginal effects

An intermediate run of this same analysis was performed with 75 of the 80 growth
names, because an FMP daily request cap interrupted the fetch. Compared with the
full universe:

- headline CAGR moved **+27.7% -> +35.2%** (7.5pp from 5 of 80 names), and
- the EMA-exit comparison **reversed sign**, from `+3.0pp / P=65%` to
  `-8.5pp / P=16%`, which would have produced the opposite recommendation.

For marginal effects, *which names are in the universe* matters more than the
bootstrap method. Any comparison must pin the universe. This is the primary
motivation for the committed dataset — see
`2026-08-05_committed-benchmark-dataset.md`.

## The revised guidance

Retire the old rule ("ignore anything below 10pp") and replace it with:

> **Never compare absolute CAGRs.** A single config's CAGR still has a ~50pp 90%
> CI at 208 trades and that is irreducible. **Always compare configs with the
> paired block bootstrap on a pinned universe**, where a real effect resolves to
> roughly +/-10pp and ~5pp differences are detectable at ~85% confidence. Require
> the CI to exclude zero, not merely a favourable point estimate.

## Caveats

- Still fully in-sample over 2023-07..2026-08. No walk-forward split and no
  multiple-testing correction (Deflated Sharpe Ratio, or PBO via CSCV) despite a
  large config sweep. These remain the biggest methodological gaps.
- The growth universe still carries survivorship and look-ahead bias: it is the
  set of names passing the fundamental screen *today*, replayed backwards.
  Absolute returns should be read as optimistic.
- Daily bars only, so intraday exits are not faithfully modelled.
- `MOG.A` is absent from the dataset (vendor symbol-format issue), so the growth
  universe is 79 of 80 names.

## Follow-up

1. Close the sim-vs-live fidelity gaps that affect which trades exist at all:
   the SPY > SMA-200 market filter, the RS >= 50 gate, and the AI-evaluator
   fail-closed gate are all live buy conditions with no equivalent in the
   simulation.
2. Add walk-forward / out-of-sample validation.
3. Apply Deflated Sharpe or PBO/CSCV to discount for the size of the sweep.
4. Answer the original Day-2 kill-switch question with a conditional event study
   rather than a portfolio sweep — far better statistical power for a rule that
   fires on a small subset of trades.
5. Revisit the plateau exit once (1) is done — it is currently adopted on a
   statistic known to be wrong, but the evidence to remove it does not replicate.
