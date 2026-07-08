# Sell Logic

## Overview

**File:** `execution_agent.py`

Sell decisions are made by two functions:
1. `monitor_portfolio_intraday(ib)` — runs every 15 minutes during market hours
2. `execute_sell(ib, client, ticker, ...)` — the actual sell execution called by the monitor

Each 15-min cycle runs through two phases sequentially:
- **Per-position loop** — trailing stop self-healing, hwm_date update, MA exit (all positions)
- **EOD Plateau Rotation** — 3:45–4:00 PM only; sell most-stalled position if portfolio full and fresh trigger exists

---

## Monitoring Schedule

```python
# main_loop() — intraday monitoring: 9:30 AM – 4:00 PM ET, every 15 minutes
is_market_open = (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)
if is_market_open:
    reconcile_with_ibkr(ib)      # sync IBKR -> Supabase (catches manual TWS closes)
    monitor_portfolio_intraday(ib)
    ib.sleep(900)
```

`reconcile_with_ibkr()` always runs before monitoring.

> [!IMPORTANT]
> `reconcile_with_ibkr()` uses `ib.portfolio()` (not `ib.positions()`). `ib.positions()` is a push
> subscription that may be empty in long-running sessions. All IBKR position checks use `ib.portfolio()`.

---

## Exit Mechanism 1 — Trailing Stop Loss (7% below peak, IBKR-managed)

The **only** exit order placed at buy time is a GTC TRAIL order via `place_trailing_stop()`.

```python
def place_trailing_stop(ib, contract, shares, stop_loss_pct) -> str:
    order = Order()
    order.orderType        = 'TRAIL'
    order.action           = 'SELL'
    order.totalQuantity    = shares
    order.trailingPercent  = round(stop_loss_pct * 100, 2)   # default: 7.0
    order.tif              = 'GTC'
    ib.placeOrder(contract, order)
```

**How IBKR manages it:**
- IBKR tracks the HWM price tick-by-tick internally
- Trailing stop = `peak_price x 0.93` — computed live by IBKR, never stored in Supabase
- If stock never gains: stop = `buy_price x 0.93` (equivalent to a hard stop-loss)
- If stock rises to $130 from $100: stop rises to $120.90, locking in gains
- **The bot does NOT track HWM price.** IBKR owns it.

**Detection:** When the trailing stop fires, IBKR closes the position. `reconcile_with_ibkr()` detects it via Case 1 (position in Supabase but not in `ib.portfolio()`) and archives it to `trade_history`.

---

## Exit Mechanism 2 — Moving Average Support Breach (EOD only)

```python
if EXIT_MA_TRIGGER_ENABLED:
    if EXIT_MA_EOD_ONLY:
        is_ma_window = (now_ny.hour == 15 and now_ny.minute >= 45)   # 3:45-4:00 PM ET
    if is_ma_window:
        ma_val = get_ma_value(ticker, current_price, EXIT_MA_TYPE, EXIT_MA_WINDOW)
        threshold = ma_val * (1 - EXIT_MA_BUFFER_PCT)
        if current_price < threshold:
            execute_sell(..., reason="EMA-21 Exit...")
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EXIT_MA_TRIGGER_ENABLED` | `true` | Enable/disable MA exit |
| `EXIT_MA_TYPE` | `EMA` | `EMA` or `SMA` |
| `EXIT_MA_WINDOW` | `21` | Lookback window in trading days |
| `EXIT_MA_BUFFER_PCT` | `0.01` | 1% buffer below MA before triggering |
| `EXIT_MA_EOD_ONLY` | `true` | Only check between 3:45-4:00 PM ET |

**Failsafe:** If the FMP historical API fails, the check is skipped (fail-safe, not fail-open).

---

## Exit Mechanism 3 — EOD Plateau Rotation (3:45–4:00 PM ET)

Handles gradual stalls — stocks that have stopped making new highs and are going sideways.

**The rule:** At 3:45 PM, if the portfolio is full AND a fresh breakout trigger exists that we do not hold, sell the position that has gone the longest without making a new intraday high.

**Activation conditions (ALL must be true):**
- Time is 3:45–4:00 PM ET
- Portfolio is at `MAX_POSITIONS` capacity
- At least one fresh trigger exists in `daily_triggers` that we do not already hold
- The most-stalled position has `days_since_hwm >= PLATEAU_DAYS` (default: 10)

