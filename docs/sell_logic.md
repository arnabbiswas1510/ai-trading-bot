# Sell Logic

## Overview

**File:** `execution_agent.py`

Sell decisions in the live trading agent are made by:
1. `monitor_portfolio_intraday(ib)` — runs every 15 minutes during market hours (9:30 AM – 4:00 PM ET)
2. `execute_sell(ib, client, ticker, ...)` — the sell execution function called when an exit condition is met

Each 15-minute cycle checks active positions against two active exit mechanisms:
- **Dynamic Trailing Stop Loss** (managed continuously in IBKR)
- **Early-Loss Kill-Switch** (Day 0-1: sell immediately if price drops 2% below entry)
- **Intraday Loss Minimiser** (Day 2+ universal; replaces old Day-4+ FAIL-only scope)
- **21-Day EMA Support Breach** (Day 7+ EOD check between 3:45–4:00 PM ET)

---

## Active Exit Mechanisms

### 1. Dynamic Trailing Stop Loss (IBKR-Managed)
- At purchase, a GTC `TRAIL` order is placed directly with IBKR (`place_trailing_stop()`).
- As the stock price rises to new high water marks (HWM), IBKR automatically ratchets up the stop price tick-by-tick.
- As unrealized gains increase or holding duration grows, `execution_agent.py` dynamically tightens the trailing percentage via `_compute_dynamic_trail_pct()`.

### 2. Day 3 Breakout Verdict & Intraday Loss Minimiser
- **Day 3 EOD Verdict**: Evaluated on Day 3 EOD (`check_breakout_verdict()`).
  - `PASS`: Price > entry + 1% AND Day 3 volume >= 75% of average. The position continues normally without time limits.
  - `FAIL`: Missed conditions.
- **Day 0-1 hard loser protection**:
  - Sell immediately when live price is below entry by `EARLY_LOSS_STOP_PCT` (default `0.02`)
- **For all positions from Day 2 onward**:
  - Sell on pullback from intraday high using `INTRADAY_PULLBACK_PCT` (default `0.005`) when near/above entry
- **Day-7 hard fallback** remains scoped to FAIL-verdict positions if no qualifying rally occurred

### 3. Moving Average Support Breach (Day 7+ EOD)
- Between 3:45 PM and 4:00 PM ET, for positions held >= 7 trading days, if `current_price < EMA-21 * 0.99` (breaks 21-day EMA by 1%), `execute_sell()` is triggered.

---

## Key Parameters

| Parameter | Default |
|---|---|
| `BREAKOUT_VERDICT_MIN_GAIN` | `0.01` |
| `BREAKOUT_VERDICT_MIN_VOL_PCT` | `0.75` |
| `INTRADAY_PULLBACK_PCT` | `0.005` |
| `EARLY_LOSS_STOP_PCT` | `0.02` |
| `INTRADAY_MINIMISER_START_DAY` | `2` |
| `RANK_REPLACE_THRESHOLD` | `15` |

---

## Legacy / Manual Scripts
- `rotate_positions.py` is a **manual standalone script** that can be run on-demand to compare held positions against new triggers. It is **not** part of the automated `execution_agent.py` daemon.
