# Retired Code Registry

Every rule, constant or code path deliberately deleted from this repository is
recorded here **before** it is removed.

## 2026-10-06: Uniform fifteen-minute research watchdog schedule

The active `*/15 * * * *` schedule in
`.github/workflows/intraday_research_review.yml` is replaced, not the watchdog
itself. Its contract test in `tests/test_intraday_reporting.py` and schedule
description in `research/intraday_reporting.py` now describe five-minute weekday
checks during 13:00-20:59 UTC and fifteen-minute checks otherwise.
The old schedule ran the observation-only cloud reporter; it never placed live
trades. Faster checks reduce alert polling delay around the open and during the
session without changing worker recovery or evidence thresholds.
See `decisions/2026-10-06_faster-research-watchdog.md`.
Restore the former schedule with
`git show c736676:.github/workflows/intraday_research_review.yml` (and the matching
test/report description from that commit) if runner or API load proves
unacceptable. Fifteen-minute monitoring outside the faster window is retained.

## Why this file exists

Deleted code is invisible. Once a rule is gone, the only trace left is a diff
buried in history that nobody will find, and the reasoning behind the removal
disappears entirely. Six months later someone re-derives the same idea, ships
it again, and reintroduces a bug that was already paid for once.

An ADR records *why a decision was made*. This registry records *what was
physically removed, where it lived, and how to bring it back with its original
context intact.* They are complementary — an ADR without this registry leaves
you knowing a rule was retired but not what its code actually did.

## Rules for this file

1. **Log before deleting.** The entry lands in the same commit as the removal.
2. **Link the ADR.** Every entry cites the decision that authorised it.
3. **Record the restore path.** Name the commit that still contains the code, so
   `git show <sha>:<path>` recovers it.
4. **Never delete entries.** This file only grows. A rule retired twice gets two
   entries.
5. **State what would have to be true to restore it.** "Never" is rarely the
   honest answer; "if the screener starts producing X" usually is.

---

## 2026-10-05 - Manual-only recovery after research input gaps

**Replaced, not removed:** the immediate permanent-block/manual-replacement
path for recoverable acquisition failures in `Worker.tick()` (`shadow_worker.py`)
and its recovery metadata handling in `shadow_store.py`. Brief failures can
retry within the existing observation deadline; a genuinely missing interval
still ends that experiment, but an explicitly labelled replacement can start
automatically with fresh evidence. Integrity and strategy-compatibility failures
remain operator-blocked. Separate runs are never joined into one experiment.

The previous path was active and stopped the October 5 RS experiment before any
simulated decision was recorded. Patch 114 repaired initial startup but retained
manual-only recovery after later gaps. The operator explicitly authorized
automatic replacement and notification on October 5. No live order logic is
retired or changed.

Restore the prior policy from `git show 8402d0b:shadow_worker.py` and
`git show 8402d0b:shadow_store.py`, with the corresponding startup/worker tests,
only if research replacements must again require individual approval.
See `decisions/2026-10-05_resilient-research-recovery.md`.

## 2026-10-05 - Shadow run creation moved after startup input validation

> The manual-only established-run recovery policy described in this earlier
> entry was subsequently replaced by the same-day resilience decision above.
> Its first-frame validation and preservation of failed evidence still apply.

**Relocated, not removed:** `Worker.tick()` in `shadow_worker.py` no longer calls
`ShadowStore.create_run()` immediately after observing the account seed. Run
creation now follows acquisition and engine validation of the complete first
frame. Startup `InputGap` errors wait visibly and retry with a newly observed
seed; they do not create or supersede a simulated experiment. Existing runs
still block permanently on missing inputs and require explicit replacement.

The old path was active. On 2026-10-05 it created a run at 09:30:13 ET and
blocked its first cycle on `RS: stale/future provider quote` a second later.
Both independent collectors rejected RS at 09:30 and recorded valid RS data by
09:35, but the simulated worker remained blocked all day. No simulated decision
or fill was committed. These are research-only paths; real trading is unchanged.

The new regression coverage is in `tests/test_shadow_startup.py`, alongside
the existing `tests/test_shadow_worker.py`. Restore the former sequence with
`git show 507b135:shadow_worker.py` and its corresponding tests only if recording
a permanently failed experiment before its first valid input becomes an explicit
requirement. Do not restore it merely to conceal missing quotes.
See `decisions/2026-10-05_shadow-startup-input-readiness.md`.

## 2026-10-04 - Research tables removed from weekly backup scope

**Retired from backup only, not deleted from Supabase:** `exit_shadow_log`,
`intraday_replay_runs`, `intraday_calibration_settings`,
`intraday_calibration_proposals`, `intraday_calibration_events`,
`intraday_research_calibration_artifacts`, `intraday_research_delivery_receipts`,
`intraday_research_incidents`, `intraday_research_reporting_state`,
`intraday_research_reports`, `intraday_shadow_checkpoints`,
`intraday_shadow_events`, `intraday_shadow_health`, `intraday_shadow_runs`.
Their required entries in `supabase_backup.TABLES` move to `NOT_BACKED_UP`.
The exporter's preference for `INTRADAY_SUPABASE_KEY` and the backup wrapper's
requirement for that vault secret are removed; the research watchdog still
requires it. Tests live in `tests/test_supabase_backup.py` and
`tests/test_supabase_backup_bws.py`; the shared loader is in
`scripts/run_intraday_reporting_bws.py`.

These active paths coupled trading-state backups to private research tables.
The operator reports recurring grant failures; local read-only requests with
the ordinary key returned authorization errors for 13 research tables.
Successful historical exports of these tables were not established here.
The operator explicitly excludes calibration and benchmarking, accepting that
future weekly snapshots cannot restore those datasets. Existing archives and
all research collection/writer paths remain intact.

Restore the former scope and credential behavior from
`git show 90ab261:supabase_backup.py` and the same commit's
`scripts/run_supabase_backup_bws.py`, shared loader and tests. Reintroduce
research backups only with an explicit retention/recovery requirement and
verified read grants, preferably in an independent job.
See `decisions/2026-10-04_trading-only-backup-scope.md`.

## 2026-10-04 - Invalid trigger ordering and provider ticker assumptions

The `InputProducer.frame()` query in `shadow_inputs.py` no longer orders
`daily_triggers` by a nonexistent `id`. It uses the actual composite key
`(triggered_at, ticker)` across every page. The active old query failed the
production preflight with PostgreSQL `42703`; no hypothetical run had yet been
created. No database column or trading rule is removed.

