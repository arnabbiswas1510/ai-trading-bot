# Entry quality and the missing right tail — an open question, measured not answered

**Date:** 2026-09-18
**Status:** Accepted (as an open question; no behaviour changed)

## Context

An audit of all 52 closed trades established two things about whether the bot
lives up to "cut losers early, let winners run".

The loser half works. Split at the Prove-It Stop (2026-09-04):

| | before | after |
|---|---|---|
| n | 31 | 21 |
| worst loss | −6.25% | **−1.62%** |
| payoff (avg win ÷ avg loss) | 0.84 | **1.96** |
| win rate | 39% | 67% |
| expectancy per trade | −$208 | **+$183** |

The winner half does not. Across all 52 closed trades there is **not one
realised return above +7%**. The best ever is LPG at +6.47%. The right tail is
not thin — it is absent.

The obvious suspect was the exit. `TRAIL_PROFIT_TIERS` clamps the trail to 1.5%
once a position is +5% up, which is inside a growth stock's daily noise, and the
give-back recorded in `sell_reason` matches the trail width almost exactly on
every winner (LPG +7.91→+6.47, ECO +6.91→+5.35, MPC +6.40→+4.60, NTRA
+5.21→+3.25).

But widening the trail was measured on 2026-09-18 and **rejected**: against the
true live configuration it gains $4,308, of which ECO alone is 59%, and removing
the three largest trades makes it *lose* $1,014. It wins on outliers, not on a
repeatable edge. See `decisions/2026-09-18_scaleout-runon-bias-and-concentration.md`.

That left an unanswered question rather than a fix, and this ADR records it.

## The hypothesis that was wrong

The first framing was that the bot's *selection* was destroying the tail: 8/50
matured trigger rows reached +20% versus 1/50 taken trades, which looked
damning.

**That comparison was invalid and is withdrawn.** It compared the taken trades'
**realised** returns against the trigger population's **maximum** gain — two
different measurements. A trade that ran to +20% and was sold at +3% counts in
the numerator of one and not the other, which is precisely the effect under
investigation.

## What the like-for-like measurement says

`research/entry_quality_review.py`, first run 2026-09-18, comparing 20-day
maximum gain on both sides:

| | n | median max gain | ≥ +10% | ≥ +20% |
|---|---|---|---|---|
| taken | 10 | **+12.83%** | 6 (60%) | 3 |
| passed over | 40 | +3.76% | 14 (35%) | 5 |

AUC(taken > passed) = **0.623**.

*(AUC — "area under the ROC curve" — is the probability that a randomly chosen
taken trigger outranks a randomly chosen passed-over one. 0.50 is a coin flip;
above 0.50 means the ranking carries information.)*

**Selection is adding value, not destroying it.** The bot buys stocks that go
on to run — a median of +12.83% within 20 days — and then exits them below +7%.
The tail is being lost *after* entry, not at entry.

No individual feature explains which triggers run. The best held-out AUC across
ten features is 0.613, and the same search run on **shuffled labels** — pure
noise — finds a median best of 0.630. The best real feature does not beat
chance. There is no ranker to ship here.

## Why no verdict is being recorded

Two sample problems, either alone disqualifying:

1. Only **50** trigger rows have matured 20-day outcomes.
2. Those 50 rows come from **four scan dates** (2026-08-14 .. 2026-08-19). Row
   count is not sample size. Fifty rows drawn from four days are four
   observations of market regime wearing fifty hats: every row on a given day
   shares the same tape, the same breadth and the same sector rotation. A
   feature can look predictive purely because the day it fired on happened to
   run.

The tool prints both caveats and refuses to render a verdict below 150 rows.

A third finding is real but currently unusable: the book was at its 5-position
limit on **all 50** matured trigger dates, and 20 of those triggers reached
+10%. There is no slot-free comparison group at all, so the opportunity cost of
holding winners longer cannot yet be estimated — only bounded from above.

## Would holding winners longer have made more money? No.

This was asked directly and is now answered three independent ways. All three
agree, and the last of them tests the exit on **52 closed trades with real
5-minute bars** — not on the thin 50-row/4-date trigger sample.

### 1. Blindly holding 20 days loses money

Across all 50 matured triggers:

| measure | result |
|---|---|
| **peak** gain within 20 days | median **+4.05%** |
| return **at** day 20 (what you would keep) | median **−4.97%**, 21/50 positive |
| deepest drawdown endured | median **−10.21%** |

