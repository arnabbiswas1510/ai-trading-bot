import os
import json
import requests
from supabase import create_client, Client
from openai import OpenAI
import datetime
from zoneinfo import ZoneInfo
from scoring import compute_liquidity_score, compute_rs_score, compute_final_score

# Initialize Supabase
raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None
raw_supabase_key = os.environ.get("SUPABASE_KEY")
SUPABASE_KEY = raw_supabase_key.strip().strip("'\"") if raw_supabase_key else None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
FMP_API_KEY = (os.environ.get("FMP_API_KEY") or "").strip().strip("'\"")
FMP_BASE_URL = "https://financialmodelingprep.com"

if not SUPABASE_URL or not SUPABASE_KEY or not OPENAI_API_KEY:
    print("❌ Missing SUPABASE_URL, SUPABASE_KEY, or OPENAI_API_KEY.")
    exit(1)

client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = OpenAI(api_key=OPENAI_API_KEY)

# ── Grade boundaries (unchanged — used for backwards-compat ai_grade field) ──
_GRADE_BOUNDARIES = [(70, "A", 15), (50, "B", 5), (30, "C", 0)]
AI_VETO_THRESHOLD = 30


def ai_grade_and_bonus(rating: int) -> tuple[str, int]:
    """Return (letter_grade, score_bonus) for an AI rating 1-100."""
    for threshold, grade, bonus in _GRADE_BOUNDARIES:
        if rating >= threshold:
            return grade, bonus
    return "D", 0   # veto — execution agent will skip this ticker

# compute_liquidity_score, compute_rs_score, compute_final_score
# are imported from scoring.py (no external dependencies — safe in CI tests).


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_trade_history():
    print("[*] Fetching recent trade history...")
    try:
        res = (client.table("trade_history")
               .select("ticker, buy_price, sell_price, sell_date, sell_reason, percent_return")
               .order("sell_date", desc=True).limit(30).execute())
        return res.data
    except Exception as e:
        print(f"⚠️ Failed to fetch trade history: {e}")
        return []


def fetch_daily_triggers():
    print("[*] Fetching today's breakouts...")
    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")
    try:
        res = client.table("daily_triggers").select("*").gte("triggered_at", today_ny).execute()
        return res.data
    except Exception as e:
        print(f"❌ Failed to fetch daily triggers: {e}")
        return []


def fetch_watchlist_data(tickers):
    if not tickers:
        return {}
    print(f"[*] Fetching fundamental data for {len(tickers)} breakouts...")
    try:
        res = (client.table("watchlist")
               .select("ticker, q_eps_growth, a_eps_growth, revenue_growth, roe, analyst_rating, company_size")
               .in_("ticker", tickers).execute())
        return {row["ticker"]: row for row in res.data}
    except Exception as e:
        print(f"❌ Failed to fetch watchlist data: {e}")
        return {}


def fetch_news_headlines(ticker: str, limit: int = 8) -> list[str]:
    """
    Fetch recent news headlines for a ticker via FMP /v3/stock_news.
    Returns an empty list on failure — sentiment will default to neutral (50).
    """
    if not FMP_API_KEY:
        return []
    try:
        url = (f"{FMP_BASE_URL}/api/v3/stock_news"
               f"?tickers={ticker}&limit={limit}&apikey={FMP_API_KEY}")
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [item.get("title", "") for item in data if item.get("title")]
    except Exception as e:
        print(f"  ⚠️ News fetch failed for {ticker}: {e}")
        return []


def update_trigger_scores(ticker: str, fields: dict):
    """Write updated score fields back to daily_triggers for a ticker."""
    try:
        client.table("daily_triggers").update(fields).eq("ticker", ticker).execute()
    except Exception as e:
        print(f"  ⚠️ Failed to update scores for {ticker}: {e}")


# compute_final_score imported from scoring.py


