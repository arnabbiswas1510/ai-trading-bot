# Disable the breakout failure penalty — it scored every trade identically

**Date:** 2026-09-17
**Status:** Accepted
**Supersedes in part:** the Phase 2 learning loop introduced with `breakout_learnings`

## Context

On 2026-09-17 no trades were placed despite a bullish market gate, five free
position slots and $95,487 in cash. All twelve available triggers were rejected
at the open. Six of them — every BREAKOUT candidate — were rejected by
`SCORE_FLOOR`, and in each case the rejection was caused by a flat −20
`failure_penalty` applied on top of an otherwise passing score.

Five of those six had been graded **A** by the AI evaluator:

| Ticker | `final_score` | AI grade (rating) | penalty | `adjusted_score` | floor |
|---|---|---|---|---|---|
| DHT | 77 | A (75) | −20 | 57 | 60 ✗ |
| ECO | 74 | A (80) | −20 | 54 | 60 ✗ |
| TEN | 73 | A (75) | −20 | 53 | 60 ✗ |
| STNG | 69 | A (72) | −20 | 49 | 60 ✗ |
| SHIP | 53 | A (70) | −20 | 33 | 60 ✗ |
| DELL | 69 | D (40) | −20 | 49 | 60 ✗ |

The upstream scoring was verified correct and is **not** implicated:
`compute_final_score(68, 82, 75, 70, 100)` returns exactly the stored 77 for
DHT. The AI rating, the technical, liquidity, sentiment and RS components all
behaved as designed. The defect is confined to the penalty applied afterwards.

## Measurement

`_compute_failure_penalty` was replayed against all 16 rows of
`breakout_learnings`, scoring **each past trade using its own entry
parameters**. If the penalty carries signal, losers should score high and
winners low.

```
LPG   +6.47% WIN  -> 20      FIVE  -0.17% LOSS -> 20
ECO   +5.35% WIN  -> 20      PSX   -0.47% LOSS -> 20
DHT   +3.47% WIN  -> 20      MPC   -0.54% LOSS -> 20
NTRA  +3.25% WIN  -> 20      GNK   -1.15% LOSS -> 20
TNK   +2.95% WIN  -> 20      CHRD  -1.48% LOSS -> 20
ECO   +2.33% WIN  -> 20      CHRD  -1.62% LOSS -> 20
NTRA  +1.14% WIN  -> 20
LPG   +0.81% WIN  -> 20
GEO   +0.52% WIN  -> 20
CDNA  +0.16% WIN  -> 20

mean penalty on WINNERS: 20.0  (n=10)
mean penalty on LOSERS : 20.0  (n=6)
winners that would be blocked at max penalty: 10/10
```

Every trade in the sample scores exactly the cap. The penalty does not
discriminate weakly — it does not discriminate at all. **All ten winners,
including the three largest, would be blocked by it.**

DHT is the sharpest illustration: the candidate rejected on 2026-09-17 and
DHT's own earlier **+3.47% winning trade** receive an identical penalty of 20.

## Why it fails, mechanically

1. **The tolerances exceed the signal.** Measured winner/loser separation on the
   four matched parameters is `volume_surge` Δ0.15, `rs_score` Δ2.1,
   `technical_score` Δ4.5, `pivot_distance_pct` Δ0.33. The match tolerances are
   ±0.5, ±10, ±10 and ±2 respectively. Every tolerance is wider than the
   difference it is meant to detect, so a match is guaranteed, not informative.

2. **A loss blames all four parameters.** `_build_failed_params_snapshot` sets
   `failed = percent_return < 0` uniformly across every parameter. There is no
   attribution of which parameter actually failed, so one small loss contributes
   four matches.

3. **The cap concealed total saturation.** `penalty = min(20, weighted × 2)`.
   DHT's uncapped value was **78**; others reach **120**. A cap absorbing a 4–6×
   overshoot turns a score into a constant.

4. **It ratcheted.** Weight scales with the number of recent losses (3× inside
   30 days), not with similarity. Each new loss tightened the gate further,
   independent of candidate quality.

5. **It only ever applied to BREAKOUT.** All 16 learning rows are
   BREAKOUT-tagged and the `_meta.trigger_type` filter exempts everything else:
   196/196 PRE_BREAKOUT rows in `trigger_history` scored 0. The penalty silently
   applied to one path only.

The six "failures" driving it were −0.17%, −0.47%, −0.54%, −1.15%, −1.48% and
−1.62% — noise-level stop-outs from the 1.5% trailing stop, exiting on day 0–1.
The learning loop attributed to *entry* parameters what is really *exit*
behaviour.

## Decision

Ship `FAILURE_PENALTY_MAX_POINTS = 0` (default, env-overridable), disabling the
penalty. `adjusted_score` now equals `final_score` for every trigger, so the
score floors apply to the AI-evaluated score directly.

The function is **retained, not deleted**, and the cap is now read from the
constant rather than hard-coded, so re-enabling is a one-variable change once
there is data to retune it against.

### Why disabling does not need more trades first

Every other provisional parameter in this project is held pending more closed
trades. This one is different: the measurement is not a comparison of outcomes
that a larger sample might sharpen. The penalty assigns an **identical value to
every observation in the sample**. A statistic with zero variance across its
inputs carries no information regardless of how many more inputs arrive, so
there is nothing to lose by switching it off. Retuning the *tolerances*, by
contrast, does require outcome data — hence the register entry below.

## Consequences

- BREAKOUT candidates are judged on `final_score` against the floor of 60. On
  the 2026-09-17 set, DHT (77), ECO (74) and TEN (73) would have cleared it.
  ECO was independently blocked by cooling-off, which is unaffected.
- No change to PRE_BREAKOUT, which was never penalised in practice.
- `breakout_learnings` continues to be written. Nothing stops collecting data;
  only the scoring use of it is switched off.
- Risk accepted: removing a filter admits more candidates. This is deliberate —
  the filter was removing winners and losers at the same rate, so the trades it
  was suppressing have the same expected value as the ones it allowed.

## Follow-ups

- **Re-tune or retire permanently** once `trigger_history` carries forward
  outcomes. Only 16 of 233 rows currently do, and none are BREAKOUT, so the
  tolerances cannot yet be fitted to anything. Registered in
  `decisions/provisional_decisions.json` as `failure-penalty-tolerances`.
- **`exit_type` is mislabelled.** `_infer_exit_type` maps every trailing-stop
  exit to `"stop_loss"`, including LPG at +6.47%. It does not affect the penalty
  (winners carry `failed=False`) but it makes the table misleading to read.
  Left unchanged here to keep this change atomic.
- **The day-0/1 stop-out pattern** that generated all six "failures" is the
  deeper issue and is the subject of the exit-parameter review.
