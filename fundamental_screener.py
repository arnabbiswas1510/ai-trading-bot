import os
import sys
import datetime
import asyncio
import requests
import pandas as pd
from supabase import create_client, Client
from telegram_notifier import TelegramNotifier
import time
import yfinance as yf
from dotenv import load_dotenv
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
sys.stdout.reconfigure(encoding='utf-8')
load_dotenv('.env')

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().strip("'\"")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip().strip("'\"")

CANSLIM_MIN_Q_EPS_GROWTH  = float(os.environ.get("CANSLIM_MIN_Q_EPS_GROWTH", 0.15))
CANSLIM_MIN_A_EPS_GROWTH  = float(os.environ.get("CANSLIM_MIN_A_EPS_GROWTH", 0.15))
CANSLIM_WATCHLIST_SIZE    = int(os.environ.get("CANSLIM_WATCHLIST_SIZE", 90))
WATCHLIST_PRUNE_DAYS      = int(os.environ.get("WATCHLIST_PRUNE_DAYS", 56))

MIN_PRICE = 10.0
MIN_AVG_VOLUME = 100000

notifier = TelegramNotifier(
    bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
)

supabase_client: Client = None
def get_supabase_client() -> Client:
    global supabase_client
    if supabase_client is None:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase_client

def get_filtered_technical_candidates() -> dict:
    """
    Fetches the entire US equity market via the Nasdaq Screener API,
    applies the technical price/volume filters locally, and returns
    a dictionary of valid {ticker: {'price': float, 'name': str}}.
    """
    print("🔄 Fetching and pre-filtering total US equity market via Nasdaq API...")
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/"
    }
    
    valid_tickers = {}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            rows = data.get('data', {}).get('rows', [])
            print(f"📥 Downloaded {len(rows)} raw tickers from Nasdaq.")
            
            for row in rows:
                try:
                    symbol = str(row.get('symbol', '')).strip()
                    name = str(row.get('name', '')).strip()
                    if not symbol or '^' in symbol or '/' in symbol:
                        continue
                        
                    price_str = str(row.get('lastsale', '')).replace('$', '').replace(',', '')
                    vol_str = str(row.get('volume', '')).replace(',', '')
                    
                    price = float(price_str) if price_str else 0.0
                    volume = float(vol_str) if vol_str else 0.0
                    
                    if price >= MIN_PRICE and volume >= MIN_AVG_VOLUME:
                        valid_tickers[symbol] = {
                            'price': price,
                            'name': name
                        }
                except Exception:
                    pass
            print(f"✅ Technical filter complete. Reduced {len(rows)} to {len(valid_tickers)} candidates.")
        else:
            print(f"❌ Failed to fetch from Nasdaq: {response.status_code}")
    except Exception as e:
        print(f"❌ Exception fetching from Nasdaq: {e}")
        
    return valid_tickers

