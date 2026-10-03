# Automatic research with operator-approved strategy deployment

Date: 2026-10-03
Status: Accepted; production activation requires the migration, credentials and deployment.

## Context

The operator paused real entries to improve the strategy, not merely to collect
benchmarks. The earlier observer, hypothetical portfolio and scheduled reports
recorded behavior but did not automatically search parameter settings. At the
last successful production inspection, there were zero usable completed shadow
trades; startup/error records were not profitability evidence.

The operator chose higher profit after costs without materially worse drawdown
or large losses, automatic investigation of existing numeric parameters,
permission before broader rule experiments, and a dashboard inbox with Telegram
notifications. Deployment must remain an operator-applied artifact using the
existing apply-and-push workflow, never automatic live promotion.

## Decision

Add a separate broker-free calibration worker, private durable proposals and
decision events, authenticated dashboard actions, and approved deployment
artifacts. Reuse the existing recorded-input selection/evaluation engine instead
of introducing a different approximation of trading.

Research defaults to five complete training sessions, five future evaluation
sessions and at most sixteen numeric candidates. These are scheduling and compute
budgets, not sufficient evidence for deployment. Candidate settings and the
evaluation dates are frozen before the first evaluation session opens. Only one
campaign evaluates future observations at a time. Recompute its entire fixed
evaluation window from the same checkpoint as data arrives; never restart its
challenger holdings daily or select a new winner using evaluation outcomes.

No financial risk or minimum-evidence tolerance is invented. `risk_policy`
starts null. Exploratory results remain visible, but cannot become a deployable
recommendation. A complete operator policy must be frozen before evaluation;
changing it afterwards requires another campaign and new future observations.
Only completion of the predeclared final session permits eligibility.

Numeric search is restricted to parameters supported by the shared replay
engine. New rule behavior requires explicit investigation approval. Unsupported
experiments remain visibly unsupported rather than being approximated or silently
substituted. Strategy-changing experiments are not automatically deployable
through a numeric environment overlay.

Approval binds evidence, settings revision, frozen policy and runtime
configuration. Artifact application verifies approval again, changes only
allowlisted strategy settings, and retains rollback information. It never changes
the separate real-entry permission. The operator still applies and pushes the
artifact; the research service cannot place orders or deploy itself.
Approval is also bound to the recorded brokerage account. Preflight checks the
replacement research images against their frozen engine identities, and the
replacement execution image against the installed live implementation and
dependencies, including orchestrator code. This does not upgrade live libraries:
research and execution retain their existing different dependency sets. Shared
strategy sources must match; results remain simulations rather than a claim of
identical live fills or runtime environments.

Private research stays in the service-role-protected calibration tables.
Telegram notifications use durable delivery receipts. The existing independent
GitHub watchdog supervises the new worker, including outside market hours, so
failure of the production host does not disable its own alarm.

## Consequences and limits

Recorded prices and explicit fill assumptions are still simulations, not a
promise of executable profit. Sessions and completed positions, after-cost
profit, drawdown, losses and ticker contributions must remain visible. A small
sample, one contributing ticker, unsupported execution or missing coverage can
justify no change.

Healthy collection is a prerequisite. This feature cannot reconstruct the
missed October 1-2 sessions, repair upstream IBKR login, or make an empty dataset
informative. Rollout and the initial five/five/sixteen research budget are
registered for review in `provisional_decisions.json`.
Any active overlay still requires a separately reviewed replacement approval to
restore previous values; there is no automatic rollback or automatic
profitability-based live promotion.

See [interactive calibration](../docs/interactive_calibration.md),
[intraday research](../docs/intraday_research.md), and
[configuration](../docs/configuration.md) for current operation.
