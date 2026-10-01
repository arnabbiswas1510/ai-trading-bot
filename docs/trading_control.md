# Real trading control

The **Real trading control** panel appears above every dashboard view, including
when portfolio data cannot load.

**Disable new real buys** selects protect-only operation: no new automatic or
manual long entries and no discretionary replacement rotations. Existing real
holdings still receive real stop management and protective exits. Those real
sales stay in real trade history and performance.

**Enable new real buys** permits the normal strategy and safety checks to submit
real orders. It does not force a buy, change strategy thresholds or start a new
research run. Enabling asks for explicit confirmation.

## One-time setup

Set `TRADING_CONTROL_TOKEN` to a randomly generated private secret of at least
32 characters in the production host's `.env`, or store it under that name in
Bitwarden and set its `.env.template` entry to `@bws` before rendering the host
environment. Never commit the value. Recreate the dashboard container through
the deployment helper to load the secret. The pipeline copies `.env.template`
but does not automatically render or replace the host's `.env`.

Paste the token into the panel's password field to authorize a change. It is
held only in page memory, not browser storage or URLs. Use the dashboard through
a trusted private connection or HTTPS; plain HTTP on an untrusted network is
not safe for a control token. The existing dashboard's other endpoints are not
authenticated by this feature.

No new Supabase SQL is needed for this control. The observer/shadow/reporting
migrations and private service credentials remain prerequisites for research.

## What the status means

The saved property is `live_entries_enabled`, with an initial value of **false**.
The UI polls every 10 seconds and shows the execution agent's last report time.
A report more than 90 seconds old is stale, not confirmation that trading or
protection is running. A changed revision awaiting acknowledgment is labelled
pending; a disconnected broker is labelled disconnected.

**ACTIVE** means the connected execution agent recently acknowledged permission
for new real buys. **INACTIVE / protect-only** means it recently acknowledged
that new real buys are forbidden. These are permission/connection states, not a
claim that a trade is occurring or that every protective order is healthy.
If status is unavailable, do not infer that the real account is safe or flat.
Long-running agent operations can make the status stale even when the process
has not crashed; inspect logs and broker protection.

Changes require no agent or container restart. A successful save serializes
against final new-buy submission; subsequent submissions must read the new value.
The agent does not need to finish its 15-minute cycle before the gate changes.
An order submitted **before** the save can still fill afterward. The switch
does not cancel pending orders, cover shorts or undo fills. Review existing
broker orders separately if you need to prevent those fills too.
Rotation reserves permission before its first stop cancellation or sell
submission and releases the reservation after submission, before waiting for
fills. A successful OFF save therefore cannot be followed by a newly initiated
rotation sale. If a rotation already holds the reservation, a competing switch
request can fail explicitly from contention; do not treat that failed request
as OFF. Its replacement buy still requires permission separately.

## Persistence and failure behavior

The dedicated Docker volume `trading-control` is mounted at `/app/control` on
the web and execution containers. Compose gives both the same
`TRADING_CONTROL_PATH=/app/control/trading-control.sqlite3`. Do not delete the
volume or run another execution agent against a different control file.
Manual buy tools must use this same path/mount; missing permission denies entry.

Ordinary restarts, the morning restart and deployments preserve the property.
`TRADING_RUNTIME_MODE` does not grant entry permission. Both accepted legacy
values, `observe` and `live`, run real protection alongside the observer and
shadow worker. The gateway is not recreated by deployment.

Missing/invalid/unreadable control storage blocks new real buys and surfaces an
error; protective management continues independently. A missing or short
`TRADING_CONTROL_TOKEN` locks dashboard changes but does **not** revoke a
previously saved ON setting. Do not remove the token as a way to stop buys:
save OFF and confirm it instead.

## Hypothetical trades are not real trades

The broker-free shadow worker retains its own portfolio in `shadow-data` and
private `intraday_shadow_*` tables. Its hypothetical buys, protective exits and
returns are displayed in Shadow Research, never added to real `trade_history`,
`portfolio_positions`, `ibkr_fills` or `account_balances`. The real-entry switch
does not disable hypothetical strategy evaluation.

This switch does not repair missing research credentials or blocked captures.
Check research health separately. Daily summaries and weekly reports require
their existing cloud credentials and notification setup.

See `decisions/2026-10-01_dashboard-live-entry-control.md` for why.
