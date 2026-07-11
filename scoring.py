"""
scoring.py — Pure scoring functions for the 5-component final_score system.

No external dependencies (no OpenAI, no Supabase, no requests).
Importable in tests without any environment variables or packages installed.

Imported by:
  - technical_screener.py (compute_rs_score)
  - ai_evaluator.py (compute_liquidity_score, compute_final_score)
  - tests/test_score_components.py
"""


def compute_liquidity_score(close_price: float, avg_volume_50: int,
                             company_size: str) -> int:
    """
    Penalises low-price, low-volume, and small-cap stocks (0-100).

    Price tier     (0-40 pts): <$10=0, $10-15=10, $15-20=20, $20-50=30, >=50=40
    Avg daily vol  (0-40 pts): <200K=0, 200K-500K=10, 500K-1M=20, 1M-2M=30, >=2M=40
    Company size   (0-20 pts): Small=4, Mid=12, Large=20, unknown=8

    SGHC ($8, 180K vol, Small)  -> 0 + 0 + 4  = 4
    NVDA ($750, 42M vol, Large) -> 40 + 40 + 20 = 100
    """
    # Price component
    if close_price >= 50:
        price_pts = 40
    elif close_price >= 20:
        price_pts = 30
    elif close_price >= 15:
        price_pts = 20
    elif close_price >= 10:
        price_pts = 10
    else:
        price_pts = 0

    # Average daily volume component
    if avg_volume_50 >= 2_000_000:
        vol_pts = 40
    elif avg_volume_50 >= 1_000_000:
        vol_pts = 30
    elif avg_volume_50 >= 500_000:
        vol_pts = 20
    elif avg_volume_50 >= 200_000:
        vol_pts = 10
    else:
        vol_pts = 0

    # Company size component
    size_pts = {"Large": 20, "Mid": 12, "Small": 4}.get(company_size or "", 8)

    return int(price_pts + vol_pts + size_pts)


def compute_rs_score(stock_12w_return: float, spy_12w_return: float) -> int:
    """
    Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.

    Excess return vs SPY:
      >= +10%  -> 100  (strong outperformer — institutional following)
       0 to 10% -> 50-100 (linear)
     -10 to 0%  -> 0-50  (linear)
      <= -10%  -> 0    (lagging the market — avoid)
    """
    excess = stock_12w_return - spy_12w_return
    if excess >= 10:
        return 100
    elif excess >= 0:
        return int(50 + excess * 5)
    elif excess >= -10:
        return max(0, int(50 + excess * 5))
    else:
        return 0


def compute_final_score(technical_score: int, liquidity_score: int,
                         ai_score: int, sentiment_score: int,
                         rs_score: int) -> int:
    """
    Weighted blend of 5 components (all 0-100) -> 0-100 final score.

      Technical  30% -- breakout mechanics (volume surge, pivot proximity, SMA)
      Liquidity  25% -- stock price, avg daily volume, company size
      AI         25% -- fundamental quality rated by GPT-4o-mini with full context
      Sentiment  10% -- recent news headline tone (FMP stock_news)
      RS vs SPY  10% -- 12-week relative strength vs S&P 500
    """
    raw = (
        technical_score  * 0.30 +
        liquidity_score  * 0.25 +
        ai_score         * 0.25 +
        sentiment_score  * 0.10 +
        rs_score         * 0.10
    )
    return int(round(min(max(raw, 0), 100)))
