# Independent observation and approval-only calibration

Date: 2026-09-30
Status: Accepted — extended by [decision-only simulation and supervised research](2026-09-30_shadow-decisions-and-supervised-research.md)

> **2026-09-30:** A separate shadow worker now produces decision inputs while
> trading is stopped, and validated labelled shadow exports can feed calibration.
> Raw observer records alone still cannot. Observation deployment also starts the
> shadow worker; cloud daily/weekly reporting replaces automatic actual-decision
> comparisons in observation mode. The original observation and approval
> boundaries below remain in force.

## Context

The operator requested completion of the earlier intraday research harness.
Recording embedded in the execution agent stops when trading is stopped, as it
was during the SHIP investigation. The operator explicitly chose an independent
observer and an observation-only deployment: installing this work must not
restart the trading agent. They also approved offline calibration with human
approval before any live strategy changes, not automatic trading optimization.

## Decision

Run `intraday_observer.py` as a separate process with its own API client ID,
spool and health identity. It requests completed selected-account positions,
visible open orders, account downloads and executions; signed quantities and
shorts remain visible. The broker connection stays on the owning thread.
The shared recorder worker persists observations and samples the candidate
universe independently of the execution daemon.

The SDK's `readonly=True` is not a broker permission boundary. The observer
additionally blocks order submission, cancellation and binding at both SDK
layers, exposes only a narrow read capability, and never imports the trading
daemon or calls reconciliation. It does not cover shorts, replace protection,
or repair the live ledger. Dedicated broker-enforced read-only credentials
remain stronger protection.

Observer events carry `capture_mode=observer` and `replay_ready=false`.
Completed component requests are not an atomic snapshot. They preserve
component receipt times, execution IDs, missing/delayed commissions and source
labels; they do not invent historical decisions, quote ticks or full execution
history. Observer-only inputs are rejected by the live-decision replay.

The dashboard reports the observer and execution recorder separately and can
export raw evidence even when replay validation rejects it. Raw exports include
the records, a content fingerprint, coverage counts and the rejection reason.
They are private account data, not financial performance results.

The offline calibration workflow freezes a candidate selected using earlier
recorded inputs before evaluating it against a later, nonoverlapping holdout.
The holdout is data not used for candidate selection. Only explicit supported
simulation overrides are allowed; recorded source configuration is not
rewritten, shared-rule mismatches are not bypassed and rejected inputs remain
rejections. Reports retain dataset/configuration/engine provenance and remain
research-only. The dashboard's existing AI-veto comparison is unchanged.

`TRADING_RUNTIME_MODE`, a GitHub repository Actions variable, defaults to
`observe`. Deployment stops the execution agent before pulling images and
starts only the observer and web app, leaving the gateway unrecreated.
Switching to `live` is an explicit operator action, not a calibration outcome.
Both runtime services have Compose profiles, so bare `docker compose up`
does not start either runtime.

## Consequences

Observation-only mode does **not** run bot-enforced exits, trailing-stop
adjustments, reconciliation or new entries. Existing broker-held orders remain
untouched and may execute independently. The operator must inspect existing
positions and broker protection before relying on this mode.

Raw data collection does not by itself make a window suitable for strategy
replay. Full replay needs the recorded live decision inputs and a coherent
starting portfolio; incomplete observer data must not be manufactured into
such a dataset. Frozen selection and separate evaluation reduce accidental
data reuse, but cannot prove the operator has never inspected the holdout.
Neither a higher historical return nor a successful report guarantees
profitability or authorizes a live strategy rewrite.

See `docs/intraday_research.md`, `docs/backtesting.md`, `docs/configuration.md`,
`docs/ibkr_totp_setup.md` and `README.md` for operation.
