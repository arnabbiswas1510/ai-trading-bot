# Fundamental Screener Logic

## Overview

The fundamental screening system has **two layers**:

1. **Pipeline Screener** (`fundamental_screener.py`) — runs on a schedule (weekly), scans the full S&P 500 + active stocks universe, and produces a top-90 watchlist stored in Supabase.
2. **CANSLIM Scoring Engine** (`backend/screener.py`) — scores individual tickers from the watchlist across all 7 CANSLIM dimensions using FMP financial data.

---

## Layer 1 — Pipeline Screener (`fundamental_screener.py`)

### Ticker Universe Construction

```
Primary source: FMP /stable/sp500-constituent  (full S&P 500, ~503 tickers)
Fallback:       FMP /stable/most-actives  +  FALLBACK_TICKERS hardcoded list
```

**Hardcoded fallback** (`FALLBACK_TICKERS`) contains ~120 pre-selected high-liquidity growth leaders (AAPL, MSFT, NVDA, AMZN, etc.) merged with the most-actives feed when the S&P 500 endpoint is restricted (HTTP 402/403).

### CANSLIM Threshold Screening (`analyze_canslim_fundamentals`)

For each ticker, two FMP endpoints are called **in parallel**:
- `GET /stable/financial-growth?symbol={ticker}&limit=4` → EPS growth data
- `GET /stable/quote?symbol={ticker}` → price/company name

**Pass criteria (all must be true):**

| Metric | Field | Threshold |
|--------|-------|-----------|
| Quarterly EPS Growth | `epsgrowth` (most recent quarter) | **> 18%** |
| Annual EPS Growth | `threeYNetIncomeGrowthPerShare` | **> 10%** |
| Institutional Sponsorship | Distinct institutional holder count (FMP `/api/v3/institutional-holder/{ticker}`) | **> 5** |

> **Graceful degradation:** If the FMP institutional-holder endpoint returns HTTP 402/403 (plan restriction),
> `inst_count` is set to `None` and the I-filter is **skipped** (the ticker can still pass on C + A alone).
> When skipped, `inst_count` is stored as `-1` in Supabase as a sentinel value.
> This is a conscious tradeoff: better to include borderline names than to silently grant a free pass to everything.

### Composite Score Formula

```python
composite_score = (q_eps_growth × 0.6) + (a_eps_growth × 0.4)
```

Weights quarterly (current) growth at 60% and annual growth at 40%, consistent with CANSLIM's emphasis on **current** earnings momentum.

### Ranking & Persistence

- Results sorted descending by `composite_score`
- Top **90** candidates written to Supabase `watchlist` table
- Entries older than **56 days (8 weeks)** are pruned on each run

**Supabase `watchlist` schema:**

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | text | Stock symbol |
| `company_name` | text | Company name from FMP quote |
| `composite_score` | float | Weighted EPS score |
| `q_eps_growth` | float | Current quarterly EPS growth (decimal) |
| `a_eps_growth` | float | 3-year annual EPS growth (decimal) |
| `revenue_growth` | float | Revenue YoY growth (decimal) |
| `inst_count` | int | Institutional holder count (currently hardcoded = 10) |
| `created_at` | timestamp | Auto-set by Supabase |

### Concurrency & Rate Limiting

- Semaphore: **10 concurrent requests** max (`asyncio.Semaphore(10)`)
- Retry logic: **3 attempts** with exponential backoff (1s → 2s → 4s)
- HTTP 429 (rate limit): auto-retries with backoff

---

## Layer 2 — Full CANSLIM Scoring Engine (`backend/screener.py`)

Scores each watchlist ticker against all 7 CANSLIM dimensions. Max total score = **100 points**.

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
Weekly Cron
    │
    ▼
fundamental_screener.py
    ├── Fetch S&P 500 tickers (FMP)
    ├── Parallel async scan: EPS growth + quote (max 10 concurrent)
    ├── Filter: q_eps > 18% AND a_eps > 10%
    ├── Score: composite = q_eps*0.6 + a_eps*0.4
    ├── Rank top 90
    └── Write to Supabase watchlist table
            │
            ▼
        [Watchlist ready for technical screening]
```
