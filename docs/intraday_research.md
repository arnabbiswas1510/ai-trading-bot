# Recorded intraday research

The **Backtester** page contains **Recorded intraday research**, separate from
the older daily-bar backtest. It compares the recorded rules with a variant
that removes only the AI D-grade veto. Scores, ranking, other entry gates,
position limits, exits and costs remain in place.

Both strategies begin with the **actual recorded account**, not a user-entered
cash reset: existing positions and their state, prior sales, re-entry
restrictions, cash/equity and broker protection. This does not manufacture
history from before recording began.

## Deploying

1. Apply `migrations/20260930_add_intraday_research.sql`,
   `migrations/20260930_add_intraday_shadow.sql`, and
   `migrations/20260930_add_intraday_reporting.sql`, and
   `migrations/20261003_add_calibration_loop.sql` in the Supabase SQL Editor.
   They are safe to re-run; their final queries report the required objects.
2. Add `INTRADAY_SUPABASE_KEY` to this application's Bitwarden project with a
   server-side Supabase service-role key. The new tables deliberately refuse
   anonymous/publishable-key access. Do not put the value in source, a patch or
   the browser. `.env.template` resolves it through `@bws`, but deployment does
   not run the resolver automatically. Render and review the host configuration,
   preserving its operator settings, then recreate affected containers to load
   the key. A Bitwarden entry alone does not update an existing host `.env` or
   running container. The resolver fails if a required secret is missing;
   a successful image deployment alone does not prove research access.
3. For the independent watchdog, configure **one** GitHub Actions repository
   secret, `BWS_ACCESS_TOKEN`: a Bitwarden machine-account token with read access
   to project `ai-trading-bot`. The workflow fetches `SUPABASE_URL`, `SUPABASE_KEY`,
   `INTRADAY_SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` by exact
   name from that project; do not duplicate those five values into Actions.
   The weekly backup uses the same Bitwarden bootstrap but only the four
   non-research secrets above; it does not require `INTRADAY_SUPABASE_KEY`
   and excludes this entire research subsystem.
4. Apply the delivered patch and deploy the web and execution images through
   the existing pipeline. Leave the GitHub repository Actions variable
   `TRADING_RUNTIME_MODE` unset or set it to `observe`: deployment starts real
   protective execution alongside the independent observer, shadow worker, calibration worker and
   dashboard. New real buys require the separate dashboard permission, initially
   OFF; neither `observe` nor `live` grants it. The gateway is not recreated.
   See [trading control](trading_control.md) and
   `decisions/2026-10-01_dashboard-live-entry-control.md`. Observer and execution
   spools live at `/app/logs/intraday_observer.sqlite3` and
   `/app/logs/intraday_capture.sqlite3`, respectively, in the persistent logs
   mount. Never share a spool between running collectors.
5. Open **Calibration > Overview & health**. Confirm a recent capture,
   recording health and recorded sessions. Missing migration, permissions,
   price coverage or starting protection is an error, not an empty account.

Migrations are **not applied automatically** by deploying the code.

### Watchdog credential bootstrap

The hosted runner installs official `bws` 2.1.0 with a pinned SHA-256 check, then
`scripts/run_intraday_reporting_bws.py` loads only the five watchdog credentials.
The project name must resolve uniquely; every required name must have exactly
one nonempty, single-line value other than `@bws`. The private research key is
mandatory and never falls back to the ordinary Supabase key.

Values are masked with escaped GitHub commands and passed directly to the
reporting child process in memory. No secret `.env`, `GITHUB_ENV`, artifact or
Bitwarden authentication cache is written. The bootstrap token is not passed to
the reporting child. Existing GitHub issue/Telegram reporting and nonzero exit
codes remain in effect after successful bootstrap.

A bootstrap failure names `BWS_ACCESS_TOKEN`, the vault operation or the missing
required secret in the failed Actions step, without exposing transport output.
Bootstrap failure occurs before database/Telegram access, so its notification is
the failed Actions run, not a claim that a collection incident was delivered.
Saving a value in Bitwarden alone cannot authenticate an unconfigured runner.
The host token file is not available to GitHub-hosted runners; an operator must
configure the Actions bootstrap secret once. See
`decisions/2026-10-04_watchdog-bitwarden-bootstrap.md` for why.

