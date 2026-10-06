# Decision-only portfolio simulation and supervised research

Date: 2026-09-30
Status: Superseded in part by [dashboard live-entry control](2026-10-01_dashboard-live-entry-control.md) and [startup input readiness](2026-10-05_shadow-startup-input-readiness.md), and extended by [interactive self-calibration](2026-10-03_interactive-self-calibration.md); shadow accounting and supervision remain accepted, with the exceptions below.

> **2026-10-05:** Before a run's first complete frame validates, input failures
> now wait/retry without creating an experiment or superseding its predecessor.
> Established-run gaps still block permanently. Operators can queue a one-use,
> run-specific replacement request; normal restarts do not independently
> authorize reseeding. No stale prices or missing intervals are fabricated.

> **2026-10-03:** A separate broker-free calibration worker now searches supported
> numeric settings and reserves future evaluation. The descriptive reporter
> itself still does not select or apply strategy settings. A persistent inbox
> adds operator decisions and approved artifacts without automatic live changes.

> **2026-10-03:** "Writes only private research state" now has one explicit
> exception: credential-safe operational metadata is written to the existing
> public-readable `agent_logs` through an independent sink. Private observations,
> hypothetical holdings and performance remain private; no real trading writes
> are added. See [independent research diagnostics](2026-10-03_independent-research-diagnostics.md).

> **2026-10-01:** Only the mutually exclusive runtime selection below is replaced.
> The real risk manager, observer and shadow worker now run together. Disabling
> new real entries does not disable real protective exits or hypothetical
> decisions. Research remains separate from real balances and trade history.

## Context

The independent observer records account state and prices while trading is
stopped, but those observations alone cannot reproduce the bot's decisions.
The operator explicitly requested the missing decision-only simulator, daily
health summaries and failure alerts, and weekly research reports. They approved
the existing Telegram recipients and persistent GitHub incidents as fallback.

## Decision

Maintain a hypothetical portfolio in a separate, broker-free worker. Initialize
from validated actual-account evidence, not an invented cash balance, and then
let only simulated executions change the hypothetical cash, holdings and
protection. Later real account activity never silently resets the simulation.
Reuse shared entry/exit logic and record point-in-time exogenous inputs,
decisions, reasons, hypothetical fills and sampled equity.

Use dedicated transactional state and an upload outbox. A restart resumes the
same hypothetical portfolio; duplicate cycles must not duplicate a sale.
Unavailable or stale inputs, incomplete initial protection and unsupported
activity produce explicit blocked status or coverage gaps, never fabricated
market data or silent successful decisions.

Exports identify shadow decisions and hypothetical window-start state
explicitly. They retain the actual seed and predecessor evidence needed to
reconstruct that state. Calibration may use these validated exports without
relabelling hypothetical holdings as real broker holdings or weakening the
existing rejection of raw observer-only datasets. Selection remains frozen
before later holdout evaluation, and live changes still require approval.

Default observation deployment runs the observer, shadow worker and dashboard,
with the live execution agent stopped. The shadow image has no brokerage SDK
and is not attached to the gateway's Docker network. It writes only private
research state, never live positions, trade history, account balances or orders.
An explicit live-mode deployment stops the observer and shadow worker.

Shared strategy declarations are imported from `research_configuration.py` by
both consumers; the live agent re-exports them without changing defaults.
`market_direction.index_verdict()` supplies the pure moving-average market
test while the live wrapper retains acquisition/logging. See
`docs/retired_code.md` for relocation identifiers and restore paths.
Invalid, duplicate, stale or future-dated index history has no usable verdict;
the live wrapper keeps its fail-closed behavior rather than buying on such data.

Automatic actual-account comparisons remain available in explicit live mode,
but do not run against observer-only sessions. The dashboard receives the
validated deployment mode; observation reporting is the independent cloud
workflow's responsibility.

An independent cloud schedule checks collection and decision progress, produces
daily summaries and weekly hypothetical research reports, and sends approved
notifications. A fresh process heartbeat does not establish fresh inputs or
successful decisions. Missing data and unavailable infrastructure remain visible
even when the production host or its database cannot report its own failure.
Persist report identities, incident state and delivery attempts so retries and
restarts do not silently lose notifications.

## Limits

This is sampled-price simulation, not a brokerage execution guarantee. Costs,
fill assumptions, missing intervals and unsupported paths must accompany
results. Partial sales are not independent completed positions, and many
positions sharing one trading date are not many independent market conditions.
There is no automatic profitability threshold, live parameter writer or restart
approval. Scheduled summaries are automated messages, not an assistant remaining
active or initiating daily interactive conversations.

Daily/weekly delivery is best effort: cloud scheduling and notification services
can be delayed. Delivery retries cannot promise exactly-once Telegram delivery
across a crash between sending and saving the receipt.

See `docs/intraday_research.md`, `docs/backtesting.md`, `docs/configuration.md`,
`docs/ibkr_totp_setup.md` and `README.md` for operation.
