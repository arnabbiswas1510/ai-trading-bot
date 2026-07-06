# Buy Logic

## Overview

**File:** `execution_agent.py` — function `run_market_open_buys(ib: IB)`

Executes at **market open (9:30–9:45 AM ET, Mon–Fri)** and acts as a **failsafe** throughout the day if a daily buy has not already occurred. Runs a 6-step cascade:
1. Liquidate ETF parking positions to free cash for incoming CANSLIM triggers
2. Stale Rotation (free the lowest-quality sideways slot for a fresh trigger)
3. Buy from `daily_triggers` (primary CANSLIM breakouts)
4. Liquidate remaining ETF slots for incoming momentum triggers
5. Buy from `momentum_triggers` (secondary relaxed screener) if slots remain
6. Park any still-empty slots in `QQQ` (bull market) or hold cash (bear market)

---

## Execution Timing

The main daemon loop triggers buys at the market open window, but a **failsafe check** ensures that a daily buy occurs unconditionally at some point during the day if there is a buy opportunity and available capital, ignoring the strict 9:30–9:45 window.

```python
if (now.hour == 9 and 30 <= now.minute <= 45) or daily_failsafe_check():
    reconcile_with_ibkr(ib)   # Sync portfolio state first
    run_market_open_buys(ib)
    time.sleep(900)            # Sleep 15 min to prevent duplicate runs
```

- Runs **once per market open** (plus anytime the daily failsafe kicks in)
- `reconcile_with_ibkr()` always runs first — uses `ib.portfolio()` (not `ib.positions()`) to ensure Supabase reflects actual IBKR holdings and detects unintended short positions.

---

## Step 1 — Fetch Recent Triggers

```python
recent_date = today - timedelta(days=TRIGGER_LOOKBACK_DAYS)   # default: 3
triggers = daily_triggers.select("*").gte("triggered_at", recent_date)
```

Looks back **3 days** to capture triggers from weekends and market holidays. If no primary triggers exist, execution falls through to the momentum cascade (it does **not** stop early).

---

## Step 2 — Load Portfolio & Cap Check

```python
holdings     = portfolio_positions.select("*")
active_tickers = [h["ticker"] for h in holdings]

# Only count real stock positions — ETF parking is displaceable liquid cash
stock_holdings = [h for h in holdings if h.get("buy_source") != "etf_parking"]
if len(stock_holdings) >= MAX_POSITIONS:
    run_etf_parking(ib, client)   # still check market direction
    return
```

Cap check counts **stock positions only**. ETF parking positions (`buy_source='etf_parking'`) are excluded because they will be sold pre-flight to make room for incoming triggers.

---

## Step 3 — Pre-flight ETF Liquidation (for primary triggers)

Before processing `daily_triggers`, any ETF parking positions are sold to free cash:

```python
new_trigger_count = count of triggers not already held
etf_to_sell = get_etf_positions(client)
if etf_to_sell and new_trigger_count > 0:
    sell_count = min(len(etf_to_sell), new_trigger_count)
    liquidate_etf_positions(ib, client, etf_to_sell[:sell_count], reason="Pre-flight...")
    # Refresh holdings after ETF sell
```

Only enough ETF positions are sold to match the number of incoming triggers.

---

## Step 4 — Stale Position Rotation

Before processing `daily_triggers`, the bot scans all holdings for **non-performing positions** and rotates
out the single worst-quality performer if a better opportunity exists and the portfolio is full.

### Trigger Conditions (all must be true)
```python
if len(stock_holdings) >= MAX_POSITIONS         # Portfolio at capacity
        and fresh_triggers_today:               # Real replacement exists in daily_triggers
    # Scan stale candidates...
```

### Stale Candidate Criteria
```python
if (days_held >= STALE_HOLD_DAYS          # default: 15 days
        and gain_from_entry < STALE_HOLD_MAX_GAIN   # default: < 3%
        and not is_power_hold             # Power Hold positions exempt
        and buy_source != "etf_parking"): # ETF parking handled by run_etf_parking()
    stale_candidates.append(...)
```

Only **one position is sold per cycle**. The next worst exits in a subsequent cycle after a replacement fills the freed slot.

---

## Step 5 — Per-Trigger Checks (primary CANSLIM loop)

For each trigger from `daily_triggers`:

#### 4a. Duplicate Position Guard
```python
if ticker in active_tickers:
    continue
```

