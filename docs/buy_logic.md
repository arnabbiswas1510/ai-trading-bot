# Buy Logic

## Overview

**File:** `execution_agent.py` — function `run_market_open_buys(ib: IB)`

Executes at **market open (9:30 AM ET, Mon–Fri)** and runs as a **failsafe throughout the day** if a daily buy has not already occurred. Triggers are sorted by `ai_rating` (descending) — highest-rated breakouts are evaluated first.

---

## Execution Timing

```python
# main_loop()
if is_market_open and not has_bought_today(client, today_str):
    reconcile_with_ibkr(ib)   # Sync before placing any new buys
    run_market_open_buys(ib)
    ib.sleep(900)
    continue
```

- Runs every 15-minute cycle until `has_bought_today()` returns True
- `reconcile_with_ibkr()` always runs first (uses `ib.portfolio()` not `ib.positions()`)
- Once a buy occurs, the loop switches to the intraday monitoring branch

---

## Buy Gate 1 — Portfolio Cap Check

```python
stock_holdings = holdings   # all portfolio_positions rows
if len(stock_holdings) >= MAX_POSITIONS:
    print("Portfolio is fully invested. Standing down.")
    return
```

All positions count toward the cap — there is no separate ETF parking logic in the current implementation.

---

## Buy Gate 2 — Trigger Availability

```python
recent_date = today - timedelta(days=TRIGGER_LOOKBACK_DAYS)   # default: 3 days
triggers = client.table("daily_triggers").select("*").gte("triggered_at", recent_date)
triggers.sort(key=lambda x: x.get("ai_rating") or 0, reverse=True)
```

Looks back 3 days to capture triggers from weekends and market holidays. Triggers are sorted by AI rating descending (best opportunities first).

---

## Buy Gate 3 — Duplicate Position Guard

```python
if ticker in active_tickers:
    continue
```

---

## Buy Gate 4 — Cooling-Off Period

```python
cooling_cutoff = today - timedelta(days=COOLING_OFF_DAYS)   # default: 3 days
recent_sells = trade_history.select("ticker").eq("ticker", ticker).gte("sell_date", cooling_cutoff)
if recent_sells.data:
    continue   # Stopped out recently — skip
```

Uses `sell_date` column (not `created_at`).

---

## Buy Gate 5 — Re-verify Portfolio Cap (within loop)

```python
portfolio_res = client.table("portfolio_positions").select("*")   # refreshed each iteration
holdings = portfolio_res.data or []
if len(holdings) >= MAX_POSITIONS:
    break
```

Catches the case where a buy earlier in the same loop iteration fills the last slot.

---

## Buy Gate 6 — Cash Sufficiency

```python
available_cash = get_available_cash(ib)   # IBKR AvailableFunds USD
if available_cash < MIN_POSITION_SIZE:
    continue
```

---

## Buy Gate 7 — Pivot Extension (O'Neil Buy Zone)

```python
pivot_price   = float(trigger["close_price"])    # Price at breakout candle
extension_pct = (current_price - pivot_price) / pivot_price

if extension_pct > MAX_PIVOT_EXTENSION:   # default: 0.05 (5%)
    continue   # Stock is "extended" -- risk/reward unfavorable
```

Prevents buying a stock that has already run more than 5% beyond its breakout pivot. Critical when evaluating 1–3 day old triggers after a weekend or holiday.

---

## Position Sizing

```python
stock_held_count = len(holdings)
remaining_slots  = max(1, MAX_POSITIONS - stock_held_count)
position_size    = available_cash / remaining_slots
shares           = int(position_size / current_price)
```

**Equal-weight across unfilled slots.** Example: `$20,000 cash, 2 remaining slots -> $10,000 per position`

---

## Order Execution

```python
contract = Stock(ticker, 'SMART', 'USD')
ib.qualifyContracts(contract)
order = MarketOrder('BUY', shares)
order.account = get_ibkr_account(ib)
trade = ib.placeOrder(contract, order)

# Wait up to 60s for fill
for _ in range(60):
    ib.sleep(1)
    if trade.orderStatus.status in ('Filled', 'Cancelled', 'Inactive'):
        break

actual_shares = int(trade.orderStatus.filled)
fill_price    = round(trade.orderStatus.avgFillPrice, 2)
```

> [!IMPORTANT]
> Fill verification uses `trade.orderStatus.filled` — NOT `ib.portfolio()`. Polling the portfolio
> immediately after a fill can return stale data if the IBKR API lags.

If `actual_shares == 0`: logs failure via Telegram, skips Supabase insert, continues to next trigger.

---

## Post-Fill: Trailing Stop + Supabase Insert

```python
# 1. Place GTC trailing stop (IBKR manages HWM price tick-by-tick)
oca_str = place_trailing_stop(ib, contract, actual_shares, STOP_LOSS_PCT)

# 2. Insert position record
portfolio_positions.insert({
    "ticker":     ticker,
    "shares":     actual_shares,
    "buy_price":  fill_price,
    "buy_reason": f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
    "buy_source": "daily_triggers",
    "stop_loss":  round(fill_price * (1 - STOP_LOSS_PCT), 2),
    "hwm_date":   today.isoformat(),   # plateau clock starts at buy date
    "oca_group":  oca_str,
})
```

| Field | Value | Notes |
|-------|-------|-------|
| `stop_loss` | `fill x (1 - STOP_LOSS_PCT)` | Reference only; live stop is IBKR-managed |
| `hwm_date` | `today` | Date of last new intraday high; plateau clock starts here |
| `oca_group` | trailing stop group ID | Used by self-healing to avoid double-placing |
| `buy_source` | `"daily_triggers"` | Tags position source |

> [!NOTE]
> **No `profit_target`, `high_water_mark`, `is_power_hold` fields are written.**
> These have been eliminated. The trailing stop is the only exit order placed at buy time.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | Maximum concurrent positions |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum USD floor per position |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Days back to look for valid triggers |
| `COOLING_OFF_DAYS` | `3` | Days before a stopped-out ticker can be re-bought |
| `MAX_PIVOT_EXTENSION` | `0.05` | Skip if price >5% above pivot breakout |
| `STOP_LOSS_PCT` | `0.07` | Trailing stop percentage (IBKR-managed) |
| `PLATEAU_DAYS` | `10` | Days without a new HWM before plateau rotation eligibility |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Enable SPY SMA200 bear market gate |
| `MARKET_DIRECTION_TICKER` | `SPY` | Ticker used to gauge market direction |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | SMA window for market direction |

---

## Buy Decision Flowchart

```
Market Open (or intraday failsafe):
    |
    v
reconcile_with_ibkr()  [uses ib.portfolio()]
    |
    v
has_bought_today()? -> YES -> switch to monitor loop
    |
    v
Fetch daily_triggers (last 3 days), sort by ai_rating desc
    |
    v
Gate 1: len(holdings) >= MAX_POSITIONS? -> return (full)
    |
    v
For each trigger (highest ai_rating first):
    +-- Already holding? -> Skip
    +-- Cooling-off? -> Skip
    +-- Portfolio full (mid-loop check)? -> Break
    +-- Cash < MIN_POSITION_SIZE? -> Skip
    +-- Price > pivot + 5%? -> Skip (extended)
    +-- shares <= 0? -> Skip
    |
    v
MarketOrder BUY -> wait up to 60s for fill
    +-- 0 shares filled? -> log failure, skip
    |
    v
place_trailing_stop() -> GTC TRAIL order on IBKR
    |
    v
INSERT into portfolio_positions [hwm_date=today, no profit_target/high_water_mark]
    |
    v
Telegram notify_buy()
```
