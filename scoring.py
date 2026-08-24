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


# ── Volatility fit against the real exit ladder ──────────────────────────────
# The bot has NO profit target. It arms a profit lock at +5% and then trails
# 1.5% below the high-water mark, and its entry stop is 2.5 x ATR CLAMPED to a
# 10%-12% band. Two consequences drive everything below:
#
#   1. The threshold that matters is +5%, not +25%. `5 / atr_pct` is under 7
#      sessions for any candidate above ~0.7%/day ATR, so "days to the lock"
#      is non-binding for almost every name the screener surfaces. Velocity is
#      therefore NOT a differentiator and must not be scored as one.
#   2. Because the stop CAPS at 12%, room measured in the stock's own daily
#      range SHRINKS as ATR rises. Past ~4.8%/day a position holds under 2.5
#      ATR of room and is routinely gapped out inside 1-2 sessions.
#
# See decisions/2026-08-24_ai-evaluator-volatility-fit.md for the measurement.
ATR_STOP_CAP_PCT   = 4.8   # above this, 2.5 x ATR exceeds the 12% stop clamp
ATR_COMFORT_LOW    = 1.5   # below this, the +5% lock may not arrive before day 7
PROFIT_LOCK_PCT    = 5.0   # the threshold est_days_to_target measures


def est_days_to_lock(atr_pct):
    """Trading days to reach the +5% profit lock at the average ATR pace.

    Returns the 999 sentinel ("not reachable in a swing window") when ATR is
    unknown or non-positive, matching the screener's default.
    """
    try:
        atr = float(atr_pct)
    except (TypeError, ValueError):
        return 999
    if atr <= 0:
        return 999
    return int(round(PROFIT_LOCK_PCT / atr))


def volatility_fit(atr_pct):
    """Classify a candidate's ATR against the stop ladder it will actually trade.

    Returns (emoji, label, tone) where tone is one of 'good' | 'warn' | 'bad' |
    'unknown'. Single source of truth for the screener, the AI prompt and the
    Telegram digest; frontend/src/lib/volatilityFit.js mirrors it for the UI.
    """
    try:
        atr = float(atr_pct or 0)
    except (TypeError, ValueError):
        atr = 0.0
    if atr <= 0:
        return ("\u2753", "Unknown volatility", "unknown")
    if atr > ATR_STOP_CAP_PCT:
        return ("\u26a0\ufe0f", f"Too volatile \u2014 under 2.5 ATR of room inside the 12% stop cap", "bad")
    if atr >= ATR_COMFORT_LOW:
        return ("\u2705", "Good volatility fit", "good")
    return ("\u26a0\ufe0f", "Quiet \u2014 may not reach the +5% lock before day-7 rotation", "warn")
