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


# ── AI batching configuration ─────────────────────────────────────────────────
# The evaluator used to send every trigger in a single prompt. With ~30 tickers
# the model reliably returned only the first few and last few entries and
# silently dropped the middle ("lost in the middle"), leaving those rows with a
# NULL final_score. Small batches keep every ticker inside the model's reliable
# attention window.
AI_BATCH_SIZE   = int(os.environ.get("AI_BATCH_SIZE", 8))
AI_BATCH_RETRIES = int(os.environ.get("AI_BATCH_RETRIES", 1))


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
    # Look back 2 days to be robust against UTC/NY date skew and late-evening runs.
    # The daily_triggers table is truncated and replaced on every screener run,
    # so this always returns the current day's data.
    two_days_ago = (datetime.datetime.now(tz).date() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        res = client.table("daily_triggers").select("*").gte("triggered_at", two_days_ago).execute()
        print(f"[*] Found {len(res.data or [])} trigger(s) since {two_days_ago}")
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


def _format_trigger_block(t: dict, fundamentals: dict, news_by_ticker: dict) -> str:
    """Renders the per-ticker context block used in the AI prompt."""
    ticker  = t["ticker"]
    f_data  = fundamentals.get(ticker, {})
    price   = t.get("close_price") or "N/A"
    # Use 'or' fallback (not .get default) — NULL DB columns return None even with a default
    avg_vol = t.get("avg_volume_50") or 0
    rs      = t.get("rs_score") or 50
    size    = f_data.get("company_size") or "Unknown"
    headlines = news_by_ticker.get(ticker, [])
    news_str  = " | ".join(headlines[:5]) if headlines else "No recent news"

    atr_pct  = t.get("atr_pct") or 0.0
    est_days = t.get("est_days_to_target") or 999
    swing_label = (
        "🚀 Fast mover" if 0 < est_days <= 15 else
        "✅ Swing-compatible" if est_days <= 30 else
        "⚠️ Slow mover" if est_days <= 60 else
        "❌ Long-term only"
    )

    return (
        f"\n- {ticker}:\n"
        f"  Price=${price}, AvgDailyVol={avg_vol:,}, CompanySize={size}, RS_vs_SPY={rs}/100\n"
        f"  VolSurge={t.get('volume_surge')}x, DistFromPivot={t.get('pivot_distance_pct')}%\n"
        f"  ATR={atr_pct}%/day, EstDaysTo25%={est_days} [{swing_label}]\n"
        f"  Q-EPS={f_data.get('q_eps_growth','N/A')}%, A-EPS={f_data.get('a_eps_growth','N/A')}%,"
        f" RevGrowth={f_data.get('revenue_growth','N/A')}%, ROE={f_data.get('roe','N/A')}%\n"
        f"  Analyst={f_data.get('analyst_rating','N/A')}\n"
        f"  RecentNews: {news_str}\n"
    )


def build_prompt(batch: list[dict], fundamentals: dict, news_by_ticker: dict,
                 history_text: str) -> str:
    """Builds the AI prompt for one batch of triggers."""
    tickers = [t["ticker"] for t in batch]
    breakouts_text = "Today's Breakouts (full context):\n"
    for t in batch:
        breakouts_text += _format_trigger_block(t, fundamentals, news_by_ticker)

    ticker_list = ", ".join(tickers)
    return f"""You are an expert AI trading system specializing in CANSLIM swing trading.
Your investor has a SWING TRADER horizon of 2-6 weeks (10-30 trading days).
They need stocks that can move +25% within that window before hitting a -7% trailing stop.
Long-term stories that take months to play out are NOT suitable — the capital must be
deployed and returned within weeks, not quarters.

{history_text}

{breakouts_text}

SCORING RULES (non-negotiable — swing trade horizon is the primary filter):

1. Rating (1-100): Probability the stock hits +25% WITHIN 2-6 WEEKS before -7% stop loss.

   SWING-TRADE VELOCITY (most important factor):
   - EstDaysTo25% <= 15 (ATR >= 1.7%/day): ideal, boost rating +10-15 pts
   - EstDaysTo25% 16-30 (ATR 0.8-1.7%/day): acceptable swing horizon
   - EstDaysTo25% 31-60 (ATR 0.4-0.8%/day): marginal — reduce rating 15 pts
   - EstDaysTo25% > 60 (ATR < 0.4%/day): NOT a swing trade — cap rating at 35

   MANDATORY LIQUIDITY PENALTIES:
   - Stock price under $15: cap rating at 45 (gap risk, no institutional interest)
   - Avg daily volume under 500,000: reduce rating by at least 20 points
   - Small-cap company: reduce rating by at least 15 points

   OTHER FACTORS:
   - Stock lagging SPY (RS < 50): reduce 10-20 points (fighting the tape)
   - Negative/concerning news: reduce rating accordingly
   - Near-term catalyst (earnings, product launch) within 2-3 weeks: boost 10 pts

2. Sentiment (1-100): How positive is the recent news for this stock?
   80-100 = very positive (earnings beat, upgrade, product launch, momentum story)
   40-60  = neutral/mixed
   1-39   = negative (lawsuit, downgrade, guidance cut, regulatory risk)

3. Rationale: 2-3 sentences from a swing trader's perspective.
   MUST address: (a) whether it can reach 25% within 2-6 weeks based on ATR,
   (b) the key risk to the thesis, (c) what would make this a conviction trade.
   Be specific — avoid generic statements.

COMPLETENESS REQUIREMENT (critical):
You MUST return exactly {len(tickers)} entries — one for EVERY ticker listed below,
using the ticker symbol as the JSON key, even if you consider a stock unattractive.
A low rating is always preferable to omitting a ticker. Do NOT skip, merge, summarise
or truncate. Never return only the best candidates.
Required tickers: {ticker_list}

Return ONLY valid JSON in this exact format:
{{
  "TICKER1": {{"rating": 85, "sentiment": 70, "rationale": "ATR of 1.8%/day suggests..."}},
  "TICKER2": {{"rating": 31, "sentiment": 25, "rationale": "ATR of 0.3%/day means..."}}
}}"""


def call_ai_batch(prompt: str) -> dict:
    """Single OpenAI call returning the parsed ratings dict. Raises on failure."""
    response = ai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are a helpful trading assistant that strictly outputs JSON."},
            {"role": "user", "content": prompt}
        ]
    )
    return json.loads(response.choices[0].message.content)


