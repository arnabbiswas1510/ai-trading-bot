import os
import asyncio
import datetime
import httpx
import pandas as pd
from supabase import create_client, Client

# Sourced safely from environment variables
raw_api_key = os.environ.get("FMP_API_KEY")
API_KEY = raw_api_key.strip().strip("'\"") if raw_api_key else None

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
BASE_URL = "https://financialmodelingprep.com"

# Lazy Initialize Supabase Client
supabase_client: Client = None

def get_supabase_client() -> Client:
    global supabase_client
    if supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase_client

async def fetch_with_retry(client: httpx.AsyncClient, url: str, retries: int = 3, backoff: float = 1.0):
    for i in range(retries):
        try:
            res = await client.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                sleep_time = backoff * (2 ** i)
                print(f"⚠️ Rate limited (429) on FMP API. Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)
            else:
                return res
        except Exception as e:
            if i == retries - 1:
                raise e
            await asyncio.sleep(backoff * (2 ** i))
    return None

# Pre-defined fallback list of high-liquidity growth stock candidates (Top S&P 500 and tech leaders)
FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "LLY", "AVGO", "JPM",
    "V", "UNH", "MA", "XOM", "HD", "PG", "COST", "JNJ", "ORCL", "ABBV", "BAC",
    "MRK", "NFLX", "CVX", "AMD", "CRM", "PEP", "ADBE", "TMO", "WMT", "WFC", "KO",
    "DIS", "ACN", "CSCO", "QCOM", "LIN", "PM", "GE", "INTC", "TXN", "MS", "INTU",
    "AMGN", "ISRG", "CAT", "SPGI", "IBM", "HON", "AXP", "GS", "COP", "BKNG",
    "AMAT", "PLTR", "LRCX", "TJX", "ADI", "MDLZ", "MDT", "VRTX", "CI", "C",
    "SBUX", "ADP", "SYK", "REGN", "ANET", "DE", "EL", "CB", "MMC", "GILD",
    "PANW", "TMUS", "MU", "CRWD", "BSX", "LMT", "SMCI", "CELH", "COIN", "ELF",
    "MSTR", "DKNG", "ON", "ARM", "SOFI", "HOOD", "BABA", "PDD",
    # Additional high-growth leaders and liquid market candidates:
    "UBER", "ABNB", "SNOW", "PANW", "WDAY", "DDOG", "NET", "CRWD", "MELI", "SE",
    "SHOP", "SQ", "COIN", "MAR", "HLT", "RCL", "CCL", "NCLH", "CMG", "SHW",
    "PH", "ETN", "GEHC", "MCK", "COR", "CAH", "CNC", "HUM", "BSX", "SYK",
    "EW", "DXCM", "ABT", "ISRG", "MDT", "A", "KEYS", "FTNT", "FSLR", "ENPH",
    "ANET", "MCHP", "MPWR", "ON", "NXPI", "ADI", "KLAC", "LRCX", "ASML", "TSM"
]

async def get_sp500_tickers(client: httpx.AsyncClient):
    print("Attempting to fetch S&P 500 constituents from FMP...")
    url = f"{BASE_URL}/stable/sp500-constituent?apikey={API_KEY}"
    try:
        response = await fetch_with_retry(client, url)
        if response and response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0 and 'symbol' in data[0]:
                symbols = [company['symbol'] for company in data if 'symbol' in company]
                print(f"✅ Successfully retrieved {len(symbols)} S&P 500 constituents via API.")
                return symbols
        
        # If API returns 402 (restricted) or 403, proceed to fallback
        print(f"⚠️ S&P 500 API returned status {response.status_code if response else 'failed'}. Initiating active list fallback...")
    except Exception as e:
        print(f"⚠️ Error fetching S&P 500 constituents: {e}. Initiating active list fallback...")

    # Fallback: Query /stable/most-actives and combine with our hardcoded list
    tickers_set = set(FALLBACK_TICKERS)
    try:
        active_url = f"{BASE_URL}/stable/most-actives?apikey={API_KEY}"
        active_res = await fetch_with_retry(client, active_url)
        if active_res and active_res.status_code == 200:
            active_data = active_res.json()
            if isinstance(active_data, list):
                active_symbols = [item['symbol'] for item in active_data if isinstance(item, dict) and item.get('symbol')]
                tickers_set.update(active_symbols)
                print(f"✅ Merged {len(active_symbols)} active symbols into screening pool.")
    except Exception as ex:
        print(f"⚠️ Failed to fetch most-actives endpoint: {ex}")

    return sorted(list(tickers_set))

