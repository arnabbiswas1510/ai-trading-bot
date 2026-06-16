# Sell Logic

## Overview

**File:** `execution_agent.py`

Sell decisions are made by two functions:
1. `monitor_portfolio_intraday(ib)` — runs every 15 minutes during market hours, enforces exits
2. `execute_sell(ib, client, ticker, ...)` — the actual sell execution called by the monitor

There is also a **Power Hold Rule** that overrides the normal profit target under specific conditions,
and a **Stale Rotation Pre-pass** that frees slots occupied by sideways positions.

---

## Monitoring Schedule

```python
# Intraday monitoring: 9:45 AM – 4:00 PM ET, every 15 minutes
if (now.hour == 9 and now.minute > 45) or (10 <= now.hour < 16):
    reconcile_with_ibkr(ib)
    monitor_portfolio_intraday(ib)
    time.sleep(900)
```

`reconcile_with_ibkr()` always runs before monitoring to catch any manual closes in IBKR TWS before the sell logic evaluates positions.

---

## monitor_portfolio_intraday — Decision Logic

Each 15-min cycle runs in two phases:
1. **Pre-pass** — Stale rotation check across the full portfolio (picks worst performer)
2. **Per-position loop** — Power Hold, trailing stop, and profit target checks

### Pre-check — IBKR Position Sync Guard
```python
if ticker not in ib_tickers:
    continue  # Already closed in IBKR — reconciliation handles it
```

Positions not found in IBKR are skipped (they will be reconciled by `reconcile_with_ibkr()`).

### Live Price Fetch
```python
current_price = get_live_price(ticker)   # FMP /stable/quote
if current_price <= 0:
    continue  # No valid price — skip this cycle
```

---

## Pre-pass — Stale Position Rotation

Before the per-position loop begins, the bot scans all holdings for **non-performing positions**
and rotates out the single worst performer if a better opportunity exists.

### Five-Gate Trigger (ALL must be true)

```python
if (days_held >= STALE_HOLD_DAYS           # default: 15 (configurable via .env)
        and gain_from_entry < STALE_HOLD_MAX_GAIN  # default: 0.03 = 3% (configurable via .env)
        and not is_power_hold               # Power Hold positions are exempt
        and len(positions) >= MAX_POSITIONS  # Portfolio must be at capacity
        and fresh_triggers_today):          # A real replacement must exist
    execute_sell(..., reason="Stale Rotation...")
```

### Tiebreaker — Worst Performer First

All qualifying stale candidates are sorted by `gain_from_entry` ascending:

```python
stale_candidates.sort(key=lambda x: x[0])   # lowest gain exits first
cull = stale_candidates[0]
```

