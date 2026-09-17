# The exit-parameter review becomes cron-backed instead of a date in a markdown table

**Date:** 2026-09-17
**Status:** Accepted.

## Context

This repo had two review systems, and only one of them could actually fire.

| | Exit-Parameter Review | Provisional Decision Register |
|---|---|---|
| Lived in | a markdown table in `AGENTS.md` | `decisions/provisional_decisions.json` |
| Trigger | **passive** — only when a session *read* the file | **active** — monthly cron |
| Automation | none | `decision_review.yml` → GitHub issue + Telegram |
| Missable | **yes, silently** | no |

The exit review governs the thresholds that decide when every position is sold.
It is arguably the most consequential recurring check in the project, and it was
the half with no alarm attached. Its six checkpoint dates were prose.

This was not hypothetical. The 2026-09-20 checkpoint was surfaced on 2026-09-17
only because a session happened to read `AGENTS.md` for an unrelated reason. Had
that not happened, the review could have slipped indefinitely — and with it a
baseline the file itself had marked **"do not cite"**, plus an explicitly *owed*
`--cliff` re-run whose absence left a rejected hypothesis unsafe to quote.

`AGENTS.md` already acknowledged the weakness — "the schedule above is a
*passive* reminder … it fires when a session reads this file" — and pointed at
the register as the fix, but the exit review itself was never actually
registered. The mechanism existed; this decision connects it.

## Decision

Register the exit review as `exit-parameters-proveit` in
`decisions/provisional_decisions.json`, so it is alerted on by the existing
monthly `decision_review.yml` cron like every other provisional decision. No new
workflow, no new infrastructure — the machinery was already built and tested.

**`revisit.min_closed_trades` is deliberately `null`.**

This is the substantive design choice. `is_due()` requires **both** thresholds to
be satisfied, so a trade-count gate is a conjunction: setting
`min_closed_trades: 60` would mean a quiet two months — precisely when nobody is
thinking about exit rules — silently suppresses the alarm. That reintroduces the
failure mode being removed, in a form that is harder to notice because the entry
*looks* tracked.

The exit review is date-driven by design. It fires on the date regardless of
trade count, and the review's own first question is "has the sample grown enough
to matter?" — with an explicit licence to answer "no, skip". A prompt that
sometimes resolves to "nothing to do" is strictly better than a prompt that
sometimes fails to arrive.

Verified: with `min_closed_trades: null` and `not_before: 2026-10-20`, the entry
evaluates `due=True` on 2026-10-20 **even with zero new closed trades**.

## Consequences

- A missed exit review now requires someone to close a GitHub issue without doing
  the work, rather than merely not reading a file.
- **Completing a review is now a two-step update**: tick the `AGENTS.md` table
  *and* append to the register entry's `history` while bumping
  `revisit.not_before` to the next checkpoint. Doing only the first leaves the
  cron firing on an already-handled date. This is documented in the note under
  the schedule.
- The register is now the single index of everything awaiting evidence: scale-out
  sizing, the cooling-off window, the shadow RS percentile, and the exit stack.

## Alternatives rejected

- **A dedicated workflow that runs `exit_rule_replay.py` in CI and posts the
  table.** Attractive, but the replay needs Supabase *and* FMP credentials and
  fetches 5-minute bars for every closed trade — slow, rate-limit-exposed, and it
  would put a live brokerage-derived dataset into CI logs. More importantly the
  output requires *judgement* (is a result carried by one trade? were winners
  harmed?), so automating the run without automating the interpretation produces
  a table nobody reads. Prompting a human to run it is the honest division of
  labour.
- **Six separate register entries, one per checkpoint date.** Noisy, and the
  register already has a bump-to-next-milestone loop for exactly this.
- **Leave it passive and rely on diligence.** This is what was already in place;
  it failed once, silently, and was caught by luck.
