# Remove the `frontend/dist` bind mount; serve the UI from the image only

**Date:** 2026-08-15
**Status:** Accepted
**Supersedes:** the "hot-swap built UI" mount introduced in `docker-compose.yml`

## Context

The position lifecycle / risk-tier dashboard (ADR
`2026-08-14_position-lifecycle-visibility.md`) was built, tested, committed,
built into an image by CI, pulled by the production box and restarted — and the
dashboard still rendered the *old* panel, including three cards that no longer
exist anywhere in the source tree.

Every layer of the pipeline reported success:

- `npm run build` passed 18/18 fingerprints in `scripts/verify-build.mjs`
- `docker_build_push.yml` built the web image with
  `no-cache-filters: frontend-builder`, forcing a fresh frontend compile
- `deploy_to_server.yml` pulled the new image and recreated the container
- `docker compose ps` showed the container healthy

## Root cause

`docker-compose.yml` mounted a host directory over the image's compiled assets:

```yaml
volumes:
  - /home/pom/docker/ai-trading-bot/frontend/dist:/app/frontend/dist  # hot-swap built UI
```

The bind mount shadows `/app/frontend/dist`. Whatever the image contains is
irrelevant — the container serves the host directory.

That host directory is never updated:

- `dist/` is matched by `.gitignore` (`git check-ignore` →
  `.gitignore:3:dist/ frontend/dist/`)
- so `frontend/dist` is not tracked in the repository (0 files in `HEAD`)
- so the deploy's `git fetch --all && git reset --hard origin/main` cannot
  refresh it

The result is a permanent stale UI: the box serves whichever bundle was last
compiled by hand on that machine, and no amount of correct building, pushing,
pulling or restarting can change it.

The failure is silent, which is what makes it expensive. There is no error in
CI, in the deploy log, in `docker compose ps`, or in the container logs. The
only symptom is that the browser shows old code — which is indistinguishable
from a caching problem, and invites debugging in entirely the wrong place.

It also silently voided the `verify-build.mjs` safety net. That script exists to
guarantee a stale bundle can never reach production; it does guarantee that for
the *image*, but the image's `dist` was never being served.

## Decision

1. **Remove the bind mount.** The compiled UI ships inside the image and is
   served from there. This makes the image the single source of truth for what
   the dashboard renders, and restores `verify-build.mjs` to being a real gate.

2. **Assert the served build after every deploy.** `deploy_to_server.yml` now
   reads `/api/version` (which returns the `GIT_COMMIT` build arg) and fails the
   job if the served commit does not match the commit checked out on the server.
   A stale UI becomes a red deploy instead of a silent one.

3. **Record the constraint where it will be read.** A comment at the mount site
   in `docker-compose.yml` and a "Deploying the dashboard" section in
   `docs/configuration.md` explain why the mount must not come back.

## Rejected alternatives

**Keep the mount and have the deploy rebuild the bundle on the box.** Requires
Node on the DietPi host, makes deploys slow on ARM, and re-introduces the
possibility of the host and image disagreeing. The image already builds the
bundle correctly; the fix is to stop overriding it.

**Commit `frontend/dist` so `git reset --hard` refreshes it.** Puts build output
in version control, produces enormous diffs on every UI change, and creates a
second source of truth that can drift from the image.

**Treat it as a caching problem and document a hard-refresh step.** Would not
have worked — the served bytes were genuinely old.

## Consequences

- UI changes now require an image rebuild to appear, which is already how every
  deploy works. There is no longer a way to hot-swap the bundle on the box; that
  capability was the direct cause of this incident.
- The stale `frontend/dist` directory left on the production server is inert
  once the mount is removed, and can be deleted.
- Any future "deployed but the UI is old" report is now answered by the deploy
  job itself, which will have failed with the served vs expected commit.

## Related

- `2026-08-14_position-lifecycle-visibility.md` — the change whose absence
  exposed this
- `docs/configuration.md` § Deploying the dashboard
