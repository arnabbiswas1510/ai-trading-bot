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

# ── CAN SLIM fundamental thresholds ───────────────────────────────────────────
# Env-tunable so they can be A/B tested and rolled back without a code change.
#
# Live trading review found the screener was admitting stocks that are not CAN
# SLIM candidates at all. Revenue growth only had to be positive (> 0), so SWK
# entered at score 81 on 0.6% revenue growth with 496% EPS growth — an easy-comp
# accounting artifact, not a growth business. O'Neil requires ~25% sales growth.
#
# MIN_REVENUE_GROWTH defaults to 15 rather than 25 as a deliberate first phase:
# 25% leaves 52 of 248 watchlist names, 15% leaves 116. Since the exit changes
# lengthen holds (cutting entry demand roughly 7x), even the strict setting
# supplies far more triggers than the 4 position slots can absorb. Raise toward
# 25 once the wider exits have been observed in live trading.
MIN_REVENUE_GROWTH   = float(os.environ.get("MIN_REVENUE_GROWTH", 15))
# Raised 15 -> 25 to match O'Neil's annual earnings requirement.
MIN_ANNUAL_EPS_GROWTH = float(os.environ.get("MIN_ANNUAL_EPS_GROWTH", 25))
# Quarterly acceleration is the strongest CAN SLIM signal — unchanged.
MIN_QUARTERLY_EPS_GROWTH = float(os.environ.get("MIN_QUARTERLY_EPS_GROWTH", 20))

# Sectors structurally incapable of the earnings acceleration CAN SLIM looks for.
# Live trading bought REITs (EGP, FR), an insurer (TRV) and a bank (WSFS) — all
# rate-driven, book-value businesses whose "growth" is an interest-rate artifact.
# Set EXCLUDED_SECTORS="" to disable the exclusion entirely.
_DEFAULT_EXCLUDED_SECTORS = "Finance,Real Estate,Utilities"
EXCLUDED_SECTORS = [
    s.strip() for s in os.environ.get("EXCLUDED_SECTORS", _DEFAULT_EXCLUDED_SECTORS).split(",")
    if s.strip()
]


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
            # Raised $10→$15: aligns with AI rating cap boundary (sub-$15 stocks capped at 45)
            {"left": "close", "operation": "egreater", "right": 15},
            # O'Neil requires strong annual earnings growth alongside quarterly
            # acceleration. Was 15 — too low to distinguish a growth business.
            {"left": "earnings_per_share_diluted_yoy_growth_ttm", "operation": "greater",
             "right": MIN_ANNUAL_EPS_GROWTH},
            # KEPT at 20%: quarterly acceleration is the strongest CANSLIM signal
            {"left": "earnings_per_share_diluted_qoq_growth_fq", "operation": "greater",
             "right": MIN_QUARTERLY_EPS_GROWTH},
            # Raised 100K→250K: eliminates dead-zone stocks that score 0 on liquidity anyway
            {"left": "average_volume_30d_calc", "operation": "greater", "right": 250000},
            # NEW: $300M market cap floor — excludes institutional-free micro-caps
            {"left": "market_cap_basic", "operation": "greater", "right": 300000000},
            # Real sales growth. Was "> 0", which only blocked outright shrinkage
            # and let cost-cutting EPS games through with the label "growth".
            {"left": "total_revenue_yoy_growth_ttm", "operation": "greater",
             "right": MIN_REVENUE_GROWTH},
            {"left": "is_primary", "operation": "equal", "right": True}
        ] + ([
            {"left": "sector", "operation": "not_in_range", "right": EXCLUDED_SECTORS}
        ] if EXCLUDED_SECTORS else []),
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
                "price": float(row[9] or 0),
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
