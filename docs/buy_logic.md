# Buy Logic

## Overview

**File:** `execution_agent.py` — function `run_market_open_buys(ib: IB)`

Executes at **market open (9:30–9:45 AM ET, Mon–Fri)**. Reads breakout triggers from Supabase and places market buy orders via IBKR for any qualifying tickers not already held.

---

## Execution Timing

The main daemon loop triggers buys only within a precise window:

```python
if now.hour == 9 and 30 <= now.minute <= 45:
    reconcile_with_ibkr(ib)   # Sync portfolio state first
    run_market_open_buys(ib)
    time.sleep(900)            # Sleep 15 min to prevent duplicate runs
```

- Runs **once per market open** (the 15-min sleep prevents re-triggering during the same window)
- `reconcile_with_ibkr()` always runs first to ensure Supabase reflects actual IBKR holdings before any buy decisions

---

## Buy Decision Pipeline

### Step 1 — Fetch Recent Triggers

```python
recent_date = today - timedelta(days=3)
triggers = daily_triggers.select("*").gte("triggered_at", recent_date)
```

Looks back **3 days** to capture triggers from weekends and market holidays. If no triggers exist, execution stops.

---

### Step 2 — Load Current Portfolio

```python
holdings = portfolio_positions.select("*")
active_tickers = [h["ticker"] for h in holdings]
```

Used to enforce the position cap and prevent buying a stock already held.

---

### Step 3 — Portfolio Cap Check

```python
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 4))  # default: 4

if len(holdings) >= MAX_POSITIONS:
    # Fully invested — skip all buys
    return
```

If the portfolio is at or above `MAX_POSITIONS`, no buys are made. Configurable via `.env`.

---

### Step 4 — Per-Trigger Checks (loop over each trigger)

For each breakout trigger:

#### 4a. Duplicate Position Guard
```python
if ticker in active_tickers:
    continue  # Already holding — skip
```

#### 4b. Cash Sufficiency Check
```python
MIN_POSITION_SIZE = float(os.getenv("MIN_POSITION_SIZE", 5000.0))  # default: $5,000

available_cash = get_available_cash(ib)  # Queries IBKR CashBalance (USD)
if available_cash < MIN_POSITION_SIZE:
    continue  # Not enough cash for even a minimum-sized position
```

Cash is fetched live from IBKR (`CashBalance` tag, falling back to `TotalCashValue`).

#### 4c. Re-verify Portfolio Cap (within loop)
```python
portfolio_res = portfolio_positions.select("*")  # Refreshed each iteration
if len(portfolio_res.data) >= MAX_POSITIONS:
    break  # Capacity reached mid-loop
```

Prevents race condition where multiple triggers could push past MAX_POSITIONS in the same run.

---

### Step 5 — Position Sizing

```python
remaining_slots = max(1, MAX_POSITIONS - len(current_holdings))
position_size = available_cash / remaining_slots
```

**Equal-weight allocation:** Cash is divided evenly across remaining unfilled slots.

Example: `$20,000 cash, 2 remaining slots → $10,000 per position`

---

### Step 6 — Live Price Fetch

```python
current_price = get_live_price(ticker)  # FMP /stable/quote
if current_price <= 0:
    current_price = trigger["close_price"]  # Fallback to yesterday's close
```

Attempts a fresh FMP quote. Falls back to the price recorded in the trigger if FMP fails.

---

### Step 7 — Share Count Calculation

```python
shares = int(position_size / current_price)
if shares <= 0:
    continue  # Price too high for computed position size
```

Integer division — no fractional shares. If the stock price exceeds the position size, the ticker is skipped.

---

### Step 8 — IBKR Market Order

```python
contract = Stock(ticker, 'SMART', 'USD')
ib.qualifyContracts(contract)
order = MarketOrder('BUY', shares)
trade = ib.placeOrder(contract, order)
ib.sleep(3)  # Wait for fill
```

- Uses IBKR Smart Routing (`SMART` exchange)
- **Market order** — no limit price
- Waits **3 seconds** for fill confirmation

---

### Step 9 — Fill Price Capture

```python
fill_price = trade.orderStatus.avgFillPrice  # Actual IBKR fill
if fill_price <= 0:
    fill_price = current_price  # Fallback to pre-order quote
```

---

### Step 10 — Stop-Loss & Profit Target Calculation

Computed from the **actual fill price**:

```python
stop_loss     = round(fill_price * 0.93, 2)   # 7% below fill
profit_target = round(fill_price * 1.25, 2)   # 25% above fill
```

| Exit Level | Formula | Description |
|------------|---------|-------------|
| Stop-Loss | fill × 0.93 | Hard cut-loss at -7% |
| Profit Target | fill × 1.25 | Take-profit at +25% |

---

### Step 11 — Supabase Position Record

```python
position_data = {
    "ticker": ticker,
    "shares": shares,
    "buy_price": fill_price,
    "buy_reason": f"CANSLIM Breakout: Vol Surge {trigger['volume_surge']}x",
    "stop_loss": stop_loss,
    "profit_target": profit_target,
    "is_power_hold": False
}
portfolio_positions.insert(position_data)
```

The `buy_reason` captures the volume surge ratio from the technical trigger for trade audit purposes.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | Maximum concurrent open positions |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum USD floor per position |
| `IB_GATEWAY_HOST` | `localhost` | IBKR Gateway hostname |
| `IB_GATEWAY_PORT` | `7497` | IBKR Gateway API port |

---

## Buy Decision Flowchart

```
Market Open (9:30–9:45 AM ET)
    │
    ├─ reconcile_with_ibkr()
    │
    ▼
Fetch triggers (last 3 days)
    │
    ├─ No triggers? → Exit
    │
    ▼
Load current portfolio
    │
    ├─ >= MAX_POSITIONS? → Exit
    │
    ▼
For each trigger:
    ├─ Already holding ticker? → Skip
    ├─ Cash < MIN_POSITION_SIZE? → Skip
    ├─ Portfolio refilled to MAX? → Break
    │
    ▼
Position size = cash / remaining_slots
    │
    ▼
Get live FMP price (fallback: trigger close)
    │
    ▼
shares = int(position_size / price)
    │
    ├─ shares <= 0? → Skip
    │
    ▼
IBKR MarketOrder BUY → wait 3s → get fill price
    │
    ▼
stop_loss = fill * 0.93
profit_target = fill * 1.25
    │
    ▼
Insert into Supabase portfolio_positions
```
