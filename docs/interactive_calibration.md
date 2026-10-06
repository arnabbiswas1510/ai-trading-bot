# Interactive self-calibration

The **Calibration > Review & approve** tab is the decision point between automatic
research and live strategy changes. The bot searches supported parameter
settings, tests a frozen candidate on future recorded observations and brings
the result here. It does **not** change live settings or enable real buys.

## Follow the process in the dashboard

Open **Calibration** in the sidebar. This page reads research services separately
from the real portfolio and remains accessible if the real-portfolio API fails.
Each unavailable source has its own error; missing data never becomes a zero
balance, zero trades, or a successful evaluation.

| Tab | What it shows |
|---|---|
| Overview & health | Collector and simulation heartbeats, latest persisted output, pending/dropped records, research blockers, risk-policy availability, pending reviews and the selected campaign's reserved dates/progress |
| Benchmark detail | Recorded rules versus the frozen candidate for the same training or future-evaluation window; after-cost equity change, sampled drawdown, fees, position counts, exact parameter differences, ticker contributions and frozen risk limits |
| Simulated activity | Recorded reference-run holdings, simulated fills, buy/hold/skip/exit decisions and actual saved equity marks, with ticker filtering and older-cycle pages |
| Review & approve | The existing authenticated inbox, durable feedback, research requests, policy settings and separately approved deployment artifacts |
| Recorded-input tools | Existing manually requested replay comparisons and exports; these remain available through Backtester too |

Use the campaign selector to inspect a particular experiment. **Open this proposal
in the review inbox** carries that selection into its feedback and approval
controls. Unsaved inbox drafts and the memory-only token survive switching
Calibration tabs, but not leaving the page. A pending inbox action locks campaign
selection. Clear the token explicitly when finished.

Health and campaign summaries refresh every 30 seconds. Simulated activity is a
manual-refresh snapshot, with 50 recorded cycles per page. Each cycle may contain
many fills or none. Older pages stay inside the same published sequence boundary;
**Refresh newest page** explicitly starts a new snapshot. Holdings describe the
latest verified checkpoint within that boundary, not the older page's date.
Actual equity marks are charted only for the displayed page. A recorded gap
breaks the curve; missing event sequences fail visibly, and a pruned older prefix
is labelled incomplete. Exports contain the selected private evidence or activity
page, not a reconstructed full trading history.

Captured dates are **not** necessarily complete usable training sessions.
A heartbeat is **not** proof of decision coverage. Closed-position counts belong
to one reference simulation run, not the live account or the frozen challenger.
Training gains selected the candidate; they are not independent validation.
Interim evaluation gains cannot authorize early approval. Reports and campaigns
may overlap: never add their dollar results together.

The benchmark view displays the risk policy fixed at selection time, including
worst-loss and concentration checks. The review inbox still checks the current
artifact and approval requirements; passing displayed numbers alone is not
authorization. An approved artifact is **not** evidence that it has been deployed.
The page does not certify current executor deployment.

See `decisions/2026-10-03_unified-calibration-dashboard.md` for why.

## Risk-adjusted benchmark metrics

**Calibration > Benchmark detail** shows a separate, versioned diagnostic risk
report for the recorded rules and the frozen candidate. Training and evaluation
remain separate. The worker reproduces the selected pair and reconciles both
results with their recorded summaries before calculating these additional
statistics. It does not search again, change candidate ranking, or add approval
requirements.

The report stores the exact equity observations, daily returns, historical
three-month US Treasury yield observations, retrieval timestamps, source hashes
and calculation-code fingerprints inside the proposal artifact. The reference is
an approximation of the return available from cash, not the realized return of a
Treasury investment. Historical feeds may contain revisions: a saved retrieval
is reproducible, but is not proof of the data vintage visible at the original
trading instant. Existing rates remain frozen as an evaluation window grows.
The explicit source is **FRED DGS3MO**, the Federal Reserve H.15 three-month
Treasury constant-maturity investment-basis yield. There is no automatic switch
to another series. The worker requests only dates that the current return
intervals can use, not today's potentially unpublished yield.

Allow outbound HTTPS to `fred.stlouisfed.org` in the calibration container.
Certificate verification remains enabled. A TLS trust error or network outage
appears in the report; repair the container's trusted CA configuration or
connectivity rather than disabling verification. Retrieval is bounded to eight
seconds per request, three calendar-year requests and 2 MiB per response, with
31 days of lookback. No key is required.

### Sampling and formulas

