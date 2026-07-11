# Improved Final Score — Design & Implementation Plan

## The Problem in One Equation

```
current: final_score = quality_score(vol, pivot, SMA) + AI_bonus(gpt-4o-mini)
```

This measures *breakout mechanics* well, but knows nothing about *stock character*
(liquidity, size, sentiment, market leadership). An 81A score is mechanically
correct — SGHC had a real volume surge near its high — but contextually wrong.

---

## Proposed: 5-Component Score (0–100 scale)

```
final_score = round(
    technical_score  * 0.30   +  # breakout quality (current quality_score)
    liquidity_score  * 0.25   +  # size, price, avg volume
    ai_score         * 0.25   +  # smarter AI prompt with richer context
    sentiment_score  * 0.10   +  # news sentiment from FMP
    rs_score         * 0.10      # relative strength vs S&P 500
)
```

All five components produce a 0–100 value. The final score is always 0–100,
replacing the current unbounded `quality + bonus` (which can exceed 100).

---

## Component 1 — Technical Score (30%) — `technical_screener.py`

**No change to the formula.** The existing `compute_quality_score()` is kept
as-is and becomes one input into the new weighted blend. Weight reduced from
100% to 30% because it is blind to stock character.

---

## Component 2 — Liquidity Score (25%) — `technical_screener.py`

New function `compute_liquidity_score()`. All data is **already computed**
in `check_technical_breakout()` — no extra API calls needed.

```python
def compute_liquidity_score(close_price: float, avg_volume_50: float,
                            company_size: str) -> int:
    """
    Penalises low-price, low-volume, small-cap stocks.
    Data sources: all already in the price history DataFrame + watchlist.
    """
    # Price tier: 0-40 points
    if   close_price >= 50:  price_pts = 40
    elif close_price >= 20:  price_pts = 30
    elif close_price >= 15:  price_pts = 20
    elif close_price >= 10:  price_pts = 10
    else:                    price_pts = 0   # <$10: very high gap/volatility risk

    # Average daily volume: 0-40 points
    # Institutional threshold: ~500K shares/day minimum
    if   avg_volume_50 >= 2_000_000: vol_pts = 40
    elif avg_volume_50 >= 1_000_000: vol_pts = 30
    elif avg_volume_50 >= 500_000:   vol_pts = 20
    elif avg_volume_50 >= 200_000:   vol_pts = 10
    else:                            vol_pts = 0

    # Company size from watchlist: 0-20 points
    size_pts = {"Large": 20, "Mid": 12, "Small": 4}.get(company_size, 8)

    return int(price_pts + vol_pts + size_pts)
```

**SGHC example:** ~$8 price → 0pts | ~200K avg vol → 10pts | Small → 4pts = **14/100**

This alone would dramatically reduce SGHC's final score even if technical + AI stay high.

> [!NOTE]
> `avg_volume_50` is already calculated in `check_technical_breakout()` (line 145).
> `company_size` is already in the `watchlist` table (fetched by `ai_evaluator.py`).
> The only wiring needed is passing `avg_volume_50` and `company_size` through.

---

## Component 3 — AI Score (25%) — `ai_evaluator.py`

### Current prompt weaknesses
- Gives gpt-4o-mini only: volume surge, pivot distance, EPS, revenue, ROE, size
- Does NOT tell it: stock price, avg volume, market cap in dollars, or news
- Asks for a 1-100 rating but the model has no idea the stock costs $5 vs $150

### Richer prompt inputs (all already available)
```
For each ticker, pass to the AI:
  - close_price (from daily_triggers)
  - avg_volume_50 (add to daily_triggers — already computed in screener)
  - company_size (from watchlist — already fetched)
  - q_eps_growth, a_eps_growth, revenue_growth, roe (from watchlist — already fetched)
  - analyst_rating (from watchlist — already fetched)
  - recent_news_headlines (NEW — from FMP, see Component 4)
  - sentiment_score (NEW — Component 4 output)
  - rs_vs_spy_12w (NEW — Component 5 output)
```

### Revised prompt instruction
```
You are an expert CANSLIM trader. Rate each stock 1-100 for probability of
hitting +25% before -7% stop loss.

CRITICAL PENALTIES you must apply:
- Stocks priced under $15: heavily penalise (lack institutional interest, gap risk)
- Average daily volume under 500K shares: penalise (poor liquidity, wide spreads)
- Small-cap stocks: penalise (no institutional sponsorship)
- Negative or declining sentiment: penalise
- RS vs SPY below 0 (stock lagging market): penalise

These are not optional — they are hard rules. An 80-quality breakout on a
$5 small-cap with 200K avg volume should score no higher than 40.
```