Direct use of internal dotted A/B share-class tickers as FMP symbols is replaced
in `quote_transport.fetch_quotes()` and `PublicMarketData.history()`.
`Recorder.sample()` and `PublicMarketData.quotes()` resolve responses through
the request's explicit provider-to-internal mapping, keeping the raw provider
identity and timestamps. This is an identifier translation, not a ticker rename
in the database or broker. Production returned HTTP 402 for `MOG.A` but HTTP 200
for `MOG-A`; the former stopped the 104-symbol quote probe after 64 successes.

Restore reference: commit `7967a6d`, specifically `shadow_inputs.py`,
`quote_transport.py`, `intraday_capture.py` and their corresponding tests.
An `id` ordering would require a real schema change; direct symbol forwarding
would require FMP to accept every internal spelling. Neither assumption is
currently true. See `docs/intraday_research.md` for the repaired input contract.

## 2026-10-04 - Shadow seed numeric sweep and quote transport relocation

`build_seed()` in `shadow_inputs.py` no longer converts every USD account tag
to a number. That active path prevented initialization when IBKR supplied its
legitimate text ledger and settlement tags; only the required account-wide USD
`NetLiquidation` is numeric input. Account/model scope, uniqueness and finite
positive equity remain mandatory. No live trading rule or broker order path is
removed; the failed path never produced a shadow seed in the observed incident.

The HTTP request loop in `Recorder.sample()` (`intraday_capture.py`) is
**relocated**, not retired, to `quote_transport.fetch_quotes()`. Both that recorder
and `PublicMarketData.quotes()` (`shadow_inputs.py`) use its bounded HTTP-402-only
individual-quote fallback. The shadow-only batch requirement is retired because
the active subscription rejects the batch endpoint. Quote validation remains in
each consumer, preserving recorder gaps and shadow all-or-nothing frames.
Regression coverage lives in `tests/test_intraday_capture.py` and
`tests/test_shadow_worker.py`. These are operational bug fixes, not strategy
decisions. Restore reference: `git show a48a0c6:shadow_inputs.py` and
`git show a48a0c6:intraday_capture.py`. Restoring the numeric sweep would require
an account schema guaranteed to contain only numbers; restoring batch-only
shadow fetching would require guaranteed batch entitlement.

## 2026-10-04 - Root-only CI dependency constraint

The active root-only test environment in
`.github/workflows/daily_screener.yml` and the corresponding unconditional
web-import prohibition in `tests/test_ci_import_hygiene.py` are replaced by
`requirements-test.txt`. The same named import guard now rejects **undeclared**
web dependencies; runtime separation and pure-pricing checks survive.
No trading rule or test coverage is removed.

The latest reproduced scheduled failure stopped at collection because FastAPI
was missing, preventing all three screening stages. Actual API tests require
that framework; installing it for tests is different from adding it to execution
images. See `decisions/2026-10-04_calibration-readiness-and-exploratory-policy.md`.
Restore references: `git show a48a0c6:.github/workflows/daily_screener.yml` and
`git show a48a0c6:tests/test_ci_import_hygiene.py`. A root-only gate could return
only if API coverage had a separate complete environment, not by silently
skipping those tests.

## 2026-10-04 - Relocated backup credentials and private exit-shadow writes

