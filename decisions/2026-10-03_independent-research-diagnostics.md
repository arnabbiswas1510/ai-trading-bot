# Independent Supabase diagnostics for research failures

Date: 2026-10-03
Status: Accepted

## Context

The operator had required diagnosis without production SSH. That requirement
was implemented for the execution agent, but not for the newer observer,
hypothetical worker and web research APIs. On October 3 the production dashboard
was reachable and acknowledged disabled real entries, while observation status,
shadow status and saved reports all returned generic research-database errors.
SSH authentication was unavailable. These responses establish an operational
visibility gap, not the underlying database failure or successful collection.

Private research health writes use the same credentials and tables as research
data. A permissions failure can therefore hide both the data and the explanation.
Several handlers also reduced exceptions to their class, discarding SQLSTATE
(the database error code) and HTTP status. Raw exception logging is not an
acceptable fix: URLs and response bodies can contain credentials or account data.

## Decision

Add a broker-independent operational diagnostic sink in `research_diagnostics.py`.
It writes bounded, allowlisted metadata to the existing `agent_logs` table using
`SUPABASE_KEY` first, independently of `INTRADAY_SUPABASE_KEY`. The private key is
used for diagnostics only if the ordinary key is absent. Do not grant public
access to private observations, shadow portfolios or performance reports.

`research_entrypoint.py` starts diagnostics before importing the web app,
observer, shadow worker, existing agent bootstrap or cloud reporter. It records
startup, uncaught failures and exit status without changing service exit behavior.
Existing execution log shipping and trading permissions remain unchanged.
Explicit events cover query/client failures, recorder stages and cloud progress,
broker observation failures, shadow blocking/upload/progress, and reporting errors.
Web research errors include a diagnostic reference and safe error category.

Queueing does not perform network I/O in API, broker or strategy threads. A
separate bounded SQLite outbox retains accepted records for retry. Each deployed
host service has a distinct spool on its existing persistent volume. Duplicates
after an acknowledgement loss are possible and retain identifying metadata.
Queue loss, spool problems and delivery failures must remain visible, not become
claims of complete logging. A total database/network outage cannot be reported
to that same database in real time; the independent GitHub/Telegram watchdog
remains necessary. The cloud runner's diagnostic spool is temporary and is not
claimed to survive replacement of that runner.

Only operational metadata is public-readable: component/event, error class/code,
HTTP status, source locations without source text or locals, credential presence
and key family/role, and bounded progress counters. No exception messages, raw
HTTP bodies, URLs, keys, account identifiers, holdings or performance records.
This is an explicit exception to the shadow worker's original "private research
state only" write boundary, limited to safe diagnostics in `agent_logs`.

Do not change the replay engine or its fingerprinted inputs to add logging.
Existing hypothetical checkpoints remain compatible; no automatic new run,
reset, migration of portfolio state or live-parameter change is authorized.

## Consequences

The existing operational key can expose a private-research permissions failure
without access to the private dataset or host. Startup credential metadata does
not prove permissions; the recorded database response code is the evidence.
An entry queued locally is not proof it reached Supabase. Diagnostic heartbeats
are not proof of broker coverage or successful simulated decisions.

This patch does not identify or repair the already-deployed private-access
failure, recover unrecorded sessions, or establish benchmark results. It makes
the next occurrence diagnosable after deployment. No new Supabase migration or
strategy parameter is required.

See `docs/intraday_research.md`, `docs/configuration.md`,
`docs/ibkr_totp_setup.md` and `README.md`.
