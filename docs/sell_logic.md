# Sell Logic

## Overview

**File:** `execution_agent.py`

Sell decisions are made by two functions:
1. `monitor_portfolio_intraday(ib)` — runs every 15 minutes during market hours, enforces exits
2. `execute_sell(ib, client, ticker, ...)` — the actual sell execution called by the monitor

Each 15-min cycle runs **three phases**:
1. **Pre-pass 0** — Bear market check: liquidate ETF parking if SPY < SMA200
2. **Per-position loop** — Power Hold, trailing stop, profit target (ETF positions skipped)
3. **Re-park** — `run_etf_parking()` called after every sell to immediately redeploy freed cash

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

## Phase 0 — Bear Market ETF Liquidation

At the start of every monitoring cycle, the market direction is checked:

```python
if ETF_PARKING_ENABLED:
    if not is_market_bullish():   # SPY.close < SPY.SMA200
        etf_positions = get_etf_positions(client)
        if etf_positions:
            liquidate_etf_positions(ib, client, etf_positions,
                reason="Bear market: SPY below SMA200. Holding cash.")
```

`is_market_bullish()` fetches SPY EOD history from FMP and compares the latest close to its SMA(200).
- Returns **True** (bull) if SPY > SMA200 → ETF stays parked
- Returns **False** (bear) → immediately liquidate all `buy_source='etf_parking'` positions → hold cash
- **Fails open** (returns True) if FMP API is unavailable, to avoid unintended cash locks

---

## Phase 1 — Stale Position Rotation (Migrated)

> [!NOTE]
> The Stale Position Rotation logic has been **migrated** to `run_market_open_buys` in `buy_logic.md`. 
> It now runs as a pre-flight check before attempting new buys, rather than as a standalone phase during intraday monitoring.

---

## Phase 2 — Per-Position Loop

#### ETF Parking Skip

```python
if pos.get("buy_source") == "etf_parking":
    continue   # ETF parking positions have their own lifecycle
```

ETF parking positions are never evaluated for stop-loss or profit target. They are managed exclusively by `run_etf_parking()` and `liquidate_etf_positions()`.

#### IBKR Sync Guard

```python
ib_map   = {p.contract.symbol: p for p in ib.portfolio()}   # NOT ib.positions()
ib_tickers = list(ib_map.keys())

if ticker not in ib_tickers:
    continue   # Already closed in IBKR — reconcile_with_ibkr() handles it
```

#### High-Water Mark Update

```python
if current_price > high_water_mark:
    high_water_mark = round(current_price, 2)
    portfolio_positions.update({"high_water_mark": high_water_mark}).eq("ticker", ticker)
```

`high_water_mark` is initialized to `buy_price` at purchase. It rises with price, never falls.
Persisted to Supabase each cycle to survive restarts.

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

**Effect:** The 25% profit target limit order is **deferred** for 8 weeks (or if already placed, it is canceled and only the trailing stop remains). The limit order is dynamically re-added by the self-healing routine once Power Hold expires. Stop-loss remains active.

**Expiry:**
```python
if is_power_hold and today >= power_hold_expiry:
    portfolio_positions.update({"is_power_hold": False, "power_hold_expiry": None})
```

---

## Check 2 — Trailing Stop Loss (7% below high-water mark)

```python
trailing_stop = round(high_water_mark * (1 - STOP_LOSS_PCT), 2)   # default: × 0.93

if current_price <= trailing_stop:
    execute_sell(..., reason=f"Trailing Stop...")
    if ETF_PARKING_ENABLED:
        run_etf_parking(ib, client)   # Re-park freed slot immediately
```

**How it works:**
- `high_water_mark` starts at `buy_price` and rises whenever the stock makes new highs
- Trailing stop is always `high_water_mark × 0.93` — computed live, never stored
- If stock never gains: trailing stop = `buy_price × 0.93` (same as a hard stop-loss)
- If stock rises to $130 from $100: trailing stop rises to $120.90, locking in gains

**Sell reason strings:**

| Scenario | Reason String |
|----------|---------------|
| Stock gained before pullback | `Trailing Stop (-7% from high of $130.00, locked in +30.0% gain)` |
| Stock never gained | `Trailing Stop (-7% from entry — position never gained)` |

No exceptions — fires even if `is_power_hold = True`.

---

## Check 3 — 25% Profit Target

```python
if current_price >= profit_target and not is_power_hold:
    execute_sell(..., reason="25% Profit Target")
    if ETF_PARKING_ENABLED:
        run_etf_parking(ib, client)   # Re-park freed slot immediately
```

- **Threshold:** `buy_price × (1 + PROFIT_TARGET_PCT)` — default +25% from entry
- **Blocked during Power Hold:** If `is_power_hold = True`, skipped entirely
- Note: trailing stop still applies during Power Hold — position is not unprotected

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
| Phase 0 | Bear market ETF exit | SPY < SMA200 → liquidate ETF parking | N/A (ETF only) |
| 1 | Trailing Stop Loss | `price <= high_water_mark × 0.93` | No — always fires |
| 2 | 25% Profit Target | `price >= buy × 1.25` | Yes — suspended during hold |
| 3 | Moving Average Breach | `price < MA * (1 - Buffer)` (EOD only) | No — EMA-21 support check |
| — | Power Hold Activation | 20% gain in ≤21 days | Suspends profit target for 8 weeks |

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
Every 15 min (9:45 AM – 4:00 PM ET)
    │
    ▼
reconcile_with_ibkr()  [ib.portfolio() — catches manual TWS closes]
    │
    ▼
PHASE 0: Bear market check
    ├─ SPY > SMA200? → BULL, continue
    └─ SPY < SMA200? → BEAR → liquidate all ETF parking positions → hold cash
    │
    ▼
PHASE 2: For each position in portfolio_positions:
    │
    ├─ buy_source == 'etf_parking'? → SKIP (managed by run_etf_parking)
    │
    ├─ Not in ib.portfolio()? → Skip (reconcile handles it)
    │
    ▼
Fetch live price (FMP) + update high_water_mark if new high
    │
    ├─ Price <= 0? → Skip cycle
    │
    ▼
Power Hold check:
    ├─ price >= entry*1.20 AND days_held <= 21 AND not power_hold?
    │   └─ Activate: is_power_hold=True, expiry=today+8weeks
    │
    ├─ is_power_hold AND today >= expiry?
    │   └─ Deactivate: is_power_hold=False
    │
    ▼
Check 1: price <= high_water_mark * 0.93 (trailing stop)?
    └─ YES → execute_sell("Trailing Stop...") → run_etf_parking()
    │
    ▼
Check 2: price >= profit_target (entry * 1.25) AND NOT power_hold?
    └─ YES → execute_sell("25% Profit Target") → run_etf_parking()
    │
    ▼
Check 3: price < EMA-21 * 0.99 (EOD only)?
    └─ YES → execute_sell("EMA-21 Exit...") → run_etf_parking()
    │
    ▼
(No exit — hold)
```
