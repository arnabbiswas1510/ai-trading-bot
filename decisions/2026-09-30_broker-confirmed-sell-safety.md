# Broker-confirmed sell safety after the SHIP incident

Date: 2026-09-30
Status: Accepted

## Evidence and limits

The retained broker fill ledger records one 1,004-share SHIP purchase on
September 18 and two separate 1,004-share sales on September 21: order 1112
at 09:31:55 ET and order 1147 at 09:44:31 ET. Without an intervening purchase,
the first closes the long and the second creates a 1,004-share short.
On September 30, a read-only broker inspection confirmed SHIP -1,004 with no
open orders while the application's portfolio table was empty.

This establishes the duplicate disposal, not which historical code path
submitted order 1147. The deployed revision and that order's submission logs
have not been recovered. The current source has stale-ledger protective
recreation paths that need protection irrespective of that attribution.

The execution agent was manually stopped during the investigation. The user
chose to close the short manually. This change neither places that closing
order nor establishes that it filled, and authoring this change does not
deploy it or restart the agent.

## Decision

Use completed, account-scoped `reqPositions()` inventory for quantity safety.
Retain signed quantities; do not filter shorts away before inspecting them.
Repeated callbacks for a contract are collapsed to its latest quantity,
including zero. Unknown accounts and ambiguous stock contracts block execution.
A disconnected broker, incomplete response, malformed stock quantity or
10-second snapshot timeout is an explicit safety error, never an empty book.
Restore the previous connection timeout after the request.

Block automatic and manual new-entry paths when inventory is unavailable or
the selected account contains a short, both at preflight and immediately
before order submission. Other accounts' positions do not authorize or block
the selected account's trades.

Every stock SELL path, including protective repair, armed exits, queued exits,
partial scale-outs, `force_sell.py` and `managed_exit.py`, uses the shared
`submit_sell_orders()` boundary. Requested whole shares cannot exceed a fresh
confirmed long in the exact qualified contract ID and account, not just a matching
ticker. Flat, short, fractional or unknown inventory is not permission
to sell; a stale larger request is rejected rather than silently resized.

Cancellation first retrieves all clients' open orders for the ticker and account.
It requires terminal acknowledgement (Filled, Cancelled or ApiCancelled), with a
10-second acknowledgement deadline, and refreshes open orders before replacement.
An order owned by another API client blocks replacement rather than being ignored.
Inactive and PendingCancel are not accepted as cancellation confirmation.
The library can synthesize Cancelled locally; an order still returned by the
broker's fresh response remains unresolved regardless of local status. Only
own-client cached submissions supplement that response; obsolete foreign-client
cache entries do not block replacement forever.
Inventory is reread after cancellation, because the old stop can fill in flight.

OCA pairs use unique group identifiers. The first leg is staged with
`transmit=False`; only the final leg transmits the group. This follows
[IBKR's documented OCA transmission procedure](https://interactivebrokers.github.io/tws-api/oca.html)
and prevents a marketable first leg filling before its sibling exists.
`ocaType=1` is retained: cancel remaining orders with blocking overfill
protection, not a guarantee of proportional quantity reduction on partial fills.

Reconciliation runs the signed-inventory check before any balance or ledger
write. Unexpected shorts quarantine that pass with an alert. Fresh inventory
determines holdings and quantities; cached portfolio data can contribute
marks only when its quantity agrees. Do not automatically cover shorts or
rewrite historical long/short accounting as part of this safeguard.

Reconciliation rechecks inventory after pricing/fill retrieval and before
balance and position mutations; if quantities changed it stops further writes
and retries on a later cycle. A completed full exit requires both its own filled
order and a broker-confirmed flat position. Archived sell fills are account-scoped
and must match the recorded long quantity; excess sales cannot be averaged into
one apparently ordinary close. Partial-sale protection uses the confirmed
remaining quantity, never an unverified subtraction after a competing fill.
It is rechecked after replacement protection settles and before partial-sale
accounting. A stop filling the remainder during that wait preserves the original
lot for reconciliation; the scale-out cutoff otherwise uses the partial order's
actual final execution time. Manual exits likewise reject unexplained
broker/ledger differences before cancelling protection, and only their own
validated executions can explain a smaller remaining quantity.
Reconciliation no longer performs share-only corrections on unexplained quantity
mismatches. Together with exact ledger/broker agreement before scale-out and
after cancellation, this preserves evidence and blocks repeat scale-outs even
across later cycles or restarts. A partial replacement-stop fill therefore needs
accounting review, not another automatic trim. This is intentionally stricter
for manual partial sales and corporate-action adjustments too.

Flex's historical aggregate does not carry selected-account/current-lot
provenance. Retain its fetch/parser for diagnosis, but never accept a returned
unscoped aggregate as automatic closing evidence, even if its quantity happens
to match. Alert and preserve the ledger instead of silently substituting a quote.

## Consequences and operational limits

An ordinary stock SELL is not broker-enforced reduce-only: client-side
checks cannot guarantee safety against an independent manual/client order
racing after the last snapshot. Operators must coordinate account activity,
review broker positions and orders after an incident, and keep the stopped
agent stopped until its account state and deployment are understood.

No trading threshold is retuned, and no profitability improvement is claimed.
The dedicated observe-only workflow and intraday recording deployment remain
separate work. This does not repair the existing SHIP trade-history row,
which combines evidence from two sales into one apparent long close.
The existing 3-day routine-log retention is unchanged; it limits historical
attribution, not the offline evidence establishing these safety defects.

Current behavior is documented in `docs/sell_logic.md`, `docs/buy_logic.md`,
`docs/configuration.md` and `README.md`. The relocated short-detection path is
recorded in `docs/retired_code.md`.
