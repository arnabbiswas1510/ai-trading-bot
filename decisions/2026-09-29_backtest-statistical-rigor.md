# Statistical-rigor harness for the strategy backtester (fidelity item #6)

- **Date:** 2026-09-29
- **Status:** Accepted
- **Roadmap:** backtest-fidelity gap #6 (statistical rigor), the last of the six
  gaps that is doable offline. Builds on item #3
  (`decisions/2026-09-29_backtest-costs-slippage.md`), whose net-P&L figures this
  harness consumes.

## Context

The strategy backtester reports one number over one window. That is precisely how
a lucky window becomes a shipped "edge". AGENTS.md already mandates four questions
for every money decision — report n first; does shipped still win; is it carried
by one trade; are winners harmed — but nothing enforced them against the strategy
backtester. `research/entry_quality_review.py` applies that discipline to the
*trigger* population; the traded/backtested population had no equivalent.

The gap is not academic. The live edge is thin and clustered: every exit
threshold was tuned on 17→52 trades bunched into a few weeks. A backtest result
that is not checked for out-of-sample stability and outlier-dependence is exactly
the same failure mode one layer up.

## Decision

1. **New harness `research/strategy_validate.py`**, fully offline over the
   committed `benchmark_data` daily bars (free, deterministic, no FMP key). It
   prints evidence for the four questions and never returns a verdict by exit code
   (0 = ran, 1 = error) — a human reads it against the questions.

2. **Q1 — n first.** The closed-trade count is printed before any P&L, with an
   explicit `n < 30` noise warning.

3. **Q2 — beat a null.** A **permutation baseline**: the breakout selection is
   compared against many random-entry runs with identical slots, sizing, market
   filter, LIVE exits and costs, but entries drawn at random from names that
   merely have valid indicators that day. This required a minimal
   `rng` hook in `strategy_backtest.simulate()`: when an `rng` is passed, section
   D drops the breakout/volume/RS conditions and shuffles the eligible names. The
   harness reports the real strategy's percentile within the null distribution.

4. **Q3 — carried by one trade.** The real run is re-scored with its top-k richest
   full exits removed (drop-top-1..k), naming each dropped trade, so an
   outlier-dependent result is unmissable.

5. **Q4 — stable out of sample.** Closed trades are grouped by `buy_date` into
   contiguous walk-forward folds; per-fold n, net, expectancy and win-rate are
   printed, with a "profitable folds k/N" summary. Scale-outs are attached to
   their parent's fold so a fold's net is self-consistent.

## Consequences

- Running it on 2023-08→2025-08 (n=232) immediately produced an honest, useful
  result the single-number backtest hid: the breakout selection **does** beat the
  random null (96.7th percentile), **but** the entire net edge is a **single
  trade** (APP +$35,639) — dropping it turns +$31,797 into −$3,841 — and only
  **1 of 4** walk-forward folds is profitable. The apparent edge is one name in
  one regime. That is a Q3/Q4 failure the operator must see before sizing up, and
  is exactly what item #6 exists to surface.
- Any future proposed change to the strategy or its parameters can now be run
  through this harness and must clear all four questions on a date-grouped,
  out-of-sample basis before it justifies capital.
- The `rng` hook on `simulate()` is inert by default (`rng=None` → the real
  breakout path), so it changes nothing about the shipped backtest.

## Alternatives considered

- **Add sklearn / a real train-test-split framework.** Rejected: this repo has no
  ML dependency and a rank/permutation statistic does not need one — the same
  reasoning `entry_quality_review.py` used for its hand-rolled AUC.
- **Shuffle trade labels (as the entry review does).** Rejected here: the entry
  review tests whether a *feature* predicts an outcome, so label-shuffling is the
  right null. The backtester tests whether the *selection rule* beats chance, so
  the correct null is random *entry selection* through the identical machinery,
  not shuffled outcomes.
- **Have the harness emit a pass/fail verdict.** Rejected: the four questions are
  read together and weigh against each other (a strategy can beat the null yet be
  carried by one trade). A single exit code would flatten exactly the nuance the
  harness exists to expose.
