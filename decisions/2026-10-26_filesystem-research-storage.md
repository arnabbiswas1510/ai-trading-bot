# Move research out of Supabase onto the production filesystem

- **Date:** 2026-10-26
- **Status:** Accepted for operator-run migration; production cutover is not implied
- **Scope:** Research persistence, reporting location, export and guarded cleanup

## Context

Supabase reported 12.38 GB of egress against a 5.5 GB allowance. This is
download bandwidth, not database size. Deleting historical rows cannot undo
charged bandwidth. Periodically exporting then deleting research would also
break existing analysis, which reads the database, and leave workers refilling
Supabase. Research evidence must survive independently of that service.

The operator explicitly approved local PostgreSQL backed by a persistent host
directory, rather than rewriting calibration's transactional SQL in SQLite.
The workstation cannot push; delivery is a numbered patch and an operator
runbook. No export, production migration or deletion is claimed by this ADR.

## Decision

Use PostgreSQL and PostgREST on private Docker networks for all 19 research
tables and their derived session view. Existing database functions retain
calibration revisions, approvals, audit transactions and leases. Preserve the
producer SQLite spools; they deliver to the selected research store.

`RESEARCH_STORAGE_BACKEND=supabase` remains the compatibility default.
An explicit `local` selection routes research to `RESEARCH_DATABASE_URL` with
`RESEARCH_DATABASE_KEY`, without falling back to cloud storage on failure.
Live watchlists, triggers, positions, trades, controls and operational
`agent_logs` remain on their existing paths. Research still reads live source
tables when needed; this is not a promise of zero Supabase traffic.

Native PostgreSQL snapshots and portable per-table files preserve existing
research before any destructive operation. Export, verification, import and
cleanup are separate operator actions. Cleanup requires an intact independent
archive copy, verified local import and unchanged source content. Unknown
research tables, schema differences, external dependencies or concurrent
changes block cleanup. No live table is a cleanup target.

Move report generation to the host for local storage. Disable the cloud
research workflow through its explicit backend variable during cutover.
The local reporter cannot detect failure of the host on which it runs; it is
not a replacement for independent host-availability monitoring. Keep notification
receipts and incident history during migration, and preserve the real-entry
permission and broker protection.

Local raw research is not subject to automatic cloud-retention deletion.
Disk is finite, not unlimited. Maintain recurring filesystem archives, a
separate-device copy, disk monitoring and a restore procedure. The existing
13-table weekly trading backup remains unchanged and cannot recover research.

## Consequences

The high-volume research reads and writes no longer consume Supabase quotas
after cutover. This does not reset accrued usage, establish that live traffic
fits a particular plan, or cure unrelated diagnostic-log bandwidth.

A brief coordinated maintenance window is necessary for final consistency.
Installing images alone does not migrate storage or delete cloud data.
Never disable protective execution merely to export research, never delete a
producer spool, and never expose the research database/API publicly.

See [the migration runbook](../docs/filesystem_research.md),
[research operation](../docs/intraday_research.md),
[configuration](../docs/configuration.md) and [backups](../docs/backups.md).
