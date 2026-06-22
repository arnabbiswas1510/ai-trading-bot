import os
import asyncio
import datetime
import httpx
import pandas as pd
from dotenv import load_dotenv
import requests
import io

# Force load from .env in the same dir
load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

FMP_BASE_URL = "https://financialmodelingprep.com"

CANSLIM_MIN_Q_EPS_GROWTH = 0.18
CANSLIM_MIN_A_EPS_GROWTH = 0.10
CANSLIM_MIN_INST_HOLDERS = 5
CANSLIM_WATCHLIST_SIZE = 90
API_CONCURRENCY = 10

SMA_WINDOW = 50
VOLUME_AVG_WINDOW = 50
VOLUME_SURGE_MIN = 1.40
ROLLING_HIGH_WINDOW = 252
PIVOT_PROXIMITY = 0.98
MIN_PRICE_HISTORY = 50
FMP_HISTORY_DAYS = 380

def get_russell_1000_tickers():
    print("Attempting to fetch Russell 1000 constituents directly from BlackRock (IWB Holdings)...")
    try:
        # Standard BlackRock/iShares export link structure for IWB
        url = "https://www.ishares.com/us/products/239706/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
        
        # Adding full browser headers to avoid anti-bot protection
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Accept': 'text/csv,application/csv,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200 and 'Ticker' in response.text:
            # The CSV from BlackRock usually has metadata in the first ~9 lines
            df = pd.read_csv(io.StringIO(response.text), skiprows=9)
            if 'Ticker' in df.columns:
                tickers = df['Ticker'].dropna().astype(str).tolist()
                tickers = [t for t in tickers if len(t) > 0 and t != 'nan' and t != '-']
                print(f"✅ Successfully retrieved {len(tickers)} tickers directly from BlackRock IWB CSV.")
                return tickers
        else:
            print(f"⚠️ Failed to parse BlackRock CSV (Status: {response.status_code}). Falling back to S&P 500...")
    except Exception as e:
        print(f"⚠️ Error fetching from BlackRock: {e}. Falling back to S&P 500...")

    # Fallback to S&P 500 constituents if BlackRock fails
    try:
        url = f"{FMP_BASE_URL}/stable/sp500-constituent?apikey={API_KEY}"
        res = requests.get(url)
        if res.status_code == 200:
            data = res.json()
            tickers = [item['symbol'] for item in data if 'symbol' in item]
            print(f"✅ Successfully retrieved {len(tickers)} tickers via FMP S&P 500 fallback.")
            return tickers
    except Exception as e:
        pass
        
    return []

async def fetch_with_retry(client: httpx.AsyncClient, url: str, retries: int = 3, backoff: float = 1.0):
    for i in range(retries):
        try:
            res = await client.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                sleep_time = backoff * (2 ** i)
                await asyncio.sleep(sleep_time)
            else:
                return res
        except Exception as e:
            if i == retries - 1:
                return None
            await asyncio.sleep(backoff * (2 ** i))
    return None

async def fetch_institutional_holder_count(ticker: str, client: httpx.AsyncClient) -> int:
    url = f"https://financialmodelingprep.com/api/v3/institutional-holder/{ticker}?apikey={API_KEY}"
    try:
        res = await fetch_with_retry(client, url)
        if res is None or res.status_code != 200:
            return None
        data = res.json()
        if not isinstance(data, list):
            return None
        holders = [h for h in data if isinstance(h, dict) and h.get("holder")]
        return len(holders)
    except Exception:
        return None

