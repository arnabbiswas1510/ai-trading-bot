# Fundamental Screener

## What It Does — Simply

Every week, the bot runs a scan across **every US-listed stock** and asks: *"Which ones are actually growing fast?"*

It uses TradingView's screener API (the same engine behind the TradingView website) to instantly filter thousands of stocks down to only those that meet all five conditions at once. The survivors get written to a `watchlist` table. The technical screener then watches only those stocks each day for breakout signals.

Think of it as the **first filter** — not looking for breakouts yet, just looking for companies that are genuinely earning more money, quarter after quarter, with real trading volume behind them.

---

## Universe

**All US-listed stocks** — the entire American equity market, not just the S&P 500 or any index subset. The TradingView scanner scans common stocks, preferred stocks, and depositary receipts listed on US exchanges, and excludes ETFs, mutual funds, and pre-IPO shares.

---

## How It Works

**File:** `tv_api_screener.py` (runs weekly via GitHub Actions)

A single POST request is made to the TradingView scanner API:

```
POST https://scanner.tradingview.com/america/scan
```

TradingView evaluates the entire US market server-side and returns only stocks matching all filter conditions. No manual CSV export is needed — the scan is fully automated.

---

## Filters Applied (All Must Be True)

| Filter | Threshold | Why It Matters |
|--------|-----------|----------------|
| **Price** | > $10 | Avoids penny stocks with thin liquidity and manipulation risk |
| **Quarterly EPS growth (QoQ)** | > 20% | The company earned more this quarter than a year ago — earnings are accelerating right now |
| **Annual EPS growth (TTM YoY)** | > 20% | Growth has been sustained over the full year, not a one-time blip |
| **30-day average volume** | > 100,000 shares/day | Enough daily activity to enter and exit cleanly without moving the price |
| **Stock type** | Common or preferred only | Excludes ETFs, mutual funds, and pre-IPO shares |

Up to **2,000 results** are returned, sorted by market cap (largest first).

---

## What Gets Extracted

For each qualifying stock, the following fields are captured from TradingView:

| Field | TradingView Column | Description |
|-------|--------------------|-------------|
| `ticker` | `name` | Stock symbol (e.g. `AAPL`) |
| `company_name` | `description` | Full company name |
| `q_eps_growth` | `earnings_per_share_diluted_qoq_growth_fq` | Quarterly EPS growth (QoQ) |
| `a_eps_growth` | `earnings_per_share_diluted_yoy_growth_ttm` | Annual EPS growth (TTM YoY) |
| `revenue_growth` | `total_revenue_yoy_growth_ttm` | Revenue growth (TTM YoY) |
| `analyst_rating` | `Recommend.All` | TradingView analyst consensus (-1 to +1, mapped to text) |
| `float_shares` | `float_shares_outstanding` | Shares in float (proxy for supply/demand) |
| `roe` | `return_on_equity` | Return on equity |
| `company_size` | Derived from `market_cap_basic` | Large (>$10B), Mid ($2B–$10B), Small (<$2B) |
| `price` | `close` | Latest close price |

---

## Retention Logic

The screener does not just replace the watchlist from scratch each week. It preserves a **retention counter** for stocks that keep qualifying:

- **New stock** — enters with `retention_period = "1d"`
- **Returning stock** — `retention_period` is incremented (1d → 2d → 3d → ...)
- **Dropped stock** — removed from the watchlist entirely (full replace each run)

This means stocks that consistently qualify accumulate a longer retention period, giving the technical screener a signal of sustained fundamental strength.

---

## CANSLIM Scoring Engine (Dashboard Only)

The `backend/screener.py` module provides a deeper per-stock CANSLIM score (0–100 pts) for the dashboard UI. This scoring uses FMP financial data (income statements, balance sheets, historical prices) and is **separate from the pipeline** — it is not used to generate the watchlist or make buy decisions.

| Dimension | Max Points | Source |
|-----------|-----------|--------|
| C — Current Earnings | 15 | FMP quarterly income statements |
| A — Annual Earnings | 15 | FMP annual income + ROE |
| N — Near 52-Week High | 15 | FMP quote + historical prices |
| S — Supply & Demand | 15 | FMP 20-day OHLCV (accumulation/distribution days) |
| L — Relative Strength | 15 | Weighted 3/6/9/12-month performance vs. watchlist peers |
| I — Institutional Sponsorship | 10 | FMP institutional holdings % |
| M — Market Direction | 15 | SPY and Nasdaq vs. SMA-50/200 |
| **Total** | **100** | |

---

## Data Flow

```
GitHub Actions (weekly cron)
    |
    v
tv_api_screener.py
    |
    +-- POST scanner.tradingview.com/america/scan
    |       Filters: EPS > 20% QoQ & YoY, volume > 100K, price > $10
    |       Returns: up to 2,000 US stocks sorted by market cap
    |
    +-- Check existing watchlist → increment retention_period for returning stocks
    |
    +-- Truncate watchlist table
    |
    +-- Insert fresh records
    |
    v
watchlist table (Supabase)
    |
    v
technical_screener.py scans only these tickers daily
```
