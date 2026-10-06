# Recover research collection without manufacturing continuous evidence

- **Date:** 2026-10-05
- **Status:** Accepted; watchdog cadence superseded by [2026-10-06](2026-10-06_faster-research-watchdog.md)
- **Scope:** Research-only resilience, supervision and operator visibility

## Context and authorization

> 2026-10-06: The fifteen-minute cloud cadence below now applies only outside
> the weekday 13:00-20:59 UTC window, when checks run every five minutes.
> Recovery mechanics and evidence requirements are unchanged.

The October 5 RS opening quote failure halted an empty simulated experiment for
the entire day. Patch 114 delayed run creation until the first valid cycle, but
preserved the old immediate-block/manual-replacement policy for established runs.
That still made one transient acquisition failure capable of stopping subsequent
days until an operator intervened.

The operator explicitly chose: "Automatically start a separate replacement and
notify me." This is authorization to recover research collection, not to trade,
alter research thresholds, fabricate observations or turn a partial day into a
complete session.

## Decision

Retry recoverable acquisitions within the existing 120-second observation
deadline. Keep the same due slot and unchanged quote freshness/completeness
requirements. Do not commit failed attempts as successful decisions. Once the
observation interval is genuinely missing, preserve a recorded gap and end that
experiment.

Use a durable one-use replacement request tied to the failed run. Validate fresh
starting inputs before superseding it. The replacement records automatic mode,
predecessor, request time and reason in its starting evidence. It is a distinct
experiment, not an extension of the missing history. Corrupt journals, invalid
engine state and semantic-configuration changes remain operator problems rather
than automatic-reset permissions.

One narrow upgrade case is explicit: the October 5 experiment failed before
recording any cycle, and patch 114 changed the engine fingerprint through
quote-diagnostic text in `shadow_inputs.py`. A recognized acquisition-gap run
with zero frames, no stored inputs/cycle events, matching semantic configuration,
and intact state/seed/configuration checksums can be replaced across revisions.
Its old state is never restored or executed by the new engine. Established
trajectories still require their original compatible engine.

Prepare an in-memory account seed during the 120 seconds immediately before the
exchange open. The observer records outside regular hours and the unchanged
simulation engine accepts a coherent pre-open seed. No run or decision is
persisted until a regular-session frame validates, and the seed must still meet
the existing 120-second freshness budget. An expired or missing provisional
seed is replaced with a newly observed one; if that occurs after the open the
day remains partial. This fixes the inherent first-day exclusion without
backdating a timestamp or relaxing the complete-session predicate.
An initial quote failure retains a still-fresh pre-open seed for a bounded retry;
it does not unnecessarily replace valid opening evidence with a later seed.

Keep notification transport off the shadow decision path. The existing
independent watchdog reads immutable replacement provenance and uses its existing
per-recipient receipts to notify even when a failure/recovery occurred between
sweeps. Failed recipients retry; acknowledged recipients are not routinely sent
duplicates. The existing at-least-once caveat remains if a delivery acknowledgement
is lost. GitHub's 15-minute schedule is best-effort, not a precise delivery SLA.

Check observer/shadow heartbeats and observer spool readiness from 30 minutes
before the exchange open (09:00 New York), including the existing 10-minute
opening grace period. Report explicit blocked/error shadow health overnight too.
Do not demand fresh premarket quotes or claim that a heartbeat certifies an
upcoming complete price/decision frame.

Expose seed time, earliest eligible full session and predecessor on the private
calibration overview. A date eligible to become complete is not evidence that the
session has completed. These additions require no new Supabase columns, secrets
or live trading configuration.

## Evidence boundaries

The full-session predicate remains opening time at or after the recorded seed
timestamp, with the session closed and its closing mark present. A later startup
cannot retroactively observe the opening. Genuine gaps can still invalidate a
campaign; its already recorded evidence remains available for inspection.

The calibration worker does not switch an existing frozen campaign to a
replacement seed. It blocks that campaign, preserves its artifacts, and waits
for sufficient evidence for a distinct later campaign. This prevents recovering
collection from becoming selective restarts of an underperforming strategy.

No parameter is selected using the RS incident's one-day sample. This is an
operational reliability decision, not evidence of profitability.

See `docs/intraday_research.md`, `docs/interactive_calibration.md`,
`docs/configuration.md` and `docs/retired_code.md`.
