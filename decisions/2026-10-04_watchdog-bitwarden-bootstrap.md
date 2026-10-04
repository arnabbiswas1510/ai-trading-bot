# 2026-10-04 — Load watchdog credentials from Bitwarden on the hosted runner

**Status:** Accepted

## Context

The October 3, 22:42 EDT watchdog run
[37171852451](https://github.com/arnabbiswas1510/ai-trading-bot/actions/runs/37171852451)
failed at commit `3b17215` before collection checks with "Private intraday database
credentials are missing". The fallback notification also failed. Thursday and
Friday runs showed the same errors.

Read-only inspection confirmed that all five reporting credentials existed,
were nonempty and were not placeholders in Bitwarden project `ai-trading-bot`.
The workflow nevertheless populated its environment directly from GitHub
Actions secrets; it contained no Bitwarden retrieval step. Production's
Bitwarden rendering cannot populate a separate GitHub-hosted runner.

## Decision

The watchdog uses one Actions repository secret, `BWS_ACCESS_TOKEN`, granting
read access to this Bitwarden project. Operator configuration of this bootstrap
secret remains required; its current availability was not verified.

Install the official `bws` 2.1.0 Linux x86-64 archive, checking SHA-256
`ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860`
before extraction. This digest was verified against both the published release
metadata and downloaded archive. No Python dependency is added.

`scripts/run_intraday_reporting_bws.py` reuses the host renderer's project-name
resolver, requires one matching project, then requests only that project's
secrets. It exports exactly `SUPABASE_URL`, `SUPABASE_KEY`,
`INTRADAY_SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS`.
Each required name must appear exactly once and have a nonempty, single-line
value other than `@bws`. The general Supabase key is not a substitute for the
private research key.

The helper masks values using escaped GitHub workflow commands, captures
Bitwarden output in memory, and launches the existing reporting entrypoint with
an in-memory child environment. It preserves arguments, GitHub notification
credentials, runtime mode and the child exit code. It removes the bootstrap
token from the child's environment. A fixed non-secret Bitwarden profile turns
off authentication-state caching. No secret environment file, Actions environment
file or artifact is produced.

## Consequences

Vault rotation reaches the next watchdog run without maintaining five duplicate
Actions secrets. A missing bootstrap credential explicitly names the required
Actions secret; lookup failures do not print raw CLI output or exceptions.
Failures before loading credentials are visible in Actions, but cannot reliably
send Telegram/database-backed notifications. Existing reporting notifications
and deliberate collection-failure exit codes are unchanged after bootstrap.

The hosted runner now depends on Bitwarden availability and the checksum-pinned
CLI distribution. Host configuration, brokerage access and the weekly backup
workflow are unchanged. See `docs/intraday_research.md` and
`docs/configuration.md` for operator setup.
