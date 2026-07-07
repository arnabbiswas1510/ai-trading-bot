import os
import requests
import datetime
import pandas as pd
from supabase import create_client, Client
from telegram_notifier import TelegramNotifier
from zoneinfo import ZoneInfo

# Sourced safely from environment variables
raw_api_key = os.environ.get("FMP_API_KEY")
FMP_API_KEY = raw_api_key.strip().strip("'\"") if raw_api_key else None

raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None

raw_supabase_key = os.environ.get("SUPABASE_KEY")
if raw_supabase_key:
    cleaned_key = raw_supabase_key.strip().strip("'\"")
    if cleaned_key != raw_supabase_key:
        print("⚠️ SUPABASE_KEY environment variable had leading/trailing whitespace, newlines, or quotes which were stripped.")
    SUPABASE_KEY = cleaned_key
else:
    SUPABASE_KEY = None
FMP_BASE_URL = "https://financialmodelingprep.com"

# ── Technical screener configuration (set in .env) ──────────────────────────────
SMA_WINDOW        = int(os.environ.get("SMA_WINDOW", 50))
VOLUME_AVG_WINDOW = int(os.environ.get("VOLUME_AVG_WINDOW", 50))
VOLUME_SURGE_MIN  = float(os.environ.get("VOLUME_SURGE_MIN", 1.40))
ROLLING_HIGH_WINDOW = int(os.environ.get("ROLLING_HIGH_WINDOW", 252))
PIVOT_PROXIMITY   = float(os.environ.get("PIVOT_PROXIMITY", 0.98))
MIN_PRICE_HISTORY = int(os.environ.get("MIN_PRICE_HISTORY", 50))
FMP_HISTORY_DAYS  = int(os.environ.get("FMP_HISTORY_DAYS", 380))
TRIGGER_PRUNE_DAYS = int(os.environ.get("TRIGGER_PRUNE_DAYS", 56))

# ── Telegram notifications ─────────────────────────────────────────────────────
notifier = TelegramNotifier(
    bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
)

# Lazy Initialize Supabase Client
supabase_client: Client = None

def get_supabase_client() -> Client:
    global supabase_client
    if supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase_client

def get_watchlist_from_supabase():
    try:
        client = get_supabase_client()
        # Fetch the most recent run's timestamp
        timestamps_res = client.table("watchlist").select("created_at").order("created_at", desc=True).limit(1).execute()
        if not timestamps_res.data:
            return []
        latest_ts = timestamps_res.data[0]["created_at"]
        
        # Check if the watchlist was populated today
        latest_date = datetime.date.fromisoformat(latest_ts.split('T')[0])
        today = datetime.datetime.now(datetime.timezone.utc).date()
        
        if latest_date != today:
            print(f"⚠️ Watchlist was last updated on {latest_date}, not today ({today}). Aborting technical screener to prevent stale data.")
            return []
            
        response = client.table("watchlist").select("ticker").execute()
        return [row['ticker'] for row in response.data]
    except Exception as e:
        print(f"❌ Failed to fetch watchlist from Supabase: {e}")
        raise e