**Relocated, not retired:** `.github/workflows/weekly_supabase_backup.yml` no
longer loads `SUPABASE_URL`, `SUPABASE_KEY`, `INTRADAY_SUPABASE_KEY`,
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` from individual Actions secrets.
The backup uses the existing Bitwarden project loader through
`scripts/run_supabase_backup_bws.py`; its only application-credential bootstrap
secret is `BWS_ACCESS_TOKEN`. Deployment SSH secrets remain separate.

The ordinary-client `client.table("exit_shadow_log").insert(...)` path in
`monitoring.py` is replaced by an isolated private writer in
`exit_shadow_store.py`. The special suppression of `exit_shadow_log`, `PGRST`
and `42P01` errors is removed: failed research writes remain nonfatal but emit
credential-safe diagnostics. Candidate calculations remain in `exit_shadow.py`;
no live exit rule or order path is retired or changed.

Both paths were active. The September 27 backup failed on the missing table;
October 4 inspection showed the ordinary writer used an anon credential while
the table's policy permits only service_role. Successful historic shadow writes
have not been established. The general trading client is deliberately not
upgraded and row-level security is not weakened.

Restore with `git show a48a0c6:monitoring.py` and
`git show a48a0c6:.github/workflows/weekly_supabase_backup.yml`. Restore direct
Actions credentials only with an explicit alternative rotation/source-of-truth
design; restore the ordinary writer only if it has deliberately scoped access,
never by making private observations public. Silent research-write failure
must not return. See
`decisions/2026-10-04_backup-vault-and-private-exit-shadow.md`.

## 2026-10-04 - Replaced batch-only intraday quote transport

`Recorder.sample()` in `intraday_capture.py` no longer requires FMP
`stable/batch-quote` access. The batch path is **retained**, with individual
`stable/quote` requests used only after an explicit batch HTTP 402. The old
unbounded sum of chunk request timeouts is replaced by a per-round time/request
budget; timestamp, symbol-limit and retention checks remain.

Batch-only collection was active, but production returned HTTP 402 for SPY
while the same credential returned a valid timestamped individual SPY quote.
This blocked recorded research prices, not real order placement. Historical
successful use of that batch endpoint has not been established. This is a
subscription-compatibility bug fix, not a trading decision; no ADR is required.
See [recording transport](intraday_research.md#quote-endpoint-compatibility-and-budgets).
The old implementation and tests are recoverable with
`git show 2128a1a3c48432852f4bdd528ab47219c7e05bc9:intraday_capture.py`
and the same commit's `tests/test_intraday_capture.py`.
Restore batch-only behavior only if batch entitlement is guaranteed for every
collector deployment; bounded worker time and explicit missing coverage must
still remain.

## 2026-10-04 - Relocated research-watchdog credential loading

**Relocated, not retired:** the direct GitHub Actions secret mappings for
`SUPABASE_URL`, `SUPABASE_KEY`, `INTRADAY_SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_IDS` in `.github/workflows/intraday_research_review.yml`.
Their new source is the workflow's in-memory Bitwarden loader,
`scripts/run_intraday_reporting_bws.py`, using the no-cache configuration
`scripts/bws_ci.toml` and the Actions `BWS_ACCESS_TOKEN` bootstrap credential;
the consumer variable
names, credentials' purpose, collection checks and Telegram alert functionality
remain in place. This does not change any broker order or trading rule.

The direct mappings were active but the required values were present in
Bitwarden and never loaded by this workflow. GitHub Actions run `37171852451`,
at 2026-10-03 22:42 America/New_York, failed before collection checks could run.
Keeping independent, unsynchronised credential sources left the watchdog unable
to evaluate recording health. The credentials themselves are not deleted or
copied into the repository.

Restore the old mapping with
`git show 2128a1a:.github/workflows/intraday_research_review.yml`.
Restore it only if the operator deliberately returns to synchronised GitHub
Actions secrets and establishes protection against drift from the vault.
This is an operational credential-loading repair, not retirement of the
watchdog's checks or alerting. See
`decisions/2026-10-04_watchdog-bitwarden-bootstrap.md` for the decision.

## 2026-10-04 - Replaced observer legacy account subscriptions

**Replaced:** `ReadOnlyBroker.connect()`'s implicit SDK legacy-account bootstrap
and `snapshot()`'s `reqAccountUpdates(False/True)` cycle, `account_value()` and
`portfolio_value()` event capture in `intraday_observer.py`, with their fake
legacy-download implementation in `tests/test_intraday_observer.py`.
These paths were active but repeatedly failed: the observer recorded broker
error 2100 and account-download timeouts, not completed snapshots. No real
trades were submitted by the observer.

The legacy subscription is shared with the protective execution client;
`readonly=True` does not disable it. Account values are now captured from
request-ID-scoped multi-account callbacks, with cancellation after each download.
Portfolio valuation is replaced, not removed: separate account/contract-scoped
read-only PnL callbacks provide broker position value and quantity, without
claiming a legacy `updatePortfolio` download or using its cached marks.
Missing or invalid responses still fail the snapshot. This is a protocol bug
repair, not a trading-rule decision; see `docs/intraday_research.md`.

Restore the original implementation with
`git show 2128a1a:intraday_observer.py` (and the matching test path).
Restoration would require proof that account subscriptions cannot displace the
execution client's subscription and that every snapshot obtains a fresh,
account-isolated completion, including reconnects and concurrent SDK bootstrap.

## 2026-10-01 - Replaced deployment-only trading permission

**Replaced:** the mutually exclusive `TRADING_RUNTIME_MODE=observe|live`
service-selection branches in `scripts/deploy_runtime.sh`, their expectations in
`tests/test_deploy_runtime.py`, and the unconditional execution-agent startup path
in `scripts/restart_6am.sh`. Reporting's live-mode research pause is replaced by
continuous research supervision. The mutually exclusive Compose profiles and
`backend/intraday_service.py:automatic_review()`'s live-label prerequisite belong
to that replaced service-selection boundary. These were active paths: Supabase agent logs
showed startup at 06:00 and real buy evaluation at 09:30 on October 1, despite
the earlier observation-only deployment. The installed cron invocation itself
could not be inspected because SSH was unavailable.

The old observation deployment stopped real risk management, while an independent
morning restart could restore buying. Permission now lives in persistent
`trading_control.py` storage, separately from service startup. Both real protection
and independent research remain running. The legacy mode name remains accepted
for compatibility, not as permission to trade.

The buy-now sentinel's skip-monitoring/sleep path in `execution_agent.py` is
replaced by the normal protection cycle; requesting buys must not suppress real
position management. Discretionary rotation is paused when new entries are
disabled, rather than liquidating a real holding for a forbidden replacement.
Protective exits are not retired or redirected into hypothetical accounting.
`rotate_positions.py`'s manual liquidation path is likewise permission-gated;
its actual protective sale primitives remain in their original modules.

Restore the original paths with `git show b3b4746:<path>`. Reintroduction would
require a demonstrated need for stopping all real protection plus a persistent
entry-permission boundary covering every restart and order entry point.
See `decisions/2026-10-01_dashboard-live-entry-control.md`.

## 2026-09-30 - Relocated strategy declarations and shared market-direction calculation

**Relocated, not retired:** the active configuration declarations below moved
from `execution_agent.py` to import-safe `research_configuration.py`. The live
agent re-exports every name; defaults and environment-variable names are
unchanged. These settings already governed live trading. This is not evidence
that any particular optional rule fired.

- Entry and sizing: `MIN_POSITION_SIZE`, `TRIGGER_LOOKBACK_DAYS`,
  `MAX_PIVOT_EXTENSION`, `MAX_PIVOT_BREAKDOWN`, `MIN_VOL_SURGE_GATE`,
  `MAX_PRE_BREAKOUT_PIVOT_DIST`, `MIN_TRIGGER_SCORE`, `MIN_PRE_BREAKOUT_SCORE`,
  `MIN_RELAXED_TRIGGER_SCORE`, `PRICE_SAFETY_RESERVE`.
- Stops, scale-out and armed exits: `ATR_STOP_MAX_PCT`, `SCALE_OUT_ENABLED`,
  `SCALE_OUT_TRIGGER_PCT`, `SCALE_OUT_FRACTION`, `ARMED_EXIT_TRAIL_PCT`,
  `ARMED_EXIT_DEADLINE_HOURS`.
- Rotation inputs: `RANK_REPLACE_THRESHOLD`, `RANK_REPLACE_FAIL_THRESHOLD`,
  `STALE_EXIT_DAYS`, `STALE_EXIT_MIN_DAYS_HELD`, `BREAKOUT_VERDICT_MIN_GAIN`,
  `BREAKOUT_VERDICT_MIN_VOL_PCT`.
- Market direction: `MARKET_DIRECTION_FILTER_ENABLED`,
  `MARKET_DIRECTION_SMA_WINDOW`, `MARKET_DIRECTION_TICKERS`,
  `MARKET_DIRECTION_BUFFER_PCT`, `MARKET_DIRECTION_SLOPE_DAYS`,
  `MARKET_DIRECTION_MAX_STALE_DAYS`.

The returned moving-average verdict in `market_regime._index_is_bullish()`
now comes from `market_direction.index_verdict()`, shared with the shadow
input producer. The live wrapper still fetches its data and logs its comparison;
the helper performs the average/buffer/slope calculation and rejects invalid,
duplicate, stale or future-dated history.

Importing the live daemon merely to obtain these settings initializes logging,
network-session and brokerage-related dependencies. The shadow worker instead
needs the same settings without those capabilities. The extraction and shared
calculation prevent an independent research copy from drifting away from the
live rules. See
`decisions/2026-09-30_shadow-decisions-and-supervised-research.md`.

Restore the original declarations and wrapper with
`git show e1860c3:execution_agent.py` and
`git show e1860c3:market_regime.py`. Do not restore daemon-only configuration
while a broker-free consumer needs it; reversing this relocation requires
another import-safe, single-source configuration boundary. Regression coverage
includes `tests/test_shadow_worker.py`, `tests/test_market_direction.py`,
`tests/test_buy_decision_golden.py`, and `tests/test_exit_core.py`.

---

---

## 2026-09-30 - Unconditional live-agent startup during deployment

The deployment sequence in `.github/workflows/deploy_to_server.yml` no longer
unconditionally removes and starts `execution-agent`. Runtime selection is
relocated into `scripts/deploy_runtime.sh`: observation mode stops trading
before image updates and selects the read-only observer plus the broker-free
shadow worker. The old path was
active in production; installing an unrelated research update could therefore
restart trading that the operator had deliberately stopped.

Recover it with `git show 4c6800e:.github/workflows/deploy_to_server.yml`.
Do not restore unconditional startup. Explicit `TRADING_RUNTIME_MODE=live`
provides the intentional live path after operator approval. See
`decisions/2026-09-30_observer-and-calibration-harness.md`.

> **2026-10-01 qualification:** The previous paragraph records the September 30
> replacement, not current operating instructions. `TRADING_RUNTIME_MODE=live`
> no longer authorizes entries. Persistent dashboard permission now controls
> new real buys while protection and research run together; see the October 1
> entry above and `decisions/2026-10-01_dashboard-live-entry-control.md`.

---

## 2026-09-30 - Share-only reconciliation and scale-out resizing after unexplained fills

`reconcile_with_ibkr()` in `reconciliation.py` no longer overwrites the ledger's
share count merely because broker quantity differs. `execute_scale_out()` in
`selling.py` no longer silently recomputes its fraction from a smaller holding
before submission. These active paths could erase an unbooked partial sale and
permit another scale-out: an offline case sold 9 of 30, then protection sold 5,
then reconciliation reset the ledger to 16 without recording either execution.
Whether this sequence occurred live is unknown.

The mismatch now preserves the recorded lot and requires accounting review;
scale-outs require exact quantity agreement before and after cancellation.
Recover the old paths with `git show a608dd9:reconciliation.py` and
`git show a608dd9:selling.py`. Restore automatic quantity correction only when
validated executions or corporate-action evidence explain the adjustment and
the accounting is recorded without allowing a repeated partial sale.
See `decisions/2026-09-30_broker-confirmed-sell-safety.md` and
`tests/test_sell_safety.py`.

---

## 2026-09-30 - Unscoped Flex aggregate as automatic closing authority

`reconcile_with_ibkr()` in `reconciliation.py` no longer automatically archives
a long using the aggregate returned by `fetch_trade_confirms_for_ticker()`.
That report path combines symbol-matching sales without selected-account or
current-lot time boundaries; its timezone-less timestamps cannot establish an
exact entry/scale-out cutoff. Even a matching total quantity is insufficient
evidence of the right round trip.

The Flex fetch/parser in `flex_query_sync.py` is retained for diagnosis, not
deleted. A nonempty unscoped result now leaves the ledger intact with an
explicit manual-review alert instead of substituting a guessed quote.
The fallback was active; whether it handled SHIP is unproven. The duplicate-sale
regression shows that a 2,008-share aggregate cannot price a 1,004-share close.
Recover the old assignment from `git show a608dd9:reconciliation.py`.
Restore automatic use only after the report supplies validated account,
contract, execution identities and timezone-aware current-lot boundaries.
See `decisions/2026-09-30_broker-confirmed-sell-safety.md` and
`tests/test_sell_safety.py`.

---

## 2026-09-30 - Unconfirmed sell replacement and independently transmitted exit legs

The implementations of `cancel_ticker_sell_orders()` (`orders.py`) and
`_cancel_existing_sells()` (`force_sell.py`) no longer treat a cancellation
request as a confirmed cancellation or swallow cancellation failures.
Manual managed exits inherit the same account-scoped protection. The behavior
is relocated to `broker_positions.cancel_confirmed_sells()`, which blocks
replacement on timeout, unknown state, or an order owned by another client.

`place_protective_stops()` and `place_oca_exit()` no longer transmit the first
leg independently or reuse timestamp-only group identifiers. Both legs are
staged in a unique OCA group before transmission. All sell submission paths
require fresh positive broker inventory, not only Supabase's recorded shares.
`execute_sell()` no longer treats absence from a positive-only holdings map
as sufficient proof of its own successful fill.

These paths were active. The SHIP fill ledger proves two full disposals of one
long, but the exact submission path and whether the cancellation/transmission
races fired in that incident are unknown. The changes remove independently
reproducible ways to create or conceal a short; they do not claim a recovered
historical stack trace.

Recover the original implementations with `git show a608dd9:orders.py`,
`git show a608dd9:selling.py`, `git show a608dd9:force_sell.py`, and
`git show a608dd9:managed_exit.py`. Related tests live in
`tests/test_oca_helpers.py`, `tests/test_scale_out.py`,
`tests/test_oca_managed_exit.py`, and `tests/test_hard_stop.py`.
`managed_exit.archive()` no longer prices a close from the last symbol-matching
fill or a quote, nor treats a negative holding as successfully flat. Its
accounting is relocated to the own-execution validator in `force_sell.py`;
`tests/test_manual_sell_safety.py` covers these manual paths.
Restore the old behavior only if the broker provides an independently proven
reduce-only guarantee and atomic replacement; ordinary stock SELL orders do
not provide that guarantee.
See `decisions/2026-09-30_broker-confirmed-sell-safety.md`.

---

## 2026-09-30 - Portfolio-only short detection (relocated)

The `reconcile_with_ibkr()` short-alert branch that ran only when
`ib.portfolio()` was populated is replaced by an account-scoped completed
`reqPositions()` inventory check in `broker_positions.py`. The old fallback
filtered `position > 0` before inspecting negatives, hiding shorts on
multi-account logins. It was active; whether that alert ever fired is unknown.
On September 30 the broker confirmed SHIP -1,004 while the database had no
holdings. The fill ledger contains two 1,004-share sales against one purchase;
the exact source of the second order remains unconfirmed.

The alert is **relocated and strengthened**, not removed: unexpected shorts
block new buys and quarantine reconciliation without closing positions or
rewriting their accounting. Recover the old branch from
`git show a608dd9:reconciliation.py` and its prior assumptions from
`git show a608dd9:tests/test_reconcile.py`. Restore it only if every supported
broker feed reliably exposes signed holdings and the fallback blind spot has
been eliminated. See `decisions/2026-09-30_broker-confirmed-sell-safety.md`.

---

## 2026-09-26 — Phase 1 backstop-slack widening (behaviour retired, constant kept)

| | |
|---|---|
| **Identifier** | The Phase 1 application of `PROVE_IT_BACKSTOP_SLACK_PCT` — the `band × (1 − PROVE_IT_BACKSTOP_SLACK_PCT)` term in `hard_stop_price()` |
| **Location** | `exit_rules.py` `hard_stop_price()` (unproven/Phase 1 branch); mirrored in `frontend/src/lib/positionRules.js`, `tests/test_hard_stop.py`, `tests/test_sell_logic.py`, `docs/sell_logic.md`, `docs/configuration.md`, `.env.template`, `README.md` |
| **Status when retired** | Active and firing live — it set the resting overnight/gap floor for every unproven position |
| **ADR** | `decisions/2026-09-26_phase1-broker-primary-stop.md` |

**What it did.** In Phase 1 (unproven), the resting broker `STP` was parked one
backstop slack (1%) **below** the entry-anchored Prove-It band — `entry − 1.99%`
day 0, `entry − 3.97%` day 1+ — so the bot's 15-minute poll + `arm_exit()` fired
first in normal operation and the resting order was a mere outage/gap backstop.

**Why retired.** That left the tight stop blind between polls and overnight: ECO
and TNK gapped down past the poll's reach on 2026-09-22 (−$1,250 / −$1,100). The
resting STP now sits **at** the band (IBKR is the primary enforcer). Re-measured on
66 closed trades this is worth **+$1,526** and cuts the worst single loss from
−$1,418 to −$1,150.

**Relocated, not deleted.** The constant `PROVE_IT_BACKSTOP_SLACK_PCT` is **still
live** — it now governs **only** the Phase 2 armed give-back floor
(`armed_floor = floor × (1 − PROVE_IT_BACKSTOP_SLACK_PCT)`). Only its Phase 1
usage was removed.

**Restore path.** `git show <pre-2026-09-26-commit>:exit_rules.py` — the Phase 1
branch returned `round(max(disaster, band × (1 − PROVE_IT_BACKSTOP_SLACK_PCT)), 2)`.

**What would bring it back.** Evidence that broker-primary Phase 1 stops are being
shaken out by intraday wicks (sub-5-minute spikes the replay cannot see) for more
than the ~$1,500 the gap protection is worth — i.e. if the wick-shakeout cost, once
enough trades exist to measure it, exceeds the gap-protection gain. Tracked in the
provisional register as `phase1-broker-primary-stop`.



Seven exit rules retired at once, replaced by the two-phase **Prove-It Stop**.
See `decisions/2026-09-04_prove-it-stop.md` for the full reasoning and the
30-trade replay evidence.

Restore point: commit immediately preceding the Prove-It commit on `main`.

### 1. Intraday Loss Minimiser (ILM)

| | |
|---|---|
| **Constants** | `INTRADAY_MINIMISER_ENABLED`, `INTRADAY_PULLBACK_PCT`, `INTRADAY_MINIMISER_START_DAY` |
| **Location** | `execution_agent.py` — config block, monitor-loop exit block |
| **Also touched** | `telegram_notifier.py`, `frontend/src/lib/exitDetails.js`, `frontend/src/lib/positionRules.js`, `tests/` |
| **Status when retired** | Disabled by default since 2026-08-04 — dead code for a month |
| **ADR** | `decisions/2026-08-04_tune-exits-on-breakout-population.md` |

**What it did.** From Day 2 onward, sold on the first N% pullback from the day's
intraday high, provided that high was within 0.5% of entry or above.

**Why retired.** It was the single most damaging exit in the system. It sold
*developing winners* because the trigger required the high to be near entry —
exactly the condition a recovering position satisfies. Roughly halved expectancy
across two independent universes (broad 2,314 entries: +1.01% → +0.59%;
screener-passing 598 entries: +0.60% → +0.18%) and suppressed the right tail the
strategy depends on (+20% outcomes fell from 3.2% to 0.8% of entries).

It fired 3 times in live trading — GE, THC, TTWO — for a combined **−$768.72**.
All three were Day-3 verdict FAILs sold on a 0.5% wiggle.

**What would justify restoring it.** Nothing in its original form. If a
pullback-based intraday exit is ever wanted again, it must not require the high
to be near entry — that gate is what made it cut winners.

### 2. Trailing-stop time lever (`TRAIL_TIME_TIERS`)

| | |
|---|---|
| **Constants** | `TRAIL_TIME_TIERS_ENABLED`, `TRAIL_TIME_TIERS` |
| **Location** | `execution_agent.py` — config block, `_compute_dynamic_trail_pct()` |
| **Status when retired** | Disabled by default since 2026-08-04 — dead code |
| **ADR** | `decisions/2026-08-04_widen-exits-and-tighten-entries.md` |

**What it did.** Tightened the trailing stop purely as calendar time passed —
6.0% at day 8, down to 3.5% beyond day 30.

**Why retired.** It penalises a position for still working. On the 2,314-entry
breakout backtest, disabling it lifted expectancy +0.51% → +0.59% and payoff
1.44 → 1.58. Never fired in live trading.

**What would justify restoring it.** Evidence that hold duration alone predicts
reversal in the screener-passing population. The current evidence says the
opposite.

### 3. Early Loss Kill-switch

| | |
|---|---|
| **Constants** | `EARLY_LOSS_STOP_PCT`, `EARLY_LOSS_STOP_MAX_DAY` |
| **Location** | `execution_agent.py` — config block, monitor-loop "Day 0 hard loser kill-switch" |
| **Status when retired** | Active. Fired once (PSX, −$152.78) |
| **ADR** | `decisions/2026-08-20_early-loss-day0-tightening.md` |

**Why retired.** Not deleted so much as **absorbed**. Prove-It Phase 1 is the
same mechanism — an entry-anchored threshold that arms a tight trailing exit —
with the arbitrary "day 0 only" window removed. The kill-switch was correct but
stopped protecting the position at midnight on the entry day, which is why NBIX
(−$2,261) and DELL (−$1,283) ran unchecked.

### 4. Early Dollar Stop

| | |
|---|---|
| **Constants** | `EARLY_DOLLAR_STOP_PCT`, `EARLY_DOLLAR_STOP_MAX_DAY`, `EFFECTIVE_POSITION_SLOTS` |
| **Functions** | `early_dollar_stop_threshold()` |
| **Location** | `execution_agent.py`; mirrored in `frontend/src/lib/positionRules.js` |
| **Status when retired** | Active. **Never fired.** |
| **ADRs** | `decisions/2026-08-18_early-dollar-stop.md`, `decisions/2026-08-20_slot-derived-early-dollar-stop.md` |

**What it did.** Capped the unrealised dollar loss on an unconfirmed position at
`(equity / EFFECTIVE_POSITION_SLOTS) × 6%` ≈ $1,500 during days 0–5.

**Why retired.** Superseded by Prove-It Phase 1, which binds far earlier in every
case. The 2026-08-20 replay already showed the dollar stop was net harmful at its
original $500 setting; raised to a slot-derived ~$1,500 it became inert instead —
nothing ever reached that band without Phase 1 firing first. A rule that can only
fire after a better rule has already fired is not a backstop, it is dead weight.

**Bonus:** this removal retires **FU-007** in
`docs/tech_debt_and_requirements_tracker.md`. `EFFECTIVE_POSITION_SLOTS` existed
only to keep this stop's slot arithmetic honest; with the stop gone, the constant
and its pending migration to `MAX_POSITIONS` both disappear.

### 5. Thesis Stop

| | |
|---|---|
| **Constants** | `THESIS_STOP_ENABLED`, `THESIS_STOP_ATR_MULT`, `THESIS_STOP_START_DAY`, `THESIS_STOP_LAST_DAY`, `THESIS_STOP_ATR_FALLBACK` |
| **Location** | `execution_agent.py`; `telegram_notifier.notify_thesis_stop()`; `frontend/src/lib/positionRules.js` |
| **Status when retired** | Active. **Never fired.** |
| **ADRs** | `decisions/2026-08-09_thesis-stop.md`, `decisions/2026-08-17_thesis-stop-reexamination.md` |

**What it did.** Days 2–5, for positions that had never *closed* above entry: exit
once more than 1×ATR below entry.

**Why retired.** Prove-It Phase 1 asks the identical question — *has this ever
closed above entry?* — and answers it with a fixed 3% band instead of an
ATR-scaled one. Keeping both means two rules racing to cut the same population,
and the ATR version always loses the race. Its `closed_above_entry` latch is
**not** retired: it is now Prove-It's phase discriminator.

**What would justify restoring it.** Evidence that ATR-scaling the Phase 1 band
beats a fixed percentage. Genuinely untested — the rule never fired, so its
calibration is unmeasured, not validated.

### 6. EMA-21 Exit

| | |
|---|---|
| **Constants** | `EXIT_MA_TRIGGER_ENABLED`, `EXIT_MA_TYPE`, `EXIT_MA_WINDOW`, `EXIT_MA_BUFFER_PCT`, `EXIT_MA_EOD_ONLY` |
| **Functions** | `get_ma_value()` |
| **Location** | `execution_agent.py` — monitor-loop "Moving Average Exit Check" |
| **Status when retired** | Active. **Never fired.** |

**What it did.** From Day 7, sold at EOD if price closed below EMA-21 × 0.99.

**Why retired.** Dominated by Prove-It Phase 2 at every gain level. A position
above +5% is held to a 1.5% trail from its peak, which is far tighter than a 1%
undercut of a 21-day average; a proven position below +5% is held to the give-back
floor 1% under entry,
which is also tighter. There is no price path where EMA-21 fires first. It was
also suppressed during power-hold, so it could not act on the one population
where a slow-moving average might have added something.

### 7. Plateau (Stale) Exit

| | |
|---|---|
| **Constants** | `STALE_EXIT_ENABLED`, `STALE_EXIT_DAYS`, `STALE_EXIT_MIN_DAYS_HELD` |
| **Location** | `execution_agent.py` — monitor-loop "Plateau (Stale) Exit" |
| **Status when retired** | Active. **Never fired as a standalone exit.** |
| **ADR** | `decisions/2026-08-04_plateau-exit-capital-velocity.md` |

**What it did.** From Day 7, sold at EOD when no new high had been made in 10
trading days, to free the slot.

**Why retired as a standalone rule.** It sold to **cash**, which is the wrong
destination. The premise — a stalled position blocks a fresh breakout — is only
true if a fresh breakout actually exists. With Prove-It's give-back floor in
place, holding dead money costs almost nothing, so exiting to cash on a timer
gives up optionality for no gain.

**Not deleted — relocated.** The staleness signal now feeds Rank & Replace as a
threshold discount: a stale position swaps on a `RANK_REPLACE_FAIL_THRESHOLD` (5) point score gap instead of 15.
Same capital-velocity intent, but it can only fire when there is somewhere better
to put the money. `STALE_EXIT_DAYS` and `STALE_EXIT_MIN_DAYS_HELD` survive in
that role; only `STALE_EXIT_ENABLED` and the standalone exit block are gone.

### 8. Breakout Failure Penalty (`failure_penalty`)

| | |
|---|---|
| **Constants** | `FAILURE_PENALTY_MAX_POINTS` (new, default `0`); cap was a hard-coded `20` |
| **Identifiers** | `_compute_failure_penalty`, `failure_penalty`, `penalty_reason` |
| **Location** | `technical_screener.py` (`_compute_failure_penalty`, Phase 2 block); consumed in `ai_evaluator.py` → `adjusted_score`; surfaced on `daily_triggers` / `trigger_history` |
| **Status when retired** | **Active and firing in live trading.** On 2026-09-17 it rejected six BREAKOUT triggers, five of them AI grade **A**. |
| **ADR** | `decisions/2026-09-17_failure-penalty-disabled.md` |

**Disabled, not deleted.** The function, its columns and its stored values all
remain. Only the cap moved to `FAILURE_PENALTY_MAX_POINTS`, which ships at `0`.
Re-enabling is a one-variable change — which is precisely why the evidence below
must stay attached to it.

**What it did.** Compared each new trigger's `volume_surge`, `rs_score`,
`technical_score` and `pivot_distance_pct` against the entry values of losing
breakouts in `breakout_learnings`, within tolerances of ±0.5, ±10, ±10 and ±2.
Each match scored 2 points, weighted 3× inside 30 days and 2× beyond, capped at
20. The total was subtracted from `final_score` to give `adjusted_score`, which
the buy loop's score floor then tested.

**Why retired.** It had **zero** discriminative power. Replayed over all 16
`breakout_learnings` rows using each trade's own entry parameters, it returned
the maximum penalty for every single one:

```
mean penalty on WINNERS: 20.0  (n=10)
mean penalty on LOSERS : 20.0  (n=6)
winners that would be blocked: 10/10
```

LPG (+6.47%), ECO (+5.35%) and DHT (+3.47%) all scored exactly what CHRD
(−1.62%) scored. DHT's rejected candidate and DHT's own winning trade were
penalised identically. The cause is that each match tolerance is **wider than
the winner/loser separation on that parameter** (Δ0.15, Δ2.1, Δ4.5, Δ0.33), so a
match was guaranteed rather than informative. The cap also concealed the scale
of the problem: DHT's uncapped penalty was 78, and others reached 120.

Two further defects, documented so they are not rediscovered the hard way:

- It applied to **BREAKOUT only**. All 16 learning rows are BREAKOUT-tagged and
  the `_meta.trigger_type` filter exempted everything else — 196/196
  PRE_BREAKOUT rows in `trigger_history` scored 0.
- A loss flagged **all four** parameters as failed
  (`failed = percent_return < 0` in `_build_failed_params_snapshot`), so there
  was never any per-parameter attribution.

**Restore path.** Set `FAILURE_PENALTY_MAX_POINTS` to a non-zero value. The
pre-change code — with the hard-coded cap of 20 — is at
`git show c0876cd:technical_screener.py`.

**What would have to be true to bring it back.** The tolerances would need to be
re-fitted to real forward outcomes and shown to separate winners from losers on
held-out data. That requires `trigger_history` outcome columns, of which only
16/233 rows are currently populated and **none** are BREAKOUT. Tracked in
`decisions/provisional_decisions.json` as `failure-penalty-tolerances`.

**Related, still live.** The per-ticker `history_penalty`
(`HISTORY_LEARNING_MAX_PENALTY`, `compute_trade_history_penalty` in
`ai_evaluator.py`) is a **different** rule and is unaffected — it penalises a
ticker for its own recent losses rather than for resembling other tickers.

---

## 2026-09-17 — All-or-nothing outcome writing (RELOCATED, not deleted)

**Identifiers:** `MIN_BARS_REQUIRED` (redefined, not removed), the `bars_have <
MIN_BARS_REQUIRED` skip in `backfill_trigger_outcomes.run()`, and the
`SETTLE_DAYS`-based cutoff in `fetch_pending()`.

**Where it lived:** `backfill_trigger_outcomes.py`; asserted by
`tests/test_trigger_outcomes.py::TestIncompleteWindowsNotWritten::test_short_window_is_skipped_not_written`.

**Status when retired:** Active, and it had fired on every run. It is what left
16 of 233 archived triggers labelled and **zero** BREAKOUT rows.

**What it did:** Refused to write any outcome for a trigger unless all 20
sessions of the longest horizon existed, so `fwd_1d` and `fwd_5d` were withheld
for ~34 days by `fwd_20d`.

**Why retired:** `fwd_1d` was already computable for 222/233 rows and `fwd_5d`
for 185/233. The failure-penalty refit needs exactly those short horizons, since
the "failures" it mislabelled were day-0/day-1 stop-outs. See
`decisions/2026-09-17_per-horizon-outcomes.md`.

**RELOCATED — the concern it protected is still enforced.** The rule existed so a
partial window could never masquerade as a complete one. That guarantee now lives
in two narrower places instead of one blanket gate:

1. `compute_outcomes()` withholds `max_gain_20d_pct`, `max_drawdown_20d_pct` and
   `ever_above_entry` unless `COMPLETE_BARS_REQUIRED` (20) sessions exist — these
   are the only fields whose *name* asserts a 20-day window.
2. `outcomes_computed_at` is stamped only on completion, so a partial row stays
   in the pending set and is topped up rather than retired.

Do **not** "simplify" either of these back into a single early-exit guard; that
reintroduces the starvation without any offsetting benefit.

**Restore path:** `git show b9c6233:backfill_trigger_outcomes.py`.

**What would bring it back:** Nothing foreseeable. If a downstream study were ever
found assuming every non-NULL row is fully measured, the correct fix is to make
that study filter on `outcomes_computed_at`, not to re-block early writes.

---

## 2026-09-18 — Marker-only log shipping (opt-in capture)

**Identifiers:** `TeeLogger.SHIP_MARKERS` (as a *capture filter*),
`_last_log_purge_date`, `AGENT_LOG_RETENTION_DAYS` as the single retention
window.

**Where it lived:** `execution_agent.py` — `TeeLogger._capture_for_shipping()`
and `flush_logs_to_supabase()`. Tests in `tests/test_log_shipping.py`
(`test_only_marked_lines_are_captured`, `test_retention_runs_once_per_day`).

**Status when retired:** Written on 2026-09-18 as patch 057 and retired the same
day, before patch 057 was ever deployed. **It never ran in production**, so
there is no live evidence for or against it — only the reasoning below.

**What it did:** Shipping to Supabase was opt-IN per line. A line was buffered
only if it contained `[TELEGRAM-FAIL]`, `CRITICAL`, `Traceback`, `❌` or `⚠️`;
everything else was discarded at capture time and never left the host. A single
retention window (14 days) applied to all rows, swept once per day.

**Why retired:** The filter was chosen to answer one question — "is the alert
channel dead?" — and it answers that well. It is close to useless for the
question actually being asked, which is "what was the agent doing when it
decided that?" A stack trace without the twenty lines that preceded it explains
nothing, and those twenty lines were exactly what the filter threw away. The
stated justification (volume, and keeping position/cash detail on the host) did
not survive contact with the numbers: ~125 print sites per cycle work out to
roughly 4–5k lines/day, or ~5 MB at steady state against a 500 MB budget. See
`decisions/2026-09-18_comprehensive-log-shipping.md`.

**RELOCATED — `SHIP_MARKERS` still exists, in a different role.** It is no
longer a capture filter; it is now the basis of `TeeLogger._classify()`, which
assigns each line a `level`. That level is what drives *tiered retention*
(INFO/TRADE expire in 3 days, WARN and above in 14) and what makes the full
firehose filterable in SQL. Do not reintroduce it as a capture gate — the
filtering it used to do at capture time is now done at query time, where the
discarded context is still available if it turns out to be needed.

`AGENT_LOG_RETENTION_DAYS` also survives, but now governs only WARN-and-above;
`AGENT_LOG_INFO_RETENTION_DAYS` governs the rest. `_last_log_purge_date` (a
date string, daily) became `_last_log_purge_at` (a timestamp, hourly), because
a daily sweep cannot bound a burst.

**Marker-only mode is retained as an escape hatch**, not deleted: set
`AGENT_LOG_SHIP_ALL=false` to restore the old capture behaviour if volume ever
does become a problem.

**Restore path:** `git show d10090c:execution_agent.py` — the patch-057 commit,
which contains the marker-only implementation in full.

**What would bring it back:** Measured evidence that the full log is actually
too expensive — the Supabase project approaching its storage quota, or insert
latency showing up in the monitor cycle. Flip `AGENT_LOG_SHIP_ALL=false` first
and confirm that fixes it before deleting anything; the constant exists so this
does not require a code change.

---

## 2026-09-18 — the Phase 1 trailing backstop (its ratcheting anchor)

**Identifiers affected:** `prove_it_trail_pct()` (the `phase == "phase1"` branch),
`hard_stop_price()` (signature gained `days_held`), new `safe_hard_stop()`.

**Where it lived:** `exit_rules.py` (`prove_it_trail_pct`, `hard_stop_price`),
`execution_agent.py` (buy-time placement, the tightening block, the self-heal
block), mirrored in `frontend/src/lib/positionRules.js`, documented in
`docs/sell_logic.md`, `docs/configuration.md` and `README.md`, and tested in
`tests/test_prove_it_stop.py::TestBackstopTrailPct` and `tests/test_hard_stop.py`.

**Status when retired:** ACTIVE, and it **fired in live trading repeatedly.**
This is not dormant code being tidied away. Ten of 49 closed trades carry its
signature (day 0, trail 0.2–1.9%, peak < 5%): MPC, PSX, FIVE, LPG#52, CHRD, GEO,
ECO#58, CHRD#61, CDNA#62 and SMTC, together netting **−$114** across ten round
trips and ten occupied position slots.

**What it did:** Phase 1 of the Prove-It Stop computed a *trailing percentage*
that placed the resting broker order one backstop slack below the entry-anchored
band. Because `place_protective_stops()` submits that as `orderType='TRAIL'`, and
an IBKR TRAIL anchor ratchets up with the high-water mark, the level did not stay
where it was placed. On SMTC (2026-09-18) an order intended to rest at $176.91
(entry −2.0%) climbed to $182.11 (entry **+0.89%**) and sold a winner at +0.76%,
23 minutes after entry.

**Why it was retired:** It contradicted its own documented contract —
`exit_rules.py` describes the Phase 1 level as "a FIXED floor rather than a
trail". A loss cap that drifts above entry is a profit-taker. A 50-trade replay
isolating the ratchet scored **+$2,590** for pinning it (+$2,902 recovered on
winners, −$313 paid on losers), though **+$2,409 of that is CPAY alone**, so the
figure is not a defensible expected value and the decision rests on correctness
rather than on the net. See `decisions/2026-09-18_phase1-static-backstop.md`.

**RELOCATED, not deleted.** Phase 1 protection still exists at the broker — it
moved from the trailing leg to the **static `STP` leg** in the same OCA group,
where `hard_stop_price()` now returns the band for unproven positions. Broker-side
protection in Phase 1 actually *improves*: the resting floor moves from the
disaster level (entry −7%) to entry −1.99% on day 0 and entry −3.97% from day 1.
Do not go looking for this behaviour under `prove_it_trail_pct()` — that function
deliberately returns `None` for Phase 1 now, and a test pins it.

**Restore path:** `git show 593aba9:exit_rules.py` — the patch-058 commit, the
last to contain the ratcheting Phase 1 branch.

**What would have to be true to bring it back:** That the ratchet was cutting
losses rather than clipping winners. The measurement says the opposite by roughly
9:1, but it is carried by one trade — if a re-run at ≥ 60 closed trades shows the
direction reversing once CPAY stops dominating, reopen it. Tracked as `FU-011` in
`decisions/provisional_decisions.json`. Note that even then the fix would be to
*widen* the slack, not to restore the ratchet: a stop that can rise above entry
is wrong independently of its expected value.

---

## The "+10 pts for near-term earnings" AI scoring boost

**Retired:** 2026-09-28 · **ADR:** `decisions/2026-09-28_earnings-blackout-and-news-veto.md`

**Identifier removed:** the prompt line in `ai_evaluator.py`'s scoring rules:

> `Near-term catalyst (earnings, product launch) within 2-3 weeks: boost 10 pts`

**Where it lived:** the `SCORING RULES → OTHER FACTORS` block of the AI evaluator
prompt string in `ai_evaluator.py` (cloud screener).

**Status when retired:** active in the shipped prompt on every screener run. Its
effect was diffuse — it nudged the model to rate names with imminent earnings
higher — and it was never isolated in a measurement, because the AI's news feed
was simultaneously dead (see the endpoint-403 bug in the same ADR), so its
real-world influence on realised trades cannot be quantified after the fact.

**What it did:** instructed the model to add ~10 rating points when a report or
launch was 2–3 weeks out, on the theory that a near catalyst is bullish.

**Why it was retired:** it is backwards for this bot's exit regime. A fresh
position sits under the Prove-It stop's tight floor (−1% day 0, −3% day 1+), so an
earnings gap is far more likely to stop the position out at a loss than to help
it. Rewarding proximity to earnings steered capital toward the highest-gap-risk
names. It is **replaced**, not merely deleted, by two things: (1) a prompt
instruction that imminent earnings are a RISK, not a bonus, and (2) a
deterministic buy-side **earnings blackout** (`EARNINGS_BLACKOUT_TRADING_DAYS`,
default 3 trading days) that defers the buy regardless of what the model says.

**Restore path:** `git show 3060fda:ai_evaluator.py` contains the boost line (the
commit immediately before this change).

**What would have to be true to bring it back:** the bot would need a materially
looser exit regime in which holding through an earnings report is the norm rather
than an almost-certain stop-out. Under the current tight Prove-It stop it should
stay retired.

---

## The backtester's 7% trailing-stop-from-peak + EMA-21×0.99 exit

**Retired:** 2026-09-29 · **ADR:** `decisions/2026-09-29_backtester-option-a-live-exits.md`

**Identifiers removed:** the exit half of `backend/backtester.py` — the
`stop_trail_factor = 1 - stop_loss_pct/100` trailing stop, the
`close < EMA-21 × (1 - DEFAULT_EXIT_BUFFER)` exit, and the module constants
`DEFAULT_STOP_LOSS_PCT = 7.0` and `DEFAULT_EXIT_BUFFER = 0.01`. The per-ticker
`EMA21` column computed only to feed that exit was also dropped.

**Where it lived:** `backend/backtester.py`, the "B. Update trailing stops &
check exits" block (roughly lines 205–260 before this change), inside the
dashboard/web image. This was NOT a live-trading code path — the live bot exits
via `execution_agent`/`monitoring.py`, never this file.

**Status when retired:** active in the dashboard **backtester** on every run. It
never touched real orders, but it silently answered exit-behaviour questions
about a strategy the bot **stopped running when the Prove-It Stop shipped on
2026-09-04**. It was a parallel re-implementation, not the live rules.

**What it did:** exited a backtest position when the day's low crossed a fixed
7%-below-peak trailing stop, or when the close fell 1% below its EMA-21. No
Prove-It phases, no dynamic trail ladder, no power-hold, no scale-out.

**Why it was retired:** it drifted from production by construction — two separate
exit code paths cannot stay in sync. Option A replaced it with a call to the
shared `daily_exit_sim.resolve_position_day`, the SAME engine
`research/strategy_backtest.py` uses, which drives the live `exit_core`/
`exit_rules`. The dashboard backtester now exits byte-for-byte the way the bot
does. The behaviour is **replaced, not merely deleted** — its new home is the
live exit engine, so this is a relocation of responsibility, not a lost feature.

**Restore path:** `git show fe8b505:backend/backtester.py` (the commit
immediately before Option A) contains the 7%-trail + EMA-21 exit block and the
two constants.

**What would have to be true to bring it back:** nothing foreseeable. A fixed
7%/EMA exit is not what the bot runs; reinstating it would re-open the exact
drift this change closed. If a *comparison* baseline is ever wanted, add it as an
explicit labelled alternative in the research harness, not as the dashboard's
default exit.