The **Calibration > Review & approve** inbox adds automatic parameter discovery and frozen future
evaluation, not just descriptive benchmarks. It requires the same private
research key and an operator-configured `TRADING_CONTROL_TOKEN` for writes.
Research defaults to five training sessions, five future sessions and sixteen
candidates. Risk tolerances are deliberately unset; exploratory results cannot
be approved for deployment until a policy is frozen in a new campaign.
See [interactive calibration](interactive_calibration.md) and
`decisions/2026-10-03_interactive-self-calibration.md`.

The **Benchmark detail** tab compares a selected campaign's recorded rules with
its frozen candidate without mixing training and future evaluation.
**Simulated activity** reads stored reference-shadow fills, decisions and equity
marks through a bounded read-only endpoint,
`GET /api/intraday/shadow/runs/{run_id}/activity`. Pages default to 50 cycles
(maximum 100); `through_sequence` freezes the published boundary and
`before_sequence` requests older cycles. Only published output is displayed.
Gaps remain explicit, sequence holes fail visibly, and pruned earlier history is
labelled rather than implied complete. Viewing these records does not replay a
strategy or contact the broker. The **Recorded-input tools** tab retains the
manual comparison/export workflow, also available through Backtester.
See `decisions/2026-10-03_unified-calibration-dashboard.md` for why.

