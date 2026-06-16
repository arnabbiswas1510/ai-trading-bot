# Technical Triggers Logic

## Overview

**File:** `technical_screener.py`

Runs daily on the fundamental watchlist from Supabase. Each ticker is checked for a CANSLIM-style price/volume breakout. Passing tickers are written to the `daily_triggers` table, which the execution agent reads at market open to place buy orders.

---

## Pipeline

```
Supabase watchlist (latest run)
    │
    ▼
Fetch EOD price history via FMP (last 380 calendar days)
    │
    ▼
Compute: SMA-50 | Avg Volume-50 | 52-week Rolling High
    │
    ▼
Apply 3 breakout conditions (ALL must pass)
    │
    ▼
Supabase daily_triggers table  (pruned at 56 days)
```

---

## Step 1 — Watchlist Fetch

Fetches only the **most recent run's tickers** from Supabase:

```python
# Gets the single latest timestamp, then fetches all tickers from that run
latest_ts = watchlist.select("created_at").order(desc).limit(1)
tickers = watchlist.select("ticker").eq("created_at", latest_ts)
```

This ensures only the freshest fundamental candidates are screened technically, not stale prior-week entries.

---

## Step 2 — Price History Download

**Endpoint:** `GET /stable/historical-price-eod/full?symbol={ticker}&from={from}&to={to}`

- **Lookback window:** 380 calendar days (guarantees 252+ trading days for a full 52-week high)
- **Minimum required:** 50 trading days (tickers with less data are skipped)
- **Retry logic:** 3 attempts, exponential backoff (1s → 2s → 4s), handles HTTP 429

Data sorted ascending by date before indicator calculation.

---

## Step 3 — Indicator Computation

All indicators computed on the sorted daily OHLCV DataFrame:

### 50-Day Simple Moving Average (SMA-50)
```python
df['sma_50'] = df['close'].rolling(window=50).mean()
```

### 50-Day Average Volume
```python
df['avg_volume_50'] = df['volume'].rolling(window=50).mean()
```

### 52-Week Rolling High
```python
window_size = min(252, len(df))   # Graceful handling for newer stocks
df['rolling_high_52w'] = df['high'].rolling(
    window=window_size,
    min_periods=min(50, window_size)
).max()
```

Uses `high` (not `close`) to capture true intraday highs in the 52-week range.

---

## Step 4 — Breakout Conditions (All 3 Required)

All conditions are evaluated on **today's bar** (`df.iloc[-1]`):

### Condition 1 — Above 50-Day SMA
```python
is_above_50ma = current_close > sma_50
```
Price must be in a confirmed medium-term uptrend.

### Condition 2 — Volume Surge >= 40% Above Average
```python
volume_surge_ratio = today_volume / avg_vol_50
has_volume_surge = volume_surge_ratio >= 1.40
```
Requires today's volume to be at least **1.4× the 50-day average** — indicates institutional accumulation driving the move.

### Condition 3 — Within 2% of 52-Week Rolling High
```python
is_breaking_high = current_close >= (rolling_high_52w * 0.98)
```
Price must be at or near a new 52-week high — the CANSLIM "pivot point" / breakout zone.

---

## Step 5 — Breakout Record Construction

If all 3 conditions pass, the following record is created:

| Field | Calculation | Description |
|-------|-------------|-------------|
| `ticker` | — | Stock symbol |
| `close_price` | `current_close` | Today's closing price |
| `volume_surge` | `today_volume / avg_vol_50` | Volume ratio (e.g. 1.65 = 65% above avg) |
| `sma_50` | `df['close'].rolling(50).mean()[-1]` | 50-day SMA value |
| `rolling_high_52w` | Max high over up to 252 trading days | 52-week high anchor |
| `pivot_distance_pct` | `((close / rolling_high) - 1.0) × 100` | % above/below the 52w high (negative = below) |

---

## Step 6 — Supabase Persistence

**Table:** `daily_triggers`

```python
client.table("daily_triggers").insert(triggers).execute()
```

**Pruning:** Records older than **56 days** are deleted on each run:
```python
prune_threshold = today - timedelta(days=56)
client.table("daily_triggers").delete().lt("triggered_at", prune_threshold).execute()
```

---

## Breakout Signal Summary

| Indicator | Threshold | Purpose |
|-----------|-----------|---------|
| Price vs. SMA-50 | > SMA-50 | Medium-term trend confirmation |
| Volume Surge | >= 1.40x 50-day avg | Institutional demand signal |
| 52-Week High Proximity | Within 2% of rolling high | CANSLIM pivot breakout zone |

A ticker must pass **all three** to generate a trigger. There is no scoring or ranking — it is a binary pass/fail gate.

---

## Edge Cases Handled

| Scenario | Handling |
|----------|----------|
| Fewer than 50 days of history | Skipped with warning |
| Fewer than 252 days (newer stock) | 52w window shrinks to available history, min 50 days |
| FMP API error / timeout | Skipped with error log |
| HTTP 429 rate limit | Exponential backoff retry (up to 3x) |
| Zero avg volume | `volume_surge_ratio` defaults to 0 (fails condition) |
| No breakouts found | DB insert skipped, log message only |