def main():
    triggers = fetch_daily_triggers()
    if not triggers:
        print("😴 No breakouts found today. Skipping AI evaluation.")
        return

    history = fetch_trade_history()

    # Fetch fundamentals from watchlist
    tickers = [t["ticker"] for t in triggers]
    fundamentals = fetch_watchlist_data(tickers)

    # Format trade history for AI context
    history_text = "Recent closed trades:\n"
    if history:
        for t in history:
            history_text += (f"- {t['ticker']}: {t.get('percent_return', 0.0):.2f}% "
                             f"(Reason: {t.get('sell_reason', 'N/A')})\n")
    else:
        history_text += "No recent trades available yet.\n"

    # Fetch news headlines per ticker (up to 8 headlines each)
    news_by_ticker = {}
    for ticker in tickers:
        headlines = fetch_news_headlines(ticker)
        news_by_ticker[ticker] = headlines
        if headlines:
            print(f"  📰 {ticker}: {len(headlines)} headlines fetched")

    # Format breakouts with all new context for the AI
    breakouts_text = "Today's Breakouts (full context):\n"
    for t in triggers:
        ticker  = t["ticker"]
        f_data  = fundamentals.get(ticker, {})
        price   = t.get("close_price", "N/A")
        avg_vol = t.get("avg_volume_50", 0)
        rs      = t.get("rs_score", 50)
        size    = f_data.get("company_size", "Unknown")
        headlines = news_by_ticker.get(ticker, [])
        news_str  = " | ".join(headlines[:5]) if headlines else "No recent news"

        breakouts_text += (
            f"\n- {ticker}:\n"
            f"  Price=${price}, AvgDailyVol={avg_vol:,}, CompanySize={size}, RS_vs_SPY={rs}/100\n"
            f"  VolSurge={t.get('volume_surge')}x, DistFromPivot={t.get('pivot_distance_pct')}%\n"
            f"  Q-EPS={f_data.get('q_eps_growth','N/A')}%, A-EPS={f_data.get('a_eps_growth','N/A')}%,"
            f" RevGrowth={f_data.get('revenue_growth','N/A')}%, ROE={f_data.get('roe','N/A')}%\n"
            f"  Analyst={f_data.get('analyst_rating','N/A')}\n"
            f"  RecentNews: {news_str}\n"
        )

    prompt = f"""You are an expert AI trading system specializing in the CANSLIM strategy.
Your task is to analyze today's breakout stocks and rate each one.

{history_text}

{breakouts_text}

SCORING RULES (non-negotiable):
1. Rating (1-100): Probability the stock hits +25% BEFORE -7% stop loss.
   MANDATORY PENALTIES — apply these regardless of other positives:
   - Stock price under $15: cap rating at 45 (gap risk, no institutional interest)
   - Avg daily volume under 500,000: reduce rating by at least 20 points (liquidity risk)
   - Small-cap company: reduce rating by at least 15 points (no institutional sponsorship)
   - Negative/concerning news: reduce rating accordingly
   - Stock lagging SPY (RS < 50): reduce rating by 10-20 points

2. Sentiment (1-100): How positive is the recent news for this stock?
   80-100 = very positive (earnings beat, upgrade, product launch)
   40-60  = neutral/mixed
   1-39   = negative (lawsuit, downgrade, guidance cut, regulatory risk)

3. Rationale: 2-3 sentences explaining the KEY factors driving the score.
   Be specific — mention price level, volume, size, news, RS. Do NOT be generic.

Return ONLY valid JSON in this exact format:
{{
  "TICKER1": {{"rating": 85, "sentiment": 70, "rationale": "Strong large-cap breakout..."}},
  "TICKER2": {{"rating": 31, "sentiment": 25, "rationale": "Sub-$10 small-cap with..."}}
}}"""

    print("[*] Sending enriched data to OpenAI for analysis...")
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a helpful trading assistant that strictly outputs JSON."},
                {"role": "user", "content": prompt}
            ]
        )

        result_text = response.choices[0].message.content
        ratings_raw = json.loads(result_text)
        print(f"✅ Received AI ratings: {list(ratings_raw.keys())}")

    except Exception as e:
        print(f"❌ OpenAI API call failed: {e}")
        return

    # ── Compute all components and write back ─────────────────────────────────
    scored_triggers = []
    for t in triggers:
        ticker = t["ticker"]
        if ticker not in ratings_raw:
            print(f"  ⚠️ No AI rating for {ticker} — skipping score update.")
            continue

        raw = ratings_raw[ticker]

        # Handle both new dict format and old int format (backwards-compat)
        if isinstance(raw, dict):
            ai_score       = int(raw.get("rating", 50))
            sentiment_score = int(raw.get("sentiment", 50))
            rationale      = str(raw.get("rationale", "")).strip()
        else:
            # Legacy: AI returned a plain integer
            ai_score       = int(raw)
            sentiment_score = 50
            rationale      = ""

        ai_score       = max(1, min(100, ai_score))
        sentiment_score = max(1, min(100, sentiment_score))

        # Grade for backwards-compat (used by execution agent D-veto)
        grade, _bonus = ai_grade_and_bonus(ai_score)

        # Liquidity score
        f_data = fundamentals.get(ticker, {})
        liq_score = compute_liquidity_score(
            close_price   = float(t.get("close_price") or 0),
            avg_volume_50 = int(t.get("avg_volume_50") or 0),
            company_size  = f_data.get("company_size", ""),
        )

        technical_score = int(t.get("technical_score") or t.get("quality_score") or 50)
        rs_score        = int(t.get("rs_score") or 50)

        final_score = compute_final_score(
            technical_score, liq_score, ai_score, sentiment_score, rs_score
        )

        print(f"   {ticker}: tech={technical_score} liq={liq_score} ai={ai_score} "
              f"sent={sentiment_score} rs={rs_score} -> final={final_score} ({grade})")
        print(f"     Rationale: {rationale}")

        fields = {
            "ai_rating":       ai_score,
            "ai_grade":        grade,
            "final_score":     final_score,
            "technical_score": technical_score,
            "liquidity_score": liq_score,
            "sentiment_score": sentiment_score,
            "rs_score":        rs_score,
            "score_rationale": rationale,
        }
        update_trigger_scores(ticker, fields)

        scored_triggers.append({**t, **fields})

    print("✅ AI evaluation complete!")

    # ── Send enriched Telegram notification ────────────────────────────────────
    if scored_triggers:
        from telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
        )
        notifier.notify_ai_evaluation_complete(scored_triggers)


if __name__ == "__main__":
    main()
