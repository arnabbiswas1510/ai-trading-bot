# Sell Logic

## Overview

**File:** `execution_agent.py`

Sell decisions are made by two functions:
1. `monitor_portfolio_intraday(ib)` — runs every 15 minutes during market hours, enforces exits
2. `execute_sell(ib, client, ticker, ...)` — the actual sell execution called by the monitor

Each 15-min cycle runs one phase:
- **Per-position loop** — Time Stop, Power Hold, trailing stop, profit target (all stock positions)

---

## Monitoring Schedule

```python
# Intraday monitoring: 9:45 AM – 4:00 PM ET, every 15 minutes
if (now.hour == 9 and now.minute > 45) or (10 <= now.hour < 16):
    reconcile_with_ibkr(ib)
    monitor_portfolio_intraday(ib)
    time.sleep(900)
```

`reconcile_with_ibkr()` always runs before monitoring to catch any manual closes in IBKR TWS.

> [!IMPORTANT]
> `reconcile_with_ibkr()` uses `ib.portfolio()` (not `ib.positions()`). `ib.positions()` is a push
> subscription that may be empty in long-running sessions, causing all positions to appear "missing".
> All IBKR position checks in this file use `ib.portfolio()`.

---

## Check 0 — Opportunity-Cost Rotation (at market open, in `run_market_open_buys`)

> [!NOTE]
> The Opportunity-Cost Rotation logic lives in `run_market_open_buys` (see `buy_logic.md`).
> It runs once per day at market open as a pre-flight check before attempting new buys.
> It is NOT part of the intraday monitor loop.

**Purpose:** Prevent mediocre positions from occupying slots that a fresh CANSLIM breakout
could fill. Only fires when the portfolio is full AND a fresh trigger exists — you always
rotate INTO something confirmed better, never sell into a void.

**Activation conditions (ALL must be true):**
- Portfolio is full (`len(holdings) >= MAX_POSITIONS`)
- A fresh breakout trigger exists in `daily_triggers`
- Candidate position: `NOT is_power_hold`, `days_held >= STALE_HOLD_DAYS` (default 15), `gain < STALE_HOLD_MAX_GAIN` (default 10%)

**Gain threshold rationale (10%, raised from 3%):**
The 3% threshold only caught literally flat stocks. CANSLIM breakouts target 20–25%+; any
position below 10% total return from entry has not proven itself worth defending over a
fresh confirmed trigger. Stocks above 10% (including consolidating gainers) are protected.

| Condition | Threshold | Config |
|-----------|-----------|--------|
| Days held | ≥ 15 calendar days | `STALE_HOLD_DAYS=15` |
| Total gain from entry | < 10% | `STALE_HOLD_MAX_GAIN=0.10` |
| Power Hold active | NOT eligible | `is_power_hold=True` blocks rotation |
| Portfolio full | Required | `MAX_POSITIONS=4` |
| Fresh trigger exists | Required | `daily_triggers` table |

---

## Check 1 — Power Hold Rule (20% in 21 Days)

**Activation condition:**

```python
days_held = (now_utc - buy_date).days
if not is_power_hold and current_price >= (buy_price * (1 + POWER_HOLD_GAIN_TRIGGER)) and days_held <= POWER_HOLD_DAYS_LIMIT:
    expiry_date = today + timedelta(weeks=POWER_HOLD_DURATION_WEEKS)
    portfolio_positions.update({"is_power_hold": True, "power_hold_expiry": expiry_date})
```

| Condition | Threshold | Config |
|-----------|-----------|--------|
| Price gain from entry | >= 20% | `POWER_HOLD_GAIN_TRIGGER=0.20` |
| Days since purchase | <= 21 days | `POWER_HOLD_DAYS_LIMIT=21` |
| Hold duration | 8 weeks | `POWER_HOLD_DURATION_WEEKS=8` |

**Effect:** The 25% profit target limit order is deferred for 8 weeks. Since the limit order is
no longer placed at buy time (see Option C below), this check activates the Power Hold flag so
the self-healing routine continues to withhold the limit for the full hold duration.

**Why this now works reliably (Option C fix):**