The peak is real; keeping it is not. These names spike and hand it back.

### 2. On the trades actually bought, the gain is two tanker stocks

Holding the 7 trigger-matched purchases for 20 days scores **+$10,970**, but
**FRO alone is 66%**; ex-FRO-and-LPG it is **+$578 across 7 trades**. FRO and
LPG are both tanker shipping in the same week — one sector's run. Helped 4,
harmed 3. Six of the seven breached the −3% Phase 1 stop, so "hold longer" here
means switching off the loss control that is the one thing demonstrably working.

**A correction made while computing this.** The first version of this figure was
**+$11,700**, and it was wrong because it assumed a stop-out forfeits the move
permanently. **LPG has four round trips**: the bot was stopped at −$62, re-entered
three times and booked +$882, +$160 and +$1,172. Correcting for re-entry
recapture drops the total to +$10,970 and cuts LPG's share from +$4,005 to
+$3,123. **Re-entry already performs part of the job "holding longer" is imagined
to do**, and any future counterfactual that ignores it will overstate its case.

### 3. The drawdown-conditional ladder — tested, and rejected

The one remaining idea was that runners are separable from faders *at the time*
by how far they have already fallen below entry (FRO/LPG/PSX never fell >3.1%;
every staller had been 8–13% underwater). If true, the +5% rung could be
loosened **only** for clean leaders — cutting losers early and letting winners
run as two rules rather than one compromise.

Implemented as `clean_dd_pct` / `clean_ladder_trail` and swept with `--clean
--runon-days 30` against the **full** live bot (Prove-It **and** scale-out):

| configuration | winners | NET | worst | harmed |
|---|---|---|---|---|
| **LIVE BASELINE (ProveIt + scale 33%@+4%)** | **+4,209** | **+10,762** | −1,269 | **8** |
| CLEAN dd≤2% → rung 3% | +2,458 | +9,011 | −1,269 | 11 |
| CLEAN dd≤3% → rung 3% | +2,017 | +8,570 | −1,269 | 11 |
| FLAT rung 3% (no condition) | +1,872 | +8,426 | −1,269 | 11 |
| CLEAN dd≤3% → rung 8% | +855 | +6,648 | −1,269 | 13 |
| FLAT rung 5% (no condition) | +238 | +6,611 | −1,269 | 13 |

**The shipped configuration wins outright.** Every loosening loses money and
harms more trades (8 → 11–13).

Two things make this conclusive rather than another thin result:

- **The condition is a flat widening in disguise.** The harness prints the
  qualifying rate: of trades reaching +5%, **14/15 (93%)** qualify at dd≤3% and
  12/15 (80%) at dd≤2%. Phase 1 already exits at −1%/−3%, so anything that
  survives to the rung is shallow-drawdown *by construction*. The conditional
  rows land within noise of the flat rows because they **are** the flat rows.
  The separation seen on 7 trades was an artifact of measuring drawdown over 20
  days *including bars after the bot had already sold* — information not
  available live.
- **Loosening hurts winners, not just losers.** The `winners` column falls from
  +4,209 to +1,872. A wider rung does not hold a runner longer on this book; it
  rides a fader further down. That is the same fact as median peak +4.05% versus
  median day-20 −4.97%, seen from the exit side.

The loss side is untouched in every row — worst single loss is **−$1,269** and
`>300` is **10** throughout. Loosening the winner side never endangered the
loser side; the two rules are properly decoupled. **The exit is not the
bottleneck. It is close to the best available setting of the knob it controls.**

## The RS columns: not broken, and not worth waiting for

`rs_percentile`, `rs_12w_return` and `rs_excess_return` read 0/245 on
`trigger_history`. Investigated 2026-09-18: **nothing is broken.**
migrations/20260917_add_rs_percentile.sql IS applied, and the screener has been
populating `daily_triggers` correctly ever since (13/13 rows, real values). The
archive runs at the START of each screener run on the PREVIOUS run's rows, and
the most recent archive (2026-09-17 23:18) carried the 09-16 cohort — created
before the migration existed, so genuinely NULL. The pipeline simply had not
cycled once.

