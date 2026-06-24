import os
import requests
import datetime
from supabase import create_client, Client

# Use environment variables if run from GitHub Actions, or .env locally
try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

TV_SCANNER_URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"

# Map TradingView numeric ratings (-1 to 1) to text
def get_rating_text(rating_val):
    if rating_val is None:
        return "— No rating"
    try:
        val = float(rating_val)
        if val <= -0.5: return "Strong Sell"
        elif val <= -0.1: return "Sell"
        elif val < 0.1: return "Neutral"
        elif val < 0.5: return "Buy"
        else: return "Strong Buy"
    except (ValueError, TypeError):
        return "— No rating"

def run_screener():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[-] Missing SUPABASE_URL or SUPABASE_KEY environment variables!")
        return

    print("[*] Connecting to TradingView Scanner API...")
    
    # Exact payload from the user's TradingView UI, mapped to our required columns
    payload = {
        "columns": [
            "name",                                      # 0: ticker
            "description",                               # 1: company_name
            "earnings_per_share_diluted_qoq_growth_fq",  # 2: q_eps_growth
            "earnings_per_share_diluted_yoy_growth_ttm", # 3: a_eps_growth
            "total_revenue_yoy_growth_ttm",              # 4: revenue_growth
            "Recommend.All",                             # 5: analyst_rating
            "float_shares_outstanding",                  # 6: float_shares
            "return_on_equity",                          # 7: roe
            "market_cap_basic",                          # 8: mcap
            "close",                                     # 9: price
            "volume"                                     # 10: volume
        ],
        "filter": [
            {"left": "close", "operation": "egreater", "right": 10},
            {"left": "earnings_per_share_diluted_yoy_growth_ttm", "operation": "greater", "right": 20},
            {"left": "earnings_per_share_diluted_qoq_growth_fq", "operation": "greater", "right": 20},
            {"left": "average_volume_30d_calc", "operation": "greater", "right": 100000},
            {"left": "is_primary", "operation": "equal", "right": True}
        ],
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "range": [0, 2000],  # Expanded to ensure we fetch all matches
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "markets": ["america"],
        "filter2": {
            "operator": "and",
            "operands": [
                {
                    "operation": {
                        "operator": "or",
                        "operands": [
                            {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "stock"}}, {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}}]}},
                            {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "stock"}}, {"expression": {"left": "typespecs", "operation": "has", "right": ["preferred"]}}]}},
                            {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "dr"}}]}},
                            {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "fund"}}, {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["etf", "mutual"]}}]}}
                        ]
                    }
                },
                {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo"]}}
            ]
        }
    }

    # Spoof headers slightly just to be safe
    headers = {
        'accept': 'application/json',
        'content-type': 'text/plain;charset=UTF-8',
        'origin': 'https://www.tradingview.com',
        'referer': 'https://www.tradingview.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
    }

    response = requests.post(TV_SCANNER_URL, json=payload, headers=headers)
    
    if response.status_code != 200:
        print(f"[-] API Error: {response.status_code} - {response.text}")
        return

    data = response.json()
    stocks = data.get('data', [])
    total_count = data.get('totalCount', 0)
    
    print(f"[+] Retrieved {len(stocks)} stocks matching CANSLIM criteria from TradingView (Total matching: {total_count})")

    if not stocks:
        print("[-] No stocks matched the screener.")
        return

    print("[*] Parsing data and formatting for Supabase...")
    records = []
    
    for stock in stocks:
        # e.g., 'NASDAQ:AAPL' -> 'AAPL'
        symbol_full = stock.get('s', '')
        exchange, ticker = symbol_full.split(':') if ':' in symbol_full else ('', symbol_full)
        
        row = stock.get('d', [])
        if len(row) < 11:
            continue
            
        mcap = float(row[8] or 0)
        if mcap >= 10_000_000_000:
            company_size = "Large"
        elif mcap >= 2_000_000_000:
            company_size = "Mid"
        else:
            company_size = "Small"
            
        records.append({
            "ticker": ticker,
            "company_name": row[1] or ticker,
            "q_eps_growth": float(row[2] or 0),
            "a_eps_growth": float(row[3] or 0),
            "revenue_growth": float(row[4] or 0),
            "analyst_rating": get_rating_text(row[5]),
            "float_shares": int(row[6] or 0),
            "roe": float(row[7] or 0),
            "company_size": company_size,
            "tv_exchange": exchange,
            "ib_exchange": "SMART",
            "currency": "USD",
            "fmp_ticker": ticker 
        })

    print("[*] Connecting to Supabase...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    from retention_helper import increment_retention

    # 1. Fetch existing watchlist to preserve retention_period
    print("[*] Querying existing watchlist...")
    incoming_tickers = [r["ticker"] for r in records]
    
    existing_map = {}
    for i in range(0, len(incoming_tickers), 100):
        chunk = incoming_tickers[i:i+100]
        res = supabase.table("watchlist").select("ticker, retention_period").in_("ticker", chunk).execute()
        for row in (res.data or []):
            existing_map[row["ticker"]] = row

    inserts = []
    for r in records:
        t = r["ticker"]
        if t in existing_map:
            # Stock is retained
            r["retention_period"] = increment_retention(existing_map[t].get("retention_period"))
        else:
            # Brand new stock
            r["retention_period"] = "1d"
            
        r["created_at"] = now
        inserts.append(r)

    # 2. Truncate table
    print("[*] Truncating watchlist table...")
    supabase.table("watchlist").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()

    # 3. Insert the fresh data
    print(f"[*] Inserting {len(inserts)} records into Supabase...")
    for i in range(0, len(inserts), 100):
        chunk = inserts[i:i+100]
        supabase.table("watchlist").insert(chunk).execute()

    print("[+] Replace complete!")

if __name__ == "__main__":
    run_screener()