Let `r` be a session-to-session fractional equity return and `x = r - rf` its
return above the Treasury cash proxy. All equity values include the replay's
modeled costs and marked open holdings. The cash proxy does not add interest to
the replay's idle cash; it is used only for risk-adjusted comparisons.

| Metric | Definition and boundary |
|---|---|
| Sharpe | `mean(x) / sample_stdev(x) * sqrt(252)` |
| Sortino | `mean(x) / sqrt(mean(min(x, 0)^2)) * sqrt(252)`; the denominator includes all observations, not just losing days |
| Annualized volatility | `sample_stdev(r) * sqrt(252)` |
| Annualized growth | `product(1+r)^(252/n) - 1`; an extrapolation, not observed annual profit |
| Calmar, window estimate | Annualized growth divided by maximum drawdown on the **same session-final series**; not a conventional 36-month Calmar history |
| Full-window sampled maximum drawdown | Largest decline from the starting equity or a later sampled peak, using all available intraday marks |
| Session-final maximum drawdown | Largest decline on the daily-return series; this is the Calmar denominator |
| Historical 95% daily VaR | Signed loss at the empirical nearest-rank 5th-percentile return, available only with at least 20 daily returns |
| Historical 95% expected shortfall | Mean signed loss of the worst `ceil(0.05*n)` daily returns; negative means the historical tail was a gain |
| Profit factor, win rate, expectancy | Completed positions opened within the window, with partial sales grouped; carried-in positions are excluded |

The daily series uses the last recorded terminal mark of each exchange session,
not every intraday tick and not an asserted official closing price. The first
partial interval from the starting portfolio is excluded: five session marks
provide only four daily returns. Missing sessions are not skipped. Calmar and
daily returns therefore have a different explicitly labelled starting point from
full-window P&L and intraday drawdown.

For each return interval, use the latest Treasury observation dated **strictly
before the interval's starting session**. Apply simple ACT/365 cash accrual:
`annual_yield_pct / 100 * calendar_days / 365`, including weekends and holidays.
An observation older than seven calendar days is rejected. This conservative
date lag avoids using an end-of-day yield in a return period that had already
begun; it does not remove possible historical feed revisions.

Sharpe, Sortino and volatility require at least two complete daily returns.
Zero excess-return variance, zero downside deviation, zero drawdown, missing
rates and nonfinite calculations produce **Unavailable**, with a reason, never
zero, infinity or a substituted 0% Treasury yield. A source outage does not
silently change the reference. Other metrics can remain visible.

Annualization assumes the usual square-root-of-time scaling and does not correct
for serial correlation. Fewer than 30 daily returns receive a strong short-sample
warning; fewer than 252 disclose sub-year extrapolation. Those are display
warnings, not investment approval thresholds. Neither additional decimal places
nor a large ratio establishes statistical confidence. The empirical tail of a
20-day sample contains only one observation.

Pre-feature completed artifacts are not backfilled. Newly produced worker results
carry diagnostic reports; an existing active campaign can acquire evaluation
diagnostics without changing its frozen strategy plan. Missing Treasury data is
retried for one unapproved campaign per enabled worker cycle, including a campaign
that has already completed evaluation. These retries use saved replay outputs,
not a new strategy simulation. Revision-checked, audited writes can replace only
the diagnostic subdocument; they never change the frozen strategy, evaluation,
status or approval. Approved, rejected and deferred proposals are excluded.
An approval that races a refresh wins or forces a stale-revision rejection; an
approved artifact cannot be overwritten.

Apply `migrations/20261003_refresh_calibration_risk_diagnostics.sql` before
deploying these diagnostic retries. The migration adds a narrowly scoped
service-role-only database function, not a new table or trading setting.
No new secret or approval gate is introduced. Legacy Backtester ratios
have different assumptions and are not relabelled as these Treasury-adjusted
campaign measurements.

See `decisions/2026-10-03_calibration-risk-metrics.md` for why.

## What runs automatically

`calibration-worker` checks every five minutes. It uses the latest reproducible
hypothetical-portfolio history, not raw error records or reconstructed daily
highs/lows. Complete input coverage, compatible engine/configuration and the
recorded starting portfolio are mandatory.

The source worker waits and retries if its first complete input cycle is not
ready. **Overview & health** displays that startup reason; no simulated run,
decision or complete training session is claimed while it waits. Brief acquisition
failures can retry inside the existing observation deadline. Genuine missing
intervals still end that run, but the worker automatically requests a separately
labelled replacement for recoverable input gaps. Integrity and configuration
errors still require operator attention. Requests are one-use, tied to the
blocked run, and do not erase its evidence.

