import os
import json
from supabase import create_client, Client
from openai import OpenAI
import datetime
from zoneinfo import ZoneInfo

# Initialize Supabase
raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None
raw_supabase_key = os.environ.get("SUPABASE_KEY")
SUPABASE_KEY = raw_supabase_key.strip().strip("'\"") if raw_supabase_key else None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or not OPENAI_API_KEY:
    print("❌ Missing SUPABASE_URL, SUPABASE_KEY, or OPENAI_API_KEY.")
    exit(1)

client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = OpenAI(api_key=OPENAI_API_KEY)

def fetch_trade_history():
    print("[*] Fetching recent trade history...")
    try:
        # We look at recently closed trades to provide context
        res = client.table("trade_history").select("ticker, buy_price, sell_price, sell_date, sell_reason, percent_return").order("sell_date", desc=True).limit(30).execute()
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
        res = client.table("watchlist").select("ticker, q_eps_growth, a_eps_growth, revenue_growth, roe, analyst_rating, company_size").in_("ticker", tickers).execute()
        return {row["ticker"]: row for row in res.data}
    except Exception as e:
        print(f"❌ Failed to fetch watchlist data: {e}")
        return {}

def update_trigger_rating(ticker, rating):
    try:
        client.table("daily_triggers").update({"ai_rating": rating}).eq("ticker", ticker).execute()
    except Exception as e:
        print(f"⚠️ Failed to update rating for {ticker}: {e}")

def main():
    triggers = fetch_daily_triggers()
    if not triggers:
        print("😴 No breakouts found today. Skipping AI evaluation.")
        return

    history = fetch_trade_history()
    
    # Fetch fundamentals
    tickers = [t["ticker"] for t in triggers]
    fundamentals = fetch_watchlist_data(tickers)
    
    # Format history for context
    history_text = "Recent closed trades:\n"
    if history:
        for t in history:
            history_text += f"- {t['ticker']}: {t.get('percent_return', 0.0):.2f}% return (Reason: {t.get('sell_reason', 'N/A')})\n"
    else:
        history_text += "No recent trades available yet.\n"

    # Format breakouts for context
    breakouts_text = "Today's Breakouts:\n"
    for t in triggers:
        ticker = t["ticker"]
        f_data = fundamentals.get(ticker, {})
        
        breakouts_text += (
            f"- {ticker}: Price=${t.get('close_price')}, VolSurge={t.get('volume_surge')}x, "
            f"Dist from Pivot={t.get('pivot_distance_pct')}%, "
            f"Q-EPS Growth={f_data.get('q_eps_growth', 'N/A')}%, A-EPS Growth={f_data.get('a_eps_growth', 'N/A')}%, "
            f"Rev Growth={f_data.get('revenue_growth', 'N/A')}%, ROE={f_data.get('roe', 'N/A')}%, Size={f_data.get('company_size', 'N/A')}\n"
        )

    prompt = f"""
You are an expert AI trading system specializing in the CANSLIM strategy and swing trading breakouts.
Your task is to analyze today's breakout stocks and rate each one from 1 to 100 based on its likelihood to hit a 25% profit target before hitting a 7% stop loss.

Context of recent bot performance (use this to bias your ratings towards what is currently working in the market):
{history_text}

{breakouts_text}

Please provide a JSON response mapping each ticker to its integer rating (1-100).
Example: {{"AAPL": 85, "MSFT": 42}}
Return ONLY valid JSON.
"""

    print("[*] Sending data to OpenAI for analysis...")
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
        ratings = json.loads(result_text)
        
        print(f"✅ Received ratings: {ratings}")
        
        # Update the database
        for t in triggers:
            ticker = t["ticker"]
            if ticker in ratings:
                print(f"[*] Updating {ticker} with rating {ratings[ticker]}")
                update_trigger_rating(ticker, ratings[ticker])
                
        print("✅ AI evaluation complete!")
        
    except Exception as e:
        print(f"❌ OpenAI API call failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()
