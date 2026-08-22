# Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed

- **Date:** 2026-08-22
- **Status:** Accepted
- **Supersedes:** the original single-index `is_market_bullish()` rule (SPY close > SMA-200, no ADR)

## Context

The CANSLIM "M" gate is the bot's only market-regime control. It is a hard gate
in `run_market_open_buys()`: when it returns `False`, no buys are placed and idle
slots hold cash.

An audit on 2026-08-22 found three problems.

**1. The failure semantics were incoherent.** Of the five ways the check could
fail, three returned `True` (bullish) and two returned `False`:

| Failure mode | Old verdict |
|---|---|
| HTTP status != 200 | **BULL** (fail-open) |
| Payload not a list | **BULL** (fail-open) |
| Fewer than 200 sessions returned | **BULL** (fail-open, with a printed warning) |
| Stale data | *not checked at all* |
| Unhandled exception | BEAR (fail-closed) |

So an expired FMP key, a rate-limit response or a truncated payload would all
authorise buying into an unknown market. The docstring described this as
deliberate ("Fails open to avoid unintended cash locks"), while the exception
handler printed "Defaulting to BEAR (fail-closed)" — the two halves of the same
function disagreed about the policy.

This was not theoretical. Eight tests in the suite
(`test_buy_fill_verification.py`, `test_sell_logic.py`, `test_trigger_audit.py`)
were passing only because an unauthenticated FMP call returned HTTP 401 and the
gate silently opened. They began failing the moment the fail-open paths were
closed, which is the clearest possible evidence that the behaviour was load-bearing
and unnoticed.

**2. The real logic was untested.** The only coverage was
`tests/test_buy_gates.py`, which patches `execution_agent.is_market_bullish` out
entirely. Nothing exercised the implementation.

**3. Single index, no dead-band.** A bare `close > SMA200` on SPY alone flips
regime on every marginal cross and ignores the Nasdaq entirely, which is where
this bot's growth names actually live.

## Decision

Rewrite `is_market_bullish()` as:

> Bullish requires **every** benchmark in `MARKET_DIRECTION_TICKERS` (default
> `SPY,QQQ`) to close more than `MARKET_DIRECTION_BUFFER_PCT` (default 1%) above
> its SMA-200, **and at least one** of those SMA-200s to be non-falling over
> `MARKET_DIRECTION_SLOPE_DAYS` (default 20) sessions.

Every failure mode is fail-**closed**: HTTP error, malformed payload, short
history, data staler than `MARKET_DIRECTION_MAX_STALE_DAYS`, unhandled exception,
or an empty benchmark list all return `False`. `MARKET_DIRECTION_FILTER_ENABLED=false`
remains the only bypass.

Standing down costs one day of opportunity. Buying into an undiagnosed bear
market costs capital. The asymmetry is not close.

## Evidence

Three tests were run. The first one failed to discriminate, and saying so matters
more than the two that worked.

**Test 1 — replay against the bot's own closed trades: no signal.** All 21 closed
trades fall inside a single six-week window (2026-07-09 → 2026-08-18) in which all
64 candidate configurations return BULL. Zero trades blocked by any config. The
trade history **cannot** decide this question and was not used to.

**Test 2 — regime grid, 4,940 sessions (2007-01-03 → 2026-08-21).** Each config
was scored on the mean forward-20-session SPY return conditioned on its verdict,
and on the share of the worst-5% forward windows it sat out.

| Config | bull days | edge (bull − bear, %) | worst-5% windows avoided |
|---|---|---|---|
| **Old rule** (SPY > SMA-200) | 76.4% | +0.018 | 59.3% |
| SPY, 1% buffer, slope | 69.1% | **+0.171** | 66.1% |
| **SPY+QQQ, 1% buffer, slope (shipped)** | 66.9% | +0.119 | **67.8%** |
| any config requiring 50-DMA > 200-DMA | — | ≈0 or negative | — |
| any "either index" (OR) config | — | negative throughout | — |

**Test 3 — sub-period split. This is the finding that matters.**

| Config | 2007-12 | 2013-19 | 2020-26 | ex-2008 |
|---|---|---|---|---|
| Old rule | +0.94 | −0.79 | −1.27 | −1.25 |
| SPY 1% + slope | +0.56 | −0.24 | −0.61 | −0.85 |
| SPY+QQQ 1% + slope | +0.29 | −0.47 | **−0.28** | −0.83 |

**The entire positive edge in the 19-year sample comes from 2008.** Excluding it,
every configuration — including the one being shipped — has a *negative* mean
forward-return edge. 200-DMA gates systematically sit out V-shaped recoveries.

## Consequences

**This change is not expected to raise mean returns, and it is not being sold as
such.** It is drawdown insurance. It buys an 8.5pp improvement in avoiding the
worst forward windows (59.3% → 67.8%) at a cost of roughly 9.5pp fewer permitted
trading days (76.4% → 66.9%), plus it removes a genuine correctness bug.

One caveat cuts in the change's favour: the measurement conditions on *index*
returns, whereas the thing being gated is breakout entries. Breakouts fail far
more often and more expensively in downtrends than SPY's mean return implies, so
the index-level edge is a lower bound on the gate's value to this strategy. That
is an argument, not a measurement, and should not be quoted as one.

Two candidate designs were tested and **rejected**:

- **`50-DMA > 200-DMA` as a requirement** — adds nothing; edge collapses to ≈0 or
  negative in every ticker/buffer combination.
- **"Either index bullish" (OR)** — the worst rows in the entire grid. An earlier
  draft of this design proposed OR'ing the bearish trip conditions while AND'ing
  the bullish ones; the grid shows that asymmetry is strictly harmful and it was
  dropped.

The slope condition is deliberately OR'd across indices while the price condition
is AND'd. Requiring *both* SMA-200s to be rising was materially more restrictive
without improving drawdown avoidance.

### Dashboard consistency

`backend/screener.py::get_market_direction()` is a second, independent "M"
implementation (^GSPC + ^IXIC, 50/200 SMA, 0/5/15 score) that feeds the dashboard.
It disagreed with the agent and would have told an operator "Confirmed Uptrend"
while the agent stood down. Its descriptive `status`/`score` fields are unchanged —
they answer a different question — but it now also returns an `execution_gate`
field computed with the agent's rule and the same env vars, so the dashboard can
never silently contradict whether buys are actually permitted.

### Follow-ups

- The parameters (1% buffer, 20-session slope, SPY+QQQ) are chosen from a grid on
  index data, not on this bot's trades. Re-test them once the closed-trade sample
  spans more than one market regime — the current sample spans six weeks. Folded
  into the scheduled exit-parameter reviews in `AGENTS.md`.
- `MARKET_DIRECTION_TICKER` (singular) is replaced by `MARKET_DIRECTION_TICKERS`
  (plural, comma-separated). Any deployment setting the old variable will silently
  fall back to the `SPY,QQQ` default; the old name is not read.