#### 4b. Cooling-Off Period
```python
cooling_cutoff = today - timedelta(days=COOLING_OFF_DAYS)   # default: 3
recent_sells = trade_history.select("ticker").eq("ticker", ticker).gte("sell_date", cooling_cutoff)
if recent_sells.data:
    continue   # Sold recently — skip
```

Uses `sell_date` column (not `created_at`).

#### 4c. Re-verify Stock Cap (within loop)
```python
portfolio_res = portfolio_positions.select("*")   # Refreshed each iteration
if sum(1 for p in portfolio_res.data if p.get("buy_source") != "etf_parking") >= MAX_POSITIONS:
    break   # Stock capacity reached mid-loop (ETFs not counted)
```

#### 4d. Cash Sufficiency
```python
available_cash = get_available_cash(ib)   # IBKR CashBalance USD
if available_cash < MIN_POSITION_SIZE:
    continue
```

---

## Step 6 — Position Sizing

```python
stock_held_count = sum(1 for h in holdings if h.get("buy_source") != "etf_parking")
remaining_slots  = max(1, MAX_POSITIONS - stock_held_count)
position_size    = available_cash / remaining_slots
```

**Equal-weight across unfilled stock slots.** ETF slots are excluded from the denominator.

Example: `$20,000 cash, 2 remaining stock slots → $10,000 per position`

---

## Step 7 — Pivot Extension Gate (O'Neil Buy Zone)

```python
pivot_price   = float(trigger["close_price"])    # Price at breakout candle
extension_pct = (current_price - pivot_price) / pivot_price

if extension_pct > MAX_PIVOT_EXTENSION:   # default: 0.05 (5%)
    continue   # Stock is "extended" — risk/reward unfavorable
```

Prevents buying a stock that has already run more than 5% beyond its pivot. Critical when the bot evaluates 1–3 day old triggers after a weekend or holiday.

---

## Step 8 — IBKR Market Order & Fill Verification

```python
contract = Stock(ticker, 'SMART', 'USD')
ib.qualifyContracts(contract)

# Marketable Limit Order (5% slippage buffer above ask) to guarantee fast fill
# 5% buffer matches MAX_PIVOT_EXTENSION and ensures we don't buy if it gapped too high,
# while giving enough headroom for FMP quote delays or wide spreads on illiquid stocks.
limit_price = round(current_price * 1.05, 2)
parent = LimitOrder('BUY', shares, limit_price)
trade = ib.placeOrder(contract, parent)

print(f"   Waiting for fill on {shares} shares of {ticker}...")
for _ in range(60):
    ib.sleep(1)
    if trade.orderStatus.status == 'Filled':
        break

if trade.orderStatus.status != 'Filled':
    ib.cancelOrder(parent)
    ib.sleep(2)

actual_shares = int(trade.orderStatus.filled)
if actual_shares == 0:
    # Order did not confirm in IBKR — skip Supabase insert
    notify_buy_failure(...)
    continue

fill_price = round(trade.orderStatus.avgFillPrice, 2)
```

> [!IMPORTANT]
> Fill verification uses `trade.orderStatus.filled` instead of polling `ib.portfolio()`.
> Polling the portfolio immediately can fail if the IBKR API lags a fraction of a second
> behind the order fill, which would previously cause the bot to silently drop the
> position insert and Telegram notification.

---

## Step 9 — Stop/Target Initialization & Supabase Record

```python
# The exit orders are attached directly to the parent buy order's ID
place_oca_bracket(
    ib, contract, actual_shares, fill_price,
    PROFIT_TARGET_PCT, STOP_LOSS_PCT,
    submit_limit_order=not is_power_hold,
    parent_order_id=trade.order.orderId
)

stop_loss     = round(fill_price * (1 - STOP_LOSS_PCT), 2)      # default: fill * 0.93
profit_target = round(fill_price * (1 + PROFIT_TARGET_PCT), 2)  # default: fill * 1.25

portfolio_positions.insert({
    "ticker":          ticker,
    "shares":          actual_shares,
    "buy_price":       fill_price,
    "buy_reason":      f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
    "buy_source":      "daily_triggers",
    "stop_loss":       stop_loss,
    "profit_target":   profit_target,
    "is_power_hold":   False,
    "high_water_mark": fill_price,
})
```

| Field | Formula | Description |
|-------|---------|-------------|
| `stop_loss` | fill × (1 − STOP_LOSS_PCT) | Stored for reference; live trailing stop uses `high_water_mark` |
| `profit_target` | fill × (1 + PROFIT_TARGET_PCT) | Take-profit at +25% (suspended during Power Hold) |
| `high_water_mark` | fill price | Initialized to fill; rises each cycle whenever price makes new high |
| `buy_source` | `"daily_triggers"` | Tags position quality — used for stale rotation priority |

