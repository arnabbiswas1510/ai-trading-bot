# 2026-09-27 — Startup crashes ship to Supabase; execution_agent import cycle removed

**Status:** Accepted

## Context

Right after the Bitwarden secret cutover
(`decisions/2026-09-27_bitwarden-secret-resolution.md`), the freshly-recreated
`execution-agent` container crash-looped. `docker logs` showed:

```
File "execution_agent.py", line 31, in <module>
    from agent_logging import (...)
File "agent_logging.py", line 18, in <module>
    import execution_agent as ea
ImportError: cannot import name 'TeeLogger' from partially initialized module 'agent_logging'
```

Two separate problems, exposed together.

### Problem 1 — a latent circular import from the modular split

The 2026-09-27 split of `execution_agent.py` into eleven modules left **ten**
siblings (`agent_logging`, `buying`, `fills`, `ibkr_data`, `market_regime`,
`monitoring`, `orders`, `reconciliation`, `selling`, `sentiment`) holding a
back-reference to the parent via a top-level `import execution_agent as ea`.

That is safe when `execution_agent` is imported as a normal module (which is how
every test and tool loads it — so the whole test suite passed). It is **not**
safe when `execution_agent` is the process entrypoint. Run as
`python execution_agent.py`, the module is registered in `sys.modules` as
`__main__`, not as `execution_agent`. The sibling's `import execution_agent`
therefore starts a *second*, fresh execution of the file from the top, which
re-enters `from agent_logging import ...` while `agent_logging` is only half
initialised — and fails. The bug could only ever manifest at container runtime,
never in CI, which is exactly why it shipped.

It had lain dormant because the container had still been running a *pre-split*
image; the secret-cutover `--force-recreate` pulled and ran the split image for
the first time.

### Problem 2 — startup crashes were invisible to Supabase

The whole point of the Supabase `agent_logs` shipping
(`decisions/2026-09-18_supabase-log-shipping.md`) is that the agent is
diagnosable **without** SSHing into the home-network prod host. But this crash
happened during *import*, before `TeeLogger` and the shipping pipeline were
installed. So nothing reached `agent_logs`; the traceback lived only in
`docker logs`, and diagnosing it required an SSH session — the precise situation
log shipping exists to eliminate.

## Decision

### Fix 1 — one lazy, entrypoint-safe reference module

Add `execution_agent_ref.py`, exposing a single `ea` proxy that resolves the
`execution_agent` module through `sys.modules` at **attribute-access** time, not
at import time:

1. `sys.modules['execution_agent']` if imported normally;
2. `__main__` when `execution_agent.py` is run directly;
3. otherwise import it (safe — in that case it is not the entrypoint).

All ten siblings now do `from execution_agent_ref import ea` instead of
`import execution_agent as ea`. No load-time cycle exists, and every entrypoint
resolves to the one running module instance (so the live `_tee` and retention
cursor are always the real ones). `python execution_agent.py --help` now exits 0;
a regression test asserts it.

### Fix 2 — a fatal-safe entrypoint that ships startup crashes

Add `agent_entrypoint.py` and point `Dockerfile.agent`'s `CMD` at it. It imports
and runs the agent inside a `try/except BaseException`; on any failure it writes a
single `CRITICAL` `[STARTUP-CRASH]` row to `agent_logs` (with the full traceback,
redaction-free because it is a controlled internal string) and re-raises so the
container still exits non-zero. It depends only on stdlib + `supabase`, never on
`execution_agent`/`agent_logging` — the very code that may have failed to import
— and reads `SUPABASE_URL`/`SUPABASE_KEY` straight from the environment. A clean
`SystemExit` (e.g. missing `FMP_API_KEY`) is passed through, not shipped.

`execution_agent.py`'s `if __name__ == "__main__"` body was extracted into a
`main()` function so the wrapper can call it and direct execution still works.

## Consequences

- The agent boots again, and running `execution_agent.py` directly is safe.
- Any *future* startup failure — import error, bad config, a missing dependency —
  now appears in `agent_logs` as a `[STARTUP-CRASH]` row, diagnosable from
  anywhere the database is reachable. The SSH-only blind spot is closed.
- Requires a CI image rebuild to take effect; the running image cannot be hot
  patched (the host has no source checkout).
- New file `execution_agent_ref.py` must be included in `Dockerfile.agent`'s
  `COPY` (it is).
