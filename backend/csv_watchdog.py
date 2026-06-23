import os
import time
import csv
import datetime
from supabase import create_client, Client
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DROPZONE_PATH = os.environ.get("CSV_DROPZONE_PATH", "/home/dietpi/docker/config/qbittorrent/downloads/tv_fileDrop")

def parse_tv_number(val_str):
    if not val_str:
        return 0.0
    val_str = str(val_str).strip().upper()
    
    # Handle negative numbers and percentage signs
    if val_str.endswith('%'):
        val_str = val_str[:-1].strip()
        
    multiplier = 1.0
    if val_str.endswith('K'):
        multiplier = 1_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('M'):
        multiplier = 1_000_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('B'):
        multiplier = 1_000_000_000.0
        val_str = val_str[:-1]
    elif val_str.endswith('T'):
        multiplier = 1_000_000_000_000.0
        val_str = val_str[:-1]
        
    try:
        return float(val_str) * multiplier
    except ValueError:
        return 0.0

class CSVHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".csv"):
            print(f"[+] Detected new CSV file: {event.src_path}")
            # Wait a brief moment to ensure file is completely written to disk
            time.sleep(2)
            process_csv(event.src_path)

def process_csv(filepath):
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[-] Missing Supabase credentials in .env file!")
            return

        print(f"[*] Processing: {filepath}")
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # 1. Parse CSV
        records = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_ticker = row.get('Symbol', '').strip()
                ticker = raw_ticker.split(':')[-1] if ':' in raw_ticker else raw_ticker
                if not ticker:
                    continue
                    
                company_name = row.get('Description', ticker).strip()
                
                # Parse new columns
                float_raw = row.get('Float', '0')
                float_shares = int(parse_tv_number(float_raw))
                
                analyst_rating = row.get('Analyst rating', '— No rating').strip()
                
                q_eps_raw = row.get('EPS dil growth Quarterly QoQ', '0')
                q_eps_growth = parse_tv_number(q_eps_raw)
                
                rev_raw = row.get('Revenue growth TTM YoY', '0')
                revenue_growth = parse_tv_number(rev_raw)
                
                roe_raw = row.get('ROE TTM', '0')
                roe = parse_tv_number(roe_raw)
                
                a_eps_raw = row.get('Earnings per share diluted growth %, TTM YoY', '0')
                a_eps_growth = parse_tv_number(a_eps_raw)
                    
                # Extract Market Cap for company size
                mcap_raw = row.get('Market capitalization', '0')
                mcap = parse_tv_number(mcap_raw)
                    
                if mcap >= 10_000_000_000:
                    company_size = "Large"
                elif mcap >= 2_000_000_000:
                    company_size = "Mid"
                else:
                    company_size = "Small"
                    
                records.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "company_size": company_size,
                    "q_eps_growth": q_eps_growth,
                    "a_eps_growth": a_eps_growth,
                    "revenue_growth": revenue_growth,
                    "analyst_rating": analyst_rating,
                    "float_shares": float_shares,
                    "roe": roe,
                    "tv_exchange": "NASDAQ",
                    "ib_exchange": "SMART",
                    "currency": "USD",
                    "fmp_ticker": ticker
                })

        if not records:
            print("[-] No valid tickers found in CSV.")
            return

        print(f"[*] Parsed {len(records)} stocks. Querying existing watchlist...")
        
        # 2. Fetch existing watchlist to check retention
        # We process in chunks if there are too many, but 265 is fine for a single IN query
        incoming_tickers = [r["ticker"] for r in records]
        
        existing_map = {}
        for i in range(0, len(incoming_tickers), 100):
            chunk = incoming_tickers[i:i+100]
            existing_res = supabase.table("watchlist").select("ticker, weeks_retained, created_at, first_seen_at").in_("ticker", chunk).execute()
            for row in (existing_res.data or []):
                existing_map[row["ticker"]] = row

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        upserts = []
        for r in records:
            t = r["ticker"]
            if t in existing_map:
                # Stock exists, increment weeks_retained
                r["weeks_retained"] = existing_map[t].get("weeks_retained", 0) + 1
                r["created_at"] = existing_map[t].get("created_at") # Audit trail persistence
                r["first_seen_at"] = existing_map[t].get("first_seen_at", now)
            else:
                # Brand new stock
                r["weeks_retained"] = 1
                r["created_at"] = now
                r["first_seen_at"] = now
                
            r["last_seen_at"] = now
            upserts.append(r)

        # 3. Upsert
        print(f"[*] Upserting {len(upserts)} records into Supabase...")
        
        # Batch upsert in chunks to avoid payload limits
        for i in range(0, len(upserts), 100):
            chunk = upserts[i:i+100]
            supabase.table("watchlist").upsert(chunk, on_conflict="ticker").execute()
            
        print("[+] Upsert complete! UI badges are preserved.")
        
        # 4. Delete the file
        try:
            os.remove(filepath)
            print(f"[*] Deleted processed file: {filepath}")
        except Exception as e:
            print(f"[-] Could not delete file: {e}")

    except Exception as e:
        print(f"[-] Error processing CSV: {e}")

def start_watchdog():
    global DROPZONE_PATH
    if not os.path.exists(DROPZONE_PATH):
        try:
            os.makedirs(DROPZONE_PATH)
            print(f"[*] Created dropzone directory: {DROPZONE_PATH}")
        except Exception as e:
            print(f"[-] Could not create directory {DROPZONE_PATH}: {e}")
            print("[*] Will watch current directory instead.")
            DROPZONE_PATH = "."

    # Process any existing files in the directory on startup
    for file in os.listdir(DROPZONE_PATH):
        if file.endswith('.csv'):
            full_path = os.path.join(DROPZONE_PATH, file)
            print(f"[*] Found existing CSV on startup: {file}")
            process_csv(full_path)
            
    event_handler = CSVHandler()
    observer = Observer()
    observer.schedule(event_handler, path=DROPZONE_PATH, recursive=False)
    observer.start()
    
    print(f"👀 Watchdog active. Monitoring {DROPZONE_PATH} for TradingView CSV drops...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    start_watchdog()
