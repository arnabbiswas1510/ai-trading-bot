import os
import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv
import sys

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv('.env')

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.strip().strip("'\"")
if SUPABASE_KEY:
    SUPABASE_KEY = SUPABASE_KEY.strip().strip("'\"")

def clear_watchlist():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing Supabase credentials in .env")
        return
        
    print("🔌 Connecting to Supabase...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    print("🗑️  Clearing all records from the 'watchlist' table...")
    # To delete all rows, we can just filter by a condition that is always true, like id > 0 or ticker != ''. 
    # Let's delete where ticker is not null.
    res = supabase.table("watchlist").delete().neq("ticker", "NULL").execute()
    
    print(f"✅ Cleaned out {len(res.data)} records from the watchlist. You have a clean slate!")

if __name__ == "__main__":
    clear_watchlist()
