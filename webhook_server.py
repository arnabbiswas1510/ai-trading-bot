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

EXCHANGE_MAP = {
    "NYSE":    {"ib_exchange": "SMART", "currency": "USD", "fmp_suffix": ""},
    "NASDAQ":  {"ib_exchange": "SMART", "currency": "USD", "fmp_suffix": ""},
    "AMEX":    {"ib_exchange": "SMART", "currency": "USD", "fmp_suffix": ""},
    "TSX":     {"ib_exchange": "TSE",   "currency": "CAD", "fmp_suffix": ".TO"},
    "LSE":     {"ib_exchange": "LSE",   "currency": "GBP", "fmp_suffix": ".L"},
    "ASX":     {"ib_exchange": "ASX",   "currency": "AUD", "fmp_suffix": ".AX"},
    "XETR":    {"ib_exchange": "IBIS",  "currency": "EUR", "fmp_suffix": ".DE"},
    "TSE":     {"ib_exchange": "TSEJ",  "currency": "JPY", "fmp_suffix": ".T"},  
    "HKEX":    {"ib_exchange": "SEHK",  "currency": "HKD", "fmp_suffix": ".HK"},
    "NSE":     {"ib_exchange": "NSE",   "currency": "INR", "fmp_suffix": ".NS"}, 
    "BSE":     {"ib_exchange": "BSE",   "currency": "INR", "fmp_suffix": ".BO"},
    "SGX":     {"ib_exchange": "SGX",   "currency": "SGD", "fmp_suffix": ".SI"}, 
    "SIX":     {"ib_exchange": "EBS",   "currency": "CHF", "fmp_suffix": ".SW"}, 
    "EPA":     {"ib_exchange": "SBF",   "currency": "EUR", "fmp_suffix": ".PA"}, 
    "MIL":     {"ib_exchange": "BVME",  "currency": "EUR", "fmp_suffix": ".MI"}, 
    "BME":     {"ib_exchange": "BM",    "currency": "EUR", "fmp_suffix": ".MC"}  
}
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
            raw_ticker = item.get("ticker")
            if not raw_ticker:
                continue
                
            tv_prefix = "NASDAQ"
            ticker = raw_ticker
            
            # Extract TradingView exchange prefix (e.g., "TSX:SHOP")
            if ":" in raw_ticker:
                parts = raw_ticker.split(":")
                tv_prefix = parts[0].upper()
                ticker = parts[1]
                
            # Map prefix to IBKR and FMP parameters
            mapping = EXCHANGE_MAP.get(tv_prefix, EXCHANGE_MAP["NASDAQ"])
            ticker_clean = ticker.strip().upper()
            fmp_ticker = f"{ticker_clean}{mapping['fmp_suffix']}"
                
            valid_records.append({
                "ticker": ticker_clean,
                "tv_exchange": tv_prefix,
                "ib_exchange": mapping["ib_exchange"],
                "currency": mapping["currency"],
                "fmp_ticker": fmp_ticker,
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
