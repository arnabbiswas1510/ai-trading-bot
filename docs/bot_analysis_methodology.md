# Bot Analysis Methodology

> **Generated:** 2026-07-11
> **Scope:** Fundamental pipeline (evening) + Technical pipeline (intraday/EOD)
> **Source files:** `tv_api_screener.py`, `technical_screener.py`, `ai_evaluator.py`, `scoring.py`

---

## Overview — Two-Stage Pipeline

```
Evening (GitHub Actions cron)
  Stage 1 -- TradingView Fundamental Screener --? watchlist (Supabase)
  Stage 2 -- FMP Technical Screener             --? daily_triggers (Supabase)
  Stage 3 -- AI Evaluator (GPT-4o-mini)         --? final_score + rationale

Market Open (execution_agent.py)
  Stage 4 -- Buy logic                          --? IBKR order
```

---

## Part 1 — Fundamental Analysis

### 1.1 What Happens Every Evening

The `tv_api_screener.py` script runs as a **GitHub Actions scheduled job** each evening. It calls the TradingView Scanner REST API directly, parses the response, and overwrites the `watchlist` table in Supabase with freshly qualifying stocks.

This watchlist is the **first and only gate** into the system. Any stock that fails here is invisible to all downstream stages — the technical screener, AI evaluator, and execution agent never see it.

---

### 1.2 TradingView Scanner API Call

#### Endpoint

```
POST https://scanner.tradingview.com/america/scan?label-product=screener-stock
Content-Type: text/plain;charset=UTF-8
Origin: https://www.tradingview.com
```

> **Note:** This is TradingView's internal scanner endpoint (the same one that powers the TradingView Stock Screener UI). It is called with a structured JSON body — there is no browser-accessible URL that encodes all these filters. The equivalent manual screener you could open in TradingView is linked below.

#### Equivalent TradingView Screener URL (manual verification)

```
https://www.tradingview.com/screener/?markets=america
  &filter=eps_diluted_growth_yoy_ttm:greater:20
  &filter=eps_diluted_growth_qoq:greater:20
  &filter=average_volume_30d:greater:100000
  &filter=close:egreater:10
  &filter=is_primary:equal:true
  &sort=market_cap_basic:desc
  &type=stock
```

> **Important:** The actual API body (below) is the canonical definition — the URL above is a close approximation for manual browsing. The API includes additional `filter2` rules (excludes ETFs, mutual funds, pre-IPO securities) that the URL cannot fully encode.

#### Full Request Payload (exact)

```json
{
  "columns": [
    "name",
    "description",
    "earnings_per_share_diluted_qoq_growth_fq",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "total_revenue_yoy_growth_ttm",
    "Recommend.All",
    "float_shares_outstanding",
    "return_on_equity",
    "market_cap_basic",
    "close",
    "volume"
  ],
  "filter": [
    { "left": "close",                                    "operation": "egreater", "right": 10     },
    { "left": "earnings_per_share_diluted_yoy_growth_ttm","operation": "greater",  "right": 20     },
    { "left": "earnings_per_share_diluted_qoq_growth_fq", "operation": "greater",  "right": 20     },
    { "left": "average_volume_30d_calc",                  "operation": "greater",  "right": 100000 },
    { "left": "is_primary",                               "operation": "equal",    "right": true   }
  ],
  "range": [0, 2000],
  "sort": { "sortBy": "market_cap_basic", "sortOrder": "desc" },
  "markets": ["america"],
  "filter2": {
    "operator": "and",
    "operands": [
      {
        "operation": {
          "operator": "or",
          "operands": [
            { "operation": { "operator": "and", "operands": [
                { "expression": { "left": "type",      "operation": "equal", "right": "stock" } },
                { "expression": { "left": "typespecs", "operation": "has",   "right": ["common"] } }
            ]}},
            { "operation": { "operator": "and", "operands": [
                { "expression": { "left": "type",      "operation": "equal", "right": "stock" } },
                { "expression": { "left": "typespecs", "operation": "has",   "right": ["preferred"] } }
            ]}},
            { "operation": { "operator": "and", "operands": [
                { "expression": { "left": "type", "operation": "equal", "right": "dr" } }
            ]}},
            { "operation": { "operator": "and", "operands": [
                { "expression": { "left": "type",      "operation": "equal", "right": "fund" } },
                { "expression": { "left": "typespecs", "operation": "has_none_of", "right": ["etf", "mutual"] } }
            ]}}
          ]
        }
      },
      { "expression": { "left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo"] } }
    ]
  }
}
```