---

## Step 10 — Momentum Cascade (if stock slots remain)

After the primary loop, if stock slots are still empty:

```python
stock_count = sum(1 for p in portfolio_res.data if p.get("buy_source") != "etf_parking")
if stock_count >= MAX_POSITIONS:
    return   # fully invested with stocks

momentum_triggers = momentum_triggers.select("*").gte("triggered_at", recent_date)
```

Before processing momentum triggers, a **second pre-flight** runs to sell any remaining ETF positions:

```python
if etf_to_sell and new_m_count > 0:
    liquidate_etf_positions(ib, client, etf_to_sell[:sell_count], reason="Momentum pre-flight...")
```

Momentum buys follow the same 7 gates as primary buys, with:
- `buy_source = "momentum_triggers"` (lower priority than `"daily_triggers"` in stale rotation)
- Same `ib.portfolio()` fill verification (same ghost-position protection)

See [Momentum Screener](momentum_screener.md) for details on how `momentum_triggers` are generated.

---

## Step 11 — ETF Cash Parking

After all buy attempts (primary + momentum), if stock slots are still empty:

```python
run_etf_parking(ib, client)
```

`run_etf_parking()` calls `is_market_bullish()` (SPY vs SMA200) and:
- **Bull market:** buys `QQQ` for remaining slots with `buy_source='etf_parking'`
- **Bear market:** holds pure cash (no ETF purchase)

ETF parking positions:
- **Count toward** `MAX_POSITIONS` but are excluded from cap checks for stock slots
- **Stop-loss / profit-target not enforced** (stored values are informational only)
- **Sold immediately** when real stock triggers arrive (pre-flight) or bear market detected

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | Maximum concurrent positions (stocks + ETF parking combined) |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum USD floor per position |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Days back to look for valid triggers (covers weekends/holidays) |
| `COOLING_OFF_DAYS` | `3` | Days before a stopped-out ticker can be re-bought |
| `MAX_PIVOT_EXTENSION` | `0.05` | Skip if price >5% above pivot (O'Neil buy zone rule) |
| `STOP_LOSS_PCT` | `0.07` | Initial trailing stop floor at purchase |
| `PROFIT_TARGET_PCT` | `0.25` | Take-profit target from entry |
| `ETF_PARKING_ENABLED` | `true` | Enable/disable ETF cash parking |
| `ETF_PARKING_TICKER` | `QQQ` | Ticker to park idle cash in |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Enable SPY SMA200 bear market gate |
| `MARKET_DIRECTION_TICKER` | `SPY` | Ticker used to gauge market direction |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | SMA window for market direction (O'Neil standard) |

---

## Buy Decision Flowchart

```
Market Open (9:30–9:45 AM ET)
    │
    ├─ reconcile_with_ibkr()  [uses ib.portfolio()]
    │
    ▼
Fetch daily_triggers (last 3 days)
    │
Load portfolio → stock-only cap check
    ├─ stocks >= MAX_POSITIONS? → run_etf_parking() → Exit
    │
    ▼
PRE-FLIGHT: sell ETF slots for incoming triggers
    │
    ▼
Stale Rotation: sell 1 sideways stock if portfolio full and triggers exist
    │
    ▼
For each daily_trigger:
    ├─ Already holding? → Skip
    ├─ Cooling-off? → Skip
    ├─ Stock cap reached? → Break (ETF not counted)
    ├─ Cash < MIN_POSITION_SIZE? → Skip
    ├─ Price > pivot + 5%? → Skip (extended)
    ├─ shares <= 0? → Skip
    │
    ▼
IBKR MarketOrder BUY → sleep 5s → verify via ib.portfolio()
    ├─ Not in portfolio? → sleep 3s → retry
    ├─ Still not in portfolio? → log failure, skip Supabase insert
    │
    ▼
Insert into portfolio_positions [buy_source='daily_triggers']
    │
    ▼
── Momentum Cascade ──
    │
Stock slots still empty?
    ├─ No → Exit
    │
    ▼
Fetch momentum_triggers → MOMENTUM PRE-FLIGHT: sell remaining ETF slots
    │
For each momentum_trigger:  [same 7 gates, buy_source='momentum_triggers']
    │
    ▼
── ETF Parking ──
    │
run_etf_parking():
    ├─ SPY > SMA200? → buy QQQ [buy_source='etf_parking']
    └─ SPY < SMA200? → hold cash
```
