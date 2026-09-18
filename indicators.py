"""Price-series indicators and the Momentum Health Score.

Extracted verbatim from execution_agent.py on 2026-09-18. Pure functions: they
take price/volume series and return numbers. No brokerage, database or network
access, which is what makes them cheap to test and safe to reason about in
isolation.

See decisions/2026-09-18_execution-agent-split.md.
"""

import os

# ── Momentum Health Score (Mₜ) — live conviction for held positions ────────────
# Computed EOD from live RS, volume ratio, and real sentiment (FMP news + GPT).
# Weights: RS decay 40%, Volume ratio 35%, Sentiment 25%.
# Used by Rank & Replace (Day 7+) to compare trigger vs held position quality.
MOMENTUM_HEALTH_RS_WEIGHT   = float(os.getenv("MOMENTUM_HEALTH_RS_WEIGHT",   0.40))
MOMENTUM_HEALTH_VOL_WEIGHT  = float(os.getenv("MOMENTUM_HEALTH_VOL_WEIGHT",  0.35))
MOMENTUM_HEALTH_SENT_WEIGHT = float(os.getenv("MOMENTUM_HEALTH_SENT_WEIGHT", 0.25))

def calculate_sma(closes: list, window: int) -> float | None:
    """Compute Simple Moving Average."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window

def calculate_ema(closes: list, window: int) -> float | None:
    """Compute Exponential Moving Average."""
    if len(closes) < window:
        return None
    alpha = 2 / (window + 1)
    # Start with SMA of the first 'window' closes
    ema = sum(closes[:window]) / window
    # Apply recursive EMA formula to subsequent closes
    for price in closes[window:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return ema

def compute_rsi(closes: list, period: int = 14) -> list:
    """Wilder's smoothed RSI from a list of closing prices.

    Returns a list of RSI values the same length as closes (first `period`
    values are None — insufficient history). Uses Wilder's exponential
    smoothing (alpha = 1/period), consistent with TradingView / standard
    charting platforms.

    Pure function — no side effects, no I/O.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    rsi = [None] * period  # first `period` values have no RSI

    # ── Seed: simple average of first `period` gains/losses ──────────────────
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avgs(ag, al):
        if al == 0:
            return 100.0
        return round(100.0 - (100.0 / (1.0 + ag / al)), 2)

    rsi.append(_rsi_from_avgs(avg_gain, avg_loss))

    # ── Wilder's smoothing for remaining bars ─────────────────────────────────
    alpha = 1.0 / period
    for i in range(period + 1, len(closes)):
        delta    = closes[i] - closes[i - 1]
        g        = max(delta, 0.0)
        l        = max(-delta, 0.0)
        avg_gain = avg_gain * (1 - alpha) + g * alpha
        avg_loss = avg_loss * (1 - alpha) + l * alpha
        rsi.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi

def detect_candlestick_reversals(ohlcv: list, hwm_price: float) -> int:
    """Detect bearish reversal candles on the last 3 bars near the plateau zone.

    Returns the total Mₜ penalty to subtract (0, -8, -15, or -20).

    Location filter: only applies when current close >= hwm_price * 0.97.
    Reversal candles during deep pullbacks (> 3% from HWM) are noise.

    Shooting Star / Pin Bar (penalty -8):
      - Upper shadow > 2× lower shadow
      - Close < open  (bearish body)
      - Upper shadow > 60% of full candle range

    Bearish Engulfing (penalty -15):
      - Today's open > yesterday's close   (gap up / opens above)
      - Today's close < yesterday's open   (body engulfs prior body)
      - Today's volume > 20-day avg volume (institutional confirmation)

    Both detected: -20 pts (capped).
    """
    if len(ohlcv) < 22:      # need 20-day vol baseline + 2 candles
        return 0

    # Location filter — only care when near the HWM
    current_close = float(ohlcv[-1].get("close", 0))
    if hwm_price <= 0 or current_close < hwm_price * 0.97:
        return 0

    vols  = [float(r.get("volume", 0)) for r in ohlcv]
    avg20 = sum(vols[-21:-1]) / 20 if sum(vols[-21:-1]) > 0 else 0

    shooting_star = False
    engulfing     = False

    # ── Shooting Star / Pin Bar: check last 3 bars ────────────────────────────
    for i in range(-3, 0):
        bar = ohlcv[i]
        o = float(bar.get("open",  0))
        h = float(bar.get("high",  0))
        l = float(bar.get("low",   0))
        c = float(bar.get("close", 0))
        full_range   = h - l
        if full_range <= 0:
            continue
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        if (c < o
                and upper_shadow > 2 * max(lower_shadow, 0.0001)
                and upper_shadow / full_range > 0.60):
            shooting_star = True
            break

    # ── Bearish Engulfing: last 2 bars ────────────────────────────────────────
    if len(ohlcv) >= 2:
        prev   = ohlcv[-2]
        curr   = ohlcv[-1]
        prev_o = float(prev.get("open",  0))
        prev_c = float(prev.get("close", 0))
        curr_o = float(curr.get("open",  0))
        curr_c = float(curr.get("close", 0))
        curr_v = float(curr.get("volume", 0))
        if (prev_c > prev_o          # prior bar bullish
                and curr_o > prev_c  # today gapped up
                and curr_c < prev_o  # today engulfs prior body
                and avg20 > 0
                and curr_v > avg20): # volume confirmation
            engulfing = True

    if shooting_star and engulfing:
        return -20
    if engulfing:
        return -15
    if shooting_star:
        return -8
    return 0