**hwm_date tracking:**
- Every monitor cycle, per position: if `current_price > intraday_peak[ticker]`, update `hwm_date = today` in Supabase
- `intraday_peak` is stored in-memory within the monitor session (defaults to `buy_price` at startup)
- `hwm_date` is the DATE of the last new high — not a price

**Why EOD?** The replacement buy happens next morning via `run_market_open_buys()`. Selling at EOD ensures the slot is free without leaving the portfolio underinvested overnight.

---

## Self-Healing: Trailing Stop Re-Placement

GTC orders survive IBKR gateway restarts, but may be missing for positions created before this feature or after a full account reset.

Every monitor cycle, per position:
```python
_open_sells = [t for t in ib.openTrades()
               if t.contract.symbol == ticker
               and t.order.action == 'SELL'
               and t.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')]

if len(_open_sells) < 1:
    cancel_ticker_sell_orders(ib, ticker)
    _new_group = place_trailing_stop(ib, contract, shares, STOP_LOSS_PCT)
    portfolio_positions.update({"oca_group": _new_group}).eq("ticker", ticker)
```

The new stop anchors from the **current market price** (slightly conservative vs. the true HWM peak, acceptable for this rare scenario).

---

## Manual Close Reconciliation (IBKR TWS)

If a position is manually closed in IBKR TWS, `reconcile_with_ibkr()` detects it:

```python
# Case 1: In Supabase but NOT in ib.portfolio()
sell_reason = "Manual close in IBKR (reconciled)"
# Uses FMP live price as sell price; logs to trade_history; removes from portfolio_positions
```

---

## execute_sell — Order Execution

```python
def execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason):
    cancel_ticker_sell_orders(ib, ticker)   # cancel trailing stop FIRST -- prevent double fill
    ib.sleep(1)
    contract = Stock(ticker, 'SMART', 'USD')
    order = MarketOrder('SELL', shares)
    trade = ib.placeOrder(contract, order)
```

**CRITICAL INVARIANT:** Supabase position deleted ONLY after confirming via `ib.portfolio()` that the position is truly gone from IBKR.

### P&L Calculation
```python
fill_price     = trade.orderStatus.avgFillPrice or current_price
profit_loss    = round((fill_price - buy_price) * shares, 2)
percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
```

### Supabase Logging
```python
trade_history.insert({
    "ticker", "shares", "buy_price", "buy_date", "buy_reason",
    "sell_price", "sell_reason", "sell_date", "profit_loss", "percent_return"
})
```

`sell_date` (not `created_at`) is used for cooling-off period checks.

---

## Exit Priority Summary

| Priority | Mechanism | Managed by | Timing |
|----------|-----------|-----------|--------|
| 1 | **Trailing Stop** (7% from peak) | IBKR (GTC TRAIL order) | Continuous, tick-by-tick |
| 2 | **MA Breach** (EMA-21 minus 1%) | Bot (`execute_sell`) | 3:45–4:00 PM ET only |
| 3 | **Plateau Rotation** (>=10 days no new HWM) | Bot (`execute_sell`) | 3:45–4:00 PM ET, full portfolio + fresh trigger |
| — | **Manual Close** | Reconcile detects it | Next 15-min cycle |

> [!NOTE]
> **Eliminated in this architecture:** Power Hold, explicit profit target limit orders, OCA bracket
> orders, `high_water_mark` price storage, `is_power_hold`, `profit_target`, `power_hold_expiry`.
> The trailing stop is the sole exit order placed at buy time.

---

## Sell Logic Flowchart

```
Every 15 min (9:30 AM - 4:00 PM ET):
    |
    v
reconcile_with_ibkr()  [catches trailing stop fires and manual TWS closes]
    |
    v
For each position in portfolio_positions:
    +-- get_live_price() -- skip if <= 0
    +-- price > intraday_peak? -> update hwm_date = today
    +-- No active SELL in IBKR? -> self-heal: place_trailing_stop()
    +-- 3:45 PM + MA enabled? -> price < EMA-21 x 0.99 -> execute_sell("MA Exit")
    +-- (hold -- trailing stop managed by IBKR)
    |
    v
EOD Plateau Rotation (3:45-4:00 PM only):
    +-- Portfolio full AND fresh triggers exist?
    |   +-- days_since_hwm >= PLATEAU_DAYS -> sell most stalled
    +-- (skip if conditions not met)
```