def calculate_yfinance_growth(ticker: str, price: float, name: str):
    """
    Uses yfinance to sequentially fetch income statements and calculate EPS growth.
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        
        # 1. Quarterly Growth
        q_stmt = ticker_obj.quarterly_income_stmt
        if q_stmt is None or q_stmt.empty:
            return None
            
        q_eps_growth = 0.0
        revenue_growth = 0.0
        
        # 'Basic EPS' or 'Diluted EPS'
        eps_row = None
        if 'Basic EPS' in q_stmt.index:
            eps_row = q_stmt.loc['Basic EPS']
        elif 'Diluted EPS' in q_stmt.index:
            eps_row = q_stmt.loc['Diluted EPS']
            
        if eps_row is not None and len(eps_row) >= 2:
            curr_eps = float(eps_row.iloc[0])
            prev_eps = float(eps_row.iloc[1])
            if prev_eps != 0:
                q_eps_growth = (curr_eps - prev_eps) / abs(prev_eps)
            else:
                q_eps_growth = 1.0 if curr_eps > 0 else 0.0
        else:
            return None

        # 2. Annual Growth
        a_stmt = ticker_obj.income_stmt
        if a_stmt is None or a_stmt.empty:
            return None
            
        a_eps_growth = 0.0
        a_eps_row = None
        if 'Basic EPS' in a_stmt.index:
            a_eps_row = a_stmt.loc['Basic EPS']
        elif 'Diluted EPS' in a_stmt.index:
            a_eps_row = a_stmt.loc['Diluted EPS']
            
        if a_eps_row is not None and len(a_eps_row) >= 2:
            curr_a_eps = float(a_eps_row.iloc[0])
            prev_a_eps = float(a_eps_row.iloc[1])
            if prev_a_eps != 0:
                a_eps_growth = (curr_a_eps - prev_a_eps) / abs(prev_a_eps)
            else:
                a_eps_growth = 1.0 if curr_a_eps > 0 else 0.0
        else:
            return None

        # Core CANSLIM EPS thresholds
        if q_eps_growth > CANSLIM_MIN_Q_EPS_GROWTH and a_eps_growth > CANSLIM_MIN_A_EPS_GROWTH:
            composite_score = (q_eps_growth * 0.6) + (a_eps_growth * 0.4)
            return {
                "ticker": ticker,
                "company_name": name,
                "composite_score": float(composite_score),
                "q_eps_growth": float(q_eps_growth),
                "a_eps_growth": float(a_eps_growth),
                "revenue_growth": 0.0, # Not strictly requiring revenue right now
                "inst_count": -1,      # Dropped institutional requirement as agreed
                "price": float(price)
            }
    except Exception as e:
        # Silently ignore yfinance parsing errors for individual tickers
        pass
    return None

def update_supabase_watchlist(candidates_list):
    if not candidates_list:
        return
        
    try:
        db_client = get_supabase_client()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        incoming_tickers = {r["ticker"] for r in candidates_list}

        existing_res = db_client.table("watchlist") \
            .select("ticker, weeks_retained, first_seen_at") \
            .in_("ticker", list(incoming_tickers)) \
            .execute()
        existing_map = {row["ticker"]: row for row in (existing_res.data or [])}

        inserts = []
        updates = []

        for record in candidates_list:
            ticker = record["ticker"]
            if ticker in existing_map:
                ex = existing_map[ticker]
                updates.append({
                    "ticker":         ticker,
                    "company_name":   record.get("company_name", "Unknown"),
                    "composite_score": record["composite_score"],
                    "q_eps_growth":   record["q_eps_growth"],
                    "a_eps_growth":   record["a_eps_growth"],
                    "revenue_growth": record["revenue_growth"],
                    "inst_count":     record["inst_count"],
                    "price":          record.get("price", 0.0),
                    "weeks_retained": (ex.get("weeks_retained") or 0) + 1,
                    "first_seen_at":  ex.get("first_seen_at") or now,
                    "last_seen_at":   now,
                    "created_at":     now,
                })
            else:
                inserts.append({
                    **record,
                    "weeks_retained": 1,
                    "first_seen_at":  now,
                    "last_seen_at":   now,
                })

        if inserts:
            db_client.table("watchlist").insert(inserts).execute()
        if updates:
            db_client.table("watchlist").upsert(updates, on_conflict="ticker").execute()

        prune_threshold = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=WATCHLIST_PRUNE_DAYS)).isoformat()
        db_client.table("watchlist").delete().lt("last_seen_at", prune_threshold).execute()

        print(f"✅ Watchlist upserted successfully. Pruned tickers absent for >{WATCHLIST_PRUNE_DAYS} days.")
    except Exception as e:
        print(f"❌ Database update error: {e}")
        raise e

def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables.")
        return

    print("🚀 Running Hybrid Screener (Nasdaq Technicals + Sequential Yahoo Fundamentals)...")
    
    valid_tickers_map = get_filtered_technical_candidates()
    if not valid_tickers_map:
        return
        
    valid_tickers = list(valid_tickers_map.keys())
    
    passed_candidates = []
    
    print(f"📈 Processing {len(valid_tickers)} candidates through Yahoo Finance sequentially...")
    
    for idx, ticker in enumerate(valid_tickers):
        if idx % 50 == 0:
            print(f"  > Scanning {idx}/{len(valid_tickers)}...")
            
        data = valid_tickers_map[ticker]
        result = calculate_yfinance_growth(ticker, data['price'], data['name'])
        
        if result:
            passed_candidates.append(result)
            print(f"  🌟 FOUND BREAKOUT: {ticker} (Score: {result['composite_score']:.2f})")
            
        # Protective sleep to avoid IP shadow ban
        time.sleep(1.5)

    df_results = pd.DataFrame(passed_candidates)
    if df_results.empty:
        print("❌ Zero assets matched qualifications.")
    else:
        df_top = df_results.sort_values(by="composite_score", ascending=False).head(CANSLIM_WATCHLIST_SIZE)
        print(f"Final watchlist candidates ({len(df_top)}):\n{df_top[['ticker', 'composite_score']].to_string(index=False)}")
        final_payload = df_top.to_dict(orient="records")
        update_supabase_watchlist(final_payload)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notifier.notify_exception("main() — fundamental_screener.py", e)
        raise
