# Backup vault credentials and isolated private exit-shadow persistence

- **Date:** 2026-10-04
- **Status:** Superseded in part by [trading-only backup scope](2026-10-04_trading-only-backup-scope.md)
- **Scope:** Operational credentials and research persistence; no trading rules

## Context

> **2026-10-04:** The 27-table backup requirement and backup dependency on the
> private research key are superseded: 13 trading-state tables remain required;
> all calibration/benchmarking research is excluded. Bitwarden bootstrap,
> input handling and isolated private research writes remain in force.

The September 27 scheduled backup failed during export because PostgREST could
not expose `exit_shadow_log` (`PGRST205`). Shipping was skipped. October 4
read-only checks reproduced the failure with both ordinary and private
credentials; the operator subsequently reapplied the existing migration.

The monitoring code also used the ordinary Supabase client to insert into a
table whose row-level security permits only `service_role`. Production's
ordinary key has the `anon` role. The missing-table error hid that second
authorization mismatch, and the blanket suppression of PGRST errors made both
problems invisible. A successful empty read does not prove inserts work.

The repaired research watchdog already loads application credentials from
Bitwarden. The backup still depended on individually maintained Actions
secrets, so a green watchdog did not validate the backup's credential source.

## Decision

1. The backup wrapper reuses `load_credentials()` from
   `scripts/run_intraday_reporting_bws.py`, including exact project/secret
   validation, masked output and the no-cache `scripts/bws_ci.toml` profile.
   The workflow installs the same checksum-pinned Bitwarden CLI. Only
   `BWS_ACCESS_TOKEN` bootstraps application credentials; deployment SSH secrets
   remain separate. Vault values go directly to the exporter subprocess
   environment, never to secret files. The child does not inherit `BWS_*`.
2. Workflow inputs are environment values passed through a quoted Bash
   argument array. Snapshot dates must be canonical valid `YYYY-MM-DD` dates
   before any client or output file is created. Existing dry-run semantics and
   exporter exit codes are preserved.
3. `exit_shadow_store.py` owns an independent cached Supabase client, requiring
   `INTRADAY_SUPABASE_KEY` without ordinary-key fallback. Its PostgREST timeout
   is 5 seconds so a research request cannot inherit an unbounded wait in the
   live monitor. The global execution-agent client and private table policy
   remain unchanged.
4. Research persistence remains nonfatal to live exits. Both calculation and
   write failures emit credential-safe independent diagnostics and a console
   warning, including safe category/error codes rather than raw messages or
   position payloads. Missing-table and permission failures are not suppressed.
5. At the operator's explicit request, all ten previously unclassified tables
   become **required** weekly exports: `daily_notifications`;
   `intraday_research_calibration_artifacts`,
   `intraday_research_delivery_receipts`, `intraday_research_incidents`,
   `intraday_research_reporting_state`, `intraday_research_reports`;
   `intraday_shadow_checkpoints`, `intraday_shadow_events`,
   `intraday_shadow_health`, `intraday_shadow_runs`. The inventory grows from
   17 to 27 tables. This retains calibration evidence with its seed, published
   history, reports and operational/delivery context instead of merely making
   the completeness test pass through exclusions. Ordering follows each
   primary key: `(report_type, report_date)` for daily notifications,
   `(run_id, sequence)` for shadow checkpoints and `id` for the other eight.
   This supersedes the prior deliberate omission of daily notifications.

The operator accepts retaining the included operational snapshots. That does
not authorize restoring stale scheduler leases as active or accepting archived
health timestamps as current; restoration must reconcile those fields before
workers resume. Existing exclusions for raw captures, agent logs, calibration
health and calibration leases remain unchanged.

## Verification and operational limits

Offline tests cover vault bootstrap reuse, missing-private-key refusal, safe
argument/exit-code forwarding, date validation, isolated writer credentials,
nonfatal credential-safe diagnostics and continued protective-order logic with
both calculation and write failures. They use synthetic credentials and mocks.

The deployment must include `exit_shadow_store.py`; `Dockerfile.agent` copies
it explicitly. No fake observation may be inserted merely to test the writer.
After deployment, a naturally occurring monitor observation establishes
end-to-end success. Empty-table reads alone cannot.

Successful table files from a failed export exist only on the disposable
runner: shipping is skipped and no fallback artifact is retained. Verify a
new dated server manifest after a successful rerun, not just green bootstrap.
The existing migration/source completeness test verifies every known table is
classified, with focused tests keeping these ten tables required. Sequential
REST exports are not a cross-table database transaction; restoring all evidence
still requires validating references and complete published sequences.

See `docs/backups.md` for current operation and `docs/retired_code.md` for the
replaced paths.
