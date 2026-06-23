# Fundamental Screener Logic

## Overview

The fundamental screening system has **two layers**:

1. **Pipeline Screener** (`csv_watchdog.py`) — A robust local file watchdog that monitors a dropzone folder for TradingView CSV exports. It parses the raw data and updates the Supabase watchlist.
2. **CANSLIM Scoring Engine** (`backend/screener.py`) — Scores individual tickers from the watchlist across all 7 CANSLIM dimensions using FMP financial data for the frontend UI.

---

## Layer 1 — Pipeline Screener (`csv_watchdog.py`)

*(Note: The old API-based `fundamental_screener.py` workflow has been deprecated in favor of this highly reliable manual-drop system to avoid API rate limits and improve data accuracy).*

### Ticker Universe Construction

```
Primary source: TradingView "Stock Screener" CSV Export
Dropzone Path:  /home/dietpi/docker/config/qbittorrent/downloads/tv_fileDrop
```

You manually screen stocks in TradingView using your custom CAN SLIM filters, ensure the required fundamental columns are visible in the table, and export to CSV. You drop this CSV into the dropzone.

### CSV Watchdog Parsing Logic

The `csv_watchdog.py` script listens for new `.csv` files. When a file is dropped, it parses the data using a robust `parse_tv_number()` function that converts TradingView shorthand strings (e.g., `43.22M`, `1.28B`, `88.44%`) into raw mathematical floats and integers.

**Extracted Fields:**
- `Analyst rating` -> `analyst_rating`
- `EPS dil growth Quarterly QoQ` -> `q_eps_growth`
- `Revenue growth TTM YoY` -> `revenue_growth`
- `Float` -> `float_shares` (Used as a proxy for Supply / Institutional Sponsorship)
- `ROE TTM` -> `roe`
- `Earnings per share diluted growth %, TTM YoY` -> `a_eps_growth`
- `Market capitalization` -> Used to calculate `company_size`

**Company Size Categorization:**
- **Large Cap:** >= $10 Billion
- **Mid Cap:** >= $2 Billion and < $10 Billion
- **Small Cap:** < $2 Billion

### Ranking & Persistence

- The watchdog uses an **Upsert** strategy on the Supabase `watchlist` table.
- **New Stocks:** Inserted with `weeks_retained = 1` and `first_seen_at = now`.
- **Retained Stocks:** The metrics are updated, `weeks_retained` increments by 1, and `last_seen_at` updates.
- **Dropped Stocks:** Left untouched until the 8-week (56 days) pruning job cleans them up.

**Supabase `watchlist` schema:**

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | text | Stock symbol |
| `company_name` | text | Company name from TradingView |
| `company_size` | varchar | Large, Mid, or Small |
| `q_eps_growth` | float | Current quarterly EPS growth |
| `a_eps_growth` | float | 3-year annual EPS growth |
| `revenue_growth` | float | Revenue YoY growth |
| `roe` | float | Return on Equity TTM |
| `float_shares` | bigint | Shares float (proxy for Supply) |
| `analyst_rating` | varchar | TradingView Analyst Rating |
| `created_at` | timestamp | Audit trail persistence |
| `first_seen_at`| timestamp | Date first entered the watchlist |
| `last_seen_at` | timestamp | Date last seen in a CSV drop |
| `weeks_retained`| int | Number of consecutive weeks screened |

---

## Layer 2 — Full CANSLIM Scoring Engine (`backend/screener.py`)

*(This layer provides the fundamental scorecard for the React UI `ScreenerView.jsx`)*

Scores each watchlist ticker against all 7 CANSLIM dimensions using the FastAPI backend and SQLite `trading_bot.db`. Max total score = **100 points**.

### M — Market Direction (Max 15 pts)

Analyzes **S&P 500 (`^GSPC`)** and **Nasdaq (`^IXIC`)** via 1-year FMP historical data.

| Market State | Condition | Score |
|---|---|---|
| Confirmed Uptrend | Both indices above 50-day & 200-day SMA | **15** |
| Uptrend Under Pressure | One or both below 50-day but above 200-day | **5** |
| Market in Correction | Any index below 200-day SMA | **0** |

### C — Current Earnings (Max 15 pts)

Source: FMP quarterly income statements (last 5–6 quarters)

**YoY EPS Growth** (most recent quarter vs. same quarter prior year):

| Growth | Points |
|--------|--------|
| >= 25% | 8 pts + up to 4 bonus pts (linear beyond 25%) |
| 0–25% | Linear: `growth × 32` |
| Negative | 0 |

