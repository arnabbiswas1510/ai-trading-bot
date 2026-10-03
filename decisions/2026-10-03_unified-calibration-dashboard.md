# Unified, evidence-scoped calibration visibility

Date: 2026-10-03
Status: Accepted; production availability requires applying and deploying the patch.

## Context

The operator wants seamless visibility into collection, simulated trades,
strategy comparisons and interactive improvement decisions. A research inbox
alone does not show whether data is arriving, whether a candidate has seen future
observations, or which stocks explain its apparent advantage. The existing app
also gates all pages on real-portfolio loading, hiding research during a failure
of the operational database.

The operator selected a unified Calibration dashboard with detailed tabs.
This is visibility over the previously approved research architecture, not
permission to alter trading rules, risk tolerances, or entry permissions.

## Decision

Add a dedicated sidebar page before the live-portfolio loading/error guards.
Poll collection, shadow and calibration data independently, with explicit source
errors. Keep the selected experiment's source window and content fingerprint
visible. Do not substitute training results when future evaluation is absent.
Explain sampled drawdown, open-position valuation, modeled costs and ticker
contributions; display frozen risk limits without claiming that they grant
deployment approval.

Expose a bounded read-only activity endpoint over stored shadow event outputs.
Freeze the published sequence boundary on the first page and use exclusive
sequence cursors for older pages. Verify checkpoint seals and recorded run
provenance, not compatibility with today's engine: historical visibility is not
an attempt to replay old inputs using new code. Reject missing page-top rows,
internal sequence holes and malformed output. Preserve explicit gap events;
label pruned prefixes. Never use a replay or broker call to populate this view.

Chart only actual stored page-local equity marks. Do not fabricate a challenger
curve from aggregate metrics, join separate runs, or sum overlapping reports.
Distinguish reference simulation holdings from real holdings and the candidate.
Absent measurements remain unavailable, not zero.

Reuse the existing approval inbox rather than creating another authorization
path. Link benchmark selection to that inbox and preserve drafts and the
memory-only token while switching tabs. Keep Backtester access intact.
No feature, rule, configuration constant or approval safeguard is removed.

## Consequences

The new page can explain partial failures without implying calibration succeeded.
Activity pagination is stable while a worker appends records. The activity curve
is deliberately not a full-history chart, and the page does not claim an approved
artifact has reached the real executor.

No schema migration, strategy change, new dependency or production action is
required by this visibility addition itself. It depends on the calibration
services and migration delivered with patch 107. Production collection status
remains unverified until an actual successful operator deployment and inspection.

Current operator instructions: `docs/interactive_calibration.md` and
`docs/intraday_research.md`.
