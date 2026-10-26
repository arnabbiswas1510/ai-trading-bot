# Research on the filesystem: export first, then cut over

This migration is **operator-run**. Applying patch 120 does not export, import,
delete data or switch a running service. Keep the existing research database
until every preservation and cutover step has succeeded.

On the connected machine, start from the same repository revision and apply:

```bash
git am 120_move-research-to-filesystem.patch
```

The patch already includes regenerated graph artifacts; do not run a second
graph update. Push from the authorized machine through the normal pipeline,
but keep the runtime backend at `supabase` until the final import is complete.
Do not overwrite the production `.env` with `.env.template`. Preserve existing
secrets, approved strategy overlays and real-entry permission.
Preserve the existing research image digest and its Python/package versions.
Checkpoint compatibility includes the Python patch version and numerical
package versions, not just strategy settings. This storage migration does not
authorize bypassing those checks or resetting existing experiments if an
unrelated image/dependency upgrade makes them incompatible.

## What moves, and what stays

All 19 research tables move to a dedicated PostgreSQL database stored on the
production filesystem: captures and membership, shadow runs/events/checkpoints,
calibration settings/proposals/audits/leases, replay results, reporting
state/receipts/incidents/artifacts and exit-shadow observations. The capture
session view is reconstructed from its underlying data.

The dashboard, worker analysis and reports use local PostgREST, the HTTP adapter
for PostgreSQL. No database or research API port is published to the LAN.
Live positions, real trades, fills, watchlists, triggers, balances, live-entry
controls and operational `agent_logs` stay on their existing paths.
Neither migration nor research can authorize a real order.

The existing weekly `supabase_backup.py` still exports only its 13 trading-state
tables. Its Parquet layout is useful for review, but it is not a lossless
research migration: JSON values, database functions and transaction semantics
also need preservation. The separate migration tools preserve native
PostgreSQL data and portable table exports.

**This does not reset billed bandwidth.** The reported 12.38 GB against 5.5 GB
is egress already consumed. The initial export also transfers data. Moving
ongoing research eliminates its repeated cloud reads/writes, not all Supabase
traffic: live operation, diagnostics and live-source reads still use Supabase.
Check the provider's usage page after migration; no fixed live-only usage is
guaranteed by this patch.

## Prerequisites and safety

Use a machine with durable connectivity, or run the commands directly on the
production host. Native PostgreSQL export requires the database connection
string and password from Supabase's **Connect** dialog. The existing
`INTRADAY_SUPABASE_KEY` is an HTTP credential and cannot authenticate `pg_dump`.
Use the direct connection or **session** pooler with certificate verification;
the transaction pooler does not preserve the session needed for a snapshot.
Never put passwords in Git, chat, command arguments or shell history.

Keep credentials in a permission-600 environment file outside the repository.
Restrict archives to the operator. They contain private account snapshots.
Do not upload them to public viewers. Preserve existing producer spools and
Docker volumes; they may contain accepted observations not yet delivered.

Perform final cutover in an operator-approved maintenance window after the
market closes. A preliminary export can run while workers are active, but it
cannot justify deleting rows added or updated later. The final export, import
and cleanup must agree on the full contents. Do not stop broker protection to
make an archive consistent.

Before the final snapshot, disable the hosted research job and pause research
producers/automatic comparisons using the documented runtime controls.
Leave real protection running. Do not permit manual dashboard research writes
during this window. Switching research backend is separate from
`TRADING_RUNTIME_MODE` and the dashboard's real-entry switch.

## Commands

These commands run **on the production Linux host**, reached from your connected
machine. That avoids publishing PostgreSQL ports or transferring database
credentials through a public endpoint. Keep your SSH session inside `tmux` if
available so an operator-network disconnect does not terminate a long export.
The migration scripts never run automatically during deployment.

### 1. Install the tools and prepare private storage

From the checkout where you applied the patch, copy the operator scripts to
the git-free host if the deployment has not copied them:

```bash
scp -i ~/.ssh/id_ed25519_dietpi_pom \
  scripts/research_migration.py scripts/research_bootstrap.py \
  scripts/research_pg_tools.py scripts/requirements-research-migration.txt \
  scripts/backup_local_research.sh \
  pom@192.168.10.2:/home/pom/docker/ai-trading-bot/scripts/
```

