# Exit-parameter review on 52 closed trades: shipped stack holds, and the `--proveit` sweep is repaired

- **Date:** 2026-09-18
- **Status:** Accepted
- **Supersedes in part:** `decisions/2026-09-17_exit-review-48-trades.md` (the
  48-trade numbers are not wrong, they are simply an earlier sample; the harness
  defect that ADR *identified* is now *fixed*)

## Context

The scheduled exit-parameter review in `AGENTS.md` was due **2026-09-20**. It was
run two days early for a reason the operator pointed out: **the 20th falls on a
Sunday**, so a Sunday run would replay exactly the same sample as the preceding
Friday. The date is a prompt, not a deadline.

Two prior corrections also needed clearing. A figure of **−$6,547.59** (30 closed
trades) was quoted in conversation on 2026-09-18 despite `AGENTS.md` already
marking it stale. The live realised total, queried the same day, is
**−$2,519.28 over 52 closed trades**.

## The review, n = 52 (26 losers, 26 winners)

All figures are deltas against the exits that **actually happened**. Realised
losses in the sample total **−$15,054**.

| Configuration | NET | winners | harmed | >300 |
|---|---|---|---|---|
| P2 arms at +3.0% peak | +$10,871 (+$202) | +$5,222 | **6** | 11 |
| **LIVE BASELINE (Prove-It + scale-out 33%@+4%)** | **+$10,762** | +$4,209 | 8 | 10 |
| **Prove-It SHIPPED (stop only)** | **+$10,669** | +$5,222 | 8 | 11 |
| P2 arms at +1.5% peak | +$10,640 (−$29) | +$5,222 | 9 | 11 |
| P2 arms at +1.0% peak | +$10,014 (−$655) | +$4,028 | 10 | 10 |
| RETIRED pre-Prove-It ruleset | +$1,643 (−$9,026) | $0 | 7 | 12 |

**The shipped stack holds. No parameter was changed.**

1. **Sample grew meaningfully:** 48 → 52 closed trades, now evenly split 26/26.
2. **Nothing displaces shipped.** The only row above it is +$202 — far inside the
   ~$500 bar this review uses.
3. **Not carried by one trade.** Largest contributor CDNA **+$1,795 is 17%** of
   net; net excluding it is still **+$8,874**; seven trades contribute over
   $1,000 each. 24 helped against 8 harmed.
4. **Zero winners harmed.** All 8 harmed trades (TTWO, CHRD, INCY, SGHC, APH,
   DXCM, GE, LPG) were **already losers**. This is the column that matters most
   and it is clean.

### One candidate to watch, deliberately not shipped

Arming Phase 2 at a **+3.0%** peak instead of +2.0% scored **+$202** and harmed
**6** trades instead of 8 — a better result on *both* axes. It is still below the
noise bar, so `PROVE_IT_P2_ARM_GAIN_PCT` stays at `0.02`. What makes it worth
recording is the **sign**: +1.0% and +1.5% both score *worse* than +2.0%, and
+3.0% scores better, so the arming gain is not at a flat optimum. Re-check at the
next review; if the direction persists on a larger sample it becomes real.

## The more important finding: the sweep could not answer its own question

The review's main product is not a number, it is a repair. `--proveit` had
**three** defects that together made it incapable of answering its own headline
question, *"does the shipped configuration still win?"*

1. **The baseline row was mislabelled.** `shipped_config()` produced a row
   labelled `SHIPPED` that actually modelled the **retired** pre-2026-09-04
   ruleset — kill-switch, Early Dollar Stop and Thesis Stop — and its docstring
   still referenced `EFFECTIVE_POSITION_SLOTS`, **deleted on 2026-09-04**. Every
   "vs shipped" comparison in that sweep was against a ruleset that has not run
   for two weeks.
2. **The live parameters were absent from the grid.** Phase 1 later-tiers were
   swept at 1.5% and 2.0% but **not the live 3.0%**; Phase 2 floors were swept at
   0.0% and +0.5% but **not the live −1.0%**. The live configuration was not in
   its own sweep.
3. **The default `--top 25` hid the baselines.** Both `SHIPPED` and `PHASE 1
   ONLY` ranked below the cut and were silently omitted from the printed table.

`AGENTS.md` had documented (1) and (3) as a *workaround* — "use `--cliff`, not
`--proveit`". A workaround around a mislabelled baseline is exactly the condition
that produced the withdrawn **+$6,429** figure on 2026-09-18: a stale baseline
that reads perfectly and silently flatters everything measured against it.

### The fix

- `shipped_config()` → **`retired_pre_proveit_config()`**, label changed to
  `RETIRED pre-ProveIt (...)`, docstring rewritten to state plainly that it is
  not current and must not be used as a baseline. Six call sites updated.
- `proveit_configs()` now scores **`shipped_proveit()`** and **`live_baseline()`**
  alongside it.
- The grid gained the live Phase 1 shape **`1.0%/d0 then 3.0%`** and `flat 3.0%`,
  and the Phase 2 floor sweep gained **`−1.0`**. The arming sweep is now held
  against the **live** `1.0/3.0` shape rather than `1.0/1.5`.
- Grid is now 38 rows; `--top 80` is required to see all of it.

The `--cliff` workaround in `AGENTS.md` is removed: `--proveit --top 80` now
answers the question directly.

## Consequences

- No runtime code changed. This touches `research/` and documentation only —
  **nothing to deploy.**
- Every `--proveit` figure produced **before 2026-09-18** was measured against
  the retired ruleset as its baseline. Absolute deltas against *actual* exits are
  unaffected (that comparison never used the baseline row), but any statement of
  the form "X beats shipped by $N" taken from a `--proveit` run is void. Figures
  from `--cliff` are unaffected, which is why that workaround existed.
- The next review is **2026-10-20** (a Tuesday). Register entry
  `exit-parameters-proveit` carries the new baseline, the corrected
  `review_command`, and this review in its `history`.
