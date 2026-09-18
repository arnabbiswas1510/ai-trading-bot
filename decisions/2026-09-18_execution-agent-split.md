# Split execution_agent.py along its pure/impure seam

**Date:** 2026-09-18
**Status:** Accepted

## Context

`execution_agent.py` had grown to **5,828 lines** holding 82 functions, 69
constants and 3 classes — everything from NYSE holiday arithmetic to IBKR order
placement to the Prove-It Stop.

The immediate prompt was a local-LLM question (a 30B model on a 32GB V100), where
the file is ~72k tokens and cannot be reasoned about in one pass. But the same
size hurts the knowledge graph: a single node absorbing a third of the codebase's
behaviour produces a hub that every query traverses and none can discriminate.

Three functions alone account for 1,815 lines — `monitor_portfolio_intraday`
(685), `reconcile_with_ibkr` (591) and `run_market_open_buys` (539). Splitting the
*file* does not shrink those; that is separate work.

### What made this dangerous

The test suite patches **214** call sites as `execution_agent.<name>`, including
module-level singletons (`supabase` ×25, `notifier` ×13, `get_live_price` ×33).
Python name resolution makes a naive move silently wrong:

> Moving a function **out** is safe only while its **callers stay in**
> `execution_agent.py`. The caller resolves the name from `execution_agent`'s
> globals, so `mock.patch("execution_agent.X")` still intercepts it. If caller and
> callee both move, the patch becomes a **no-op** — and a test asserting "we did
> NOT sell" would pass while the real code sells.

## Decision

Extract only the **pure** layer — functions that take values and return values,
with no brokerage, database or network access — and keep every orchestrator in
place.

| Module | Lines | Contents |
|---|---|---|
| `exit_rules.py` | 577 | Prove-It Stop, power hold, trail ladder, OCA sizing, hard stop, sell-state codes — **and their constants** |
| `indicators.py` | 277 | SMA/EMA/RSI, candlestick reversals, Momentum Health Score |
| `market_calendar.py` | 125 | NYSE holidays, trading-day arithmetic, RTH check |

`execution_agent.py`: **5,828 → 4,952 lines.**

`monitor_portfolio_intraday`, `run_market_open_buys`, `reconcile_with_ibkr`,
`execute_sell`, `execute_scale_out`, `process_exit_requests` and `main_loop` all
stay, so all 214 patch points keep working.

### Constants move with the logic they govern

`config.py` (89 lines) is reserved for values shared **across containers**
(`MAX_POSITIONS`, `STOP_LOSS_PCT`). The 24 constants moved here are
execution-agent-local, and each carries the measured evidence that chose it — the
Prove-It replay table, the power-hold backtest, the HWM ladder result. Keeping
that rationale beside the rule is the point: a reader of `exit_rules.py` sees the
threshold and the experiment that set it without leaving the file.

Extraction was performed by script against AST line ranges, not by retyping.
**Verified byte-identical**: 82/82 functions, 69/69 constants and 3/3 classes are
textually unchanged. This is a pure move.

### Cross-module patching: `patch_everywhere()`

Constants are imported **by value**, so each importing module holds its own
binding. Of six tests patching a moved name, two broke immediately (their reader
moved) and four kept passing only because their reader happened to stay.
Re-pointing those four at `exit_rules` would have broken them the other way.
There is no single correct module to patch.

`tests/conftest.py` now provides `patch_everywhere(name, value)`, which sets the
name on every loaded project module that binds it, and **raises if it matches
nothing** — so a rename cannot quietly turn a test into a no-op. This is what
makes the next extraction safe.

## A latent production bug this surfaced

`Dockerfile.agent` copies source files **individually**. The split would have
shipped a container missing `exit_rules.py` and crashed the live agent on import.

Checking the full import closure revealed the same bug already present:
**`flex_query_sync.py` had never been in the image.** Its import is wrapped in

```python
try:
    from flex_query_sync import fetch_trade_confirms_for_ticker
except ImportError:
    def fetch_trade_confirms_for_ticker(ticker): return None   # no-op stub
```

commented "not available in test environments" — but it was equally unavailable
in **production**. So **Tier 3 of the sell-price reconstruction ladder**
(authoritative IBKR TradeConfirm fills) has always returned `None` in the live
agent, silently falling through to an FMP estimate.

That is the most plausible origin of the NBIX mis-price repaired on 2026-09-15,
where a wrong-day FMP estimate of $152.74 stood in for a real fill of $158.5043 —
an $835.82 error in the largest loss in the sample. See
`decisions/2026-09-15_nbix-reconstructed-sell-price.md`.

Both modules are now copied. `fetch_trade_confirms_for_ticker()` returns `None`
when unconfigured, and the agent already receives `IBKR_FLEX_EXEC_QUERY_ID` via
`env_file`, so enabling it is safe and Tier 3 becomes live.

`tests/test_agent_image_completeness.py` walks the real import closure and fails
if any reachable module is absent from the `COPY` line. Verified non-vacuous
against the previous Dockerfile.

## Consequences

- No behaviour change. 747 tests pass; the moved code is byte-identical.
- **Tier 3 sell-price recovery starts working**, which is a real behaviour change
  in reconciliation — reconstructed sell prices should now come from IBKR
  TradeConfirm rather than an FMP estimate. Watch the `sell_price_source` column.
- The graph gains three cohesive nodes instead of one hub.
- `exit_rules.py` is independently readable and testable with no IBKR connection.
- **Not addressed:** the three 500–700-line orchestrator functions. Splitting them
  requires solving the `supabase`/`notifier` singleton coupling and is deferred.

## What would justify going further

Only a concrete need. The remaining 4,952 lines are dominated by genuinely
stateful orchestration, where extraction trades one kind of complexity for
another. `patch_everywhere()` and the image-completeness test are the
prerequisites that make a Stage 2 safe if it is ever wanted.
