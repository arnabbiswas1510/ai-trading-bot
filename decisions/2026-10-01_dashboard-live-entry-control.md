# Dashboard permission for real entries, with continuous protection and research

Date: 2026-10-01
Status: Accepted

## Context

The operator asked for an immediate dashboard switch and a visible indication
of whether real trading is active. They explicitly chose to block new real
entries while continuing protective management of existing real holdings.
Hypothetical decisions must remain separate from real trade history and returns.

Stopping the execution service at deployment was not a persistent permission
boundary. Supabase logs showed agent startup at 06:00 and real entry evaluation
at 09:30 on October 1. The morning restart script could restart it unconditionally.
No direct inspection of the installed crontab was possible; SSH was unavailable.
The message about unfilled slots described ordinary rejected candidates, not a
disabled trader.

## Decision

Store `live_entries_enabled`, initially false, in a dedicated local SQLite
database shared only by the web and execution containers. The existing dashboard
SQLite settings volume is not shared; neither control nor simulated trades enter
real financial tables. A separate Supabase schema adds no useful boundary for
this host-local permission, and existing private `intraday_shadow_*` tables
already isolate hypothetical events and performance.

Read permission before real buy evaluation and again at final submission.
Serialize final submission and dashboard changes with SQLite's write transaction.
Discretionary rotation reserves permission before the first cancellation or
sell submission and releases it before waiting for a fill; its replacement buy
must obtain permission again. Contention is an explicit unsuccessful toggle,
not a silent OFF acknowledgment.
Do not cache permission between orders. Missing, invalid, locked or inaccessible
storage denies new entries without disabling existing-position protection.
The toggle does not cancel previously submitted broker orders; an in-flight
submission that acquired the lock first may complete before the toggle is saved.

Inactive means protect-only, not zero real orders. Real stops, sales, fills and
reconciliation remain real activity. Replacement rotations are disabled while
new real entries are forbidden. Shadow strategy configuration does not contain
this operational property and shadow buys and exits continue independently.

The existing dashboard has no operator login. Protect this new mutation endpoint
with `TRADING_CONTROL_TOKEN`, a server-side secret of at least 32 characters.
The browser holds the operator-entered token in memory only. Unconfigured
credentials lock mutations; they never silently enable or disable a saved
permission. Require trusted private access or HTTPS; this is not a replacement
for securing other existing dashboard endpoints.

Show saved permission separately from fresh execution-agent acknowledgment and
broker connectivity. A stale or absent heartbeat must not look like confirmed
protection. A heartbeat confirms the agent's observed control revision, not
successful execution of every risk rule or completeness of broker orders.

Run execution, independent observation and hypothetical simulation together.
Keep `TRADING_RUNTIME_MODE=observe|live` as validated deployment compatibility
input only, never as authority to enable entries. Deploy the morning helper to
the git-free host so cron and deployment agree. Preserve the saved permission
through ordinary restarts and image updates.

## Consequences

This is an operational safety control, not a calibrated trading threshold, so
there is no small-sample parameter to register for later review. Existing strategy
thresholds and research approval requirements do not change.

The initial deployment needs no new Supabase migration. An operator must provision
the control secret and confirm the deployed agent's acknowledgment before treating
the UI as evidence of runtime state. Merely authoring this patch does not stop
the previously running production agent.

See `docs/trading_control.md`, `docs/configuration.md`, `docs/buy_logic.md`,
`docs/sell_logic.md`, `docs/intraday_research.md` and `README.md`.
