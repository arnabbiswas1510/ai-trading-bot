# 2026-08-13 — Reject "confirmed-breakout-first" trigger ranking

## Status
Accepted (change **rejected**; production ranking unchanged)

## Context

On 2026-08-12 the bot bought **DELL**, a `PRE_BREAKOUT` (coiling) trigger scoring
82, in preference to **URGN**, a confirmed `BREAKOUT` scoring 78.

`run_market_open_buys()` (`execution_agent.py` ~L1593–1598) sorts `daily_triggers`
purely by `final_score` (falling back to `quality_score`, then `ai_rating`). There
is no precedence for confirmed breakouts over coils. The proposal was to make
`trigger_type` the primary sort key — all `BREAKOUT` candidates ranked above all
`PRE_BREAKOUT` candidates, score used only as a tiebreak within each class.

Nothing in the existing ADRs supported that change:

- `2026-08-04_tune-exits-on-breakout-population.md` finding #4 — breakout entry
  *timing* carries essentially no edge over random entry; priority is
  selection > exits > timing.
- `2026-08-09_thesis-stop.md` — tightening entry filters was previously rejected
  as curve-fitting.
- `2026-08-01_breakout-quality-floor-and-quota-waterfall.md` — the `+10`
  PRE_BREAKOUT score boost was already removed and coils already carry a *higher*
  floor (65 vs 60).
- `2026-08-09_trigger-history-and-decisions.md` — explicitly lists "Does
  PRE_BREAKOUT convert better than BREAKOUT?" as an **open, unanswered** question.

The `trigger_history` / `trigger_decisions` / `trigger_outcomes` audit tables were
built to answer this forward-looking, but they are not yet present on the queried
Supabase project and would in any case take months to accumulate signal. So the
question was answered instead by historical counterfactual replay.

## Method

New harness `research/rank_policy_bt.py`.

Because ranking only matters when candidates **compete for a scarce slot**,
per-trade expectancy is the wrong metric — a coil that drifts sideways for 20 days
consumes a slot that a breakout could have used. The replay therefore simulates the
real `MAX_POSITIONS` constraint and compares portfolio CAGR.

- Both screener detectors are replayed bar-by-bar over the committed
  `benchmark_data/` daily set (313 symbols, 2023-07-03 .. 2026-08-04, 775 days):
  - `BREAKOUT` — close > SMA-50, volume ≥ 1.5× 50-day avg, close ≥ 95% of the
    252-day high, RS ≥ 50.
  - `PRE_BREAKOUT` — within 8% of (but below) the 52-week high, close > SMA-50,
    RS ≥ 50, 3-day avg volume < 1.0× 50-day avg, ≥ 2 of last 3 closes rising.
- Scores use the **production** functions (`compute_quality_score`,
  `compute_pre_breakout_quality_score`, `compute_rs_score`,
  `compute_liquidity_score`) with a point-in-time SPY 12-week return for RS.
- Exits are the shipped ladder: 7% base trail, profit tiers 50/30/20 →
  5.0/6.0/6.5%, power-hold, EMA-21 exit from day 7.
- Cooling-off 7 days, entry at next open.
- Policies compared:
  - **A** `score_first` — current production.
  - **B** `confirmed_first` — BREAKOUT class ranked above PRE_BREAKOUT.
  - **C** `breakout_only` — coils never bought (upper-bound reference).
- Run across **both** universes (broad unselected, screener-passing), at 5 and 4
  slots, with score floors ON and OFF — 8 configurations.
- Significance via the paired stationary-block bootstrap (`research/boot_fixed.py`,
  2000 reps, 60-day blocks), which pairs the two arms on the calendar axis.

## Results

CAGR by policy (floors ON):

| universe | slots | A score-first | B confirmed-first | C breakout-only |
|---|---|---|---|---|
| broad | 5 | **+14.9%** | +8.7% | +7.9% |
| screener-passing | 5 | +10.9% | **+19.1%** | +19.2% |
| broad | 4 | **+15.3%** | +10.7% | +7.1% |
| screener-passing | 4 | +7.1% | +3.8% | +21.9% |

Paired bootstrap, B − A (median, 90% CI):

| universe | slots | floors | B − A | 90% CI | P(B>A) |
|---|---|---|---|---|---|
| broad | 5 | ON | −6.35pp | [−18.23, +3.24] | 14% |
| screener-passing | 5 | ON | +9.11pp | [−2.86, +21.41] | 89% |
| broad | 5 | OFF | −6.35pp | [−18.23, +3.24] | 14% |
| screener-passing | 5 | OFF | −0.39pp | [−9.72, +9.94] | 47% |
| broad | 4 | ON | −5.18pp | [−14.11, +5.50] | 20% |
| screener-passing | 4 | ON | −3.16pp | [−16.01, +7.23] | 32% |
| broad | 4 | OFF | −5.18pp | [−14.11, +5.50] | 20% |
| screener-passing | 4 | OFF | −1.25pp | [−15.84, +11.80] | 44% |

**Not a single configuration is statistically significant** — every 90% CI spans
zero. Worse, the point estimate **changes sign** between universes and flips again
when the score floors are removed, which is the classic overfitting signature the
sweep-vs-bootstrap rule in `2026-08-04` was written to catch.

Per-trigger-type expectancy under policy A is likewise a coin toss: broad universe
BREAKOUT +1.02% vs PRE_BREAKOUT +1.01% (n=142 / 87); screener-passing at 4 slots
floors-off BREAKOUT **−0.26%** vs PRE_BREAKOUT **+1.11%** — i.e. in the universe
the bot actually trades, coils were the *better* class, the opposite of the
proposal's premise.

## Decision

**Keep the score-first ranking. Do not add a `trigger_type` precedence key.**

The DELL-over-URGN outcome was the ranking working as designed, not a defect.
Score-first is also the more defensible policy on priors: it ranks on a blended
5-component quality estimate rather than on a single categorical label, and ADR
`2026-08-04` already showed entry timing (which is all `trigger_type` encodes)
carries no edge.

Policy C (breakout-only) shows the largest swings in both directions and is
therefore also rejected — dropping coils outright would cut trade count ~15% in
the traded universe with no reliable CAGR gain.

## Limitations (must accompany any citation of this result)

1. Historical `ai_score` and `sentiment_score` were never archived, so the
   `final_score` proxy holds both at a constant. Constants **cancel out of the
   within-day ordering**, so the A-vs-B ranking comparison is fair; only the
   absolute score-floor level is approximate. This is why floors-ON and floors-OFF
   are both reported.
2. Daily bars only; entry at the next open. Same convention as every other harness
   in `research/`.
3. `pass_names.txt` is a *today* snapshot of screener-passing names replayed over
   history, so it carries survivorship and look-ahead bias. Per the standing rule,
   a result is actionable only if it holds in **both** universes — this one holds
   in neither consistently.

## Follow-ups

- The forward-looking answer still requires `trigger_history` / `trigger_decisions`
  / `trigger_outcomes` to exist and populate. Those tables were **not found** on the
  queried Supabase project (PGRST205) despite being believed applied — reconcile
  which project the migrations landed on.
- Archive `ai_score` and `sentiment_score` per trigger so a future replay can use
  the true `final_score` instead of a proxy.

## Reproduce

```bash
cd research && python3 rank_policy_bt.py
```