def fetch_with_retry_sync(url, retries=3, backoff=1.0):
    import time
    for i in range(retries):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                sleep_time = backoff * (2 ** i)
                print(f"⚠️ Rate limited (429) on FMP API. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                return res
        except Exception as e:
            if i == retries - 1:
                raise e
            time.sleep(backoff * (2 ** i))
    return None

def check_technical_breakout(ticker):
    try:
        # Request EOD stable data for the past 380 calendar days to guarantee 252+ trading days
        to_date = datetime.datetime.now(ZoneInfo('America/New_York')).date()
        from_date = to_date - datetime.timedelta(days=FMP_HISTORY_DAYS)
        
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        
        url = f"{FMP_BASE_URL}/stable/historical-price-eod/full?symbol={ticker}&from={from_str}&to={to_str}&apikey={FMP_API_KEY}"
        response = fetch_with_retry_sync(url)
        
        if response is None or response.status_code != 200:
            print(f"⚠️ FMP API error ({response.status_code if response else 'failed'}) for {ticker}")
            return None
            
        data = response.json()
        if not data or not isinstance(data, list):
            return None
            
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        
        if len(df) < MIN_PRICE_HISTORY:
            print(f"⚠️ Insufficient price history for {ticker} (minimum {MIN_PRICE_HISTORY} days required)")
            return None
            
        df['sma_50'] = df['close'].rolling(window=SMA_WINDOW).mean()
        df['avg_volume_50'] = df['volume'].rolling(window=VOLUME_AVG_WINDOW).mean()
        
        # Calculate rolling high (configurable window, default 252 trading days)
        # Handle newer stocks with less than the full window gracefully
        window_size = min(ROLLING_HIGH_WINDOW, len(df))
        df['rolling_high_52w'] = df['high'].rolling(window=window_size, min_periods=min(MIN_PRICE_HISTORY, window_size)).max()
        
        today = df.iloc[-1]
        current_close = today['close']
        sma_50 = today['sma_50']
        today_volume = today['volume']
        avg_vol_50 = today['avg_volume_50']
        
        is_above_50ma = current_close > sma_50
        volume_surge_ratio = today_volume / avg_vol_50 if avg_vol_50 > 0 else 0
        has_volume_surge = volume_surge_ratio >= VOLUME_SURGE_MIN
        
        # Proximity to peak breakout (within configured % of the rolling high)
        is_breaking_high = current_close >= (today['rolling_high_52w'] * PIVOT_PROXIMITY)
        
        if is_above_50ma and has_volume_surge and is_breaking_high:
            rolling_high = today['rolling_high_52w']
            pivot_dist = ((current_close / rolling_high) - 1.0) * 100.0 if rolling_high > 0 else 0.0
            
            today_ny = datetime.datetime.now(ZoneInfo("America/New_York")).date().strftime("%Y-%m-%d")
            
            return {
                "ticker": ticker,
                "close_price": float(round(current_close, 2)),
                "volume_surge": float(round(volume_surge_ratio, 2)),
                "sma_50": float(round(sma_50, 2)),
                "rolling_high_52w": float(round(rolling_high, 2)),
                "pivot_distance_pct": float(round(pivot_dist, 2)),
                "triggered_at": today_ny
            }
    except Exception as e:
        print(f"❌ Error processing technical indicators for {ticker}: {e}")
    return None

def write_triggers_to_supabase(triggers):
    if not triggers:
        print("😴 No breakouts found today. Database insertion skipped.")
        return
    try:
        client = get_supabase_client()
        from retention_helper import increment_retention
        
        print("[*] Querying existing daily triggers...")
        existing_res = client.table("daily_triggers").select("ticker, retention_period").execute()
        existing_map = {row["ticker"]: row for row in (existing_res.data or [])}

        for t in triggers:
            ticker = t["ticker"]
            if ticker in existing_map:
                t["retention_period"] = increment_retention(existing_map[ticker].get("retention_period"))
            else:
                t["retention_period"] = "1d"

        print("🧹 Truncating daily_triggers table...")
        client.table("daily_triggers").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()
        
        print(f"📤 Pushing {len(triggers)} breakouts to 'daily_triggers'...")
        client.table("daily_triggers").insert(triggers).execute()
        print("✅ Breakouts replaced successfully.")
    except Exception as e:
        print(f"❌ Failed to log breakout signals: {e}")
        raise e

if __name__ == "__main__":
    if not FMP_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables. Please check FMP_API_KEY, SUPABASE_URL, and SUPABASE_KEY.")
        exit(1)

    try:
        print("⏳ Synchronizing cloud fundamental watchlist data...")
        watchlist = get_watchlist_from_supabase()
        
        if not watchlist:
            print("💭 Target tracking watchlist is empty or could not be retrieved.")
        else:
            print(f"🔍 Analyzing {len(watchlist)} assets for volume breakouts...")
            active_triggers = []
            for ticker in watchlist:
                trigger_data = check_technical_breakout(ticker)
                if trigger_data:
                    print(f"🔥 Breakout detected for {ticker}! Price: ${trigger_data['close_price']}, Volume Surge: {trigger_data['volume_surge']}x")
                    active_triggers.append(trigger_data)
                    
            write_triggers_to_supabase(active_triggers)
    except Exception as e:
        notifier.notify_exception("main block — technical_screener.py", e)
        raise
