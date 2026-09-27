# Split execution_agent.py orchestrators into focused modules

**Date:** 2026-09-27
**Status:** Accepted
**Supersedes the deferral in:** decisions/2026-09-18_execution-agent-split.md (the
follow-on work it explicitly deferred is now done)

## Context

The 2026-09-18 pure/impure split (`exit_rules.py`, `indicators.py`,
`market_calendar.py`) took `execution_agent.py` from 5,828 to ~5,662 lines but
deliberately left the three big I/O-coupled orchestrators in place —
`monitor_portfolio_intraday`, `reconcile_with_ibkr`, `run_market_open_buys` —
because they could not be moved byte-identically and the book was not flat. That
work was parked in the provisional-decision register as `orchestrator-split`
(kind `work-item`), gated on a quiet book (`not_before: 2026-10-06`,
`max_open_positions: 2`, `min_position_age_days: 7`).

On 2026-09-27 the operator authorised the work early against a **flat book**
(zero rows in `portfolio_positions`, weekend, no capital deployed), which
satisfies the safety intent of those preconditions more strongly than the
thresholds themselves. The prompt is unchanged: a 32GB local LLM cannot hold a
5,662-line / ~72k-token file in one pass and hallucinates against it.

## Decision

`execution_agent.py` was reduced from **5,662 to 852 lines** — an 85% cut — by
extracting eleven focused modules. The daemon file now holds only module init
(env load, TeeLogger bootstrap, IBKR/Supabase/notifier singletons), the
`main_loop`, and thin `from <module> import (...)` re-export shims. No behaviour
changed; this is a pure reorganisation.

| Module | Lines | Contents |
|---|---|---|
| `market_regime.py` | 158 | index bull/bear gate, delayed-price fetch |
| `sentiment.py` | 240 | RS/volume/distribution health inputs |
| `ibkr_data.py` | 503 | live pricing + account/cash rollup (IBKR-first) |
| `orders.py` | 742 | order & exit primitives (trail/stop/arm/OCA/power-hold/cancel/notify) |
| `trade_history.py` | 246 | trade-history insert + exit-context formatting |
| `fills.py` | 327 | fill ingestion + commission accounting |
| `reconciliation.py` | 694 | `reconcile_with_ibkr` + `_sync_ibkr_position_values` |
| `buying.py` | 667 | `run_market_open_buys` + schema/position-size gates |
| `monitoring.py` | 807 | `monitor_portfolio_intraday` + learning-row helpers |
| `selling.py` | 300 | `execute_sell` + `execute_scale_out` |
| `agent_logging.py` | 435 | `TeeLogger` + Supabase log ship/purge |

## What made this dangerous, and the evidence that replaces byte-identity

The register's closing question was explicit: *"Can the split be proven safe
without byte-identity? If not, say what evidence replaces it — 'tests pass' is
not sufficient here."* Three things answer it.

### 1. The `ea.`-prefix invariant — a static, greppable guarantee

The suite patches names as `execution_agent.<name>` at ~260 sites. Python
resolves a called name in the **callee's own module globals**, so a moved
function whose internal call stops resolving through `execution_agent`'s
namespace turns `mock.patch("execution_agent.X")` into a silent no-op — a "did
NOT sell" test would pass while the code sells.

Mitigation, applied by construction to every module: each module does
`import execution_agent as ea` and references **every patched sibling, every
patched constant, and the frozen `datetime` clock via `ea.<name>`** (a live
attribute lookup), never binding it locally; `execution_agent.py` re-exports
every moved symbol. This keeps all ~260 patches live *by construction*, not by
luck — a property you can verify with grep, independent of whether any test
exercises a given path. The extraction was done with a tokenizer-based tool that
prefixes only NAME tokens (never strings/comments/attributes/defs), so log
strings like `"get_live_price()"` and docstrings were left intact.

Two classes of name needed care beyond the mechanical set:
- **Mutable flags patched on the module** (`_IBKR_VALUATION_WARNING_SHOWN` in
  `reconciliation`, `_schema_alert_sent` in `buying`, `_last_log_purge_at` and
  the installed `_tee` in `agent_logging`). Their definitions **stay in
  `execution_agent`**; the moved code reads *and writes* them as `ea.<flag>` and
  drops the now-invalid `global` statement. Tests set
  `execution_agent._IBKR_VALUATION_WARNING_SHOWN = ...` and
  `ea.__dict__["_tee"] = ...` directly, and those writes are honoured.
- **Non-mechanical internal helpers that tests still patch on `ea`**
  (`_ship_diag`, `_purge_agent_logs`, `flush_logs_to_supabase`). These are not
  in the canonical patched set, but `test_log_shipping.py` does
  `monkeypatch.setattr(ea, "_ship_diag", ...)`. Their intra-module call sites are
  therefore `ea.`-prefixed too. This was the one bug the suite caught rather than
  the invariant preventing — see below.

### 2. The golden characterization harness

`tests/golden_log.py` records the ordered money-path event sequence
(`execute_sell` / `execute_scale_out` / `arm_exit` / `place_protective_stops` /
cancel / notify) that `monitor_portfolio_intraday` emits, and
`tests/test_exit_path_golden.py` pins it. This is a stronger oracle than
per-branch assertions for the riskiest move (the monitor orchestrator): it fails
if the *order or set* of side effects changes at all. It stayed green.

### 3. The suite held at its exact baseline

935 tests pass before and after — the same count, not merely "green". Six test
files needed edits, all legitimate and none loosening a check:
- Three source-scraping characterization tests read the function text from its
  new home (`test_exit_context.py`, `test_trade_history_insert.py` →
  `trade_history.py`; `test_exit_timestamp.py`, and the reconcile-branch check in
  `test_exit_context.py` → `reconciliation.py`). These tests assert on *source
  shape*, so they must point at the file that now contains the source.

The one bug the process caught: `flush_logs_to_supabase` called `_ship_diag`
bare, so `monkeypatch.setattr(ea, "_ship_diag", ...)` no-op'd and
`test_retention_failure_is_reported_as_a_purge_failure` failed. This is exactly
the failure mode the invariant exists to prevent, caught here because the log
helpers sit outside the mechanical patched set. Fixed by `ea.`-prefixing the
internal call. That the suite surfaced it — rather than it slipping through — is
the point.

## Consequences

- The daemon file is now digestible by a 32GB local LLM, and the knowledge graph
  no longer has one hub node absorbing a third of the codebase's behaviour.
- Circularity is fine: modules bind the partially-initialised `execution_agent`
  at import and only touch `ea.<name>` at call time, so no attribute is read
  before it exists.
- `Dockerfile.agent`'s COPY manifest lists all eleven modules;
  `test_agent_image_completeness` enforces that every imported module ships.
- Import-time side effects (stdout tee, `sys.excepthook`, shutdown flush hooks)
  remain in `execution_agent` so importing a module for its pure helpers does not
  trigger them.

## Register

`orchestrator-split` is marked **resolved** by this ADR.