The calibration worker also stores separately versioned diagnostic risk reports.
It reproduces only the recorded reference and frozen candidate, reconciles their
summaries, and uses terminal-session equity marks rather than intraday samples
as independent daily returns. Historical three-month Treasury rates and source
hashes accompany Sharpe/Sortino; Calmar uses the same daily window for both growth
and drawdown. Missing data and short samples remain explicit. Core selection and
replay fingerprints are unchanged, so an existing campaign is not reseeded just
to add diagnostics. Missing Treasury references are retried from saved outputs,
even after evaluation completes, but only while the campaign remains unapproved
and research is enabled. Apply
`migrations/20261003_refresh_calibration_risk_diagnostics.sql` before deploying
the worker; its row-locked function can update only diagnostics, not frozen
strategy evidence or approvals. See [risk metric definitions](interactive_calibration.md#risk-adjusted-benchmark-metrics)
and `decisions/2026-10-03_calibration-risk-metrics.md`.

**New real buys OFF means protect-only, not an idle execution agent.** Real
Prove-It monitoring, protective exits, order repair and ledger reconciliation
continue. The observer itself never manages positions. Existing broker-held
orders remain live and can fill; the switch does not cancel them. Check the
separate execution-agent status and broker protection before relying on it.

## Diagnose research failures without production SSH

Start with Supabase's existing `agent_logs`, using the ordinary operational
credential. Research diagnostics do **not** require a working private research
key or permission to read the private capture/shadow tables:

```sql
SELECT logged_at, session_id, seq, level, repeat_count, message
FROM agent_logs
WHERE message LIKE '[RESEARCH-DIAGNOSTIC]%'
ORDER BY logged_at DESC, id DESC
LIMIT 100;
```

Each structured message identifies the service and event. Errors retain safe
database codes, HTTP status when available, and source locations. For example,
`42501` indicates denied privileges; `42P01` or `PGRST205` indicates an unavailable
table; `401` without a more specific database code indicates authentication
failure. These are evidence, unlike the generic API migration/key hint.
API errors include a diagnostic reference so the failed request can be matched
to its log. The reference means queued for delivery, not confirmed persisted.

Startup records describe whether required credentials are present and their key
family/role, never their values. `capture_progress` reports persisted observation
progress; `shadow_cycle_committed` reports local simulation progress;
`shadow_upload_progress` includes remaining uploads. A diagnostic heartbeat only
proves the logging process is alive. Waiting for a market session, a blocked
run, and successfully simulating positions are different states.

Production Docker commands run through `research_entrypoint.py`, which installs
diagnostics before application imports. The web app, observer, execution
recorder, shadow worker, calibration worker and cloud reporter use the same safe diagnostic format.
The existing execution-agent narrative logs remain available separately.
For an isolated CLI diagnostic, use the same wrapper, e.g.
`python research_entrypoint.py intraday-observer --once`; ordinary direct module
invocation does not install the independent shipper.

Host diagnostic directories survive container recreation at
`/app/data/research-diagnostics` (web), `/app/logs/observer-diagnostics` (observer),
`/app/logs/execution-diagnostics` (execution), and `/app/shadow/diagnostics`
(shadow), and `/app/calibration/diagnostics` (calibration). Each contains `research-diagnostics.sqlite3`, separate from raw research
spools. Bounded queues/outboxes retry delivery and report lost records; an abrupt
process crash can lose a not-yet-journaled queue tail. A lost acknowledgement can
duplicate a row, identifiable by its session/sequence/diagnostic ID.
If the diagnostic disk spool is unusable but Supabase is reachable, the worker
sends a safe `diagnostic_spool_failed` notification directly, at most once every
30 seconds. This fallback does not make unwritten local events durable.

If Supabase itself or the host's network is unavailable, no logger can publish
there immediately. Pending host diagnostics retry after recovery; the independent
GitHub/Telegram watchdog remains the outage alarm. Its GitHub runner uses a
temporary diagnostic spool, not storage guaranteed across workflow runs.
Keep both ordinary `SUPABASE_KEY` and private `INTRADAY_SUPABASE_KEY` values in
Bitwarden project `ai-trading-bot`. The watchdog imports both using its Actions
`BWS_ACCESS_TOKEN`, so private-key failures do not disable ordinary diagnostics.
Never weaken private-table permissions to make the status page work.

### Startup and upload prerequisites

`ReadOnlyBroker` supports weak references so the real `ib_insync`/`eventkit`
callback registry can attach its error and disconnection handlers. A traceback
ending in `Broker capability does not expose __weakref__` identifies an old,
broken observer image, not a filesystem permission problem. Brokerage write
methods remain forbidden at both SDK layers; weak-reference support does not
grant order access.

The recorder constructs `SyncClientOptions`, including its ten-second request
timeout, for the synchronous Supabase client. With the deployed Supabase SDK,
the base `ClientOptions` lacks `storage` and fails before any database request.
`AttributeError: 'ClientOptions' object has no attribute 'storage'` therefore
requires the recorder compatibility fix, not a schema or permission change.
Regression tests construct the actual SDK objects without network connections;
fake event handlers alone cannot reproduce these startup failures.

After recovery, preserve the existing SQLite spools and check that pending
uploads drain. Uploaded startup/error records are not usable broker snapshots or
simulated trades. Require completed `observer_snapshot` records and advancing
shadow cycles during an actual market session before counting a day as evidence.
A connected local gateway socket does not prove IBKR's upstream account/data
connection is healthy. Broker errors such as `2110` and incomplete requests remain
explicit gaps; the observer retries without restarting the gateway or inventing
account state.

See `decisions/2026-10-03_independent-research-diagnostics.md` for why.

## Independent observer

The observer owns a separate IBKR connection (client ID `71` by default) and
requests completed, account-scoped signed inventory, visible orders from all
clients, account values and executions. Negative holdings are recorded as
shorts, never filtered away. Fills retain execution IDs, observation times and
commission availability; commission updates are separate observations, not
additional executions to count twice.

Account downloads use the request-ID-scoped `reqAccountUpdatesMulti` protocol,
including the SDK's concurrent connection-bootstrap reads. The legacy
`reqAccountUpdates` subscription is never acquired or cancelled by the observer:
it can displace the execution agent's single-account subscription even with
`readonly=True`. Every download waits for its own `accountUpdateMultiEnd` and
accepts only callbacks matching its request ID, account and model. Requests are
cancelled on success or failure. Shared SDK account caches and unscoped
`accountValueEvent` notifications are not freshness evidence.

The multi-account API does **not** supply portfolio marks. Each nonzero holding
therefore gets a separate read-only `reqPnLSingle` request, scoped to its account
and contract, and cancelled after its first matching valuation response.
The signed quantity must match the completed inventory request. For stocks in the
account's base currency (identified by the fresh `NetLiquidation` currency),
market price is calculated from the broker's position value divided by signed
quantity; this is explicitly labelled `IBKR_PNL_SINGLE_VALUE`, not a legacy
account download or a quote. Non-stock or foreign-currency unit prices remain
unavailable rather than assuming a contract multiplier or exchange rate.
Daily realized P&L is not substituted for
legacy realized P&L. Response times are local receipt times, not broker price
timestamps, and `component_times.portfolio_marks` records the acquisition window.
Timeouts, invalid/missing valuations, request errors and disconnects remain failed
snapshots; no cached marks or artificial zero values fill the gap.

The connection uses `readonly=True`, but that SDK flag is **not** a broker
permission boundary. A restricted broker interface also blocks SDK order
submission, cancellation and binding. The observer imports no trading daemon
and never writes portfolio positions, trade history or live settings.
Dedicated read-only brokerage credentials remain stronger protection.

On the production host, after applying the migration and provisioning secrets:

```bash
cd /home/pom/docker/ai-trading-bot
TRADING_RUNTIME_MODE=observe sh scripts/deploy_runtime.sh
docker compose --profile observe logs -f intraday-observer
```

Docker's `unless-stopped` supervision resumes an enabled observer after reboot
or failed reconnect attempts, while respecting an explicit stop. Each process
allows three reconnect retries with a five-second delay; failures create gaps,
not claims of continuous coverage. The gateway is never restarted by the
observer. A disconnected gateway prevents broker observations; already accepted
events remain in the disk spool and candidate recording has its own worker.

For an isolated diagnostic, `python3 intraday_observer.py --once` requests one
snapshot and waits up to 45 seconds for its cloud upload. Supply `--host`,
`--port`, `--account` and `--spool` for that environment. Do not run it alongside
another observer using the same client ID, spool or health identity. A nonzero
exit means the diagnostic was not successful; retained local events do not
prove cloud persistence. Normal continuous operation omits `--once`.

The observer's health ID is `intraday-observer`; the embedded recorder retains
`execution-agent`. The dashboard shows each collector separately; an observer
heartbeat never stands in for the real execution agent's status.

Observer records are labelled `capture_mode=observer`, `replay_ready=false`.
They are useful for later execution audits and price-path research, but they
do not contain live buy/monitor decision inputs. The live-decision replay
rejects them explicitly rather than inventing those inputs.

## Decision-only worker

`shadow-worker` runs in the `observe` profile using `Dockerfile.shadow`. It has
no IBKR SDK, order-management modules or gateway connection. Its separate
`research_bridge` network is not the gateway's Docker network. This is an
application/deployment boundary, not a claim that service-role database
credentials are themselves read-only.

The worker reads completed observer snapshots, source trading tables and FMP.
Candidate reads are ordered by both `daily_triggers` key columns,
`(triggered_at, ticker)`, on every page. That table has no `id` column.
Date-only screener trigger labels remain dates; their availability is established
by the separate acquisition timestamp, not an invented intraday trigger time.
Missing screener ATR (average true range, a measure of daily price movement)
keeps the live static-stop fallback. Independently derived ATR is diagnostic
evidence, expressed in percentage points rather than silently substituting a
different stop-sizing input.
An explicit `IBKR_ACCOUNT` or `--account` selects the seed account. Unsupported
shorts, incomplete protection, conflicting quantities, partial initial orders,
manual requests or missing history prevent initialization. The worker never
silently assumes an empty portfolio or resets cash. Subsequent actual account
activity does not replace the hypothetical portfolio.

Both adjacent completed account downloads must contain exactly one positive,
finite, account-wide USD `NetLiquidation` row. Duplicate/conflicting equity,
foreign-account values, model-specific values and unsupported equity currencies
block initialization. Other account tags remain raw evidence: legitimate text
such as `SettledCashByDate` or `$LEDGER-Currency` is not parsed as money.
Seed cash remains observed equity minus the observed position value; no ledger
cash tag substitutes for the broker's equity.

Its dedicated `shadow-data` volume holds `/app/shadow/shadow.sqlite3`.
Inputs are staged before simulation, and portfolio changes, decisions and upload
outbox entries are committed transactionally. Re-uploading an acknowledged cycle
does not create another trade. Do not remove this volume to "fix" a worker.
The local spool is bounded to 1 GiB and each staged frame to 16 MiB; reaching a
limit blocks visibly rather than dropping predecessor evidence.
The cloud's private shadow run/event/checkpoint/health tables hold replicated
research evidence, not live `portfolio_positions` or `trade_history` changes.

The schedule follows actual NYSE sessions, including holidays, daylight-saving
changes and early closes. Missing intervals cannot be backfilled with today's
quotes. A blocked run retains its state and reason; a deliberate new run uses
fresh actual-account evidence instead of pretending the missing path is known.
Normal restarts do not request a new run.

For an operator-approved fresh start after investigating a blocked run, stop the
existing worker first so there is only one writer to its SQLite store:

```bash
docker compose --profile observe stop shadow-worker
docker compose --profile observe run --rm --no-deps shadow-worker \
  python shadow_worker.py --once --new-run
docker compose --profile observe up -d --no-deps shadow-worker
```

Run the diagnostic during a regular exchange session. Inspect its exit/result
and the recorded health before treating the new run as usable. This starts
another **hypothetical** portfolio; it neither repairs nor trades the real
account. Connection failures, acquisition limits and unsupported strategy paths
remain reasons to investigate, not permission to loosen validation.

## Independent daily/weekly supervision

Enable `.github/workflows/intraday_research_review.yml` on the repository's
default branch. Configure the GitHub Actions secret `BWS_ACCESS_TOKEN` with
read access to Bitwarden project `ai-trading-bot`. The workflow imports
`SUPABASE_URL`, `INTRADAY_SUPABASE_KEY`, `SUPABASE_KEY` (independent operational
diagnostics), `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` from that project,
using the already approved recipients. Its built-in `GITHUB_TOKEN` needs
`issues: write`; no broker credentials are needed. Host provisioning does not
configure the Actions bootstrap token.

The cloud workflow runs every 15 minutes independently of the production host.
It checks actual broker/quote/decision progress as well as heartbeat age, sends
failure/recovery notifications, and maintains persistent GitHub incidents.
Daily summaries become due 30 minutes after the exchange closes. Weekly reports
cover the preceding completed week and become due Monday morning New York time.
The weekly due time is 08:00 New York time.
GitHub cron is best effort, not an exact-minute alarm.

Reports, period identities and per-recipient delivery receipts are persistent.
Delayed executions catch up due reports; successful recipients are not normally
notified again just because another recipient failed. A crash after sending but
before saving the receipt can still duplicate a Telegram message. Database
outages use GitHub incidents rather than requiring an unavailable database to
record its own failure.

Reports distinguish missing evidence from zero profit and hypothetical results
from real account performance. Weekly figures describe recorded decisions,
costs, completed positions, sampled account declines and profit concentration
(how much comes from only a few names). Without a separately frozen calibration
result, the report says no calibrated recommendation is available. Neither the
reporter nor the simulator changes live settings or restarts trading.

These summaries do not mean an assistant remains active between conversations.
The separate calibration worker performs automatic research and brings proposals
to the inbox without requiring an assistant session. Search starts when its
configured usable training window exists; the default adds five predeclared
future evaluation sessions. Deployment readiness depends on explicit evidence
and risk limits, not elapsed calendar weeks. Few completed positions, repeated
gaps or one market regime can still leave every result exploratory.

## Using the dashboard

The **Decision-only portfolio** panel shows separate shadow-worker health,
hypothetical portfolio runs and saved daily/weekly reports. Its export is a
labelled shadow dataset, not the raw-observation export or an actual-account
decision recording. Missing reports, missing progress and blocked initialization
are not evidence that the process is healthy. A stopped or blocked simulator
must be investigated before counting its calendar time as useful research.

Select recorded start/end sessions and run the comparison. No starting-capital
override is offered. The server saves the job, returns its identifier and runs
it in the background; the page polls its status. Closing the page does not
cancel server work. A web restart marks interrupted jobs failed rather than
leaving them appearing successful.

Read the starting-account summary and limitations before interpreting the
comparison. A positive **variant minus baseline** difference means removing
the veto increased final modelled account value in that window. A negative
difference means it reduced it. Account value includes unsold positions, and
costs include commission and adverse execution-price assumptions.

Rejected runs retain their reason. Do not narrow or alter a dataset just to
hide a recording gap or an unsupported trade. The JSON export preserves a
replayable dataset only when the required inputs pass validation.

The dashboard comparison tests one change, not every possible strategy.
The research inbox automates bounded multi-candidate selection and future
evaluation; the offline CLI remains available below. Neither manual nor automatic
research jobs can write live parameters or place orders.

**Export raw observations** preserves evidence even when no replay is possible.
It includes raw records, a SHA-256 content fingerprint, counts of dates/runs/
quotes, known incomplete events, an estimated first retention expiry and the
replay-input rejection reason. A raw bundle is not a validated strategy dataset.
**Export recorded inputs** still requires the existing strict validation.
Both exports contain private account information: store them securely and do
not commit them or upload them to third-party systems.

## What is recorded

| Data | Purpose |
|---|---|
| Effective configuration and build revision | Prevent a result from silently combining different rules |
| Buy/monitor/EOD inputs and phase observations | Preserve what the bot could know, including uncomputed/failed gates |
| Unfiltered candidate membership and source-labelled prices | Include vetoed/capacity-skipped names and possible alternative holdings |
| Actual positions, prior trades/fills and protective orders | Initialize both variants from the same real account |
| Decisions, fills and protection changes | Audit actual activity without forcing simulated trades to follow it |
| Errors, pending/dropped records and heartbeat | Make incomplete recording visible |

A background worker handles database/network work and retries outside live
decision-making. It never uses the live IB connection from another thread.
Additional candidate sampling uses FMP and retains its source and observation
time; FMP is not renamed “IBKR.” Existing live mark/quote lookups are recorded
without making another blocking broker request just for research.

Producer errors update in-memory health without logging on the trading thread.
Graceful shutdown drains accepted events into the bounded disk spool and counts
capacity failures as dropped records. Pending starting-snapshot enrichment
resumes after restart with the original configuration and observation times.
Forced termination can still lose events that have not reached the spool.

Prices are samples, not a continuous tick history. Five-minute sampling can
miss intervening stop touches. Unknown broker trailing protection cannot be
reconstructed from a position's highest price; an unresolved initial order
state rejects the comparison. Rotation/manual activity and other unsupported
paths also reject it rather than being silently omitted.

Initial protection must use supported GTC (good-until-cancelled) orders and
supported OCA (one-cancels-all) grouping. DAY orders, which expire at the end of
the trading session, are rejected rather than incorrectly carried into tomorrow.
Every recorded monitor cycle needs its preceding buy attempt, including an
explicit outcome when an early gate prevents further evaluation. An opening buy
check alone does not establish complete intraday coverage.

Missing quotes for unrelated retained symbols and identified technical gaps
outside regular trading hours are disclosed as coverage warnings, not treated
as missing trading decisions. Required holding/candidate quotes, regular-session
gaps and unexplained sequence gaps remain blocking. Unsupported financial
activity still rejects the comparison even if it occurred overnight.

## Automatic comparisons and data window

With `INTRADAY_AUTO_COMPARE=true`, the web service checks hourly for recorded
decision frames, independently of real-entry permission, and saves at
most one automatic attempt per week over up to 30 calendar days ending in the
preceding week. Insufficient data produces a saved rejection, not a strategy
recommendation. Manual requests support up to 93 calendar days by default, with
50,000-row and 64-MiB input limits to protect the web worker. Evaluate longer
retained history as multiple windows; **do not sum their dollar differences as
one continuous portfolio backtest** because each has its own recorded start.
The independent cloud watchdog separately owns daily and weekly
observation/shadow reports. Manual actual-decision comparisons remain available
for valid historical data.

Retain 365 days of raw observations initially. Check coverage and decision
reproduction from the first complete session rather than waiting weeks to find
failures. Automatic exploratory comparisons begin once their training window is
available. Deployment requires the operator's predeclared evidence and risk
policy plus separate approval; a larger sample and varied market conditions
remain important even after numerical gates pass. Partial exits do not count as
separate positions, and many observations from one date are not independent
market conditions.

Keep evaluation data that was not used to select parameters. Examine losses
and maximum account decline as well as profit, and whether one or two winners
explain the difference. There is no automated “profitable enough” promotion.
Every live change requires human approval.

## Offline calibration: select first, evaluate later

Use **Export recorded inputs** for actual recorded decisions, or **Export shadow
decision inputs** for the hypothetical portfolio. Select two distinct
full-session windows from the same compatible account/configuration and research
run. The earlier window is training data; the later *holdout* is evaluation data
that must not have informed the parameter choice. Schema 2 records actual-account
decisions; schema 3 identifies shadow inputs and their hypothetical checkpoint
provenance. Raw observer bundles, synthetic cash-only examples, gaps and
unsupported activity remain rejected. The decision-only worker supplies the
missing inputs while new real buys are disabled; observation without that worker
still cannot calibrate the strategy.

Keep private inputs and output files outside the repository. A candidate file
contains 1-32 explicitly named experiments; the recorded-configuration baseline
is added automatically. For example, this is an **experiment definition**, not
a recommendation to change live thresholds:

```json
{
  "experiments": [
    {"name": "without-veto", "disable_ai_veto": true},
    {"name": "score70", "decision_config": {"min_trigger_score": 70}},
    {"name": "no-scale-out", "exit_config": {"scale_out_enabled": false}}
  ]
}
```

Run from the repository using the Python environment that supports the replay:

```bash
python3 research/calibrate_intraday.py select \
  /private/research/training.json /private/research/candidates.json \
  --output /private/research/selection.json

python3 research/calibrate_intraday.py evaluate \
  /private/research/selection.json /private/research/holdout.json \
  --output /private/research/evaluation.json
```

Replace `/private/research` with your secure local directory. No credentials or
broker connection are needed. Output files are written atomically, and existing
files are refused unless `--overwrite` is explicitly supplied.

For shadow exports, use the **same research image and nonsecret strategy
environment** that created the run. Python/package versions and rule globals
are included in the checkpoint fingerprint. Worker and dashboard install the
same `requirements-shadow.txt`; an arbitrary local Python environment may
correctly reject the export as incompatible. The build publishes a commit-SHA
shadow image tag; preserve the deployed image digest rather than relying on
mutable `latest`. Run calibration with networking disabled, mounting only the
private input/output directory. Broker/database/API credentials are unnecessary.

To include a completed frozen evaluation in later weekly reports, explicitly
archive its output from a secure environment with `SUPABASE_URL` and the
server-side `INTRADAY_SUPABASE_KEY` configured:

```bash
python3 research/intraday_reporting.py \
  --save-calibration /private/research/evaluation.json
```

This validates and saves an existing evaluation to private research storage; it
does not select parameters, re-run a sweep or apply anything to trading.

`select` ranks after-cost final account value against the simulated
recorded-configuration baseline using **training data only**. Ties prefer no
change. It freezes the winner, all tested candidates/rejections, configuration
differences, source dataset fingerprint and replay-engine fingerprint.
`evaluate` verifies that plan and runs only the recorded baseline and frozen
winner on the holdout, never a new sweep. The holdout must be strictly later,
including quote/snapshot observation timestamps; account, configuration, costs
and engine must remain compatible. Do not relabel inputs to bypass a rejection.

Supported experimental fields are deliberately limited to per-instance settings
the replay actually uses:

| Group | Fields | Accepted values |
|---|---|---|
| Top level | `disable_ai_veto` | Boolean; ranking and other gates remain intact |
| `decision_config` | `min_trigger_score`, `min_pre_breakout_score`, `min_relaxed_trigger_score` | 0-100 |
| `decision_config` | `min_vol_surge_gate` | 0-10 in the engine's ratio units |
| `decision_config` | `max_pivot_extension`, `max_pivot_breakdown`, `max_pre_breakout_pivot_dist` | 0-0.2 as fractions |
| `exit_config` | `scale_out_enabled` | Boolean |
| `exit_config` | `scale_out_trigger_pct`, `scale_out_fraction` | 0.005-0.5 and 0.01-0.99, respectively |
| `exit_config` | `armed_exit_deadline_hours` | 0.25-24 hours |

These are research input bounds, not changed production defaults. Shared
Prove-It bands, the profit-trail ladder, power-hold rules, slots/sizing, costs,
cooling-off and rotation are not exposed as tunable fields. Unsupported
overrides, duplicate names, non-finite numbers and unknown fields are errors.

Reports show completed **positions**, not individual partial-sale rows; distinct
sessions; after-cost account change including holdings; sampled peak-to-trough
decline; realised losses; and contributions from individual tickers. Removing
the top one/three ticker contributions is an attribution sensitivity check,
not a new simulation with replacement trades. Window results cannot be summed into a continuous portfolio. Actual-decision
windows begin from their recorded account; shadow windows begin from the
verified baseline hypothetical checkpoint. Both holdout variants inherit that
same checkpoint. Within an automatic campaign, progressive evaluations replay
the whole fixed window from that start, preserving each strategy's evolving
holdings rather than resetting them every session.

Fewer than 30 completed positions produces an exploratory-sample warning, not
an automatic rejection or approval gate. More candidate trials increase the
chance of finding a lucky winner. File fingerprints detect accidental changes,
not prior human inspection of holdout data; reusing the holdout for new choices
invalidates its independence. The tool never declares a strategy approved or
profitable, writes settings, or switches deployment back to live mode.

See `decisions/2026-09-30_observer-and-calibration-harness.md` and
`decisions/2026-09-30_shadow-decisions-and-supervised-research.md` for why these
boundaries are required.

## Storage and credentials

`INTRADAY_SAMPLE_SECONDS=300` and `INTRADAY_MAX_SYMBOLS=250` can produce roughly
4.9 million individual quote observations in 252 full trading sessions at the
cap, before accounting for snapshots and audit records. These are observations
inside event payloads, not necessarily individual database rows. Measure actual
database/disk growth; do not assume a free Supabase tier can hold a year.

The collector maintains a rolling raw-data horizon through
`purge_intraday_capture`. Candidate membership follows that horizon so an
expired live trigger can still be valued in an alternative portfolio.
Collector spool limits and unavailable prices remain visible failures.

Raw events, saved comparison results, calibration decisions/artifacts and
simulated portfolio/reporting state are all excluded from weekly trading-state
backups. Preserve needed research evidence independently and export important
input datasets before their retention expires. A result's fingerprint
identifies its input but cannot recover deleted observations.
See `decisions/2026-10-04_trading-only-backup-scope.md` for the backup boundary.

Shadow runs are not automatically prefix-pruned: their actual seed and every
predecessor frame are required to reproduce later hypothetical holdings.
Full-prefix export is bounded to 50,000 engine events and 64 MiB; these are
explicit capacity limits, not a promise of unlimited history. Archive complete
private datasets and retain the matching image/environment. Never delete an
active run's early frames to make a later export fit.
The dashboard allows five minutes for full-prefix export, rather than the
30-second timeout used for ordinary status requests.

See [configuration](configuration.md), [backups](backups.md), and
`decisions/2026-09-30_intraday-capture-and-approved-research.md` for why.

## Quote endpoint compatibility and budgets

Execution recording and the independent observer use `Recorder.sample()`;
the shadow worker uses `PublicMarketData.quotes()`. Both share the injected,
storage-free HTTP transport in `quote_transport.py`.
It first requests FMP `stable/batch-quote` in groups of at most 100. An explicit
HTTP 402 from that endpoint selects `stable/quote`, one symbol per request,
for the remainder of that consumer instance. A restart probes batch access again.
Authentication, rate-limit, network and server failures never select a different
endpoint. An individual request failure ends that round instead of repeating the
failure across the retained universe. This fixes subscription compatibility
without changing trading rules or requiring a subscription upgrade.

Internal dotted A/B share-class symbols are translated only at the FMP boundary:
`MOG.A` requests `MOG-A`, and `BRK.B` requests `BRK-B`. Existing hyphenated names
and exchange suffixes such as `.L` or `.AX` are unchanged. Request evidence carries
an explicit provider-to-internal `symbol_map`; accepted quotes retain the
internal ticker plus `provider_symbol`. The shadow quote also retains its raw,
unmodified provider row. Recorder samples include the credential-free
`quote_requests` evidence. Wrong share classes, unrequested symbols and ambiguous
inputs such as both `MOG.A` and `MOG-A` are not merged or accepted as complete.
Shadow daily-history requests use the same translation while keeping history
cache keys in the internal spelling. Database and brokerage identifiers are
not renamed, and failed requests are never "fixed" by dropping a stock.

Each round has a dispatch budget of the smallest of **30 seconds**, half
`INTRADAY_SAMPLE_SECONDS`, and half `INTRADAY_MAX_QUOTE_AGE_SECONDS`; at most
**N + 1 HTTP requests** are dispatched for N retained symbols. There are no
retries or redirects. Each request's connect/read timeouts are capped at 3/10
seconds respectively, with each additionally limited to half the remaining
round budget. Recorder shutdown is checked between requests. Expired-budget responses
are not recorded, and further requests stop; the recorder's next round starts with the first
unserved symbol rather than repeatedly favoring the alphabet's beginning.
These are cooperative dispatch/socket timeout limits, not a hard operating-system
deadline: DNS resolution and a server continuously trickling response bytes can
outlast a Requests socket timeout.

Each recorded quote names its actual `stable/batch-quote` or `stable/quote`
endpoint, retains FMP's provider timestamp, and undergoes the same positive,
finite price and freshness validation. Missing/stale/timestampless quotes are
never fabricated. Duplicate and unrequested rows are not recorded. Samples
include the attempted endpoints, selection reason, request count/limit and time
budget. Health includes the selected endpoint and reason; successful HTTP-402
fallback emits an informational diagnostic, not a coverage error.
Unserved symbols remain in `missing_quotes` with `complete=false`; exhausted
budgets explicitly report `quote_budget_exhausted`. Retention and symbol-cap
guards still apply to the entire requested universe.

Shadow frames are all-or-nothing: any transport error, incomplete coverage,
duplicate/unrequested row, invalid price or missing/invalid provider timestamp
blocks the frame. Their request evidence retains endpoint, parameters without
the API key, request/receipt times, HTTP status, fallback reason and round limits;
each quote retains its raw provider row. Retrieval does not relabel an old quote
as current: Friday timestamps stay Friday on Sunday, and frame acquisition
rejects stale/future provider quotes under its unchanged 600-second age gate.
Completed shadow evidence remains usable by same-window replay and calibration.