**EPS Acceleration** (current YoY growth > prior YoY growth): **+2 pts**

**Revenue Growth YoY**:

| Revenue Growth | Points |
|----------------|--------|
| >= 25% | +3 pts |
| 0–25% | Linear: `growth × 12` |

*Capped at 15 pts total.*

### A — Annual Earnings (Max 15 pts)

Source: FMP annual income statements + balance sheets (last 3 years)

**3-Year EPS CAGR** (`(eps_y0 / eps_y2)^0.5 - 1`):

| CAGR | Points |
|------|--------|
| >= 25% | 10 pts |
| >= 20% | 8 pts |
| 0–20% | Linear: `cagr × 40` |

**Return on Equity (ROE)**:

| ROE | Points |
|-----|--------|
| >= 17% | +5 pts |
| 0–17% | Linear: `roe × 29.4` |

*Capped at 15 pts total.*

### N — New Catalyst / Near 52-Week High (Max 15 pts)

Source: FMP quote (`yearHigh`, `priceAvg50`, `priceAvg200`) + historical data

**Distance from 52-week high:**

| Distance | Points |
|----------|--------|
| <= 5% below high | 12 pts |
| <= 15% below high | 10 pts |
| <= 25% below high | 5 pts |
| > 25% below high | 0 |

**Moving average confirmations:**
- Above 50-day SMA: **+2 pts**
- Above 200-day SMA: **+1 pt**

*Capped at 15 pts total.*

### S — Supply & Demand (Max 15 pts)

Source: Last 20 days of FMP historical OHLCV

**Accumulation vs. Distribution Days** (over past 20 trading days):
- **Accumulation day**: volume > 1.1x 50-day avg volume AND close change > +0.5%
- **Distribution day**: volume > 1.1x 50-day avg volume AND close change < -0.5%

| Condition | Points |
|-----------|--------|
| Accumulation > Distribution | 8 pts + min(4, acc_days - dist_days) bonus |
| Equal | 4 pts |
| Distribution >= Accumulation | 0 |

**Shares Outstanding (Float):**

| Float | Points |
|-------|--------|
| < 150M shares | +3 pts |
| 150M – 500M shares | +1.5 pts |
| > 500M shares | 0 |

*Capped at 15 pts total.*

### L — Relative Strength (Max 15 pts)

**RS Rating** (percentile rank among watchlist, 1–99):

```python
# Weighted RS Score (4 time periods):
weighted = (perf_3m × 0.40) + (perf_6m × 0.20) + (perf_9m × 0.20) + (perf_12m × 0.20)
# Converted to percentile rank (1–99) vs. all watchlist stocks
```

| RS Percentile | Points |
|---------------|--------|
| >= 90 | 13 pts |
| >= 80 | 10 pts |
| >= 60 | 5 pts |
| < 60 | 0 |

**Outperforming S&P 500** (last 3 months vs. last 100 days of `^GSPC`): **+2 pts**

*Capped at 15 pts total.*

### I — Institutional Sponsorship (Max 10 pts)

Source: FMP institutional holdings, computed as % of shares outstanding

| Institutional Ownership | Points | Rationale |
|------------------------|--------|-----------|
| 30%–85% | 10 pts | Sweet spot: institutional interest without overownership |
| 10%–30% or 85%–95% | 6 pts | Low or overcrowded |
| < 10% or > 95% | 2 pts | Neglected or dangerously overcrowded |
| Data unavailable | 5 pts | Neutral default |

*Capped at 10 pts total.*

---

## Total Score Summary

| Dimension | Max Points |
|-----------|-----------|
| C — Current Earnings | 15 |
| A — Annual Earnings | 15 |
| N — New High / Catalyst | 15 |
| S — Supply & Demand | 15 |
| L — Relative Strength | 15 |
| I — Institutional Sponsorship | 10 |
| M — Market Direction | 15 |
| **Total** | **100** |

---

## Data Flow

```
TradingView CSV Export
    │
    ▼
Dropzone Folder (/home/dietpi/.../tv_fileDrop)
    │
    ▼
csv_watchdog.py (Watchdog Listener)
    ├── Parses TradingView CSV
    ├── Converts M/B shorthands to mathematical numbers
    ├── Calculates Large/Mid/Small company_size
    ├── Merges with existing Supabase watchlist state (Upsert)
    └── Increments weeks_retained logic
            │
            ▼
        [Watchlist ready for technical screening and React UI]
```
