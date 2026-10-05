# Record intraday evidence and compare strategies from the actual account

Date: 2026-09-30
Status: Accepted - deployment and calibration scope extended by
`2026-09-30_observer-and-calibration-harness.md`; backup scope superseded in part
by [trading-only backups](2026-10-04_trading-only-backup-scope.md).

> **2026-10-04:** Saved comparison results are no longer in weekly backups.
> The entire calibration/benchmarking subsystem is excluded. Collection,
> retention and human-approval boundaries remain unchanged.

> **2026-09-30:** An independent observer can collect while trading is stopped;
> it does not claim to capture live decisions. Offline frozen-selection and
> holdout calibration extends the original single-variant interface. The
> dashboard AI-veto comparison and human-approval boundary remain unchanged.

## Context

The recorded-input replay cannot recover missing intraday facts from daily
trigger summaries. The operator requested automatic collection, database
storage and dashboard comparisons to support strategy improvement. They
explicitly selected human approval before any live changes, and an actual
recorded starting portfolio, including prior trades and protective orders,
instead of resetting both simulations to an invented cash-only portfolio.

## Decision

Capture immutable, timestamped live inputs and audit events without adding
network work to the order-decision path. An isolated worker persists queued
events, records source-labelled candidate price samples, and maintains a
durable local spool and visible recording health. Recording failure must not
disable protective exits, and missing observations must not become successful
research results.

The private `intraday_capture_events` table stores observations; symbol
membership survives trigger expiry and process restarts; health is separate
from evidence. A manually applied, re-runnable migration creates these objects
and `intraday_replay_runs`. Their contents contain private account state, so
they require a server-side service-role credential, not anonymous browser
access. No credential is recorded in a capture.

Replay begins from a complete captured actual-account snapshot. Existing
positions, cost basis, position flags, prior sales/re-entry restrictions and
broker protective orders must be available and coherent. An absent or unsafe
snapshot is an explicit rejection, not permission to start flat. Subsequent
simulated trades evolve independently; real later fills are audit evidence,
not instructions to force a counterfactual portfolio to match the real one.

The first supported comparison removes only the AI D-grade veto. It does not
remove AI scores from ranking, bypass other gates, optimize every parameter, or
write strategy settings. The dashboard starts a bounded background job and
displays saved results or the reason the recording cannot be replayed.
Automatic comparisons run weekly over up to the latest 30 recorded calendar
days ending in the preceding week. Manual requests are bounded to 93 calendar
days, 50,000 source records and 64 MiB per worker input.

## Fidelity boundaries

The recorded starting account is real; the subsequent alternative strategy is
still a sampled-price simulation. Broker and FMP observations retain their
sources and availability times. Unknown trailing-order state, missing candidate
prices, configuration changes, unsupported manual/rotation activity and gaps
are not filled in using later data. A supplied order stop level seeds the
sampled protection model; it is not a recovered history of every broker tick.

Schema 1's initially-flat offline example remains supported separately. The
dashboard must not mistake it for an actual-account replay.

## Retention and interpretation

Default to five-minute candidate samples, a 365-day rolling raw-data horizon,
and at most 250 sampled symbols. Rejected and expired candidates remain in the
sampling universe for the horizon, since an alternative portfolio may still
hold them. Exceeding the cap is a coverage failure, not silent ticker selection.
Quotes older than 600 seconds are not suitable to fill missing observations.
These are operational starting points, not profitability-optimized thresholds.

Raw captures are deliberately excluded from forever-retained weekly full
backups: copying the entire rolling history every week would defeat retention
and multiply storage. Export a research dataset before expiry if it must be
preserved. Saved comparison results remain in the normal backup.

Four to eight weeks is an instrumentation period; three months supports
exploration; six to twelve months across different market conditions supports
more serious evaluation. About 100 completed positions is a planning heuristic,
not a statistical guarantee or an automatic promotion gate. Partial-sale rows
are not independent trades. Decisions must also consider distinct trading
dates, market conditions, trading costs, losses, concentration in individual
winners, and performance on data not used to choose the rule.

**No automatic live strategy changes are authorized.** Better historical
results alone are not evidence of a repeatable future advantage.

See `docs/intraday_research.md`, `docs/backtesting.md`, `docs/configuration.md`
and `docs/backups.md` for operation and deployment.