async def analyze_canslim_fundamentals(ticker: str, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    async with semaphore:
        try:
            growth_url = f"{FMP_BASE_URL}/stable/financial-growth?symbol={ticker}&limit=4&apikey={API_KEY}"
            quote_url = f"{FMP_BASE_URL}/stable/quote?symbol={ticker}&apikey={API_KEY}"
            
            growth_task = fetch_with_retry(client, growth_url)
            quote_task = fetch_with_retry(client, quote_url)

            growth_res, quote_res = await asyncio.gather(growth_task, quote_task, return_exceptions=True)

            if isinstance(growth_res, Exception) or growth_res is None or growth_res.status_code != 200 or isinstance(quote_res, Exception) or quote_res is None or quote_res.status_code != 200:
                return None

            growth_data = growth_res.json()
            quote_data = quote_res.json()

            if not isinstance(growth_data, list) or len(growth_data) == 0:
                return None
            if not isinstance(quote_data, list) or len(quote_data) == 0:
                return None

            latest_growth = growth_data[0]
            quote = quote_data[0]

            q_eps_growth = float(latest_growth.get('epsgrowth', 0) or 0)
            a_eps_growth = float(latest_growth.get('threeYNetIncomeGrowthPerShare', 0) or 0)
            revenue_growth = float(latest_growth.get('revenueGrowth', 0) or 0)

            inst_count = await fetch_institutional_holder_count(ticker, client)

            inst_filter_passed = (inst_count is None) or (inst_count > CANSLIM_MIN_INST_HOLDERS)
            if q_eps_growth > CANSLIM_MIN_Q_EPS_GROWTH and a_eps_growth > CANSLIM_MIN_A_EPS_GROWTH and inst_filter_passed:
                composite_score = (q_eps_growth * 0.6) + (a_eps_growth * 0.4)
                return {
                    "ticker": ticker,
                    "company_name": quote.get('name', 'Unknown'),
                    "composite_score": composite_score,
                }
        except Exception:
            pass
        return None

def fetch_with_retry_sync(url, retries=3, backoff=1.0):
    import time
    for i in range(retries):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                time.sleep(backoff * (2 ** i))
            else:
                return res
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(backoff * (2 ** i))
    return None

def check_technical_breakout(ticker):
    try:
        to_date = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=FMP_HISTORY_DAYS)
        
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        
        url = f"{FMP_BASE_URL}/stable/historical-price-eod/full?symbol={ticker}&from={from_str}&to={to_str}&apikey={API_KEY}"
        response = fetch_with_retry_sync(url)
        
        if response is None or response.status_code != 200:
            return None
            
        data = response.json()
        if not data or not isinstance(data, list):
            return None
            
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        
        if len(df) < MIN_PRICE_HISTORY:
            return None
            
        df['sma_50'] = df['close'].rolling(window=SMA_WINDOW).mean()
        df['avg_volume_50'] = df['volume'].rolling(window=VOLUME_AVG_WINDOW).mean()
        
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
        
        is_breaking_high = current_close >= (today['rolling_high_52w'] * PIVOT_PROXIMITY)
        
        if is_above_50ma and has_volume_surge and is_breaking_high:
            rolling_high = today['rolling_high_52w']
            pivot_dist = ((current_close / rolling_high) - 1.0) * 100.0 if rolling_high > 0 else 0.0
            return {
                "ticker": ticker,
                "close_price": float(round(current_close, 2)),
                "volume_surge": float(round(volume_surge_ratio, 2)),
                "sma_50": float(round(sma_50, 2)),
                "rolling_high_52w": float(round(rolling_high, 2)),
                "pivot_distance_pct": float(round(pivot_dist, 2))
            }
    except Exception as e:
        pass
    return None

async def main():
    if not API_KEY:
        print("Missing FMP_API_KEY in environment.")
        return

    tickers = get_russell_1000_tickers()
    if not tickers:
        print("Could not retrieve tickers.")
        return

    print(f"Total tickers to screen: {len(tickers)}")
    print("Running fundamental screen...")
    
    semaphore = asyncio.Semaphore(API_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        tasks = [analyze_canslim_fundamentals(ticker, client, semaphore) for ticker in tickers]
        results = await asyncio.gather(*tasks)
        
    passed_fundamentals = [r for r in results if r is not None]
    
    df_fundamentals = pd.DataFrame(passed_fundamentals)
    if df_fundamentals.empty:
        print("No tickers passed the fundamental screen.")
        return
        
    # Take top 90 by composite score
    df_top = df_fundamentals.sort_values(by="composite_score", ascending=False).head(CANSLIM_WATCHLIST_SIZE)
    watchlist = df_top['ticker'].tolist()
    
    print(f"{len(watchlist)} tickers passed fundamental screen and made the top {CANSLIM_WATCHLIST_SIZE}.")
    print("Running technical screen on watchlist...")
    
    breakouts = []
    for ticker in watchlist:
        trigger_data = check_technical_breakout(ticker)
        if trigger_data:
            breakouts.append(trigger_data)
            print(f"🔥 Breakout: {ticker} at ${trigger_data['close_price']} (Vol Surge: {trigger_data['volume_surge']}x, Peak Dist: {trigger_data['pivot_distance_pct']}%)")
            
    print(f"\nSummary: {len(breakouts)} breakouts found today from the screened list.")

if __name__ == "__main__":
    asyncio.run(main())