---

### 1.3 Fundamental Filter Thresholds

| Filter | Condition | Rationale |
|---|---|---|
| **Price** | `close >= $10` | Eliminates penny stocks with gap risk |
| **Annual EPS Growth (TTM)** | `YoY EPS growth > 20%` | CANSLIM "E" — earnings acceleration |
| **Quarterly EPS Growth** | `QoQ EPS growth > 20%` | CANSLIM "C" — recent acceleration |
| **Average 30-day Volume** | `avg volume > 100,000` | Minimum liquidity floor |
| **Primary listing** | `is_primary = true` | Excludes ADRs listed under duplicate tickers |
| **Security type** | Common stock, preferred, DR, or closed-end fund | Excludes ETFs, mutual funds, pre-IPO |

> **Warning — Known dead zone.** The 100K volume floor and the $10 price floor are misaligned with the downstream technical system (see `fundamental_filter_alignment_plan.md`). Stocks in the 100K–500K volume band and $10–$15 price band pass this gate but score near zero on liquidity in Stage 3. The plan proposes raising volume to 250K and price to $15.

---

### 1.4 What the Watchlist Stores

| Field | Source TV Column | Description |
|---|---|---|
| `ticker` | `name` | Symbol (e.g. NVDA) |
| `company_name` | `description` | Company display name |
| `q_eps_growth` | `earnings_per_share_diluted_qoq_growth_fq` | Latest quarterly EPS growth % |
| `a_eps_growth` | `earnings_per_share_diluted_yoy_growth_ttm` | Annual TTM EPS growth % |
| `revenue_growth` | `total_revenue_yoy_growth_ttm` | Annual revenue growth % |
| `analyst_rating` | `Recommend.All` (-1 to 1) | Mapped: Strong Sell / Sell / Neutral / Buy / Strong Buy |
| `float_shares` | `float_shares_outstanding` | Shares in float |
| `roe` | `return_on_equity` | Return on equity % |
| `company_size` | Derived from `market_cap_basic` | Large (>=10B), Mid (>=2B), Small (<2B) |
| `price` | `close` | Last close price |
| `tv_exchange` | Prefix of TV symbol | NASDAQ, NYSE, etc. |
| `retention_period` | Incremented daily | How many consecutive days on watchlist |

---

## Part 2 — Technical Analysis

### 2.1 What Happens After the Watchlist Is Built

`technical_screener.py` iterates every ticker in the watchlist and calls the **FMP historical price API** to fetch up to 380 days of OHLCV data per ticker. It runs three indicator conditions and, on a pass, computes a rich set of metrics.

---

### 2.2 Breakout Detection — Three Hard Gates (all must pass)

| Gate | Condition | Live Setting | Env var |
|---|---|---|---|
| **1. Above 50-day SMA** | `close > SMA(50)` | Required (no override) | `SMA_WINDOW=50` |
| **2. Volume surge** | `today_volume / avg_volume(50) >= 1.40x` | >= 1.40x | `VOLUME_SURGE_MIN=1.40` |
| **3. Near 52-week high** | `close >= rolling_high(252) x 0.98` | Within 2% of high | `PIVOT_PROXIMITY=0.98` |

