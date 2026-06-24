import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None
raw_supabase_key = os.environ.get("SUPABASE_KEY")
SUPABASE_KEY = raw_supabase_key.strip().strip("'\"") if raw_supabase_key else None

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Checking schema...")
try:
    # Attempt to query specifically for company_size
    res = supabase.table("watchlist").select("company_size, analyst_rating").limit(1).execute()
    print("SUCCESS! The columns exist.")
except Exception as e:
    print(f"ERROR: {e}")