Use your working SSH hostname/key if this machine is outside the LAN. The LAN
host listens on port 22; do not assume it is reachable from the public internet.
The normal deployment must also install the patched Compose file, runtime
script and newly built images before cutover.

On the production host:

```bash
cd /home/pom/docker/ai-trading-bot
umask 077
mkdir -p research-archives
python3 -m venv "$HOME/.venvs/research-migration"
"$HOME/.venvs/research-migration/bin/pip" install \
  -r scripts/requirements-research-migration.txt
PY="$HOME/.venvs/research-migration/bin/python"
docker pull postgres:17
```

`--docker-tools` supplies native `pg_dump`, `pg_restore` and `psql` using
disposable PostgreSQL client containers, not the application's broker
containers. The Python process still needs network access to both databases.
It uses credentials through private environment/configuration, not command
arguments. Do not turn on shell tracing (`set -x`).

Create a private environment file, for example
`$HOME/.config/ai-trading-bot/research-migration.env`, in an editor and restrict
it to mode `0600`. Put the **native source connection string** in
`SOURCE_RESEARCH_PG_DSN`, with valid shell quoting and TLS verification.
Keep it outside the repository. The preflight requires permission to inspect
the source cluster identity using `pg_control_system()` as well as read/export
research; cleanup also requires ownership/admin privileges. If managed
PostgreSQL denies a required permission, stop and resolve it with the database
administrator. A REST key or a less privileged fallback is not sufficient.

Load only this trusted, operator-authored file:

```bash
set -a
. "$HOME/.config/ai-trading-bot/research-migration.env"
set +a
```

### 2. Create the isolated local database, without switching workers

```bash
"$PY" scripts/research_bootstrap.py secrets --output research-local.env
```

This creates a new permission-600 file and refuses to overwrite one. In a
private editor, merge its four generated assignments into the existing host
`.env`: `RESEARCH_POSTGRES_PASSWORD`, `RESEARCH_API_PASSWORD`,
`RESEARCH_JWT_SECRET`, `RESEARCH_DATABASE_KEY`. Replace duplicate assignments;
do not print or paste values into chat, logs or Git.
Keep `RESEARCH_STORAGE_BACKEND=supabase` for now. Add the non-secret
`RESEARCH_DATABASE_URL=http://research-api:3000` and your host storage root
`RESEARCH_DATA_DIR=/home/pom/docker/ai-trading-bot/research-data`.
Restrict both secret files to `0600`.

```bash
docker compose up -d --wait --wait-timeout 120 research-db
set -a
. ./research-local.env
set +a
DB_IP=$(docker inspect research-db \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
export LOCAL_RESEARCH_PG_DSN="host=$DB_IP port=5432 dbname=research user=postgres password=$RESEARCH_POSTGRES_PASSWORD sslmode=disable"
"$PY" scripts/research_bootstrap.py roles \
  --confirm 'BOOTSTRAP LOCAL RESEARCH ONLY'
"$PY" scripts/research_migration.py --docker-tools prepare
```

The local DSN's `sslmode=disable` is for the private host Docker bridge only,
not Supabase or an internet connection. The database has exactly one private
network; do not publish its port to make these commands work.
Recalculate `DB_IP` after container replacement. The preflight rejects a wrong
inventory, unsupported dependencies or source/target identity collision.

The generated API JWT expires after 365 days. Renew it before expiry using
`research_bootstrap.py token --output NEW_PRIVATE_TOKEN_FILE` with the existing
`RESEARCH_JWT_SECRET` loaded. Replace only `RESEARCH_DATABASE_KEY` in `.env`
and recreate consumers during maintenance. Do not regenerate administrator
passwords or the signing secret as a token-renewal shortcut.

### 3. Take the final snapshot during research maintenance

Finish any active research requests. Set the GitHub repository Actions
variable `RESEARCH_STORAGE_BACKEND=local`, and wait for any already-running
**Intraday Research Collection Watchdog** invocation to finish.
This disables only that research job, not screening or trading backups.
Prevent automatic deployment and the host's scheduled 06:00 container-restart
job from running during this maintenance window; otherwise they can restart
the paused research workers. Record and restore any temporarily suspended
schedule afterward. Do not stop the running execution agent or gateway for
the duration of the export.

Stop the three independent research workers:

```bash
docker compose stop intraday-observer shadow-worker calibration-worker
```