**FMP API called per ticker:**
```
GET https://financialmodelingprep.com/stable/historical-price-eod/full
    ?symbol={TICKER}&from={today-380d}&to={today}&apikey={FMP_API_KEY}
```

---

### 2.3 Technical (Quality) Score — 0 to 100

Computed only when all 3 gates pass:

| Component | Max pts | Formula |
|---|---|---|
| **Volume surge** | 40 | `min(surge_ratio / 3.0, 1.0) x 40` — 3x average = full marks |
| **Pivot proximity** | 40 | `max(0, 1 + dist_from_high_pct / 5) x 40` — at pivot = 40, -5% = 0 |
| **SMA margin** | 20 | `min(max((close - SMA50) / SMA50 x 100 / 10, 0), 1) x 20` — capped at 10% above |

```
quality_score = (vol_norm x 40) + (prox_norm x 40) + (sma_norm x 20)
```

---

### 2.4 Relative Strength Score — 0 to 100

Compares the stock's 12-week return vs SPY's 12-week return (fetched once per run, reused for all tickers).

| Excess return vs SPY | RS Score |
|---|---|
| >= +10% | 100 |
| 0% to +10% | 50–100 (linear) |
| -10% to 0% | 0–50 (linear) |
| <= -10% | 0 |

---

### 2.5 ATR-14 Swing Velocity

Computed from existing OHLCV data (no extra API calls):

- **True Range** = `max(High-Low, |High-PrevClose|, |Low-PrevClose|)`
- **ATR-14** = 14-day rolling average of True Range
- **ATR%** = `(ATR-14 / current_close) x 100`
- **Est. days to +25% target** = `round(25 / ATR%)`

| Classification | Condition |
|---|---|
| Fast mover | Est. days <= 15 (ATR >= 1.67%/day) |
| Swing-compatible | Est. days <= 30 |
| Slow mover | Est. days <= 60 |
| Long-term only | Est. days > 60 (ATR < 0.42%/day) |

---

### 2.6 Data Written to `daily_triggers`

```
ticker, close_price, volume_surge, sma_50, rolling_high_52w,
pivot_distance_pct, quality_score, technical_score,
avg_volume_50, rs_score, atr_pct, est_days_to_target, triggered_at
```

---

## Part 3 — AI Evaluation (GPT-4o-mini)

### 3.1 Input Context Per Ticker

For each breakout in `daily_triggers` the AI receives:

```
Price, AvgDailyVol, CompanySize, RS_vs_SPY
VolSurge, DistFromPivot
ATR%/day, EstDaysTo25% [swing label]
Q-EPS%, A-EPS%, RevGrowth%, ROE%
Analyst consensus
Up to 5 recent news headlines (FMP /api/v3/stock_news)
Last 30 closed trades with % return + sell reason
```

### 3.2 AI Scoring Rules

The AI is instructed to think as a 2–6 week swing trader targeting +25% before a -7% stop:

| Rule | Threshold | Effect |
|---|---|---|
| ATR >= 1.7%/day (<=15 days to target) | Ideal | Boost +10–15 pts |
| ATR 0.8–1.7%/day (16–30 days) | Acceptable | No penalty |
| ATR 0.4–0.8%/day (31–60 days) | Marginal | -15 pts |
| ATR < 0.4%/day (>60 days) | Not a swing trade | Cap at 35 |
| Price < $15 | Gap risk | Cap at 45 |
| Avg volume < 500K | Illiquid | -20 pts minimum |
| Small-cap | Institutional avoidance | -15 pts minimum |
| RS < 50 (lagging SPY) | Fighting the tape | -10 to -20 pts |
| Near-term catalyst <= 3 weeks | Positive | +10 pts |

The AI returns: `rating (1–100)`, `sentiment (1–100)`, and a 2–3 sentence `rationale` from a swing trader's perspective.

---

## Part 4 — Final Score Formula

### 4.1 Five-Component Weighted Blend

