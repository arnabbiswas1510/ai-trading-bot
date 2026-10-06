# Faster research watchdog checks around market hours

- **Date:** 2026-10-06
- **Status:** Accepted
- **Scope:** Cloud monitoring cadence only

## Context

Research now records reproducible simulated decisions. The operator requested
faster detection of problems, without complicating deployment, and will deploy
after market close. The watchdog reports incidents; it does not drive the
worker's existing bounded retries or automatic recovery.

## Decision

Use three non-overlapping UTC schedules in
`.github/workflows/intraday_research_review.yml`:

- `*/5 13-20 * * 1-5`: five-minute weekday checks around US market hours.
- `*/15 0-12,21-23 * * 1-5`: fifteen-minute checks at other weekday times.
- `*/15 * * * 0,6`: fifteen-minute weekend checks.

The padded UTC window covers 09:00 pre-open readiness and the regular session
in both New York daylight-saving and standard time. At 16:00 standard time,
the fifteen-minute schedule supplies the closing check. Holiday/early-close
awareness remains in the existing Python health checks; no timezone scheduler,
new service, environment variable or deployment gate is introduced.

Use the normal apply/push/deploy process after market close. Do not change the
running experiment, live-buy permission, observation frequency, freshness
limits, opening grace, incident deduplication or recovery policy.

## Consequences

Nominal weekday invocations increase from 96 to 160; weekends remain 96.
For a ten-minute stale-data threshold, the faster polling reduces the nominal
detection interval from 10-25 to 10-15 minutes after data stops. GitHub delays,
job startup and serialized runs mean these are not delivery guarantees.
The required training/evaluation sessions and quality of observations do not
change, and missing evidence cannot be recovered by polling faster.

The contract test covers every minute of a week for cadence and overlap, plus
09:00-16:00 New York in both DST regimes. Existing reporting tests retain
coverage of failure thresholds and durable notification behavior.

Current behavior is documented in `docs/intraday_research.md`,
`docs/configuration.md` and `docs/interactive_calibration.md`.
