# Evidence-bound risk ratios for calibration

Date: 2026-10-03
Status: Accepted; requires operator application and deployment.

## Context

The operator requested Sharpe, Calmar, maximum drawdown and related risk
statistics in the benchmarking dashboard, and selected historical three-month
US Treasury yields as the cash reference, stored with each experiment.
The chosen data series is the Federal Reserve H.15 Treasury three-month
constant-maturity yield, explicitly retrieved as FRED DGS3MO. The Treasury-hosted
XML endpoint was unreachable during development; the FRED series metadata and
public CSV were verified instead. This is one declared source, not an automatic
runtime fallback.

Current campaign summaries contain aggregate profit and sampled drawdown, not a
daily return series. Treating intraday marks as independent daily observations,
assuming a zero cash return, or annualizing five observations without a warning
would give misleading apparent precision. The legacy historical Backtester's
zero-reference ratios are not suitable for reuse under the selected reference
and missing-data requirements.

## Decision

Attach a versioned diagnostic sidecar to the existing proposal artifact. Reproduce
only the already-selected reference/candidate pair, reconcile their entire
summaries, and retain the exact daily marks, returns, Treasury observations,
source/retrieval hashes and analytics-code fingerprints.

Do not edit core replay or calibration-selection code and their fingerprints.
Risk analytics are not an additional optimization objective, financial tolerance
or automatic approval gate. Existing active campaigns can receive new evaluation
diagnostics without changing their frozen selection. Approved artifacts remain
immutable. Pre-feature completed artifacts show no risk report if one was never recorded.

Use N terminal session marks to compute N-1 sampled session-to-session returns.
Exclude the opening partial interval and reject missing sessions. Annualize with
252 periods. Sharpe uses sample excess-return deviation; Sortino uses downside
deviation over the full sample. Calmar's annualized growth and drawdown use the
same terminal-session series, separately from full-window intraday drawdown.

Use a Treasury observation strictly before each interval's starting session,
reject dates older than seven calendar days, and accrue the annual yield as a
simple ACT/365 cash proxy across actual calendar days. Freeze prior retrieved
observations when future evaluation expands; never silently revise earlier
reference values or replace missing rates with zero. Historical feed revisions
remain a stated limitation, not a claim of point-in-time market-data vintages.
Only freeze reference dates needed by the observed intervals: through the day
before the penultimate session, not the current session. Freezing a still
unpublished current-session yield as absent would prevent later evaluation
windows from ever incorporating it.

Dispersion requires two returns. Empirical 95% VaR/expected shortfall requires
twenty returns, with the actual tail count and a sparse-sample warning. Undefined
denominators and nonfinite calculations are unavailable, not zero or infinity.
Warnings below thirty daily returns and below one 252-session year do not impose
new investment thresholds. Annualized Calmar is a window extrapolation, not a
36-month result. Trade metrics exclude positions carried into the window and
merge partial sales of newly opened positions.

## Consequences

The dashboard can compare risk on identical, explicitly scoped observations
without pretending short histories are reliable. It stores enough evidence to
reproduce the calculation even if the source later revises historical yields.
Treasury outages are visible and disable adjusted ratios rather than blocking or
silently altering the existing strategy research process.

The worker performs up to two additional bounded replays when first constructing
diagnostics. Treasury-only retries use saved outputs, not another strategy replay.
One eligible unapproved campaign is retried per enabled worker cycle, including
terminal ready/no-change campaigns; otherwise a transient final-day outage would
leave its adjusted ratios unavailable permanently.

`migrations/20261003_refresh_calibration_risk_diagnostics.sql` adds a dedicated
service-role-only refresh function. The existing general mutation function's
frozen-evidence guard is not relaxed. The new function locks the proposal,
checks revision/status and expected artifact hash, changes only risk diagnostics,
increments the revision and appends an audit event atomically. Strategy plans,
evaluation results, status and approvals remain unchanged. Approved, deferred and
rejected artifacts are never automatically rewritten.

No new secret, live-trading change, candidate ranking change or approval-policy
change is introduced.

Current behavior and formulas: `docs/interactive_calibration.md`,
`docs/configuration.md` and `docs/intraday_research.md`.