def compute_momentum_health_score(
    pos: dict,
    ohlcv: list,
    live_sentiment: int = 50,
    days_held: int = 0,
) -> tuple[float, dict]:
    """Live Momentum Health Score Mₜ (0–100) for a held position.

    Returns (score, debug_info) where debug_info has keys:
      rs_component, vol_component, sentiment_component,
      rsi_penalty, candle_penalty, raw_score, final_score.

    Formula:
      Mₜ_raw = 0.40 * RS + 0.35 * Vol + 0.25 * Sentiment
      Mₜ     = max(0, Mₜ_raw - RSI_divergence_penalty - candle_reversal_penalty)

    Day 7+ only: RSI divergence and candlestick penalties activate after
    days_held >= 7. Before that they are 0 (breakout consolidation phase).

    RS component (0-100):
        (live_rs / entry_rs) * 100, capped at 100. Default 50 if no baseline.

    Volume component (0-100):
        V_ratio = today_vol / 20-day_avg_vol
        ≥ 1.5x → 100 | 1.0-1.5x → 50-100 | 0.5-1.0x → 0-50 | < 0.5x → 0

    Sentiment component (0-100):
        live_sentiment from GPT-4o-mini / FMP stock_news.

    RSI Divergence penalty (Day 7+, applied post-blend):
        Price made higher high vs 5 days ago, but RSI made lower high.
        Gap < 5 RSI pts → -10 | 5-15 pts → -18 | > 15 pts → -25

    Candlestick Reversal penalty (Day 7+, applied post-blend):
        Shooting star/pin bar → -8 | Bearish engulfing (vol) → -15 | Both → -20
        Only when price is within 3% of HWM (near plateau top).
    """
    # ── RS component ─────────────────────────────────────────────────────────
    entry_rs = pos.get("entry_rs_score")
    live_rs  = pos.get("live_rs_score")
    if entry_rs and entry_rs > 0 and live_rs is not None:
        rs_ratio     = live_rs / entry_rs
        rs_component = min(100.0, rs_ratio * 100.0)
    else:
        rs_component = 50.0

    # ── Volume component ─────────────────────────────────────────────────────
    vol_component = 50.0
    if len(ohlcv) >= 21:
        vols      = [float(r.get("volume", 0)) for r in ohlcv]
        avg20     = sum(vols[-21:-1]) / 20
        today_vol = vols[-1]
        if avg20 > 0:
            v_ratio = today_vol / avg20
            if v_ratio >= 1.5:
                vol_component = 100.0
            elif v_ratio >= 1.0:
                vol_component = 50.0 + (v_ratio - 1.0) / 0.5 * 50.0
            elif v_ratio >= 0.5:
                vol_component = (v_ratio - 0.5) / 0.5 * 50.0
            else:
                vol_component = 0.0

    # ── Sentiment component ───────────────────────────────────────────────────
    sentiment_component = float(max(1, min(100, live_sentiment)))

    # ── Weighted blend ───────────────────────────────────────────────────────
    raw_score = (
        MOMENTUM_HEALTH_RS_WEIGHT   * rs_component +
        MOMENTUM_HEALTH_VOL_WEIGHT  * vol_component +
        MOMENTUM_HEALTH_SENT_WEIGHT * sentiment_component
    )

    # ── Day 7+ penalty signals ───────────────────────────────────────────────
    rsi_penalty    = 0
    candle_penalty = 0

    if days_held >= 7 and len(ohlcv) >= 20:
        closes = [float(r.get("close", 0)) for r in ohlcv]
        rsi_vals = compute_rsi(closes, period=14)

        # RSI divergence: price up, RSI down (compare today vs 5 days ago)
        lookback = 5
        if (len(rsi_vals) >= lookback + 1
                and rsi_vals[-1] is not None
                and rsi_vals[-1 - lookback] is not None):
            price_now  = closes[-1]
            price_then = closes[-1 - lookback]
            rsi_now    = rsi_vals[-1]
            rsi_then   = rsi_vals[-1 - lookback]

            # Bearish divergence: price higher but RSI lower
            if price_now > price_then and rsi_now < rsi_then:
                div_gap = rsi_then - rsi_now  # positive number
                if div_gap > 15:
                    rsi_penalty = 25
                elif div_gap >= 5:
                    rsi_penalty = 18
                else:
                    rsi_penalty = 10

        # Candlestick reversal near HWM plateau
        hwm_price = float(pos.get("hwm_price") or pos.get("buy_price") or 0)
        candle_penalty_raw = detect_candlestick_reversals(ohlcv, hwm_price)
        candle_penalty = abs(candle_penalty_raw)  # stored as positive for subtraction

    penalty_total = rsi_penalty + candle_penalty
    final_score   = max(0.0, raw_score - penalty_total)

    debug = {
        "rs_component":        round(rs_component, 1),
        "vol_component":       round(vol_component, 1),
        "sentiment_component": round(sentiment_component, 1),
        "rsi_penalty":         -rsi_penalty,
        "candle_penalty":      -candle_penalty,
        "raw_score":           round(raw_score, 1),
        "final_score":         round(final_score, 1),
    }
    return round(final_score, 1), debug
