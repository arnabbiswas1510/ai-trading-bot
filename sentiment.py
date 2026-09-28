"""sentiment.py — FMP/OpenAI research-data fetchers used by the agent loop.

Extracted from execution_agent.py (2026-09-27 orchestrator split). Pure data
acquisition: relative-strength, OHLCV, headline sentiment, market regime and
distribution-day detection. Behaviour is unchanged — the same code, relocated.

SAFETY INVARIANT (see decisions/2026-09-27_execution-agent-modular-split.md):
names the test-suite patches on `execution_agent` are referenced through
`ea.<name>` so `mock.patch("execution_agent.X")` keeps intercepting after the
move. That includes the *sibling* fetchers `_fetch_current_rs` and `_fetch_ohlcv`
— both are patched by tests, so callers here (`_get_entry_rs`, `_get_market_regime`)
must call them as `ea._fetch_current_rs` / `ea._fetch_ohlcv`, not directly, or the
patch would become a no-op.
"""
from __future__ import annotations

import os
import datetime
from zoneinfo import ZoneInfo

from execution_agent_ref import ea


def _get_entry_rs(ticker: str, trigger_rs_score) -> int | None:
    """Return entry_rs_score for a newly opened position.

    Prefers the rs_score already in the trigger row (written by ai_evaluator.py).
    Falls back to a live FMP fetch if the trigger has no rs_score (e.g. the
    AI evaluator hadn't run yet when the buy was executed, or this is a manual
    reconcile buy). This guarantees every position has an RS baseline so that
    Rule 1 (RS Decay) is never permanently blind due to a NULL entry_rs_score.

    Returns None only if the live fetch also fails (FMP API down) — callers must
    handle None gracefully (Rule 1 will skip that position and Rule 2 still applies).
    """
    if trigger_rs_score is not None:
        return int(trigger_rs_score)
    live = ea._fetch_current_rs(ticker)
    if live is not None:
        print(f"   📊 {ticker}: entry_rs_score backfilled live ({live}) — trigger had no rs_score")
    return live


def _fetch_ohlcv(ticker: str, days: int = 100) -> list:
    """Fetch OHLCV rows from FMP for the last `days` calendar days.

    Returns a list of dicts sorted ascending by date, each containing at minimum:
    {'date': str, 'open': float, 'high': float, 'low': float,
     'close': float, 'volume': int}
    Returns [] on any failure. Shared by _get_market_regime and the EOD metrics
    loop so we don't duplicate FMP calls.
    """
    try:
        tz_o    = ZoneInfo("America/New_York")
        to_date = datetime.datetime.now(tz_o).date()
        from_dt = to_date - datetime.timedelta(days=days)
        url = (
            "https://financialmodelingprep.com/stable/historical-price-eod/full"
            f"?symbol={ticker}&from={from_dt}&to={to_date}&apikey={ea.FMP_API_KEY}"
        )
        r = ea.fmp_session.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data or not isinstance(data, list):
            return []
        return sorted(data, key=lambda x: x["date"])
    except Exception as _e:
        print(f"   ⚠️ _fetch_ohlcv({ticker}) failed: {_e}")
        return []


def fetch_held_position_sentiment(ticker: str) -> int:
    """Fetch live sentiment score (1-100) for a held position using FMP news + GPT-4o-mini.

    Calls FMP /stable/news/stock (limit=8, 1 credit) and asks GPT-4o-mini to score
    headline tone on a 1-100 scale. Falls back to 50 (neutral) on any failure.
    Called once per position at EOD (3:45 PM) — ~4 calls/day, ~80/month.

    Endpoint note: the legacy /api/v3/stock_news endpoint this used to call now
    returns HTTP 403 on the current FMP plan, which silently pinned every held
    position's sentiment at the neutral 50 fallback. The supported replacement is
    /stable/news/stock?symbols= (same `title` field). See
    decisions/2026-09-28_earnings-blackout-and-news-veto.md.
    """
    import json as _json_sent
    from openai import OpenAI as _OpenAI

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key or not ea.FMP_API_KEY:
        return 50   # graceful degradation: neutral score

    # ── 1. Fetch headlines ───────────────────────────────────────────────────
    try:
        url = (f"https://financialmodelingprep.com/stable/news/stock"
               f"?symbols={ticker}&limit=8&apikey={ea.FMP_API_KEY}")
        r = ea.fmp_session.get(url, timeout=8)
        if r.status_code != 200:
            return 50
        headlines = [item.get("title", "") for item in r.json() if item.get("title")]
    except Exception as _e:
        print(f"   ⚠️ fetch_held_position_sentiment({ticker}) news fetch failed: {_e}")
        return 50

    if not headlines:
        return 50

    # ── 2. Score with GPT-4o-mini ────────────────────────────────────────────
    try:
        ai = _OpenAI(api_key=openai_key)
        headlines_text = "\n".join(f"- {h}" for h in headlines[:8])
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Output ONLY valid JSON."},
                {"role": "user", "content": (
                    f"Score the overall news sentiment for ${ticker} based on these recent headlines.\n\n"
                    f"{headlines_text}\n\n"
                    "Return a single JSON object: {{\"sentiment\": <integer 1-100>}}\n"
                    "80-100=very positive, 40-60=neutral/mixed, 1-39=negative."
                )},
            ],
            max_tokens=30,
        )
        result = _json_sent.loads(resp.choices[0].message.content)
        score = int(result.get("sentiment", 50))
        score = max(1, min(100, score))
        print(f"   📰 {ticker}: live sentiment score {score}/100 ({len(headlines)} headlines)")
        return score
    except Exception as _ge:
        print(f"   ⚠️ fetch_held_position_sentiment({ticker}) GPT failed: {_ge}")
        return 50