Previously, a +25% limit order was placed at buy time as part of the native IBKR bracket. If the
stock surged ≥20% within the first day or two, IBKR's limit order would fill at +25% *before*
`monitor_portfolio_intraday()` had a chance to detect the ≥20% surge and cancel the limit.
This caused FBNC (+26% in 23h) and BWB (+25% in 26h) to be sold at their profit target instead
of entering an 8-week Power Hold.

The fix: no limit order is placed at buy time. Only the trailing stop is submitted. The
self-healing routine adds the limit at day 22+ if Power Hold has not activated.

**Expiry:**
```python
if is_power_hold and today >= power_hold_expiry:
    portfolio_positions.update({"is_power_hold": False, "power_hold_expiry": None})
    # Self-healing re-places full OCA bracket (trailing stop + 25% limit) next cycle
```

---

## Check 2 — Trailing Stop Loss (7% below high-water mark)

```python
trailing_stop = round(high_water_mark * (1 - STOP_LOSS_PCT), 2)   # default: × 0.93
```

Managed entirely by IBKR via GTC `TrailingStopOrder` placed at buy time. Python does NOT
call `execute_sell()` for stop-loss triggers. `reconcile_with_ibkr()` detects the closed
position and archives it on the next cycle.

**How it works:**
- `high_water_mark` starts at `buy_price` and rises whenever the stock makes new highs
- Trailing stop is always `high_water_mark × 0.93` — computed live by IBKR, never stored
- If stock never gains: trailing stop = `buy_price × 0.93` (same as a hard stop-loss)
- If stock rises to $130 from $100: trailing stop rises to $120.90, locking in gains

No exceptions — fires even during Power Hold.

---

## Check 3 — 25% Profit Target

Managed by IBKR via GTC `LimitOrder` placed by the self-healing routine at day 22+.

> [!IMPORTANT]
> **Option C change:** The +25% limit order is NOT placed at buy time. It is added
> automatically by `monitor_portfolio_intraday()` self-healing once `days_held > 21`
> and `is_power_hold = False`. During the first 21-day window, only the trailing stop
> protects the position. This prevents IBKR from filling the profit target before
> Power Hold has a chance to activate.

```python
# Self-healing logic (runs every cycle):
should_have_limit = days_held > POWER_HOLD_DAYS_LIMIT and not is_power_hold
expected_orders   = 2 if should_have_limit else 1
# If fewer orders than expected → place_oca_bracket(submit_limit_order=should_have_limit)
```

- **Blocked during Power Hold:** If `is_power_hold = True`, `should_have_limit = False` → limit never placed
- **Restored after Power Hold expiry:** `is_power_hold` set to False → limit added on next self-healing cycle

---

## Check 4 — Moving Average Support Breach

```python
if EXIT_MA_TRIGGER_ENABLED:
    # If EOD_ONLY is enabled, only runs between 3:45 PM and 4:00 PM ET
    if not EXIT_MA_EOD_ONLY or (now.hour == 15 and now.minute >= 45):
        ma_val = get_ma_value(ticker, current_price, EXIT_MA_TYPE, EXIT_MA_WINDOW)
        if ma_val is not None:
            threshold = ma_val * (1 - EXIT_MA_BUFFER_PCT)
            if current_price < threshold:
                execute_sell(..., reason=f"EMA-21 Exit...")
```

**How it works:**
- **Trigger Condition:** Sells the stock if the current price falls below its moving average (EMA-21 by default) minus the buffer.
- **Whipsaw Protection:**
  - **EOD-Only Checking:** If `EXIT_MA_EOD_ONLY` is enabled, the exit is only evaluated near the market close (3:45–4:00 PM Eastern Time), ignoring intraday whipsaws/noise.
  - **Buffer Percentage:** Introduces a buffer (default 1.0%, `EXIT_MA_BUFFER_PCT=0.01`) below the moving average price before triggering the exit.
- **Failsafe**: If the Financial Modeling Prep (FMP) historical EOD API fails or returns no history, the bot fails safe and does not trigger an exit.

---

## Sell Trigger Priority