```
final_score = (Technical x 0.30)
            + (Liquidity x 0.25)
            + (AI        x 0.25)
            + (Sentiment x 0.10)
            + (RS        x 0.10)
```

### 4.2 Liquidity Score (0–100)

| Sub-component | Max pts | Tiers |
|---|---|---|
| **Price** | 40 | <$10=0, $10-15=10, $15-20=20, $20-50=30, >=$50=40 |
| **Avg daily volume** | 40 | <200K=0, 200K-500K=10, 500K-1M=20, 1M-2M=30, >=2M=40 |
| **Company size** | 20 | Small=4, Mid=12, Large=20, unknown=8 |

### 4.3 Letter Grades

| Final Score | Grade | Execution |
|---|---|---|
| >= 70 | A | Buy candidate |
| 50–69 | B | Buy candidate |
| 30–49 | C | Marginal |
| < 30 | D | Vetoed — skipped by execution agent |

> **Important:** The AI `rating` (not `final_score`) drives the D-veto. If `ai_rating < 30`, the execution agent skips the trigger regardless of final_score.

---

## Part 5 — End-to-End Pipeline Timeline

```
18:00 ET  tv_api_screener.py
          POST https://scanner.tradingview.com/america/scan
          Filters: EPS YoY >20%, EPS QoQ >20%, Avg Vol >100K, Price >=$10
          Result: overwrites Supabase watchlist (~200-500 stocks)

18:30 ET  technical_screener.py
          For each watchlist ticker:
            GET FMP historical-price-eod (380 days)
            Gate 1: close > SMA(50)
            Gate 2: volume >= 1.40x avg_vol(50)
            Gate 3: close >= 52w_high x 0.98
            If all pass: compute quality_score, rs_score, ATR%
          Result: writes to Supabase daily_triggers

19:00 ET  ai_evaluator.py
          For each daily_trigger:
            Fetch watchlist fundamentals + FMP news headlines
            Call GPT-4o-mini: rating, sentiment, rationale
            Compute: liquidity_score, final_score
            Update daily_triggers with all scores + rationale
          Result: Telegram notification sent to chat

09:30 ET  execution_agent.py  (next market open)
          Read daily_triggers (lookback: 3 days)
          Skip: ai_rating < 30 (D-veto)
          Skip: price extended >5% from pivot
          Buy: highest final_score not already held
          Result: IBKR market order placed
```

---

## Configuration Reference

| Parameter | Live Value | Source |
|---|---|---|
| Fundamental: Price floor | >= $10 | `tv_api_screener.py` hardcoded |
| Fundamental: Annual EPS growth | > 20% | `tv_api_screener.py` hardcoded |
| Fundamental: Quarterly EPS growth | > 20% | `tv_api_screener.py` hardcoded |
| Fundamental: Avg volume floor | > 100,000 | `tv_api_screener.py` hardcoded |
| Technical: SMA window | 50 days | `.env` `SMA_WINDOW=50` |
| Technical: Volume surge min | 1.40x | `.env` `VOLUME_SURGE_MIN=1.40` |
| Technical: Rolling high window | 252 days | `.env` `ROLLING_HIGH_WINDOW=252` |
| Technical: Pivot proximity | 0.98 (within 2%) | `.env` `PIVOT_PROXIMITY=0.98` |
| Technical: Min price history | 50 days | code default `MIN_PRICE_HISTORY=50` |
| Execution: Max positions | 4 | `.env` `MAX_POSITIONS=4` |
| Execution: Stop loss | 7% | `.env` `STOP_LOSS_PCT=0.07` |
| Execution: Plateau exit | 10 days | `.env` `PLATEAU_DAYS=10` |
| Execution: Cooling off | 3 days | `.env` `COOLING_OFF_DAYS=3` |
| Execution: Trigger lookback | 3 days | `.env` `TRIGGER_LOOKBACK_DAYS=3` |
| Execution: Max pivot extension | 5% | `.env` `MAX_PIVOT_EXTENSION=0.05` |