Temporarily set `INTRADAY_CAPTURE_ENABLED=false`,
`EXIT_SHADOW_LOG_ENABLED=false`, and `INTRADAY_AUTO_COMPARE=false` in host
`.env`, recording their prior values privately. Recreate only the execution
and web services to apply these flags:

```bash
docker compose up -d --no-deps execution-agent trading-bot
docker inspect execution-agent --format '{{.State.Status}}'
```

This briefly reconnects the execution agent; it does **not** stop broker-held
protective orders or change live-entry permission. Perform it only in the
approved maintenance window, and confirm protective execution is healthy
before proceeding. Do not restart/recreate the gateway.
Do not use the dashboard's research controls until maintenance ends.
Preserve all spool files/volumes; graceful worker shutdown retains pending
records for delivery after cutover.

```bash
SNAPSHOT="/home/pom/docker/ai-trading-bot/research-archives/$(date -u +%Y%m%dT%H%M%SZ)"
"$PY" scripts/research_migration.py --docker-tools export --output "$SNAPSHOT"
"$PY" scripts/research_migration.py check-archive --archive "$SNAPSHOT"
```

No export option deletes source data. An incomplete export is not usable for
cleanup. If any source row changes afterward, the locked cleanup comparison
fails even if the row count stays the same. Investigate remaining writers;
do not bypass the comparison.

### 4. Make a second copy and import

Mount a separate backup device or a private share backed by another machine.
Substitute its real mount point below. Do not create an empty directory and
pretend that it is mounted; the tools reject a copy on the same filesystem:

```bash
SECOND="/mnt/research-backup/$(basename "$SNAPSHOT")"
"$PY" scripts/research_migration.py archive \
  --archive "$SNAPSHOT" --destination "$SECOND"
"$PY" scripts/research_migration.py --docker-tools import \
  --archive "$SNAPSHOT" --workdir "${SNAPSHOT}.import-work" \
  --confirm 'IMPORT INTO EMPTY LOCAL RESEARCH' --trust-archive
"$PY" scripts/research_migration.py verify --archive "$SNAPSHOT"
```

The second-copy check requires a different filesystem/device, not another
directory on the same disk. The importer requires a fresh target and explicitly
trusted archive because database restore includes executable SQL functions.
Use only your own verified export. Full readback must succeed before its
database-local import proof is marked verified.

### 5. Prepare local routing, then explicitly clear cloud research

Set host `.env` to `RESEARCH_STORAGE_BACKEND=local`. Keep all three temporary
research flags false and the independent research workers stopped. Provision
`GITHUB_REPOSITORY=arnabbiswas1510/ai-trading-bot` and a host `GITHUB_TOKEN`
with this repository's **Issues: read/write** permission, plus the existing
Telegram credentials. The Actions job's temporary token is not a permanent
host credential.

Start the private API and apply local routing to execution/web while research
remains paused:

```bash
docker compose up -d research-api
docker compose up -d --no-deps execution-agent trading-bot
docker compose exec -T execution-agent python -c \
  'from research_storage import backend,research_client; assert backend()=="local"; print(research_client().table("intraday_calibration_settings").select("id").execute().data)'
```

Do not visit research pages or run analysis before the final verification:
the exact imported target must still match the archive. The export tools do
not infer that consumers are paused merely because a confirmation was typed.

```bash
"$PY" scripts/research_migration.py verify --archive "$SNAPSHOT"
read -r MANIFEST_SHA < "$SNAPSHOT/COMPLETE"
"$PY" scripts/research_migration.py purge \
  --archive "$SNAPSHOT" --second-copy "$SECOND" \
  --manifest-sha256 "$MANIFEST_SHA" \
  --confirm 'PURGE EXPORTED CLOUD RESEARCH ONLY' \
  --cutover-confirm 'RESEARCH WRITERS PAUSED AND ROUTED LOCAL'
```

**This is the only destructive command.** It rechecks archive integrity,
the independent copy, identities, imported evidence and locked source contents,
then revokes cloud research API access and empties only the named research
tables. It does not drop live tables or use cascading truncation.
If it fails, keep the archives and paused research state, read the failure and
resolve it; do not issue a manual `DELETE` to work around a guard.

### 6. Resume research and check the actual destination

Restore the three recording/comparison flags to their prior values in `.env`.
Keep both host and GitHub backend settings at `local`.

