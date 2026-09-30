# Share the Bitwarden bootstrap token; resolve the project by name

- **Date:** 2026-09-29
- **Status:** Accepted
- **Supersedes (in part):** `2026-09-28_bitwarden-project-scoped-secret-list.md`,
  which required `BWS_PROJECT_ID` to come from the bootstrap file.

## Context

Two applications now run on this host — this trading bot and the Garmin AI
coach — and both authenticate to Bitwarden Secrets Manager as the *same*
machine account. The bootstrap token file therefore contained something common
to both, but lived at `~/.config/ai-trading-bot/bws.env`, i.e. inside one
application's directory.

The coach worked around this with a symlink: `~/.config/bws/bws.env` →
`~/.config/ai-trading-bot/bws.env`. That is upside-down. The trading bot's
directory owned a file the coach depended on, so removing or rotating this
app's config could silently break an unrelated application, and nothing in
either repository recorded the coupling.

The file also mixed two kinds of value:

| Key | Scope |
|---|---|
| `BWS_ACCESS_TOKEN` | genuinely shared — same machine account for both apps |
| `BWS_PROJECT_ID` | **per-application** — identifies *this* app's project |

Centralising the file as-is would have published this application's project id
to every app that reads it. That is precisely the failure the previous ADR
guards against: secrets are matched downstream by env-var *name*, so a
same-named key resolved against the wrong project silently renders the wrong
credential. The coach already defends against it by discarding any
file-supplied `BWS_PROJECT_ID`; relying on every future reader to remember that
is not a control.

## Decision

Split the file by scope, and move only the shared half.

**The shared file holds only the token.** `~/.config/bws/bws.env` contains
`BWS_ACCESS_TOKEN` and nothing else. It is a neutral location owned by neither
application.

**The project is resolved by name.** `BWS_PROJECT_NAME` (default
`ai-trading-bot`) is configuration belonging to *this repository*, so the shared
file no longer needs to carry anything app-specific. `render_env.sh` resolves
the name to an id via `bws project list`, mirroring what the coach already does.

**A project id found in the bootstrap file is discarded.** Only an explicit
`BWS_PROJECT_ID` exported into the environment overrides name resolution. A
value read from the shared file is logged and ignored, because it belongs to
whichever application wrote it.

**An app-specific file still wins if present.** The search order is
`~/.config/ai-trading-bot/bws.env`, then `~/.config/bws/bws.env`, so this app
can be given a different token later without disturbing the coach.

**`build_secret_map()` is scoped and refuses ambiguity.** Ported from the
coach: it filters by `projectId` and raises on conflicting duplicate keys rather
than letting the last one win. `render_env.sh` already scopes the `bws secret
list` call, so this is defence in depth — but with one token now reaching two
projects, the cost of a silent mismatch went up.

## Consequences

- The symlink is gone and neither application's config directory owns the
  other's dependency.
- The fail-closed contract is preserved: no bootstrap file, no resolvable
  project, or any unmet `@bws` sentinel leaves the existing `.env` untouched and
  exits non-zero. Verified end-to-end against a stub `bws`.
- One extra `bws project list` call per render. Renders are manual and
  infrequent, so this is not a concern.
- Rotating the machine-account token is now a single-file operation that fixes
  both applications at once — previously it had to be done in a path that looked
  like it belonged to only one of them.
- Existing deployments that still have an app-specific `bws.env` keep working
  untouched, because it is checked first.

## Alternatives considered

- **Move the file and keep both keys in it.** Rejected: it would hand this
  app's project id to every reader of a shared file, which is the exact
  mis-scoping the previous ADR exists to prevent.
- **Keep a small app-specific file holding only `BWS_PROJECT_ID`.** Rejected:
  a project id is not a secret and does not belong in a credential file. It is
  ordinary application configuration and now lives in the repository.
- **Invert the symlink** (real file under `~/.config/bws`, link left behind at
  the old path). Rejected as the end state — it preserves the confusing
  indirection — though it is used as a transitional step so the move can happen
  before this change is deployed.
