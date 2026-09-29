# A leave-one-out jackknife makes "carried by one trade?" arithmetic, not a judgement call

- **Date:** 2026-09-28
- **Status:** Accepted — research tooling only. **No live parameter changed.**
- **Builds on:** `decisions/2026-09-18_runon-window-winners-run.md`,
  `decisions/2026-09-22_slot-opportunity-cost-harness.md`
- **Follow-up:** `decisions/provisional_decisions.json` → `ladder-width-runon`

## Context

Every attempt so far to loosen the profit-side exit — widen the `+5% → 1.5%`
profit ladder, or make the `+10%` power hold reachable — has produced a positive
headline that dissolved under scrutiny because it was **carried by a single
trade**. The 2026-09-18 run-on measurement found ladder 5% beat shipped by
+$5,685, but **ECO alone was +$3,794 of it (67%)**; ex-top-3 it *lost* $1,014.

AGENTS.md already makes "is any result carried by a single trade?" a **mandatory**
question every exit review must answer. Until now it was answered by reading the
per-trade delta table by eye. That is error-prone in exactly the case that
matters most: a position that exits in several round trips (NTRA closed in five)
spreads its contribution across rows, so no single *row* looks dominant even when
the *name* carries the entire edge.

## Decision

Add `--jackknife` to `research/exit_rule_replay.py`. For every challenger in the
loosening sweep (`runon_configs()`), against the shipped baseline, it reports:

- `full_edge` — the challenger's net advantage over shipped across all trades,
  which is exactly `challenger.net − baseline.net`;
- `drop1` — the edge after removing the single most-favorable trade
  (leave-one-out);
- `drop3` — the edge after removing the best three;
- `top1%` — the share of the edge carried by its single best *trade*;
- the single biggest **name**, with every leg of a multi-leg position summed, and
  its share of the edge.

A challenger is flagged **NOT SHIPPABLE** unless it clears the ~$500 noise bar,
stays positive after leave-one-out, keeps `drop3` above ~$500 (an edge that
vanishes when three trades are removed *is* those three trades), **and** no single
name is ≥50% of it.

### Why leave-one-out is closed-form here

`score()`'s `net` is the exact sum of every trade's dollar delta, so the edge over
the baseline is additive per trade:

```
edge_i    = delta(challenger, trade_i) − delta(baseline, trade_i)
full_edge = Σ edge_i = challenger.net − baseline.net
```

Dropping trade *j* simply removes `edge_j`, so the worst single trade to lose is
the one with the largest positive edge — no re-simulation loop is needed. To
guarantee the jackknife can never drift from `score()`, both now compute a
trade's delta through one shared helper, `_trade_delta()`; `score()` was
refactored onto it in the same change (identical logic, verified by the
unchanged headline totals).

## Evidence — first run, 67 closed trades

```
challenger configuration          full     -1     -3  top1%  helped hurt
RunOn: P2 ladder trail 5%       13,262  8,998    958     32     13   18
RunOn: SHIPPED + power hold ≥5%  11,832  4,839 -3,690     59     15   16
RunOn: P2 ladder trail 8%        9,862  5,598 -2,442     43     15   16
```

Best challenger, ladder 5%:

- full edge over baseline **+$13,262**
- after dropping best 1 trade **+$8,998**
- after dropping best 3 trades **+$958**
- single biggest **name: NTRA +$15,952 = 120% of the edge** — every other name
  combined *loses* money under the wider rung.

**Verdict: NOT SHIPPABLE.** The per-row `top1%` of 32% looks survivable, and is
exactly the trap this tool exists to close: NTRA's edge is split across three of
its five legs, so no single row is dominant while the name carries all of it.
Only the name-level aggregation exposes it.

This confirms the standing `ladder-width-runon` deferral on a larger sample (67
vs the 52 it was last measured on) and gives the scheduled December review a
sharper instrument than the human eye.

## Scope and non-goals

- **No live parameter changed.** `TRAIL_PROFIT_TIERS` is still `+5% → 1.5%` and
  `POWER_HOLD_GAIN_PCT` is still 10.0.
- The jackknife is the **pass/fail** gate on fragility; `--slotcost` remains the
  **magnitude** gate (does the edge survive the 5-slot opportunity cost). A
  candidate must clear both, and then be promoted shadow-first, before any live
  change.
- `--jackknife` implies a run-on window (default 30 days), like `--runon` and
  `--slotcost`, and shares their opt-in gating: with all three off, every other
  sweep replays byte-identically.

## Consequence

- New flag `--jackknife`, new functions `jackknife()` / `report_jackknife()`, and
  a shared `_trade_delta()` in `research/exit_rule_replay.py`.
- Guarded by `tests/test_jackknife.py` (additivity, leave-one-out, name-level
  concentration, and the NOT-SHIPPABLE verdict path).
- `ladder-width-runon` review command and questions updated to run and read the
  jackknife; its history records this run.
- AGENTS.md exit-review command list gains the `--jackknife` line.
