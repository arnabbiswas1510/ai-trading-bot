import os
import datetime
from flask import Flask, request, jsonify
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().strip("'\"")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip().strip("'\"")

app = Flask(__name__)

# Initialize Supabase client globally
supabase_client: Client = None

def get_supabase_client() -> Client:
    global supabase_client
    if supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing Supabase credentials in .env file")
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase_client

@app.route('/tv-webhook', methods=['POST'])
def tradingview_webhook():
    """
    Receives JSON payload from TradingView alerts.
    Expected format:
    {
        "ticker": "AAPL",
        "price": 150.50,
        "composite_score": 0.85
    }
    Or a list of tickers if batching is used.
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload provided"}), 400

        # Handle both single objects and lists of objects
        candidates = data if isinstance(data, list) else [data]
        
        valid_records = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        for item in candidates:
            ticker = item.get("ticker")
            if not ticker:
                continue
                
            # Clean ticker if TradingView sends exchange prefix (e.g., "NASDAQ:AAPL")
            if ":" in ticker:
                ticker = ticker.split(":")[1]
                
            valid_records.append({
                "ticker": ticker.strip().upper(),
                "price": float(item.get("price", 0.0)),
                "volume": float(item.get("volume", 0.0)),
                "avg_volume": float(item.get("avg_volume", 0.0)),
                "composite_score": float(item.get("composite_score", 0.0)),
                "q_eps_growth": float(item.get("q_eps_growth", 0.0)),
                "a_eps_growth": float(item.get("a_eps_growth", 0.0)),
                "revenue_growth": float(item.get("revenue_growth", 0.0)),
                "inst_count": int(item.get("inst_count", 0)),
                "last_seen_at": item.get("time", now),
                "weeks_retained": 1,
                "first_seen_at": item.get("time", now)
            })

        if not valid_records:
            return jsonify({"error": "No valid tickers found in payload"}), 400

        # Upsert into Supabase
        db_client = get_supabase_client()
        
        # Check existing to increment weeks_retained
        incoming_tickers = [r["ticker"] for r in valid_records]
        existing_res = db_client.table("watchlist").select("ticker, weeks_retained, first_seen_at").in_("ticker", incoming_tickers).execute()
        existing_map = {row["ticker"]: row for row in (existing_res.data or [])}

        upserts = []
        for record in valid_records:
            t = record["ticker"]
            if t in existing_map:
                record["weeks_retained"] = existing_map[t].get("weeks_retained", 0) + 1
                record["first_seen_at"] = existing_map[t].get("first_seen_at", now)
            upserts.append(record)

        db_client.table("watchlist").upsert(upserts, on_conflict="ticker").execute()
        
        print(f"Webhook successfully processed and inserted {len(upserts)} tickers into Supabase.")
        return jsonify({"status": "success", "inserted": len(upserts)}), 200

    except Exception as e:
        print(f"Webhook processing error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Run the server on all interfaces, port 5050
    print("Starting TradingView Webhook Server on port 5050...")
    app.run(host='0.0.0.0', port=5050, debug=False)
