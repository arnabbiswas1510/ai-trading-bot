# 2026-09-27 — Secrets resolved from Bitwarden at deploy time (`@bws` sentinel)

**Status:** Accepted

## Context

On 2026-09-27, while diagnosing why Telegram trade alerts had gone silent, we
discovered that live credentials had been committed to `.env.template` in the
**public** GitHub repo `arnabbiswas1510/ai-trading-bot` for roughly 80 days:

- the Telegram bot token `8997092181:AAG…` — which was **actively abused**: the
  bot had been renamed to a scam handle ("CTT TYT yoon19000 ❤️❤️❤️") by whoever
  scraped the token; and
- the IBKR Flex Web Service token `744951508262970976219401`, plus the live
  account number `U12941651`, the Flex username, and the Flex query IDs.

The Supabase keys, FMP key and IBKR password were **not** leaked — they were
blank or absent in the template. But the two tokens above were real, public, and
one was already compromised.

Root cause: `.env.template` served double duty as both the *shape* of the config
and, carelessly, a place where real values had been pasted. There was no
mechanism forcing the committed file to stay free of secrets, and no place to
keep the real values that was outside the repo.

## Decision

Real secrets live in **Bitwarden Secrets Manager** (project `ai-trading-bot`, 12
secrets keyed by their exact env-var names). The repo keeps only a sentinel.

1. **`.env.template` carries `KEY=@bws`** for every secret. `@bws` means "resolve
   this from the Bitwarden vault by this key name at deploy time." Non-secret
   config (thresholds, `MAX_POSITIONS`, etc.) stays as literal values in the
   template as before. `OPENAI_API_KEY` is left blank (optional, not stored).

2. **`scripts/render_env.py`** turns the template into a real `.env`: every
   `=@bws` line is replaced by the matching vault value; blanks, comments and
   literal config pass through untouched. It is **fail-closed** — if any sentinel
   key is missing from the vault or resolves empty, it writes nothing and exits 3.
   Kept import-safe so its logic is unit-tested (`tests/test_render_env.py`)
   without a live vault.

3. **`scripts/render_env.sh`** is the host entry point: it locates `bws`, sources
   the machine-account token from `~/.config/ai-trading-bot/bws.env` (chmod 600,
   outside the repo), runs `bws secret list -o json`, pipes it through
   `render_env.py`, and **atomically** replaces `.env` with a chmod-600 temp file.
   Any error aborts non-zero and leaves the existing `.env` untouched.

4. **`tests/test_no_secrets_committed.py`** is the standing guard: it asserts
   every secret line in `.env.template` is the `@bws` sentinel, and that the two
   leaked credential strings never reappear in any tracked file. The banned
   strings are assembled from fragments so the guard does not match itself.

Both leaked tokens were **rotated** before this change: a fresh IBKR Flex token
was generated, and the hijacked Telegram bot was abandoned and replaced with a
new bot and chat ID.

## Why deploy wiring is deferred

`deploy_to_server.yml` is deliberately **not** changed here. Pushing to `main`
auto-triggers a build+deploy; if render were wired into it, the host `.env` would
be regenerated from Bitwarden automatically — clobbering any hand-tuned host
config with template defaults, and doing so before the operator has validated the
output. The current deploy only SCPs `docker-compose.yml` and never touches
`.env`, so shipping this change is safe. The first cutover is run **by hand** on
the host (render, `diff` against the live `.env`, then
`docker compose up -d --force-recreate`). Wiring render into the deploy is a
follow-up once that manual cutover is proven.

## Consequences

- The repo can no longer carry a real secret without a test failing.
- Adding or rotating a secret is a Bitwarden operation, not a code change.
- The host gains one runtime dependency: Bitwarden reachability at render time.
  This is confined to the explicit render step; `restart_6am.sh` still uses
  `docker restart` (baked env) and is intentionally **not** made to depend on the
  vault.
