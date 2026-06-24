import os
import glob
import csv
from dotenv import load_dotenv
from supabase import create_client, Client

def get_latest_csv(downloads_folder):
    list_of_files = glob.glob(os.path.join(downloads_folder, '*.csv'))
    if not list_of_files:
        return None
    return max(list_of_files, key=os.path.getctime)

def upload_to_supabase():
    downloads_folder = os.path.join(os.path.expanduser('~'), 'Downloads')
    latest_csv = get_latest_csv(downloads_folder)
    
    if not latest_csv:
        print("[-] Could not find any CSV files in Downloads folder!")
        return
        
    print(f"[*] Found latest export: {latest_csv}")
    
    print("[*] Parsing CSV data...")
    records = []
    with open(latest_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_ticker = row.get('Symbol', '').strip()
            # TradingView sometimes exports as "EXCHANGE:TICKER"
            ticker = raw_ticker.split(':')[-1] if ':' in raw_ticker else raw_ticker
            
            if not ticker:
                continue
                
            company_name = row.get('Description', ticker).strip()
            
            # Extract Annual EPS Growth (TTM YoY)
            a_eps_raw = row.get('Earnings per share diluted growth %, TTM YoY', '25.0')
            try:
                a_eps_growth = float(a_eps_raw) if a_eps_raw else 25.0
            except ValueError:
                a_eps_growth = 25.0
                
            records.append({
                "ticker": ticker,
                "company_name": company_name,
                "composite_score": 99,           # Not in CSV
                "q_eps_growth": 25.0,            # Not in CSV
                "a_eps_growth": a_eps_growth,    # Real value from CSV
                "revenue_growth": 25.0,          # Not in CSV
                "inst_count": 100,               # Not in CSV
                "tv_exchange": "NASDAQ", 
                "ib_exchange": "SMART",
                "currency": "USD",
                "fmp_ticker": ticker
            })
                    
    print(f"[*] Successfully parsed {len(records)} stocks.")
    
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        print("[-] Missing Supabase credentials in .env file!")
        return
        
    supabase: Client = create_client(url, key)
    
    from retention_helper import increment_retention
    
    # 1. Fetch existing retention periods
    print("[*] Fetching existing retention periods...")
    incoming_tickers = [r["ticker"] for r in records]
    
    existing_map = {}
    for i in range(0, len(incoming_tickers), 100):
        chunk = incoming_tickers[i:i+100]
        res = supabase.table("watchlist").select("ticker, retention_period").in_("ticker", chunk).execute()
        for row in (res.data or []):
            existing_map[row["ticker"]] = row

    for r in records:
        t = r["ticker"]
        if t in existing_map:
            r["retention_period"] = increment_retention(existing_map[t].get("retention_period"))
        else:
            r["retention_period"] = "1d"

    print("[*] Clearing old watchlist in Supabase...")
    supabase.table("watchlist").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()
    
    print("[*] Inserting new tickers...")
    # Batch insert in chunks of 100 to avoid payload limits
    for i in range(0, len(records), 100):
        chunk = records[i:i+100]
        supabase.table("watchlist").insert(chunk).execute()
        
    print("\n✅ Upload complete! Supabase Watchlist is fully populated with your CSV data.")

if __name__ == "__main__":
    upload_to_supabase()
