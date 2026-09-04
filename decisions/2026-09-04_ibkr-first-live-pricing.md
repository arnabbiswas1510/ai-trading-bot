# Price live-position exit logic from IBKR, not FMP

- **Date:** 2026-09-04
- **Status:** Accepted

## Context

`decisions/2026-09-03_ibkr-sourced-position-values.md` moved **dashboard
valuation** onto IBKR's own mark (`PortfolioItem.marketPrice`), but left the
**execution agent's exit logic** pricing every open position from FMP:

```
current_price = get_live_price(ticker)   # FMP stable/quote
```

That meant the two sources of truth the earlier ADR set out to eliminate still
coexisted, one layer down. The dashboard showed IBKR's mark while every exit
rule — the day-0 kill-switch, Early Dollar Stop, Thesis Stop, Intraday Loss
Minimiser, EMA-21 exit, plateau/rank-replace — decided on an FMP quote, and the
`account_balances` cash rollup in `reconcile_with_ibkr()` valued positions with
a *third* independent FMP call (`quote-short`).

Two concrete hazards followed:

**Decision-vs-fill divergence.** Live orders fill against IBKR. When FMP and
IBKR disagreed — vendor latency at the open, a halt, a bad tick — an exit could
arm or fire on a price the broker never showed, and then fill somewhere else.
This is the exact "price latency" fill problem that motivated the request.

**Operator confusion.** A dashboard mark and an exit-trigger mark that come from
different vendors cannot be reconciled by eye. "Why did it sell there?" had no
answerable price on screen.

The reason FMP was used at all is real and must be preserved: `ib.reqTickers()`
**blocks indefinitely when the ushmds data farm is down** (commit `83cf3a4`), so
it cannot sit inside the 15-minute monitoring loop. FMP was the crash-safe
choice. But `ib.portfolio()` — already used by the 2026-09-03 change — is a
**non-blocking** read of the account-update stream and carries `marketPrice`,
so the broker's own mark is available without the `reqTickers()` hazard.

## Decision

Add an IBKR-first price accessor for open positions and route every live-trade
decision and valuation through it. FMP becomes a **fallback**, not the source.

### New helpers (`execution_agent.py`)

```python
build_ibkr_price_map(ib) -> {symbol: PortfolioItem}   # one non-blocking read
get_position_price(ib, ticker, ib_map=None) -> (price, source)
```

`get_position_price()` returns `PortfolioItem.marketPrice` when it is present
and positive (`source="ibkr"`), and only then calls `get_live_price()`
(`source="fmp"`) when IBKR has no usable mark — data farm down, or the position
not yet in the account stream. It is NaN-safe and never raises. Cost basis
remains the final fallback in the cash rollup.

`monitor_portfolio_intraday()` builds the map **once per cycle** and threads it
through every position, so an entire monitoring pass is priced from one
consistent broker snapshot. The per-position log line now shows the source, e.g.
`Current: $150.25 (ibkr)`.

### Call sites moved from FMP to IBKR-first

| Location | Was | Now |
|---|---|---|
| `monitor_portfolio_intraday()` main loop | `get_live_price` | `get_position_price(ib, t, map)` |
| Day-3 breakout verdict price | `get_live_price` | `get_position_price(ib, t)` |
| Thesis-stop follow-through latch | `get_live_price` | `get_position_price(ib, t)` |
| Rank & Replace sell price | `get_live_price` | `get_position_price(ib, t)` |
| `process_exit_requests()` exit price | `get_live_price` | `get_position_price(ib, t)` |
| `reconcile_with_ibkr()` cash rollup | per-ticker FMP `quote-short` | `get_position_price` over one map |

### Deliberately left on FMP

- **Screening / watchlist / research** (`backend/`, `technical_screener.py`,
  `ai_evaluator.py` sentiment): these price non-held candidates for which no
  IBKR position exists. Broker parity is irrelevant here.
- **The post-sale `PRICE_UNCERTAIN` placeholder** in Case 1 reconciliation: the
  position is already gone from IBKR, so there is no mark to read; FMP as a
  flagged last resort is correct and unchanged.
- **Buy entries** were already IBKR-only via `fetch_ibkr_delayed_price()`
  (`force_buy.py`, `rotate_positions.py`) and are untouched.

## Consequences

- Exit decisions and the price shown on the dashboard are now the same IBKR mark
  the order will fill against. The dual-source ambiguity is closed end-to-end.
- The `reqTickers()` blocking hazard is **not** reintroduced: only the
  non-blocking `ib.portfolio()` path is used; FMP still covers a dead data farm.
- Behaviour of the exit rules is unchanged in the common case (IBKR ≈ FMP); it
  changes only when the two sources disagreed — which is precisely the case this
  fixes.

## Tests

`tests/test_position_price.py` covers the IBKR-hit, ticker-absent, zero-mark,
NaN-mark, both-fail, precomputed-map, and non-fatal-`portfolio()`-exception
paths. Monitor-rule test helpers now yield `marketPrice=0.0` so they exercise
the FMP-fallback branch they already inject via `get_live_price`. Full suite:
545 passing.