def _get_market_regime() -> str:
    """Return current market regime based on SPY vs its 21-day EMA.

    'uptrend'    — SPY close > 21-day EMA (healthy market, consolidations more forgiving)
    'correction' — SPY close < 21-day EMA (all stalls more suspect)
    'neutral'    — SPY within 0.5% of 21-day EMA
    Returns 'neutral' on any API failure.
    """
    try:
        spy_ohlcv = ea._fetch_ohlcv("SPY", days=40)
        if len(spy_ohlcv) < 22:
            return "neutral"
        closes = [float(r["close"]) for r in spy_ohlcv]
        # 21-day EMA
        k      = 2 / (21 + 1)
        ema21  = closes[0]
        for c in closes[1:]:
            ema21 = c * k + ema21 * (1 - k)
        spy_now = closes[-1]
        diff_pct = (spy_now / ema21 - 1) * 100
        if diff_pct > 0.5:
            return "uptrend"
        if diff_pct < -0.5:
            return "correction"
        return "neutral"
    except Exception:
        return "neutral"


def _fetch_current_rs(ticker: str) -> int | None:
    """Fetch the stock's current 12-week return vs SPY and return its live RS score.

    Uses the same FMP endpoint as the screener — no new dependency.
    Returns None on any API failure (caller must treat as 'no data, skip Tier 1').
    Called once per position per EOD cycle (~4 FMP calls/day total).

    NOTE: scoring.py and technical_screener.py are NOT available in the
    execution agent container (Dockerfile.agent only copies execution_agent.py).
    The RS formula is inlined here verbatim from scoring.compute_rs_score.
    SPY baseline defaults to 0.0 — acceptable because this is used only to
    detect *decay* in RS (entry_rs_score vs live_rs_score), not absolute rank.
    """
    def _rs_from_excess(stock_12w: float, spy_12w: float = 0.0) -> int:
        """Inline of scoring.compute_rs_score — no external module needed."""
        excess = stock_12w - spy_12w
        if excess >= 10:
            return 100
        elif excess >= 0:
            return int(50 + excess * 5)
        elif excess >= -10:
            return max(0, int(50 + excess * 5))
        else:
            return 0

    try:
        tz_rs     = ZoneInfo("America/New_York")
        to_date   = datetime.datetime.now(tz_rs).date()
        from_date = to_date - datetime.timedelta(days=100)
        url = (
            "https://financialmodelingprep.com/stable/historical-price-eod/full"
            f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={ea.FMP_API_KEY}"
        )
        r = ea.fmp_session.get(url, timeout=10)
        if r.status_code != 200:
            print(f"   ⚠️ FMP historical API returned status code {r.status_code} for {ticker}.")
            return None
        data = r.json()
        if not data or not isinstance(data, list) or len(data) < 2:
            return None
        closes = sorted(data, key=lambda x: x["date"])
        lookback = min(60, len(closes) - 1)
        p_now  = float(closes[-1]["close"])
        p_then = float(closes[-1 - lookback]["close"])
        if p_then <= 0:
            return None
        stock_12w = round(((p_now / p_then) - 1.0) * 100.0, 2)
        return _rs_from_excess(stock_12w)  # SPY baseline = 0.0 (decay detection only)
    except Exception as _e:
        print(f"   ⚠️ _fetch_current_rs({ticker}) failed: {_e}")
        return None


def check_volume_distribution(ticker: str, ohlcv: list) -> bool:
    """
    Check if the stock closed sideways/down on above-average volume
    on at least 2 of the last 3 trading days.
    """
    if len(ohlcv) < 54:  # Need 50 days baseline + 3 check days + 1 day for prev_close
        return False

    closes = [float(r["close"]) for r in ohlcv]
    volumes = [float(r.get("volume", 0)) for r in ohlcv]

    distribution_days = 0
    # Check last 3 trading days
    for i in range(-3, 0):
        # 50-day average volume up to day i (excluding day i)
        hist_vols = volumes[i-50:i]
        if not hist_vols:
            continue
        avg_vol = sum(hist_vols) / len(hist_vols)

        day_close = closes[i]
        prev_close = closes[i-1]
        day_vol = volumes[i]

        # Sideways/down (close is <= previous close * 1.002) on above-average volume
        if day_close <= prev_close * 1.002 and day_vol > avg_vol:
            distribution_days += 1

    return distribution_days >= 2
