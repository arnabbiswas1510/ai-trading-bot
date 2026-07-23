# Decision: Backtester Accuracy Rewrite

**Date:** 2026-07-23
**Status:** Implemented

## Problem

The original `backend/backtester.py` had 4 critical bugs that caused it to
misrepresent live bot behaviour, producing inflated or misleading backtest results:

1. **Look-ahead bias on entry** — breakout detected and bought on the same EOD candle
   (`high > prev_high20` → `buy_price = close` on day T). Live bot detects after close
   and buys next morning's open.

2. **Fixed hard stop instead of trailing stop** — `stop_loss = buy_price × 0.93` set
   once at entry and never updated. Live bot uses an IBKR GTC TRAIL that rises with
   peak price. Winners that run 15% and pull back 7% behave very differently under
   these two models.

3. **max_positions=5 (wrong default)** — live bot runs `MAX_POSITIONS=4`.

4. **Proportional position sizing** — `allocation = total_equity / N`. Live bot uses
   a fixed `$20,000` block per position regardless of portfolio size.

Additionally, 6 moderate divergences in exit logic and 13 metrics were missing.

## Decision

Full rewrite of `backtester.py` to match production `execution_agent.py` behaviour:

- **Entry**: breakout detected on day T (EOD), bought at day T+1 open price
- **Trailing stop**: `peak_price` advances with each new intraday high; stop = `peak × (1 − 7%)`
- **Positions**: `max_positions=4` default
- **Sizing**: fixed `$20,000` block, capped at available cash
- **Exit 1**: trailing stop fires when `low ≤ peak × 0.93`
- **Exit 2**: EMA-21 exit when `close < EMA21 × 0.99` (replaces SMA-50 break)
- **Market filter**: SPY `close > EMA-21` (replaces SMA-200 — matches live)
- **No fixed profit target**: live bot removed this; backtester now matches

## New Metrics Added (13)

CAGR, Sharpe, Sortino, Calmar, Profit Factor, Avg Win%, Avg Loss%,
Win/Loss Ratio, Expectancy ($), Avg Hold Days, Max Consecutive Losses,
Underwater Days, Alpha vs S&P 500.

## API Compatibility

`BacktestRequest` in `main.py`:
- Added `position_size: float = 20_000.0` (new, optional with default)
- `profit_target_pct` retained for frontend compatibility — passed through but ignored

## Files Changed

- `backend/backtester.py` — full rewrite
- `backend/main.py` — added `position_size` to `BacktestRequest` and call site
