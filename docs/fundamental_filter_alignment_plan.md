# Fundamental Filter Alignment Plan

## Problem Statement

The `tv_api_screener.py` fundamental filter is the **first and only gate** into the
watchlist. Any stock that fails it is permanently invisible to the technical screener,
AI evaluator, and execution agent — no matter how strong its technical setup is.

The current filter thresholds are inconsistent with the downstream technical rating
system, creating a "dead zone" of stocks that either:
- Pass the fundamental gate but are already disqualified by the technical system, OR
- Would have been excellent technical buys but were eliminated at the first gate

---

## Current Filter Audit

```python
# Current filter in tv_api_screener.py
{"left": "close",                                  "operation": "egreater", "right": 10},
{"left": "earnings_per_share_diluted_yoy_growth_ttm","operation": "greater",  "right": 20},
{"left": "earnings_per_share_diluted_qoq_growth_fq", "operation": "greater",  "right": 20},
{"left": "average_volume_30d_calc",                "operation": "greater",  "right": 100000},
{"left": "is_primary",                             "operation": "equal",    "right": True}
```

---

## Issues Found

### Issue 1 — Volume Dead Zone (Highest Priority)

| Layer | Threshold | Effect |
|---|---|---|
| Fundamental gate | avg volume > **100K** | Stock enters watchlist |
| Technical liquidity score | avg volume < **200K** | 0/40 volume points |
| Technical liquidity score | avg volume < **500K** | 10/40 volume points |
| AI rating penalty | avg volume < **500K** | Rating reduced >=20 pts |

A stock with 150K daily volume passes the fundamental gate, consumes a watchlist slot,
runs an FMP API call during the technical screener, and still scores near-zero on
liquidity — it will never be bought. The 100K-500K band is wasted computation.

**Fix:** Raise volume floor from 100K to **250K**.
This still admits stocks below the 500K ideal (for discovery), but eliminates
the true dead-zone stocks that have no chance of passing the technical system.

---

### Issue 2 — Price Threshold Mismatch

| Layer | Threshold | Effect |
|---|---|---|
| Fundamental gate | price > **$10** | Stock enters watchlist |
| Technical liquidity score | price < **$10** | 0/40 price points |
| Technical liquidity score | price **$10-$15** | 10/40 price points |
| AI rating penalty | price < **$15** | Rating capped at 45 |

Stocks between $10-$15 pass the fundamental gate but the AI immediately caps
their rating at 45 — they effectively cannot receive an A or B grade.
SGHC is the real-world example of this exact pattern.

**Fix:** Raise price floor from $10 to **$15**.
This aligns the fundamental gate with the AI rating system's penalty boundary.

---

### Issue 3 — No Market Cap Floor

The screener *sorts* by market cap descending but applies **no minimum**.
A micro-cap stock with $50M market cap and >20% EPS growth passes the filter.
The technical rating will penalise it via the "Small" company_size penalty (4/20 pts)
but it still consumes a watchlist slot and an FMP API call.

**Fix:** Add a minimum market cap of **$300M**.

Rationale:
- $300M is the institutional micro-cap boundary
- Small-cap in the scoring system starts at $2B, but $300M eliminates the true micro-caps
  that have zero institutional following and are manipulable
- Still admits many legitimate small-cap growth stocks that can outperform

---

### Issue 4 — Annual EPS Threshold May Miss Momentum Breakouts

**Current:** Annual YoY EPS growth > **20%** (hard cutoff)

Three categories of potential breakout stocks are excluded:
1. **One bad quarter** — Stock with 3 years of 25%+ growth has one soft quarter (18% YoY).
   It is completely excluded even if the quarterly trend is recovering.
2. **Newly profitable** — Company that turned profitable recently (e.g., EPS -$0.02 to $0.05).
   TradingView may return NULL or compute this as N/A, failing the filter.
   These are often the biggest early-stage breakout movers.
3. **15-20% growers** — Legitimate CANSLIM candidates slightly below the 20% threshold.

**Fix:** Relax annual EPS threshold from 20% to **15%**.
Keep quarterly EPS at 20% — QoQ acceleration is the more predictive CANSLIM signal
and is harder to game with a single data point.

---

### Issue 5 — Revenue Growth Not Required

The screener **fetches** `revenue_growth` but does not **filter** on it.
A company with declining revenue but rising EPS (via layoffs or cost-cutting) passes.
This is fundamentally weaker than a growth stock and does not fit the CANSLIM model.

**Fix:** Add `revenue_growth > 0%` as a filter.
This requires at least positive revenue growth — eliminates cost-cutting EPS games
without being so strict that growth-stage companies reinvesting into expansion are excluded.

---

## Proposed Filter (Final)

```python
"filter": [
    # Raised from $10 — aligns with AI penalty boundary of $15
    {"left": "close", "operation": "egreater", "right": 15},

    # Relaxed from 20% to 15% to capture momentum stocks near threshold
    # Annual is secondary to quarterly in CANSLIM predictiveness
    {"left": "earnings_per_share_diluted_yoy_growth_ttm", "operation": "greater", "right": 15},

    # Kept at 20% — quarterly acceleration is the strongest CANSLIM signal
    {"left": "earnings_per_share_diluted_qoq_growth_fq", "operation": "greater", "right": 20},

    # Raised from 100K to 250K to eliminate dead-zone stocks that can't be bought
    {"left": "average_volume_30d_calc", "operation": "greater", "right": 250000},

    # NEW — minimum $300M market cap to exclude institutional-free micro-caps
    {"left": "market_cap_basic", "operation": "greater", "right": 300000000},

    # NEW — revenue must be growing, not just EPS via cost-cutting
    {"left": "total_revenue_yoy_growth_ttm", "operation": "greater", "right": 0},

    {"left": "is_primary", "operation": "equal", "right": True}
]
```

---

## Expected Impact

| Change | Stocks lost | Stocks gained |
|---|---|---|
| Price $10 to $15 | SGHC-like sub-$15 stocks | Cleaner list, no more capped-at-45 admits |
| Volume 100K to 250K | Thinly traded micro-stocks | Fewer wasted API calls |
| Market cap $300M floor | Pure micro-caps | Nothing lost that could realistically be bought |
| EPS annual 20% to 15% | Nothing lost | 15-20% growers, recently-turned-profitable stocks |
| Revenue growth > 0% | Cost-cutting EPS games | Stronger fundamental quality |

**Net effect:** Smaller but higher-quality watchlist. The technical screener and AI
evaluator see fewer candidates but each candidate has a realistic chance of scoring
well across all 5 components of the final score.

---

## What This Does NOT Change

- The quarterly EPS requirement (kept at 20%) — strongest signal, no change
- The technical screener logic (`technical_screener.py`) — unchanged
- The 5-component scoring formula — unchanged
- The execution agent — unchanged
- The Telegram notifications — unchanged

Only the `filter` array in `tv_api_screener.py` is modified.

---

## Risk

> [!WARNING]
> The TradingView scanner is queried once per day (via GitHub Actions). After deploying
> this change, the first screener run will regenerate the watchlist from scratch.
> Tickers currently on the watchlist that do not meet the new thresholds will be dropped.
> This is intentional and safe — the execution agent only buys from `daily_triggers`,
> not from the watchlist directly. Any open positions are unaffected.

---

## Implementation Order

1. Update the `filter` array in `tv_api_screener.py` (5 lines changed, 2 lines added)
2. Run the screener manually once to verify the new watchlist size is reasonable
3. Monitor for one week — confirm previously missed momentum stocks appear

**Estimated effort:** 15 minutes of code change, one manual screener run to verify.
