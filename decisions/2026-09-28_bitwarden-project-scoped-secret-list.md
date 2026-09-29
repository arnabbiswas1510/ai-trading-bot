# 2026-09-28 — Bitwarden `secret list` is scoped to one project (fail-closed)

**Status:** Accepted

## Context

`scripts/render_env.sh` materialises the host `.env` by resolving every `@bws`
sentinel in `.env.template` against Bitwarden Secrets Manager. It did so with a
single unscoped call:

```sh
bws secret list -o json
```

`bws secret list` with no project argument returns **every secret the machine
account can read across every project it has access to**. When the vault held
only the `ai-trading-bot` project this was harmless. A second project was then
added to the same Bitwarden organisation, and the machine account could see it,
so the listing began returning secrets from more than one project.

That is a correctness and safety problem because of how the values are matched
downstream. `scripts/render_env.py` builds a `{key: value}` map keyed by the
secret's **env-var name** (`secrets = {s["key"]: s.get("value", "") for s in
secrets_list}`) and substitutes each `KEY=@bws` line by that name. If two
projects both define a secret with the same key name (e.g. `SUPABASE_KEY`,
`TELEGRAM_BOT_TOKEN`), the merge is order-dependent and could silently resolve
the **wrong project's** value into the live `.env` — exactly the class of
silent-wrong-credential failure the `@bws` design existed to prevent.

## Decision

The secret listing is now **scoped to a single project**, and the scope is
**mandatory and fail-closed**, consistent with the rest of `render_env.sh`.

1. `render_env.sh` reads `BWS_PROJECT_ID`. It is sourced from the same
   out-of-repo bootstrap file that already supplies the machine-account token
   (`~/.config/ai-trading-bot/bws.env`, chmod 600), and an explicit
   `BWS_PROJECT_ID` in the environment takes precedence over the bootstrap value
   (captured before sourcing so the source cannot clobber a deliberate override).

2. If `BWS_PROJECT_ID` is unset or empty, the script fails non-zero **before**
   listing anything and leaves the existing `.env` untouched — the same
   fail-closed contract as a missing `BWS_ACCESS_TOKEN` or a missing `@bws` key.
   Unscoped, all-project behaviour is no longer reachable.

3. The listing is `bws secret list "$BWS_PROJECT_ID" -o json`. Everything
   downstream (the `BWS_SECRETS_JSON` handoff, `render_env.py`, the atomic
   chmod-600 replace) is unchanged.

## Consequences

- The operator must add `BWS_PROJECT_ID=<project-uuid>` to
  `~/.config/ai-trading-bot/bws.env` (or export it) before the next render. Until
  they do, `render_env.sh` aborts with a message naming the missing variable and
  the existing `.env` is preserved — a safe, loud failure, not a silent wrong
  value.
- A same-named secret in any other Bitwarden project can no longer bleed into
  this deployment's `.env`.
- No secret values were read, moved, or logged by this change; it only adds a
  positional project filter to the existing CLI call.

See `decisions/2026-09-27_bitwarden-secret-resolution.md` for the original
`@bws` design this hardens.
