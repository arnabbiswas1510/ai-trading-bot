# Weekly-backup ship step: trust the host key on connect, not via a separate scan

- **Date:** 2026-09-28
- **Status:** Accepted

## Problem

The weekly Supabase backup (`weekly_supabase_backup.yml`) had been failing on
most scheduled runs. Investigation on 2026-09-28 found **two independent
causes**, and this ADR records both so a future reader does not conflate them.

### Cause 1 — the latest run (2026-09-27): missing table, export step

`exit_shadow_log` was added to `TABLES` in `supabase_backup.py` on 2026-09-27
(commit e6cce81, the exit-shadow logger) together with migration
`20260927_add_exit_shadow_log.sql`. Migrations in this project are applied by
hand in the Supabase SQL Editor, and this one had not been applied. PostgREST
therefore returned `PGRST205 "Could not find the table 'public.exit_shadow_log'"`,
the "loud on partial failure" export marked the table `failed`, and the job
exited non-zero. This also meant the live agent's 15-minute shadow-decision
logging was silently dropping every row (the insert in `monitoring.py` is
guarded so it never crashes the agent). **Fix: apply the migration.** No code
change — the migration already existed and is idempotent.

### Cause 2 — runs #2–#5 (2026-08-23 … 2026-09-13): the rsync ship step

Those runs exported Supabase fine and died at **"Ship to production server"**,
the `rsync` over SSH to the home DietPi host. This ADR addresses that step.

Two facts had to be separated first:

- **Port 2222 is not misconfigured.** The router forwards *external* port 2222
  to the host's *internal* port 22. `deploy_to_server.yml` uses the identical
  `DEPLOY_HOST:2222` and deploys succeed routinely, which proves the host/port
  are correct. (On the LAN, connecting to `192.168.1.2:2222` is *refused*
  because sshd listens on 22 there; that is expected, not a fault.)
- **The ship step was more brittle than the deploy, by design.** It ran a
  separate one-shot `ssh-keyscan -p 2222` into `known_hosts`, then rsync'd with
  `StrictHostKeyChecking=yes`. If that ~5-second scan hit a momentarily slow or
  unreachable home network it returned nothing, `known_hosts` stayed empty, and
  rsync then hard-failed *"Host key verification failed"* — even if the host
  became reachable a second later. There was no retry. The home hop is the only
  weekly-scheduled connection back to a box on a home internet line, so any
  transient unreachability in that one window sank the run.

## Decision

Drop the separate `ssh-keyscan` and let ssh accept the host key on the rsync
connection itself, using `StrictHostKeyChecking=accept-new`.

`accept-new` auto-accepts a never-before-seen host key on first connect but
still refuses if a *previously known* key changes. Each GitHub runner is
ephemeral with an empty `known_hosts`, so every run is a clean first connect.

## Why this is not a security downgrade

The old `ssh-keyscan`-then-`StrictHostKeyChecking=yes` sequence *looked* like
key pinning but was only **trust-on-first-use**: the scan grabbed whatever key
answered on that connection, and rsync then checked against that same
just-grabbed key. A man-in-the-middle would answer the scan with its own key and
pass the check. `accept-new` provides the *same* TOFU on the rsync connection —
identical actual protection — minus the fragile separate scan and its 5-second
single-shot window. It also matches how `deploy_to_server.yml` already trusts
this host (via `appleboy/*` actions with lenient host checking), and the backup
only pushes data *to* the box, a lower-stakes payload than the deploy's
executable `docker-compose.yml`.

## What would make this a real pin (not done here)

Genuine MITM protection needs the host's public key captured once (at home) and
stored as a `DEPLOY_KNOWN_HOSTS` GitHub secret that the workflow writes verbatim
into `known_hosts` before an rsync with `StrictHostKeyChecking=yes` — no live
scan at all. That requires a manual operator step to generate the secret and was
deliberately **not** done in this change; a retry was explicitly declined.

## Consequences

- The ship step no longer fails spuriously when the home network is briefly slow
  during a single scan window; it fails only if the host is genuinely
  unreachable for the whole rsync, which is the correct behaviour.
- Security is unchanged (TOFU either way). If real pinning is wanted later, the
  `DEPLOY_KNOWN_HOSTS`-secret path above is the upgrade.
- Cause 1 (the `exit_shadow_log` 404) is fixed operationally by applying
  `20260927_add_exit_shadow_log.sql`, not by this workflow change.
