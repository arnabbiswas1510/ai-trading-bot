import os
import requests
import datetime
import pandas as pd
from supabase import create_client, Client

# Sourced safely from environment variables
FMP_API_KEY = os.environ.get("FMP_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
FMP_BASE_URL = "https://financialmodelingprep.com"

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
        response = get_supabase_client().table("watchlist").select("ticker").execute()
        return [row['ticker'] for row in response.data]
    except Exception as e:
        print(f"❌ Failed to fetch watchlist from Supabase: {e}")
        return []

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
        to_date = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=380)
        
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
        
        if len(df) < 50:
            print(f"⚠️ Insufficient price history for {ticker} (minimum 50 days required)")
            return None
            
        df['sma_50'] = df['close'].rolling(window=50).mean()
        df['avg_volume_50'] = df['volume'].rolling(window=50).mean()
        
        # Calculate 52-week rolling high (252 trading days)
        # Handle newer stocks with less than 252 days of history gracefully
        window_size = min(252, len(df))
        df['rolling_high_52w'] = df['high'].rolling(window=window_size, min_periods=min(50, window_size)).max()
        
        today = df.iloc[-1]
        current_close = today['close']
        sma_50 = today['sma_50']
        today_volume = today['volume']
        avg_vol_50 = today['avg_volume_50']
        
        is_above_50ma = current_close > sma_50
        volume_surge_ratio = today_volume / avg_vol_50 if avg_vol_50 > 0 else 0
        has_volume_surge = volume_surge_ratio >= 1.40  # 40% above 50-day volume average
        
        # Proximity to peak breakout (within 2% of the rolling 52-week high)
        is_breaking_high = current_close >= (today['rolling_high_52w'] * 0.98)
        
        if is_above_50ma and has_volume_surge and is_breaking_high:
            return {
                "ticker": ticker,
                "close_price": float(round(current_close, 2)),
                "volume_surge": float(round(volume_surge_ratio, 2)),
                "sma_50": float(round(sma_50, 2))
            }
    except Exception as e:
        print(f"❌ Error processing technical indicators for {ticker}: {e}")
    return None

def write_triggers_to_supabase(triggers):
    if not triggers:
        print("😴 No breakouts found today. Database insertion skipped.")
        return
    try:
        print(f"📤 Pushing {len(triggers)} breakouts to 'daily_triggers'...")
        get_supabase_client().table("daily_triggers").insert(triggers).execute()
        print("✅ Breakouts recorded successfully.")
    except Exception as e:
        print(f"❌ Failed to log breakout signals: {e}")

if __name__ == "__main__":
    if not FMP_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables. Please check FMP_API_KEY, SUPABASE_URL, and SUPABASE_KEY.")
        exit(1)

    print("⏳ Synchronizing cloud fundamental watchlist data...")
    watchlist = get_watchlist_from_supabase()
    
    if not watchlist:
        print("📭 Target tracking watchlist is empty or could not be retrieved.")
    else:
        print(f"🔍 Analyzing {len(watchlist)} assets for volume breakouts...")
        active_triggers = []
        for ticker in watchlist:
            trigger_data = check_technical_breakout(ticker)
            if trigger_data:
                print(f"🔥 Breakout detected for {ticker}! Price: ${trigger_data['close_price']}, Volume Surge: {trigger_data['volume_surge']}x")
                active_triggers.append(trigger_data)
                
        write_triggers_to_supabase(active_triggers)