```bash
sh scripts/deploy_runtime.sh
docker inspect research-db research-api research-reporting \
  intraday-observer shadow-worker calibration-worker execution-agent \
  --format '{{.Name}} {{.State.Status}}'
docker compose exec -T research-db psql -U postgres -d research -c \
  'SELECT id, last_seen_at FROM intraday_capture_health ORDER BY id'
```

The deployment helper checks the database-local import proof and authenticated
settings access before replacing consumers. Check advancing local observations,
spool delivery, calibration state and reports during actual market hours.
Require real output, not just running containers. Preserve source archive
timestamps: an imported old heartbeat is not proof of current health.

## Recurring local archives and separate review

The same exporter can snapshot the local database while research continues.
It takes one consistent PostgreSQL snapshot rather than copying live data files:

```bash
SNAPSHOT="/home/pom/docker/ai-trading-bot/research-archives/$(date -u +%Y%m%dT%H%M%SZ)"
"$PY" scripts/research_migration.py --docker-tools export \
  --database local --output "$SNAPSHOT"
"$PY" scripts/research_migration.py check-archive --archive "$SNAPSHOT"
"$PY" scripts/research_migration.py archive --archive "$SNAPSHOT" \
  --destination "/mnt/research-backup/$(basename "$SNAPSHOT")"
```

The provided wrapper performs those steps, loads a private operator-owned
environment file, recalculates the container address, uses `umask 077` and
exits on failure:

```bash
bash scripts/backup_local_research.sh \
  "$HOME/.config/ai-trading-bot/research-backup.env" /mnt/research-backup
```

The private file needs `RESEARCH_POSTGRES_PASSWORD`. Optional
`RESEARCH_ARCHIVE_DIR` changes the primary archive directory.
`RESEARCH_MIGRATION_PYTHON` may be set in the calling environment to choose
another Python environment; the default is the venv created above.
No cloud credential is needed for these local snapshots.
After a successful manual run, add an operator cron entry, for example Sunday
15:00 in the host's timezone:

```cron
0 15 * * 0 /bin/bash /home/pom/docker/ai-trading-bot/scripts/backup_local_research.sh /home/pom/.config/ai-trading-bot/research-backup.env /mnt/research-backup >> /home/pom/docker/ai-trading-bot/logs/research_backup.log 2>&1
```

The backup device must be mounted. The wrapper does not remove older snapshots
and never runs `purge`. Configure monitoring of the job's exit status, restrict
the log file, and monitor free space; cron logging alone is not an outage alert.

The portable JSONL tables can be inspected with local tools without Supabase.
Each line wraps the record in `row` and records `sql_null_json_columns`
separately: a database NULL and a JSON value of `null` are not interchangeable.
For complete application analysis use the migrated dashboard or restore the
archive into an isolated local PostgreSQL instance with the same bootstrap and
`import` command; `verify --target-only` does not require the old cloud server.
Do not attach an offline review instance to live research writers or brokerage
credentials. Keep exact numeric/JSON values from the authoritative archive;
spreadsheet conversion may round numbers or flatten structured evidence.

## Recovery and ongoing retention

Keep at least two verified copies of the final archive, including one on a
different device. A second directory on the same disk is not protection against
disk failure. Do not remove the cloud copy until the target has been restored
and verified from the archive.

Local research has no automatic age-based deletion. Existing bounded producer
queues still acknowledge delivered items; that is not deletion from the durable
research database. Watch filesystem capacity and maintain recurring native
snapshots. There is no literal unlimited disk: the last observed production
filesystem had 428 GB free on October 26, 2026, and that is not a reservation.

Back up a running PostgreSQL database using a consistent native snapshot, not
by copying its live data directory. Preserve the local credentials separately.
Never use `docker compose down -v` or delete `research-data` as a recovery step.

If import fails, keep cloud data and original spools intact; do not run cleanup.
If cutover fails before cleanup, stop research writes, investigate and choose
one authoritative store before resuming. After cleanup, setting the backend
back to `supabase` is **not** rollback: that database no longer contains the
research. Restore the verified archive to a private local instance instead.
If newer local rows exist, preserve them before any restore; never overwrite
them with an older migration snapshot.

Local reporting preserves its period keys and delivery receipts. It runs on
the same host as research, so it cannot alert when that entire host fails.
An external host-availability monitor is a separate operational responsibility.
Telegram/GitHub reporting still requires valid credentials and connectivity.

See `decisions/2026-10-26_filesystem-research-storage.md` for the rationale.
