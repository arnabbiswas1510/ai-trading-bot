import time
import psutil
import pyautogui
import os
import subprocess
import sys
import json
import glob
from dotenv import load_dotenv
from supabase import create_client, Client

try:
    import pygetwindow as gw
except ImportError:
    print("pygetwindow not found. Please pip install pygetwindow")
    sys.exit(1)

# Configure PyAutoGUI
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5

CONFIG_FILE = "tv_config.json"

def calibrate():
    print("\n" + "="*50)
    print("FIRST TIME SETUP: CALIBRATING TRADINGVIEW")
    print("="*50)
    print("We need to teach the robot exactly where the export buttons are.")
    print("Please make sure TradingView is maximized and open on your screen.\n")
    
    input("STEP 1: Move your mouse exactly over the 'Fundamentals Screener v' header button. Then press ENTER here...")
    header_x, header_y = pyautogui.position()
    print(f"✅ Saved Header Coordinates: ({header_x}, {header_y})")
    
    print("\nSTEP 2: Now physically click that header in TradingView so the menu drops down.")
    input("Once the menu is open, move your mouse exactly over 'Download results as CSV' and press ENTER here...")
    export_x, export_y = pyautogui.position()
    print(f"✅ Saved Export Coordinates: ({export_x}, {export_y})")
    
    # Save to config
    config = {
        "header_x": header_x,
        "header_y": header_y,
        "export_x": export_x,
        "export_y": export_y
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f)
        
    print("\n🎉 Calibration complete! The robot now has eyes.")
    print("You never have to do this again unless you change your monitor size.")
    print("="*50 + "\n")
    return config

def get_config():
    if not os.path.exists(CONFIG_FILE):
        return calibrate()
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def is_tradingview_running():
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and 'TradingView' in proc.info['name']:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def bring_to_front():
    windows = gw.getWindowsWithTitle('TradingView')
    if windows:
        win = windows[0]
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            win.maximize()
            return True
        except Exception:
            return True
    return False

def get_latest_csv(downloads_folder):
    list_of_files = glob.glob(os.path.join(downloads_folder, '*.csv'))
    if not list_of_files:
        return None
    latest_file = max(list_of_files, key=os.path.getctime)
    return latest_file

def sync_watchlist():
    config = get_config()
    
    print("=======================================")
    print("TradingView CSV Auto-Sync Bot")
    print("=======================================")
    
    if not is_tradingview_running():
        print("[!] TradingView is closed. Please open it first.")
        return
        
    print("[*] Maximizing window and grabbing focus...")
    bring_to_front()
    time.sleep(2)
    
    # 1. Click Header
    print("[*] Clicking 'Fundamentals Screener' header...")
    pyautogui.click(x=config['header_x'], y=config['header_y'])
    time.sleep(1.5) # Wait for menu
    
    # 2. Click Export
    print("[*] Clicking 'Download results as CSV'...")
    pyautogui.click(x=config['export_x'], y=config['export_y'])
    
    print("[*] Waiting 5 seconds for CSV to download...")
    time.sleep(5)
    
    # 3. Find Downloaded CSV
    downloads_folder = os.path.join(os.path.expanduser('~'), 'Downloads')
    latest_csv = get_latest_csv(downloads_folder)
    
    if not latest_csv:
        print("[-] Could not find any CSV files in Downloads folder!")
        return
        
    print(f"[*] Found latest export: {latest_csv}")
    
    # 4. Parse CSV
    print("[*] Parsing tickers...")
    tickers = []
    with open(latest_csv, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        # Skip header
        for line in lines[1:]:
            parts = line.split(',')
            if len(parts) > 0:
                ticker = parts[0].strip().strip('"')
                if ticker:
                    tickers.append(ticker)
                    
    print(f"[*] Successfully parsed {len(tickers)} tickers.")
    
    # 5. Push to Supabase
    print("[*] Connecting to Supabase...")
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        print("[-] Missing Supabase credentials in .env file!")
        return
        
    supabase: Client = create_client(url, key)
    
    records = []
    for t in tickers:
        records.append({
            "ticker": t,
            "company_name": t,
            "composite_score": 99,
            "q_eps_growth": 25.0,
            "a_eps_growth": 25.0,
            "revenue_growth": 25.0,
            "inst_count": 100,
            "tv_exchange": "NASDAQ", # Defaulting to US for now
            "ib_exchange": "SMART",
            "currency": "USD",
            "fmp_ticker": t
        })
        
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

    print("[*] Clearing old watchlist...")
    supabase.table("watchlist").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()
    
    print("[*] Inserting new tickers...")
        
    # Batch insert in chunks of 100
    for i in range(0, len(records), 100):
        chunk = records[i:i+100]
        supabase.table("watchlist").insert(chunk).execute()
        
    print("[*] Cleaning up CSV file...")
    try:
        os.remove(latest_csv)
    except Exception as e:
        print(f"Warning: Could not delete CSV: {e}")
        
    print("\n✅ Sync complete! Supabase Watchlist is fully updated.")

if __name__ == "__main__":
    # If no config, run calibration immediately
    if not os.path.exists(CONFIG_FILE):
        get_config()
    else:
        print("Starting in 5 seconds. PLEASE DO NOT TOUCH YOUR MOUSE OR KEYBOARD...")
        time.sleep(5)
        sync_watchlist()