Waiting for it to refill naturally would have cost ~2 months. It did not need to
be waited for: a 12-week return is a function of price history and can be
reconstructed exactly for any past date. `research/backfill_rs_percentile.py`
does so for all 245 rows, using the last close at or BEFORE each trigger date and
the close 60 trading days earlier — **no lookahead** — and importing
`compute_rs_excess` / `rank_percentiles` from `scoring.py` rather than
reimplementing them, so backfilled values cannot drift from live ones.

Validated before writing: all **13** live-written `daily_triggers` rows reproduce
**exactly** — 12-week return, excess return and percentile — against the
screener's own output. 245/245 rows backfilled, row count unchanged.

### Why the percentile was needed: `rs_score` is saturated

| rs_score | rows |
|---|---|
| **100** | **185 / 245 (76%)** |
| everything else | 60 |

`compute_rs_score` clips excess return at +10%. CDNA, which beat SPY by **112%**,
scores exactly the same as a stock that beat it by 10.1%. For three quarters of
the archive the feature is a **constant**, which is why it measured AUC 0.387 —
worse than a coin flip — in the review above. The percentile ranks the unclipped
excess within each day's cohort and cannot saturate.

### What the backfilled data says: still no signal

The 20-day outcome cannot be accelerated (a trigger must actually live 20 trading
days), but the 5-day outcome already covers **200 rows across 19 distinct
dates** — four times the rows and five times the dates of the 20-day sample.

Re-run with `--horizon 5d`, label `fwd_5d_pct >= +5%`:

| feature | AUC(all) | AUC(held-out) |
|---|---|---|
| rs_excess_return | **0.608** | 0.640 |
| rs_12w_return | 0.601 | 0.628 |
| rs_score | 0.584 | 0.668 |
| rs_percentile | 0.580 | 0.613 |
| *(best of all other features)* | 0.574 | — |
| **permutation baseline (shuffled labels)** | **0.593** | — |

**All four RS variants take the top four slots** — which random noise would not
usually do — but the best of them scores **0.608 against a noise floor of
0.593**. That is not a signal, and the tool reports it as such. The two horizons
also disagree (`rs_percentile` is 0.370 on the 20-day sample), which is the
signature of no real effect rather than of a weak one.

Unclipped `rs_excess_return` does edge out clipped `rs_score` on the full sample
(0.608 vs 0.584), the direction the percentile was built to exploit, but the
held-out split reverses it (0.640 vs 0.668). Treat as noise.

**Conclusion: RS is now collected, backfilled, validated and testable — and it
does not yet discriminate.** It is no longer a *missing data* excuse; it is a
measured null result on 19 dates, to be re-tested at 20-day maturity.

## Decision

1. Change nothing in the trading logic.
2. Register the question as `entry-quality-right-tail` in
   `decisions/provisional_decisions.json`, gated on **both** ≥150 matured rows
   and ≥20 distinct trigger dates, not before 2026-11-01. The register is wired
   to the monthly cron that opens a persistent GitHub issue, so it cannot be
   silently forgotten.
3. Ship `research/entry_quality_review.py` so the measurement is reproducible
   rather than re-derived.
4. Record explicitly that **if nothing clears the permutation baseline, the
   honest conclusion is that entry quality is not measurable from the features
   currently stored** — and the next move is to store better features, not to
   ship a weak ranker.

4. Keep `clean_dd_pct` in the harness as a **rejected** candidate with its
   qualifying-rate diagnostic, so the idea is not re-derived and re-shipped
   without the confound being re-checked. It is research code only and is
   disabled by default (`None`); no live constant was added.

## Consequences

- The "winners are clipped by the trail" story is now **retired**. The trail does
  cap give-back, but that money is not recoverable by loosening: the same
  loosening costs more on the trades that stall, and on this book the stalls are
  the majority. Three lines of evidence agree. **Every remaining lever is on the
  entry side — finding stocks that keep running past +5% — not on the exit.**
- Relative strength is no longer a missing-data excuse. The shadow columns were
  **backfilled on 2026-09-18** (245/245, validated exactly against live output),
  so "no signal" is now a measured result rather than an absence of data. It
  remains provisional on the 20-day horizon, which cannot be accelerated.
- `shipped_proveit()` in `research/exit_rule_replay.py` claimed in its docstring
  to model the live bot "exactly" while omitting scale-out. That false claim
  produced the withdrawn +$6,429 figure. The docstring now states the omission
  and why it biases loosening experiments upward.