Only **one position is sold per cycle**. If multiple stale positions exist,
the next worst exits in a subsequent cycle after a replacement buy fills the freed slot
(which happens at the next morning's 9:30 AM market open).

### Power Hold Exemption

Positions with `is_power_hold = True` are **never** considered for stale rotation.
A stock that surged 20%+ in ≤21 days has earned its hold period — any sideways
movement during the 8-week window is expected consolidation after a strong move.

### Opportunity Cost Gate

Rotation only fires if `get_fresh_triggers_today()` returns at least one ticker
**not already held**. This prevents exiting a position into an empty opportunity —
capital is only redeployed when there is actually a better signal available today.

### Sell Reason Format

```
Stale Rotation — held 18d, only +1.8% gain. Freeing slot for: NVDA
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STALE_HOLD_DAYS` | `15` | Min days held before eligible for rotation |
| `STALE_HOLD_MAX_GAIN` | `0.03` | Max gain (decimal) qualifying as "sideways" |

Both are set in `.env` and read at startup.

---

## Check 1 — Power Hold Rule (20% in 21 Days)

**Activation condition:**

```python
days_held = (now_utc - buy_date).days
if not is_power_hold and current_price >= (buy_price * 1.20) and days_held <= 21:
    # Activate Power Hold
    expiry_date = today + timedelta(weeks=8)
    portfolio_positions.update({
        "is_power_hold": True,
        "power_hold_expiry": expiry_date
    })
```

| Condition | Threshold |
|-----------|-----------|
| Price gain from entry | >= 20% |
| Days since purchase | <= 21 days |

**Effect:** The 25% profit target is **suspended** for 8 weeks from activation date. The stop-loss (-7%) remains active.

**Expiry:**
```python
if is_power_hold and today >= power_hold_expiry:
    # Deactivate — restore standard 25% profit target
    portfolio_positions.update({"is_power_hold": False, "power_hold_expiry": None})
```

Once the 8-week hold expires, the 25% profit target resumes immediately on the next monitoring cycle.

---

## Check 2 — Trailing Stop Loss (7% below high-water mark)

```python
# Rise the watermark whenever price makes a new high
if current_price > high_water_mark:
    high_water_mark = round(current_price, 2)
    # Persisted to Supabase portfolio_positions.high_water_mark

trailing_stop = round(high_water_mark * 0.93, 2)

if current_price <= trailing_stop:
    execute_sell(..., reason=f"Trailing Stop (...)")
```

**How it works:**
- `high_water_mark` is initialized to `buy_price` at purchase and stored in `portfolio_positions`
- Each monitoring cycle, if `current_price > high_water_mark`, the watermark is raised and persisted to Supabase
- The trailing stop is always `high_water_mark × 0.93` — computed live, never stored
- If the stock never gains, the trailing stop equals `buy_price × 0.93` (identical to the old hard stop)
- If the stock rises to $130 from $100 entry, the trailing stop rises to $120.90 — locking in profit

**Sell reason strings:**
| Scenario | Reason String |
|----------|---------------|
| Stock gained before pullback | `Trailing Stop (-7% from high of $130.00, locked in +30.0% gain)` |
| Stock never gained | `Trailing Stop (-7% from entry — position never gained)` |

- **No exceptions:** Fires even if `is_power_hold = True` (stop protection always active)

---

## Check 3 — 25% Profit Target

```python
if current_price >= profit_target and not is_power_hold:
    execute_sell(..., reason="25% Profit Target")
```

- **Threshold:** Price rises to or above `buy_price × 1.25` (25% gain from entry)
- **Blocked during Power Hold:** If `is_power_hold = True`, this check is skipped entirely
- Note: During a Power Hold, the trailing stop still applies — the position is not unprotected

---

## Sell Trigger Priority

| Priority | Trigger | Condition | Power Hold Override? |
|----------|---------|-----------|---------------------|
| Pre-pass | Stale Rotation | `days_held >= 15 AND gain < 3% AND full AND fresh trigger` | Yes — exempt |
| 1 | Trailing Stop Loss | `price <= high_water_mark × 0.93` | No — always fires |
| 2 | 25% Profit Target | `price >= buy × 1.25` | Yes — suspended during hold |
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
ib.sleep(3)   # Wait for fill
```

- Smart routing (`SMART` exchange), market order
- Waits **3 seconds** for fill confirmation

### Fill Price Capture
```python
fill_price = trade.orderStatus.avgFillPrice
if fill_price <= 0:
    fill_price = current_price   # Fallback to pre-order FMP quote
```

### P&L Calculation
```python
profit_loss     = round((fill_price - buy_price) * shares, 2)
percent_return  = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
```

### Supabase Logging

**Two database operations** (not wrapped in a transaction — sequential):

1. **Delete** from `portfolio_positions`:
```python
portfolio_positions.delete().eq("ticker", ticker)
```

2. **Insert** into `trade_history`:
```python
trade_history.insert({
    "ticker": ticker,
    "shares": shares,
    "buy_price": buy_price,
    "buy_date": buy_date.isoformat(),
    "buy_reason": buy_reason,
    "sell_price": fill_price,
    "sell_reason": reason,       # "7% Stop Loss" or "25% Profit Target"
    "profit_loss": profit_loss,
    "percent_return": percent_return
})
```

---

## Manual Close Reconciliation (IBKR TWS)

If a position is **manually closed in IBKR TWS** (bypassing the bot), `reconcile_with_ibkr()` handles it:

```python
# Case 1: In Supabase but NOT in IBKR
# Sell price resolution priority:
# 1. reqExecutions() → find most recent SLD fill for this ticker
# 2. FMP live quote (up to 15 min stale)
# 3. buy_price fallback (zero-loss placeholder)

sell_reason = "Manual close in IBKR (reconciled)"
```

The reconciler logs to `trade_history` with the best available sell price and removes from `portfolio_positions`.

---

## Mock Sell (CLI Testing)

For testing without IBKR connectivity:

```bash
python execution_agent.py --mock-sell AAPL --price 195.50 --reason "Manual test exit"
```

```python
def handle_mock_sell(ticker, price, reason):
    # Fetches position from Supabase
    # Computes P&L exactly as execute_sell would
    # Deletes from portfolio_positions
    # Inserts into trade_history
    # Does NOT touch IBKR
```

Identical accounting logic to `execute_sell`, bypasses all IBKR calls.

---

## Sell Logic Flowchart

```
Every 15 min (9:45 AM – 4:00 PM ET)
    │
    ▼
reconcile_with_ibkr()  ← catches manual TWS closes first
    │
    ▼
PRE-PASS: Stale Rotation scan (across all positions)
    ├─ Portfolio not full? → Skip
    ├─ No fresh triggers today? → Skip
    ├─ Collect stale candidates (days ≥ 15, gain < 3%, not Power Hold)
    ├─ None qualify? → Skip
    └─ Sort by gain asc → sell worst performer → free 1 slot
    │
    ▼
For each position in portfolio_positions:
    │
    ├─ Not in IBKR? → Skip (reconciler handled it)
    │
    ▼
Fetch live price (FMP) + update high_water_mark
    │
    ├─ Price <= 0? → Skip cycle
    │
    ▼
Check Power Hold activation:
    ├─ price >= entry*1.20 AND days_held <= 21 AND not already power_hold?
    │   └─ Set is_power_hold=True, expiry = today + 8 weeks
    │
    ├─ is_power_hold AND today >= expiry?
    │   └─ Set is_power_hold=False
    │
    ▼
Check 1: price <= high_water_mark * 0.93 (trailing stop)?
    └─ YES → execute_sell("Trailing Stop...") → continue
    │
    ▼
Check 2: price >= profit_target (entry * 1.25) AND NOT power_hold?
    └─ YES → execute_sell("25% Profit Target") → continue
    │
    ▼
(No exit triggered — hold position)
```
