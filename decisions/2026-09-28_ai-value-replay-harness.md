# AI-value A/B replay harness (does the AI actually pick winners?)

- **Date:** 2026-09-28
- **Status:** Accepted
- **Scope:** `research/ai_value_replay.py` (new, read-only research tool),
  `decisions/provisional_decisions.json` (`ai-value-ab-replay` review command)
- **Related:** `decisions/2026-09-28_archive-entry-scores-to-trade-history.md`
  (the archival this measurement will eventually consume),
  `decisions/2026-09-28_earnings-blackout-and-news-veto.md` (the news-feed fix
  that makes the AI's news inputs live again and motivates asking the question).

## Context

The register entry `ai-value-ab-replay` recorded an open question — *does the AI
evaluator's score and its grade-D veto change the SET of trades taken in a way
that improves P&L?* — and named a harness that did **not yet exist**
(`research/ai_value_replay.py … build it when this comes due`). With the news
feed just repaired, "can we lean on the AI to pick winners?" became a live design
question, and it cannot be answered by intuition: the prior inference in the
register baseline (composite entry-score/return correlation ≈ −0.05, best trades
had the lowest scores) was *inferred* from the veto log, not measured against
realised returns.

This ADR records the harness that turns the question into a number.

## Decision

Build `research/ai_value_replay.py`, a read-only A/B replay that holds the EXIT
fixed (the live Prove-It stop, imported from `exit_rule_replay.py` so the
mechanics are byte-identical to the exit-review harness) and varies only the
ENTRY SELECTION:

- **Arm A "AI ON":** rank each day's candidates by `adjusted_score` (AI-blended)
  and drop any `ai_grade == "D"` (the live veto).
- **Arm B "AI OFF":** rank the same candidates by `quality_score` (the
  AI-independent technical/quality score) and apply no veto.
- **AI contribution = P&L(Arm A) − P&L(Arm B).**

Design choices that make the attribution clean:

1. **Data source is `trigger_decisions`, not `daily_triggers`.** The latter is a
   rolling table holding only the current day's rows; the former retains every
   per-day candidate decision with both scores, the grade, and the reason code.
2. **Eligibility isolates the AI lever.** A candidate enters the shared pool only
   if its reason code is `BOUGHT`, `AI_VETO`, `SLOTS_FULL` or `INSUFFICIENT_CASH`.
   Non-AI gates (`COOLING_OFF`, `ALREADY_HELD`, `BELOW_PIVOT`, `SCORE_FLOOR`,
   `EARNINGS_IMMINENT`, `NO_AI_SCORE`) exclude a name from BOTH arms. Capacity is
   **re-derived** by the walk-forward rather than taken from the log, because a
   slot the log saw full may be free once an arm's earlier picks have exited.
3. **Forward outcomes are simulated, not read.** Vetoed and capacity-skipped names
   were never bought, so they have no realised P&L. Each candidate is entered at
   its `decision_date` session open and replayed forward on 5-minute bars under
   the Prove-It stop; a position still open at the horizon is marked out at the
   last close. Both arms size every slot at the same fixed notional, so the delta
   is pure selection.
4. **A dedicated `--veto-audit`** reports the forward outcome of every `AI_VETO`'d
   name on its own. This is the highest-signal reading available on a small sample
   because it is a direct per-name counterfactual and does not depend on slot
   contention.

## Preliminary reading — NOT a finding, do not cite as settled

Run on 2026-09-28 against the then-current 287-row / 23-date / 17-veto sample
(67 closed trades, ZERO carrying the archived per-trade grade yet):

- **`--veto-audit`:** the 17 grade-D vetoes were net **losers** — 14 lost, 3 won
  — so the veto *gave up* **−$2,140** at $20k/name, i.e. it **saved money**. The
  notable winner it removed was DELL 2026-09-04 (+8.28%, quality 72).
- **Full A/B:** Arm A (AI on) **−$7,018** vs Arm B (AI off) **−$8,328**, so
  **AI contribution = +$1,310** — achieved by trading *less* (24 picks vs 42, the
  **same** 17% win rate), i.e. the veto steers out of losers rather than finding
  more winners. Both arms lose in absolute terms; capacity (`SLOTS_FULL` was the
  modal skip reason) still dominates entry selection.

These numbers are directionally encouraging for the veto but must not move any
parameter. The sample is tiny, single-regime, and capacity-bound; the register
entry gates the real read at 90 closed trades / 2026-12-01, and archival of the
per-trade grade (migration `20260928`) had produced zero rows at the time of
writing. The harness exists so that read is a measurement, not a memory.

## Consequences

- The register's `review_command` now points at the real tool.
- No trading behaviour changes. The harness reads Supabase + FMP and writes
  nothing; bars are cached under `/tmp`.
- Future sessions answer "is the AI helping?" by running this, not by inferring
  from the veto log.

## Alternatives considered

- **Correlate archived entry scores against realised returns.** Rejected as the
  primary method: correlation cannot see the trades the AI *prevented* (the veto),
  which is half the question. The A/B replay scores the counterfactual directly.
- **Replay only actually-bought trades.** Rejected: that is the exit-review
  harness's job and cannot measure selection, because it never considers the names
  the AI kept out.