**Overview & health** shows the current seed timestamp, earliest eligible full
session and automatic replacement's predecessor. Eligibility is not a completed
session count: the whole subsequent session must still be recorded. The independent
watchdog sends a replacement notification even when recovery occurred between
its sweeps, using durable per-recipient receipts. Its GitHub schedule runs every
five minutes Monday-Friday during 13:00-20:59 UTC and every fifteen minutes
otherwise. This covers pre-open and regular trading in both New York DST regimes,
but remains best-effort, not immediate delivery. It does not accelerate training
or automatic worker recovery. See `decisions/2026-10-06_faster-research-watchdog.md`.

The source can prepare a genuine account seed in the two minutes before the
open. If that seed remains fresh when the first valid market frame arrives,
the opening day can be eligible. If startup needs a later fresh seed, the
dashboard instead shows a later eligible session; it does not count the partial
day to make progress appear faster.

A frozen campaign never switches to a replacement's seed. If its source fails,
that campaign is blocked with its evidence retained; later research needs enough
complete sessions from its own new run. Separate experiments are never stitched
together to hide a gap.
See `decisions/2026-10-05_resilient-research-recovery.md` and the recovery
commands in [intraday research](intraday_research.md#queue-one-recovery-including-outside-market-hours).

With default settings, a weekly campaign compares at most sixteen numeric
candidates using the latest five complete trading sessions. A selected candidate
is frozen before the next eligible session opens and evaluated over five
predeclared future trading sessions. Weekends and exchange holidays do not
count. A partial first recording day does not count as a complete session.

Training means the earlier data used to select the candidate. Evaluation
(the **holdout**) means later data not used to choose it. The comparison uses
the current recorded rules as its **baseline**, the reference strategy against
which profit and risk differences are calculated. Both sides begin the
evaluation from the same recorded checkpoint. Daily progress recomputes the
whole accumulated window, preserving each side's continuous hypothetical
holdings rather than resetting cash every day.

Only the fixed final evaluation session can produce an eligible recommendation.
An early profitable day cannot end the experiment. One campaign runs at a time;
deferring an active evaluation prevents a replacement campaign until explicitly
resolved. Shelving an unstarted rule idea does not stop ordinary numeric research.
Rejected data blocks the campaign rather than deleting unfavorable observations.
An engine change or new source run cannot silently reseed an existing campaign.

The first exploratory result therefore needs usable training history. A first
prospective comparison needs the additional future evaluation sessions. Starting
from no usable history, the defaults require at least ten complete sessions,
not ten calendar days. Missing inputs extend this. Five evaluation sessions do
not, by themselves, justify investing money in a new setting.

## What it can investigate

Automatic numeric trials cover supported score/volume/pivot gates, the armed
exit deadline, and scale-out trigger/fraction. They do not automatically toggle
rule behavior. A hypothesis or an operator request for a broader change enters
the inbox first; **approve investigation** grants research permission only.
The shared engine can represent a limited set of rule experiments, including
removing the AI veto or changing whether scale-out is enabled.

A request outside the engine's supported fields is marked as needing engine
support. It is not simulated using a substitute rule. Prove-It bands, trailing
tiers, position limits, market-direction logic and arbitrary new strategies are
not silently optimized by this release.

## Your decisions

Read the proposed settings, training trials, later evaluation results and
limitations. Profit includes modeled costs and marked open holdings; it is not
all realized cash. **Drawdown** is the drop from a previous equity peak.
**Ticker concentration** means how much of the apparent improvement comes from
one stock rather than being spread across several.

You can leave durable comments, request a structured experiment, approve its
investigation, reject it, defer it, resume it, or approve an eligible numeric
deployment artifact. Actions carry revision checks: if new results or another
operator changed the proposal, reload and review before deciding again.

New installations initially have **no risk policy**: software defaults must not
guess an operator's acceptable financial risk. Set all of the following in the
inbox before a new campaign:

| Policy field | Meaning |
|---|---|
| `min_completed_positions` | Minimum fully closed positions; partial sales are not independent trades |
| `min_distinct_sessions` | Minimum distinct evaluation dates, at least two |
| `min_improvement_usd` | Required after-cost gain over the reference strategy, greater than zero |
| `max_drawdown_increase_pp` | Allowed extra peak-to-trough drawdown, in percentage points |
| `max_worst_loss_increase_usd` | Allowed worsening of the largest completed-position loss |
| `min_positive_tickers` | Minimum distinct stocks contributing improvement |
| `max_largest_contributor_fraction` | Maximum share of positive improvement attributable to one stock |

Without a policy, research remains exploratory and approval is blocked. A policy
added or relaxed after seeing results cannot qualify that experiment: another
campaign with future evidence is required. A result can also legitimately say
**no change justified**. No threshold guarantees future profitability.

### Production policy

The operator-approved cloud settings saved on 2026-10-04 use five complete
training sessions, ten future evaluation sessions and at most sixteen candidates.
These are explicit settings, not changes to the five/five software defaults.

| Requirement | Approved value |
|---|---|
| Fully completed positions | At least 10 in each strategy |
| Distinct evaluation sessions | At least 10 |
| Modeled profit improvement after costs | At least $1,000 |
| Additional maximum drawdown | At most 1 percentage point |
| Additional worst completed-position loss | At most $100 |
| Stocks contributing positive improvement | At least 3 |
| One stock's share of total positive improvement | At most 50%, before negative contributors are deducted |

For example, 5% reference-strategy drawdown permits at most 6%, not 5.05%.
These are exploratory operator tolerances, not statistically calibrated
profitability thresholds. Fewer than 30 completed positions remains a small
sample even if eligibility passes. Too few trades means no eligible result;
the bot must not force entries to meet the quota.

From no usable history, this policy needs at least fifteen complete sessions,
plus enough completed positions. Missing data or a partial initial session
extends that interval; no fixed first-recommendation date is promised.
The policy was saved before any proposal existed. Human approval and separate
operator deployment remain mandatory, and real buying remains OFF.
Its date-driven review is in `decisions/provisional_decisions.json`; it does not
wait for real trades while real entries are paused. See
`decisions/2026-10-04_calibration-readiness-and-exploratory-policy.md`.

## Approval and deployment

All mutations and artifact downloads require the existing private
`TRADING_CONTROL_TOKEN` (at least 32 characters). The browser keeps it only in
memory. Use HTTPS or a localhost SSH tunnel before entering it. This is shared
operator authorization, not separate user identities.

Approval authorizes a specific artifact, not general permission for the bot to
rewrite its strategy. The configured `IBKR_ACCOUNT` must match the account used
by the recorded experiment; an unknown or different account blocks approval.
Candidate images must retain the approved engine code/dependency fingerprints,
not merely the same numeric settings: research images match the frozen research
engine, while execution retains the installed live implementation and libraries.
An engine upgrade needs a separate review.
Download the complete artifact JSON from the proposal.
Use the supplied application script from a clean authorized checkout:

```bash
python3 scripts/apply_calibration_artifact.py approved-strategy-....json \
  --dashboard-url https://your-private-dashboard --apply-and-push
```

Follow the script's authentication instructions. It checks current approval and
configuration before applying the embedded patch. Omitting `--apply-and-push`
does not push. Keep the original artifact and its rollback information.
Deployment is the existing operator-controlled pipeline; this workstation
cannot push. The separate new-real-buy switch is unchanged.

Broader rule changes still need an implemented, reviewed code change; permission
to investigate is not permission to deploy a numeric approximation of the rule.
The position dashboard receives deployed exit settings from the backend, so
approved scale-out thresholds and armed-exit deadlines do not retain stale
hardcoded display values.

Research and live execution currently use different dependency sets, including
pandas 3.0.5 in research versus 2.2.2 in execution. Results remain modeled
comparisons, not a claim of identical live execution. Deployment checks preserve
the installed live environment; they do not silently upgrade live libraries to
match research. Shared strategy sources must match, and both replacement
research images must reproduce the frozen research engine identity.

Preflight requires the authenticated dashboard to be available. A failed check
does not recreate or stop protective services. Restoring prior strategy values
requires a separately reviewed replacement approval; reversing an active patch
or deleting its activation marker is not the supported rollback mechanism.

## Visibility and failures

The inbox shows worker health, evidence, proposals and decision history.
Telegram announces frozen selections, completed evaluations and weekly
insufficient-data updates, with a proposal identifier pointing back to the
inbox. Delivery receipts survive restarts and retry failed recipients.

The independent GitHub watchdog flags calibration heartbeats older than thirty
minutes and explicit failed/blocked cycles. Its public incidents contain only
operational descriptions, not account data or performance. Configure its
private research credential independently from the host's Bitwarden entry.

See `decisions/2026-10-03_interactive-self-calibration.md` for why.