---

## Component 4 — Sentiment Score (10%) — `ai_evaluator.py`

### Data source: FMP `/v3/stock_news`
Already have FMP API key. Endpoint is available on all FMP tiers.

```python
def fetch_news_headlines(ticker: str, limit: int = 8) -> list[str]:
    url = (f"https://financialmodelingprep.com/api/v3/stock_news"
           f"?tickers={ticker}&limit={limit}&apikey={FMP_API_KEY}")
    resp = requests.get(url, timeout=8)
    if resp.status_code != 200:
        return []
    return [item.get("title", "") for item in resp.json()]
```

### Sentiment rating
Include headlines in the AI prompt and ask it to rate sentiment separately:
```json
{
  "NVDA": 88,
  "SGHC": 31,
  "NVDA_sentiment": 75,
  "SGHC_sentiment": 20
}
```
Sentiment 0-100 → maps to 0-100 for this component.

**Cost:** 8 FMP calls per screener run (one per breakout stock). Well within limits.

---

## Component 5 — Relative Strength vs S&P 500 (10%) — `technical_screener.py`

### What it measures
A stock that is outperforming the S&P over the last 12 weeks has the
institutional buying that CANSLIM demands. A stock lagging the market —
even with a volume surge — is fighting the tape.

### Formula
```python
def compute_rs_score(stock_12w_return: float, spy_12w_return: float) -> int:
    """
    RS Score 0–100 based on excess return vs SPY over 12 weeks.
    Excess > +10%: 100 | Excess 0-10%: 50-100 | Excess < 0: 0-50 | Excess < -10%: 0
    """
    excess = stock_12w_return - spy_12w_return
    if   excess >= 10:  return 100
    elif excess >= 0:   return int(50 + excess * 5)
    elif excess >= -10: return int(50 + excess * 5)  # 0-50 range
    else:               return 0
```

### Data source
SPY's 12-week return is one additional FMP call per screener run (not per ticker).
The stock's 12-week return is computable from the price history already fetched.
**Cost: 1 extra FMP call total.**

---

## Database Changes

### `daily_triggers` table — add columns
```sql
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS avg_volume_50    BIGINT;
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS liquidity_score  INT;
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS sentiment_score  INT;
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS rs_score         INT;
ALTER TABLE daily_triggers ADD COLUMN IF NOT EXISTS technical_score  INT;  -- rename of quality_score
```

> [!NOTE]
> `quality_score` is kept for backwards compatibility. The new components are
> additive columns. No existing data is lost.

---

## File-by-File Changes

### `technical_screener.py`
- Add `compute_liquidity_score(close_price, avg_volume_50, company_size)`
- Add `compute_rs_score(stock_12w_return, spy_12w_return)`
- Fetch SPY 12-week price (1 extra FMP call)
- Pass `avg_volume_50` and `company_size` into the trigger record
- Write `liquidity_score`, `rs_score`, `avg_volume_50` to `daily_triggers`

### `ai_evaluator.py`
- Enrich prompt with `close_price`, `avg_volume_50`, `company_size`
- Add `fetch_news_headlines(ticker)` → pass to prompt
- Ask AI for both a rating AND a sentiment score per ticker
- Compute weighted `final_score` using all 5 components
- Write `sentiment_score` and new `final_score` to `daily_triggers`

### `watchlist` (Supabase) — no change needed
`company_size` already there. Already fetched by `ai_evaluator.py`.

---

## Impact Simulation: SGHC vs NVDA

| Component | Weight | SGHC (est.) | NVDA (est.) |
|---|---|---|---|
| Technical score | 30% | 66 | 72 |
| Liquidity score | 25% | **14** | **95** |
| AI score | 25% | **35** | 82 |
| Sentiment score | 10% | 40 | 78 |
| RS vs SPY | 10% | 30 | 85 |
| **Final score** | | **~44** | **~80** |

Current system gives both 78–81. New system correctly separates them.

---

## Implementation Order

1. **`technical_screener.py`** — add liquidity + RS score, pass `avg_volume_50` forward
2. **Supabase migration** — add new columns to `daily_triggers`
3. **`ai_evaluator.py`** — richer prompt + sentiment + new weighted formula
4. **Tests** — extend `test_buy_gates.py` with liquidity score unit tests
5. **One screener dry-run** — verify scores look reasonable before live trading

**Total estimated effort:** 2-3 hours of implementation.
No external API costs beyond the FMP calls already made.
No changes to `execution_agent.py` at this stage.