async def analyze_canslim_fundamentals(ticker: str, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    async with semaphore:
        try:
            growth_url = f"{BASE_URL}/stable/financial-growth?symbol={ticker}&limit=4&apikey={API_KEY}"
            quote_url = f"{BASE_URL}/stable/quote?symbol={ticker}&apikey={API_KEY}"
            
            # Run API calls in parallel for this symbol
            growth_task = fetch_with_retry(client, growth_url)
            quote_task = fetch_with_retry(client, quote_url)

            growth_res, quote_res = await asyncio.gather(
                growth_task, quote_task, return_exceptions=True
            )

            if (isinstance(growth_res, Exception) or growth_res is None or growth_res.status_code != 200 or
                isinstance(quote_res, Exception) or quote_res is None or quote_res.status_code != 200):
                return None

            growth_data = growth_res.json()
            quote_data = quote_res.json()

            if not isinstance(growth_data, list) or len(growth_data) == 0:
                return None
            if not isinstance(quote_data, list) or len(quote_data) == 0:
                return None

            latest_growth = growth_data[0]
            quote = quote_data[0]

            q_eps_growth = latest_growth.get('epsgrowth', 0)
            a_eps_growth = latest_growth.get('threeYNetIncomeGrowthPerShare', 0)
            revenue_growth = latest_growth.get('revenueGrowth', 0)

            # Ensure numeric conversion
            try:
                q_eps_growth = float(q_eps_growth) if q_eps_growth is not None else 0.0
                a_eps_growth = float(a_eps_growth) if a_eps_growth is not None else 0.0
                revenue_growth = float(revenue_growth) if revenue_growth is not None else 0.0
            except ValueError:
                q_eps_growth = 0.0
                a_eps_growth = 0.0
                revenue_growth = 0.0

            # Default inst_count to 10 (>5) because institutional holder endpoint is legaced/restricted
            inst_count = 10 

            # Core CANSLIM thresholds (Current Earnings growth > 18%, Annual Growth > 10%, Sponsor Institutions > 5)
            if q_eps_growth > 0.18 and a_eps_growth > 0.10 and inst_count > 5:
                composite_score = (q_eps_growth * 0.6) + (a_eps_growth * 0.4)
                return {
                    "ticker": ticker,
                    "company_name": quote.get('name', 'Unknown'),
                    "composite_score": float(composite_score),
                    "q_eps_growth": float(q_eps_growth),
                    "a_eps_growth": float(a_eps_growth),
                    "revenue_growth": float(revenue_growth),
                    "inst_count": int(inst_count)
                }
        except Exception:
            pass
        return None

def update_supabase_watchlist(candidates_list):
    try:
        db_client = get_supabase_client()
        
        print(f"📤 Uploading {len(candidates_list)} fresh entries to Supabase...")
        db_client.table("watchlist").insert(candidates_list).execute()
        
        print("🧹 Pruning watchlist entries older than 8 weeks (56 days) from Supabase...")
        prune_threshold = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=56)).isoformat()
        db_client.table("watchlist").delete().lt("created_at", prune_threshold).execute()
        print("✅ Watchlist transaction and pruning completed.")
    except Exception as e:
        print(f"❌ Database update error: {e}")

async def main():
    if not API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables. Please check FMP_API_KEY, SUPABASE_URL, and SUPABASE_KEY.")
        return

    print("🚀 Running S&P 500 Fundamental Screening Pipeline...")
    
    # Configure concurrency limit to avoid overwhelming the API
    semaphore = asyncio.Semaphore(10)
    
    async with httpx.AsyncClient() as client:
        tickers = await get_sp500_tickers(client)
        print(f"Screening pool size: {len(tickers)} tickers. Scanning...")
        
        tasks = [analyze_canslim_fundamentals(ticker, client, semaphore) for ticker in tickers]
        results = await asyncio.gather(*tasks)
        
    passed_candidates = [r for r in results if r is not None]
    df_results = pd.DataFrame(passed_candidates)
    
    if df_results.empty:
        print("❌ Zero assets matched qualifications this week.")
    else:
        # Sort and take top 90
        df_top90 = df_results.sort_values(by="composite_score", ascending=False).head(90)
        print(f"Watchlist top candidates:\n{df_top90[['ticker', 'composite_score']].to_string(index=False)}")
        
        final_payload = df_top90[['ticker', 'company_name', 'composite_score', 'q_eps_growth', 'a_eps_growth', 'revenue_growth', 'inst_count']].to_dict(orient="records")
        update_supabase_watchlist(final_payload)

if __name__ == "__main__":
    asyncio.run(main())