| Priority | Trigger | Condition | Power Hold Override? |
|----------|---------|-----------|---------------------|
| 0 | Opportunity-Cost Rotation | held ≥15d AND gain < 10% AND portfolio full AND fresh trigger | No — exempt if `is_power_hold=True` |
| 1 | Trailing Stop Loss | `price <= high_water_mark × 0.93` (IBKR-managed) | No — always fires |
| 2 | 25% Profit Target | IBKR limit order at `buy × 1.25` (added day 22+ by self-healing) | Yes — limit not placed during Power Hold |
| 3 | Moving Average Breach | `price < MA * (1 - Buffer)` (EOD only) | No — EMA-21 support check |
| — | Power Hold Activation | 20% gain in ≤21 days | Defers limit order for 8 weeks |

---

## execute_sell — Order Execution

```python
def execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason):
```

### Order Placement
```python
contract = Stock(ticker, 'SMART', 'USD')
ib.qualifyContracts(contract)
order = MarketOrder('SELL', shares)
trade = ib.placeOrder(contract, order)
ib.sleep(3)
```

### P&L Calculation
```python
fill_price     = trade.orderStatus.avgFillPrice or current_price
profit_loss    = round((fill_price - buy_price) * shares, 2)
percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
```

### Supabase Logging (two sequential operations)

1. **Delete** from `portfolio_positions`
2. **Insert** into `trade_history`:
```python
trade_history.insert({
    "ticker":         ticker,
    "shares":         shares,
    "buy_price":      buy_price,
    "buy_date":       buy_date.isoformat(),
    "buy_reason":     buy_reason,
    "sell_price":     fill_price,
    "sell_reason":    reason,
    "sell_date":      today.isoformat(),
    "profit_loss":    profit_loss,
    "percent_return": percent_return,
})
```

`sell_date` (not `created_at`) is used for cooling-off period checks.

---

## Manual Close Reconciliation (IBKR TWS)

If a position is **manually closed in IBKR TWS**, `reconcile_with_ibkr()` detects it via `ib.portfolio()`:

```python
# Case 1: In Supabase but NOT in IBKR portfolio
sell_reason = "Manual close in IBKR (reconciled)"
# Uses FMP live price as sell price; logs to trade_history; removes from portfolio_positions
```

---

## Mock Sell (CLI Testing)

```bash
python execution_agent.py --mock-sell AAPL --price 195.50 --reason "Manual test exit"
```

Identical accounting logic to `execute_sell`, bypasses all IBKR calls.

---

## Sell Logic Flowchart

```
Every market open:
    │
    ▼
run_market_open_buys() — Opportunity-Cost Rotation pre-flight
    ├─ Portfolio full AND fresh trigger exists?
    │   ├─ Find positions: NOT power_hold, days_held ≥15, gain < 10%
    │   └─ If any: sell worst, proceed to buy
    │
    ▼
Every 15 min (9:45 AM – 4:00 PM ET)
    │
    ▼
reconcile_with_ibkr()  [ib.portfolio() — catches manual TWS closes]
    │
    ▼
PHASE 2: For each position in portfolio_positions:
    │
    ├─ Not in ib.portfolio()? → Skip (reconcile handles it)
    │
    ▼
Fetch live price (FMP) + update high_water_mark if new high
    │
    ├─ Price <= 0? → Skip cycle
    │
    ▼
Self-healing OCA bracket check
    ├─ Missing trailing stop OR (day 22+ AND missing limit)? → place_oca_bracket()
    │
    ▼
Power Hold check:
    ├─ price >= entry*1.20 AND days_held <= 21 AND not power_hold?
    │   └─ Activate: is_power_hold=True, expiry=today+8weeks
    │       (no limit order to cancel — none was placed at buy time)
    │
    ├─ is_power_hold AND today >= expiry?
    │   └─ Deactivate: is_power_hold=False
    │       (self-healing will add limit order next cycle)
    │
    ▼
Check 1: price <= high_water_mark * 0.93 (trailing stop — IBKR-managed)?
    └─ Detected next cycle via reconcile_with_ibkr()
    │
    ▼
Check 2: profit target (entry * 1.25) — IBKR limit order (added day 22+)?
    └─ Detected next cycle via reconcile_with_ibkr()
    │
    ▼
Check 3: price < EMA-21 * 0.99 (EOD only)?
    └─ YES → execute_sell("EMA-21 Exit...")
    │
    ▼
(No exit — hold)
```
