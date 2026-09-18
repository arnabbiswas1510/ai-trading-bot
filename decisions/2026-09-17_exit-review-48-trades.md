# Exit-parameter review on 48 closed trades: shipped Prove-It confirmed, cliff fix rejected, nothing changed

**Date:** 2026-09-17
**Status:** Accepted — no code change. Confirms the shipped exit stack *against tighter alternatives only*; see the 2026-09-18 erratum below.
**Supersedes:** the 30-trade baseline table in `AGENTS.md` (figures only, not reasoning).

> **Erratum, 2026-09-18.** Every sweep in this review ran on a harness that
> truncated price history at the realised exit, which makes the **loosening**
> direction unscoreable: a rule that holds longer is credited the live exit
> price and its upside is deleted. The conclusions here about *tighter* rules —
> which is all of them — are unaffected and stand. The review did not, and with
> that harness could not, test whether the shipped exits sell winners too early.
> That question was reopened the next day with a run-on window; see
> `decisions/2026-09-18_runon-window-winners-run.md`.

## Context

This is the **2026-09-20 exit-parameter review**, run three days early because the
sample was already sufficient and because the published baseline was known to be
stale.

Every exit threshold the bot ships was originally tuned on **17 closed trades**,
and every ADR behind them says so. The review schedule exists so those numbers get
corrected by evidence instead of ossifying. Two things made this particular review
overdue rather than routine:

1. The baseline table in `AGENTS.md` was explicitly marked **"do not cite"** after
   two `trade_history` repairs on 2026-09-15 (the NBIX re-pricing and the NTRA
   round-trip backfill) changed the replay's *input*.
2. `AGENTS.md` recorded an **owed** `--cliff` re-run. The unarmed-window fix had
   been rejected on 2026-09-10, but NTRA RT1 (−$706.66) — the single trade that
   motivated the hypothesis — was **absent from the sample that rejected it**.
   That rejection was therefore not safe to cite.

## What was measured

`research/exit_rule_replay.py` on **48 closed trades** (26 losers, 22 winners;
−$15,054 of realised losses among the losers). Every figure is a **delta against
the exits that actually happened**.

| Configuration | NET | winners | harmed | >300 |
|---|---|---|---|---|
| **Prove-It (SHIPPED)** | **+$10,673** | +$5,222 | 8 | 11 |
| + P2 arms at +1.5% peak | +$10,644 (−$29) | +$5,222 | 9 | 11 |
| + P2 arms at +1.0% peak | +$10,018 (−$655) | +$4,028 | 10 | 10 |
| + P2 arms at +0.5% peak | +$9,433 (−$1,240) | +$3,444 | 11 | 10 |
| Cliff fix (unarmed keeps P1 band) | +$9,280 (−$1,393) | +$3,531 | 9 | 12 |

Adding an always-on 7% or 10% GTC base trail underneath changed the shipped
result by **$0** — it never becomes the binding constraint.

## Findings, against the four questions a review must answer

**1. Is the sample big enough?** Yes. n=48 against a checkpoint requirement of
~22, and well past the ~30 threshold below which differences are noise.

**2. Does the shipped configuration still win?** Yes, and by a clear margin. It
ranks **first of ten** whole-stack configurations. The nearest challenger
(arming at +1.5%) is −$29 — deep inside noise — and buys that near-parity by
harming one additional trade. Nothing beats it.

**3. Is the result carried by one trade?** No, and this is the strongest part of
the result. Twenty-three trades improve against eight harmed, and **seven**
contribute more than $1,000 each: CDNA +$1,795, FR +$1,269, NBIX +$1,216,
RSI +$1,188, NBIX +$1,180, FRO +$1,030, HWM +$1,016. Removing the single largest
contributor still leaves **+$8,878**.

**4. Has anything started harming winners?** No. **All eight harmed trades were
already losers** — TTWO, CHRD, INCY, SGHC, APH, DXCM, GE and LPG all closed red in
reality. The aggregate winners column is **+$5,222**. The stack's cost falls
entirely on trades that lost anyway, making a few losses somewhat larger while
rescuing far more elsewhere. For a book whose owner is deliberately rigid about
cutting losers early, this is the desired shape.

## The owed re-run, discharged

The unarmed-window ("cliff") fix is **rejected again, now on the corrected
sample**. With NTRA RT1 present it scores **−$1,393** against shipped and raises
the count of trades losing more than $300 from **11 to 12**.

The result moved *against* the fix as the sample grew and as the motivating trade
was added — the opposite of what the hypothesis predicted. The 2026-09-10
rejection was directionally right; only its magnitude (−$1,691 on 39 trades) is
superseded. `decisions/2026-09-10_prove-it-unarmed-window-measured-not-closed.md`
has had its `Status:` updated accordingly; its body is unchanged, as an ADR body
must be.

## A harness caveat worth recording

`--proveit` is the wrong command for "does the shipped configuration still win?",
despite `AGENTS.md` previously pointing at it. Two reasons:

- Every row in `proveit_configs()` uses a **breakeven Phase 2 floor**
  (`floor+0.0`) and Phase 1 tiers of 1.0/1.5 or 1.0/2.0. The live configuration
  uses a **−1% floor** and a **3.0%** later band, so **none of the swept rows is
  the shipped rule**.
- It seeds the comparison with `shipped_config()` — the *pre-2026-09-04* stack —
  and the default `--top 25` then cuts that row from the table entirely, so the
  output contains no baseline at all.

`--cliff` places the true shipped Prove-It first via `shipped_proveit()`, which is
why it is now documented as the command that answers this question. This is a
documentation fix, not a harness bug: both modes compute correct deltas, but only
one of them contains the rule actually running in production.

## Decision

**Change nothing.** `PROVE_IT_P2_ARM_GAIN_PCT` stays at `0.02`, the unarmed
window stays open by design, and the Phase 1/Phase 2 shape is unchanged.

The three questions `AGENTS.md` left open are addressed as follows:

- **Is `PROVE_IT_P1_LATER_PCT = 3%` CPAY-shaped?** Partially answered. The shipped
  3% band beats every 1.5% and 2.0% variant in the sweep, but those rows also
  differ in their Phase 2 floor, so this is **not a clean one-variable
  comparison** and the question stays open. A dedicated single-variable sweep is
  the honest way to close it.
- **Does the Phase 2 floor need 1% of slack?** Still open — not isolated by this run.
- **Is `POWER_HOLD_GAIN_PCT = 10%` reachable?** Still dormant; nothing in the
  48-trade sample triggers it.

## Consequences

- The `AGENTS.md` baseline table is replaced with figures that can be cited.
- The last outstanding caveat on the unarmed-window decision is removed.
- The exit review remains the **only** review in this repo with no automated
  trigger. That gap is addressed separately in
  `decisions/2026-09-17_exit-review-automation.md`.
