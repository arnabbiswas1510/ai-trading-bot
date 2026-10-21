# Evidence-scoped daily calibration health check

Date: 2026-10-20
Status: Accepted; production availability requires applying and deploying the patch.

## Context

Eight daily screener runs failed before the fundamental, technical and AI stages
could run. A historical test used fixed September examples with the real clock;
its decaying score crossed the assertion threshold on October 10 UTC. Patch 118
fixes that test without changing trading rules.

While screening was broken, collector and research heartbeats remained visible.
The candidate table stopped at October 8. Those rows expired from the three-day
lookback, leaving all 130 recorded buy cycles on October 12-16 empty despite an
allowing market gate. Two parameter searches reported no improvement, but the
later window had no new entries to discriminate between entry settings. Counting
completed sessions alone was not evidence of a functioning research pipeline.

The October 20 manual rerun completed all screening stages and produced five
dated, AI-scored candidates. This restored the next-session feed, not the missing
past inputs. Separately, collection interruptions and incomplete daily reports
show that successful screening cannot certify the whole pipeline either.

The operator requested a single check to run daily and selected a button in the
existing Calibration dashboard.

## Decision

Add a read-only daily-health endpoint and a manually invoked panel in
Calibration > Overview & health. Assess screening, candidate availability,
recorded session coverage, simulation evidence, worker health and calibration
progress independently. Reuse existing persisted research evidence; never run
trading, repair state, generate a new replay or alter an experiment for this check.

Separate selected-session evidence from current liveness and next-session
preparation. Use the NYSE calendar and America/New_York dates, including late
workflow starts after UTC midnight. Default to the latest completed session.
An after-close screening success cannot retroactively turn an input-starved
session into a useful experiment.

Use PASS, WAIT, FAIL and UNKNOWN with explicit evidence and actionable next
steps. Missing or malformed evidence, failed reads and bounded-query overflow
must never become zeros or PASS. Preserve independent results when a source is
unavailable. Waiting for future evaluation or enough observations is distinct
from a broken collector. No-change decisions and zero qualifying stocks are
possible outcomes, not reasons to force trades or loosen risk requirements.

The browser clears old results on a new check or date selection and rejects
contradictory or malformed reports. The report stays a time-stamped manual
snapshot, not an implicit continuous monitor. Existing scheduled alerts remain.

## Consequences

The operator gets one daily stage-by-stage handoff without mistaking live
heartbeats for completed research. External GitHub availability and authorized
research database access remain dependencies; outages are visible as UNKNOWN.
Reported operational success is not statistical evidence of profitability,
verification of real broker execution or authorization to enable entries.

No trading rule, approval gate, financial threshold, feature or schema is removed
or replaced. No new dependency, migration, secret or production mutation is
introduced. This is an operational observability decision, not a trading
parameter selected on a small sample.

Current operator instructions: `docs/interactive_calibration.md`,
`docs/configuration.md` and `README.md`.
