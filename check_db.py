import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None

raw_supabase_key = os.environ.get("SUPABASE_KEY")
SUPABASE_KEY = raw_supabase_key.strip().strip("'\"") if raw_supabase_key else None

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[ERROR] Missing environment variables. Please check SUPABASE_URL and SUPABASE_KEY.")
    exit(1)

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    print("Fetching the latest 5 stocks from the watchlist table...")
    response = supabase_client.table("watchlist").select("*").order("created_at", desc=True).limit(5).execute()
    
    if not response.data:
        print("Table is currently empty.")
    else:
        for row in response.data:
            print("-" * 40)
            print(f"Ticker:         {row.get('ticker')}")
            print(f"Created At:     {row.get('created_at')}")
            print(f"Company Name:   {row.get('company_name')}")
            print(f"Size:           {row.get('company_size')}")
            print(f"Analyst Rating: {row.get('analyst_rating')}")
            print(f"Q-EPS Growth:   {row.get('q_eps_growth')}")
            print(f"A-EPS Growth:   {row.get('a_eps_growth')}")
            print(f"Revenue Growth: {row.get('revenue_growth')}")
            print(f"Float Shares:   {row.get('float_shares')}")
            print(f"ROE:            {row.get('roe')}")
            
except Exception as e:
    print(f"[ERROR] Error fetching from Supabase: {e}")