def evaluate_triggers(triggers: list[dict], fundamentals: dict,
                      news_by_ticker: dict, history_text: str) -> tuple[dict, list[str]]:
    """
    Evaluates all *triggers* in small batches, validating that the model returned
    an entry for every ticker and retrying the stragglers.

    Returns (ratings_by_ticker, still_missing_tickers).
    """
    ratings: dict = {}

    for start in range(0, len(triggers), AI_BATCH_SIZE):
        batch = triggers[start:start + AI_BATCH_SIZE]
        batch_no = start // AI_BATCH_SIZE + 1
        total_batches = (len(triggers) + AI_BATCH_SIZE - 1) // AI_BATCH_SIZE
        pending = list(batch)

        for attempt in range(AI_BATCH_RETRIES + 1):
            wanted = [t["ticker"] for t in pending]
            label = f"batch {batch_no}/{total_batches}"
            if attempt:
                label += f" (retry {attempt} for {len(wanted)} missing)"
            print(f"[*] Sending {label}: {', '.join(wanted)}")

            try:
                got = call_ai_batch(build_prompt(pending, fundamentals, news_by_ticker, history_text))
            except Exception as e:
                print(f"  ❌ OpenAI call failed for {label}: {e}")
                got = {}

            # Normalise keys so casing/whitespace drift doesn't look like a miss.
            normalised = {str(k).strip().upper(): v for k, v in got.items()}
            for t in pending:
                key = t["ticker"].strip().upper()
                if key in normalised:
                    ratings[t["ticker"]] = normalised[key]

            pending = [t for t in pending if t["ticker"] not in ratings]
            if not pending:
                break

        if pending:
            print(f"  ⚠️ Still missing after retries: {[t['ticker'] for t in pending]}")

    missing = [t["ticker"] for t in triggers if t["ticker"] not in ratings]
    return ratings, missing


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

    # ── Batched AI evaluation (with completeness validation + retry) ──────────
    ratings_raw, missing = evaluate_triggers(
        triggers, fundamentals, news_by_ticker, history_text
    )
    print(f"✅ Received AI ratings for {len(ratings_raw)}/{len(triggers)} tickers.")

    if missing:
        # Fail loudly. These rows keep a NULL final_score and the execution agent
        # now fails closed on them, so they are silently un-buyable otherwise.
        print(f"❌ No AI rating for {len(missing)} ticker(s): {', '.join(missing)}")
        try:
            from telegram_notifier import TelegramNotifier
            TelegramNotifier(
                bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
            ).notify_exception(
                "ai_evaluator.py — incomplete AI ratings",
                RuntimeError(
                    f"{len(missing)} of {len(triggers)} triggers were not rated by the AI "
                    f"and will be skipped by the buy loop: {', '.join(missing)}"
                )
            )
        except Exception as _alert_err:
            print(f"  ⚠️ Could not send missing-ratings alert: {_alert_err}")

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

        # ── Optional Pre-Breakout score boost (disabled by default) ───────────
        # Kept as an opt-in override only. Default is 0 to avoid systematically
        # promoting unconfirmed setups above confirmed breakouts.
        trigger_type  = str(t.get("trigger_type") or "BREAKOUT")
        if trigger_type == "PRE_BREAKOUT":
            boost = int(os.environ.get("PRE_BREAKOUT_SCORE_BOOST", 0))
            final_score = min(100, final_score + boost)
            if boost > 0:
                print(f"   ⏳ {ticker}: PRE_BREAKOUT +{boost}pt boost applied → final_score={final_score}")

        atr_pct        = float(t.get("atr_pct") or 0.0)
        est_days       = int(t.get("est_days_to_target") or 999)

        print(f"   {ticker}: tech={technical_score} liq={liq_score} ai={ai_score} "
              f"sent={sentiment_score} rs={rs_score} atr={atr_pct}% est={est_days}d -> final={final_score} ({grade}) [{trigger_type}]")
        print(f"     Rationale: {rationale}")

        fields = {
            "ai_rating":          ai_score,
            "ai_grade":           grade,
            "final_score":        final_score,
            "technical_score":    technical_score,
            "liquidity_score":    liq_score,
            "sentiment_score":    sentiment_score,
            "rs_score":           rs_score,
            "score_rationale":    rationale,
            # swing-trade velocity fields (written by screener, confirmed here for display)
            "atr_pct":            atr_pct,
            "est_days_to_target": est_days,
            "trigger_type":       trigger_type,
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

