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

## What runs automatically

`calibration-worker` checks every five minutes. It uses the latest reproducible
hypothetical-portfolio history, not raw error records or reconstructed daily
highs/lows. Complete input coverage, compatible engine/configuration and the
recorded starting portfolio are mandatory.

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

Research settings initially have **no risk policy**. This is deliberate: the
operator has not yet specified what "materially worse risk" means in dollars and
percentage points. Set all of the following in the inbox before a new campaign:

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
