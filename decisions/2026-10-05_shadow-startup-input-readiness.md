# Validate startup inputs before creating a simulated research run

- **Date:** 2026-10-05
- **Status:** Accepted
- **Scope:** Research startup and explicit recovery; no live trading changes

## Evidence

Production revision `507b135` created its first shadow run at 09:30:13 ET on
2026-10-05. Its first acquisition failed at 09:30:14 with
`RS: stale/future provider quote.` Both independent capture streams rejected RS
at 09:30:34. At 09:35:34 they recorded a valid $405.38 quote whose provider
timestamp was 09:35:16. The shadow run nevertheless remained blocked all day.
It contains one gap and zero committed decision cycles or simulated fills.

The exact rejected provider timestamp was not retained, so we cannot say
whether it was stale or future-dated, or claim a specific timestamp offset.
Rejecting an unusable quote was correct. Creating a permanent experiment before
establishing that its first input frame was usable coupled an opening data delay
to an all-day halt. A normal process restart deliberately cannot clear that halt.

## Decision

For an unstarted run, acquire a newly observed account seed and full first frame,
then validate the frame with the pure engine before persisting a run or
superseding its predecessor. Startup `InputGap` failures report waiting health
and a `shadow_startup_waiting` diagnostic. The existing worker poll retries with
a fresh seed. Input failures do not mutate any prior experiment.

Keep the current 600-second quote-age limit, prohibition on future timestamps,
complete-universe requirement and 120-second scheduled-acquisition window.
Do not omit RS or any other candidate, relabel old prices, reuse a failed frame,
or backfill elapsed market time. Seed/frame acquisition crossing session close
cannot create a run. Successful initial inputs and output are persisted before
the history-reference cache is marked committed. Established runs retain their
durable input staging, gap/block semantics and explicit-replacement requirement.

Quote rejection text additionally preserves ticker, provider/receipt/capture
timestamps and age in seconds. It contains no credentials or raw HTTP URLs.
The old RS provider timestamp cannot be retroactively recovered by this change.

Add `--queue-new-run` for an operator-authorized, one-use recovery request in
the existing local SQLite metadata table. It only accepts the currently blocked
run, names that run, and exits without network access. Stop the normal writer
while recording the request. The request survives restart and failed startup;
successful run creation consumes it in the same transaction that supersedes the
old run. A mismatched or malformed request fails rather than resetting another
experiment. This is not a recurring automatic reset or a live trading action.
There is no Supabase schema change or new environment variable.

## Consequences and recovery

A rejected first observation is visible waiting, not a simulated decision or
usable training session. Partial initial days still do not count toward the
five complete training sessions; ten later evaluation sessions and the saved
risk policy remain unchanged. No profitability inference is made from this fix.

The October 5 failed run and original gap remain preserved. The operator
authorized a fresh research run, but after-hours preparation must not fabricate
an overnight start. Queue its replacement, deploy the fixed image, and let the
normal worker acquire valid regular-session inputs. Until that happens, report
recovery as pending, not running.

See `docs/intraday_research.md` for recovery commands,
`docs/interactive_calibration.md` for UI/progress and
`docs/retired_code.md` for the relocated creation path.
