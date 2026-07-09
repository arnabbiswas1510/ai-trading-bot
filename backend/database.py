from zoneinfo import ZoneInfo
import sqlite3
import json
import os
import datetime
from supabase import create_client, Client

# Load environment variables from .env if it exists
def load_env():
    env_paths = [".env", "../.env", os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")]
    for path in env_paths:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip() and not line.strip().startswith("#"):
                        parts = line.strip().split("=", 1)
                        if len(parts) == 2:
                            os.environ[parts[0].strip()] = parts[1].strip()
            break

load_env()

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "trading_bot.db"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

_supabase_client: Client = None

def get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client

def get_db_connection():
    parent_dir = os.path.dirname(DB_PATH)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Settings Table is kept locally in SQLite
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # Default settings setup
    default_settings = {
        "watchlist": "AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,AMD,AVGO,SMCI,ANET,CELH,COIN,ELF",
        "stop_loss_pct": "7.0",
        "profit_target_pct": "25.0",
        "initial_balance": "100000.0",
        "cash_balance": "100000.0",
        "fmp_api_key": "",
        "last_watchlist_gen_time": ""
    }
    
    for key, value in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row['value']
    return default

def set_setting(key, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_watchlist():
    try:
        client = get_supabase_client()
        # Fetch the most recent run's timestamp
        timestamps_res = client.table("watchlist").select("created_at").order("created_at", desc=True).limit(1).execute()
        if not timestamps_res.data:
            return []
        latest_ts = timestamps_res.data[0]["created_at"]
        res = client.table("watchlist").select("ticker").eq("created_at", latest_ts).execute()
        return [row["ticker"] for row in res.data]
    except Exception as e:
        print(f"Error fetching watchlist from Supabase: {e}")
        # Fallback to local SQLite settings watchlist if Supabase fails
        raw_watchlist = get_setting("watchlist", "")
        if not raw_watchlist:
            return []
        return [t.strip().upper() for t in raw_watchlist.split(",") if t.strip()]

def _get_week_start(dt: datetime.datetime) -> datetime.datetime:
    """Return UTC midnight of the Monday starting the ISO week containing dt."""
    return (dt - datetime.timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc
    )


def save_screener_results(results):
    try:
        client = get_supabase_client()

        payload = []
        for r in results:
            details = r.get("details", {})
            payload.append({
                "ticker": r["ticker"],
                "company_name": details.get("company_name") or r.get("company_name") or "Unknown",
                "q_eps_growth": float(details.get("c_growth_yoy", 0.0) / 100.0),
                "a_eps_growth": float(details.get("a_eps_growth_cagr", 0.0) / 100.0),
                "revenue_growth": float(details.get("c_rev_growth_yoy", 0.0) / 100.0),
                "price": float(details.get("current_price") or 0.0),
                "rs_rating": float(details.get("l_rs_rating") or 85.0),
                "roe": float(details.get("a_roe") or 22.0),
                "sma50": float(details.get("sma50") or 0.0),
                "n_pct_from_high": float(details.get("n_pct_from_high") or 3.5),
                "s_acc_days": int(details.get("s_acc_days") or 12),
                "s_dist_days": int(details.get("s_dist_days") or 6),
                "total_score": float(r["total_score"])
            })

        if payload:
            # Replace only THIS week's snapshot so re-runs don't create duplicates
            # while preserving previous weeks' data for week-over-week comparison.
            now = datetime.datetime.now(datetime.timezone.utc)
            week_start = _get_week_start(now)
            week_end = week_start + datetime.timedelta(days=7)
            client.table("watchlist").delete() \
                .gte("created_at", week_start.isoformat()) \
                .lt("created_at", week_end.isoformat()) \
                .execute()
            client.table("watchlist").insert(payload).execute()

            # Prune rows older than 8 weeks (keeps last ~8 weekly snapshots)
            prune_threshold = (now - datetime.timedelta(days=56)).isoformat()
            client.table("watchlist").delete().lt("created_at", prune_threshold).execute()
    except Exception as e:
        print(f"Error saving screener results to Supabase: {e}")

def _bg_update_fmp_cache(tickers):
    try:
        from fmp_client import FMPClient
        import pandas as pd
        import datetime
        from screener import calculate_rs_scores

        fmp = FMPClient()
        if not fmp.is_configured():
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS fmp_cache (ticker TEXT PRIMARY KEY, rs_rating REAL, inst_held_percent REAL, last_updated TEXT)")
        conn.commit()

        # Only update missing tickers or those stale for more than 1 day
        stale_cutoff = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
        cursor.execute("SELECT ticker FROM fmp_cache WHERE last_updated >= ?", (stale_cutoff,))
        fresh_tickers = {r["ticker"] for r in cursor.fetchall()}

        missing_tickers = [t for t in tickers if t not in fresh_tickers]
        if not missing_tickers:
            conn.close()
            return

        print(f"[BG FMP Cache] Fetching fresh FMP data for {len(missing_tickers)} tickers...")

        end_dt = datetime.datetime.now()
        start_dt = end_dt - datetime.timedelta(days=365)
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        historical_data = {}
        inst_percentages = {}

        for t in missing_tickers:
            try:
                # 1. Fetch historical price series
                df = fmp.get_historical_prices(t, start_str, end_str)
                if not df.empty:
                    historical_data[t] = df

                # 2. Fetch quote to get shares outstanding
                quote = fmp.get_quote(t)
                market_cap = quote.get("marketCap") if quote else None
                price = quote.get("price") if quote else None
                shares_out = int(market_cap / price) if market_cap and price else None

                if shares_out:
                    inst_pct = fmp.get_institutional_holdings_percentage(t, shares_out)
                    inst_percentages[t] = round(inst_pct, 1)
                else:
                    inst_percentages[t] = 0.0
            except Exception as e:
                print(f"[BG FMP Cache] Error getting FMP data for {t}: {e}")

        # To rank RS ratings properly, we pass all historical data (including already cached ones)
        all_historical_data = {}
        for t in tickers:
            if t in historical_data:
                all_historical_data[t] = historical_data[t]
            else:
                try:
                    df = fmp.get_historical_prices(t, start_str, end_str)
                    if not df.empty:
                        all_historical_data[t] = df
                except Exception:
                    pass

        rs_ratings = calculate_rs_scores(tickers, all_historical_data)

        # Write to cache
        now_str = datetime.datetime.now().isoformat()
        for t in missing_tickers:
            rs_val = rs_ratings.get(t, 85.0)
            inst_val = inst_percentages.get(t, 100.0)
            cursor.execute("INSERT OR REPLACE INTO fmp_cache (ticker, rs_rating, inst_held_percent, last_updated) VALUES (?, ?, ?, ?)", (t, rs_val, inst_val, now_str))

        conn.commit()
        conn.close()
        print(f"[BG FMP Cache] Cache successfully updated for {len(missing_tickers)} tickers.")
    except Exception as e:
        print(f"[BG FMP Cache] Error in background thread: {e}")

def get_screener_results():
    try:
        client = get_supabase_client()

        now = datetime.datetime.now(datetime.timezone.utc)
        curr_week_start = _get_week_start(now)
        prev_week_start = curr_week_start - datetime.timedelta(days=7)
        prev_week_end = curr_week_start  # exclusive

        # Current week's snapshot
        curr_res = client.table("watchlist").select("*") \
            .gte("created_at", curr_week_start.isoformat()) \
            .execute()
        curr_rows = curr_res.data

        # Previous week's snapshot (for NEW/RETAINED/REMOVED comparison)
        prev_res = client.table("watchlist").select("ticker") \
            .gte("created_at", prev_week_start.isoformat()) \
            .lt("created_at", prev_week_end.isoformat()) \
            .execute()
        prev_tickers = {row["ticker"] for row in prev_res.data}

        if not curr_rows:
            # Fall back to most recent rows if nothing in current week yet
            fallback = client.table("watchlist").select("*") \
                .order("created_at", desc=True).limit(90).execute()
            curr_rows = fallback.data

        if not curr_rows:
            return {"watchlist": [], "removed": []}

        # Initialize/Load sqlite FMP cache
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS fmp_cache (ticker TEXT PRIMARY KEY, rs_rating REAL, inst_held_percent REAL, last_updated TEXT)")
        conn.commit()
        cursor.execute("SELECT ticker, rs_rating, inst_held_percent FROM fmp_cache")
        cache = {r["ticker"]: (r["rs_rating"], r["inst_held_percent"]) for r in cursor.fetchall()}
        conn.close()

        # Trigger background FMP update thread for missing/stale data
        import threading
        watchlist_tickers = [row["ticker"] for row in curr_rows]
        threading.Thread(target=_bg_update_fmp_cache, args=(watchlist_tickers,), daemon=True).start()

        curr_tickers = set()
        results = []
        for row in curr_rows:
            ticker = row["ticker"]
            curr_tickers.add(ticker)
            q_eps = row.get("q_eps_growth", 0.0) or 0.0
            a_eps = row.get("a_eps_growth", 0.0) or 0.0
            rev   = row.get("revenue_growth", 0.0) or 0.0
            
            # Retrieve cached metrics
            cached_rs, cached_inst_pct = cache.get(ticker, (85.0, 100.0))
            inst = cached_inst_pct / 10.0
            comp  = 0.0

            # Retrieve saved metrics or default if not present
            price = float(row.get("price") or 0.0)
            rs_rating = cached_rs
            roe = float(row.get("roe") or 22.0)
            sma50 = float(row.get("sma50") or 0.0)
            n_pct_from_high = float(row.get("n_pct_from_high") or 3.5)
            s_acc_days = int(row.get("s_acc_days") or 12)
            s_dist_days = int(row.get("s_dist_days") or 6)

            # Determine component scores
            # Use saved total_score if available, otherwise compute a fallback from fundamental comp score
            db_total_score = row.get("total_score")
            if db_total_score is not None and db_total_score > 0:
                total_score = float(db_total_score)
                # Re-calculate individual component scores from saved indicators
                score_c = min(15.0, round(8.0 + max(0.0, (q_eps - 0.18) * 10.0), 1))
                # ROE score component
                base_a = min(10.0, round(8.0 + max(0.0, (a_eps - 0.10) * 10.0), 1))
                roe_score = 5.0 if roe >= 17.0 else max(0.0, round((roe / 17.0) * 5.0, 1))
                score_a = min(15.0, base_a + roe_score)
                # N component
                score_n = 0.0
                if n_pct_from_high <= 5.0:
                    score_n += 12.0
                elif n_pct_from_high <= 15.0:
                    score_n += 10.0
                elif n_pct_from_high <= 25.0:
                    score_n += 5.0
                if price > sma50:
                    score_n += 2.0
                score_n += 1.0 # assume above 200 SMA
                score_n = min(15.0, score_n)
                # S component
                score_s = 0.0
                if s_acc_days > s_dist_days:
                    score_s += 8.0 + min(4.0, (s_acc_days - s_dist_days) * 1.0)
                elif s_acc_days == s_dist_days:
                    score_s += 4.0
                score_s += 1.5 # float default
                score_s = min(15.0, score_s)
                # L component
                score_l = 0.0
                if rs_rating >= 90:
                    score_l += 13.0
                elif rs_rating >= 80:
                    score_l += 10.0
                elif rs_rating >= 60:
                    score_l += 5.0
                score_l += 2.0 # S&P 500 comparison default
                score_l = min(15.0, score_l)
                # I component
                score_i = min(10.0, round(5.0 + max(0.0, (inst - 5.0) * 0.5), 1))
                # M component
                score_m = 15.0
            else:
                total_score = min(99.0, round(60.0 + (comp * 80.0), 1))
                score_c = min(15.0, round(8.0 + max(0.0, (q_eps - 0.18) * 10.0), 1))
                score_a = min(15.0, round(8.0 + max(0.0, (a_eps - 0.10) * 10.0), 1))
                score_n = 12.0
                score_s = 10.0
                
                # Dynamic L component score
                score_l = 0.0
                if rs_rating >= 90:
                    score_l += 13.0
                elif rs_rating >= 80:
                    score_l += 10.0
                elif rs_rating >= 60:
                    score_l += 5.0
                score_l += 2.0
                score_l = min(15.0, score_l)
                
                # Dynamic I component score
                score_i = min(10.0, round(5.0 + max(0.0, (inst - 5.0) * 0.5), 1))
                score_m = 15.0

            change_status = "NEW" if (prev_tickers and ticker not in prev_tickers) else "RETAINED"

            results.append({
                "ticker": ticker,
                "score_c": score_c,
                "score_a": score_a,
                "score_n": score_n,
                "score_s": score_s,
                "score_l": score_l,
                "score_i": score_i,
                "score_m": score_m,
                "total_score": total_score,
                "change_status": change_status,
                "retention_period": row.get("retention_period") or "1d",
                "details": {
                    "current_price": price,
                    "c_growth_yoy": round(q_eps * 100.0, 1),
                    "c_rev_growth_yoy": round(rev * 100.0, 1),
                    "a_eps_growth_cagr": round(a_eps * 100.0, 1),
                    "l_rs_rating": rs_rating,
                    "i_held_percent_inst": float(inst * 10),
                    "a_roe": roe,
                    "n_pct_from_high": n_pct_from_high,
                    "sma50": sma50,
                    "s_acc_days": s_acc_days,
                    "s_dist_days": s_dist_days,
                    "company_size": row.get("company_size"),
                    "analyst_rating": row.get("analyst_rating"),
                    "float_shares": int(row.get("float_shares") or 0)
                },
                "timestamp": row.get("created_at") or now.isoformat()
            })

        results.sort(key=lambda x: x["total_score"], reverse=True)

        # Tickers in last week's snapshot that didn't make this week's cut
        removed_tickers = sorted(prev_tickers - curr_tickers) if prev_tickers else []

        return {"watchlist": results, "removed": removed_tickers, "total_screened": len(results)}
    except Exception as e:
        print(f"Error getting screener results from Supabase: {e}")
        return {"watchlist": [], "removed": []}

def get_positions():
    try:
        client = get_supabase_client()
        res = client.table("portfolio_positions").select("*").execute()
        
        positions = []
        for row in res.data:
            positions.append({
                "id": row.get("ticker"),
                "ticker": row["ticker"],
                "shares": row["shares"],
                "buy_price": float(row["buy_price"]),
                "buy_date": row["buy_date"],
                "buy_source": row.get("buy_source", "daily_triggers"),
                "buy_reason": row.get("buy_reason", "CANSLIM Breakout"),
                "current_price": float(row.get("current_price") or row["buy_price"]),
                "stop_loss": float(row["stop_loss"]),
                "profit_target": float(row["profit_target"]) if row.get("profit_target") is not None else 0.0,
                "high_water_mark": float(row.get("high_water_mark") or row["buy_price"]),
                "hwm_date": row.get("hwm_date"),
                "is_power_hold": bool(row.get("is_power_hold", False)),
                "power_hold_expiry": row.get("power_hold_expiry"),
                # Entry conviction snapshot — written by execution_agent at buy time
                "entry_final_score": row.get("entry_final_score"),
                "entry_ai_rating": row.get("entry_ai_rating"),
                "entry_ai_grade": row.get("entry_ai_grade"),
                "active": 1,
            })
        return positions
    except Exception as e:
        print(f"Error getting positions from Supabase: {e}")
        return []

def get_position(ticker):
    try:
        client = get_supabase_client()
        res = client.table("portfolio_positions").select("*").eq("ticker", ticker.upper()).execute()
        if res.data:
            row = res.data[0]
            return {
                "id": row.get("ticker"),
                "ticker": row["ticker"],
                "shares": row["shares"],
                "buy_price": float(row["buy_price"]),
                "buy_date": row["buy_date"],
                "current_price": float(row.get("current_price") or row["buy_price"]),
                "stop_loss": float(row["stop_loss"]),
                "profit_target": float(row["profit_target"]),
                "active": 1,
                "buy_reason": row.get("buy_reason", "CANSLIM Breakout")
            }
        return None
    except Exception as e:
        print(f"Error getting position for {ticker} from Supabase: {e}")
        return None

def buy_position(ticker, shares, price, date):
    # Calculate cash balance dynamically based on initial balance, trade history, and active positions
    initial = float(get_setting("initial_balance", 100000.0))
    positions = get_positions()
    history = get_trade_history()
    
    realized_pnl = sum(t["profit_loss"] for t in history)
    open_cost = sum(p["shares"] * p["buy_price"] for p in positions)
    cash = initial + realized_pnl - open_cost
    
    cost = shares * price
    if cost > cash:
        raise ValueError("Insufficient cash balance")
        
    client = get_supabase_client()
    
    stop_loss_pct = float(get_setting("stop_loss_pct", 7.0))
    profit_target_pct = float(get_setting("profit_target_pct", 25.0))
    
    stop_loss = round(price * (1.0 - (stop_loss_pct / 100.0)), 2)
    profit_target = round(price * (1.0 + (profit_target_pct / 100.0)), 2)
    
    position_data = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": price,
        "buy_date": date,
        "buy_reason": "Manual Purchase from Web UI",
        "stop_loss": stop_loss,
        "profit_target": profit_target,
        "is_power_hold": False
    }
    client.table("portfolio_positions").insert(position_data).execute()

def sell_position(ticker, price, date, reason):
    client = get_supabase_client()
    res = client.table("portfolio_positions").select("*").eq("ticker", ticker.upper()).execute()
    if not res.data:
        raise ValueError(f"No active position for {ticker}")
        
    pos = res.data[0]
    shares = int(pos['shares'])
    buy_price = float(pos['buy_price'])
    buy_date = pos['buy_date']
    buy_reason = pos.get('buy_reason', 'CANSLIM Breakout')
    
    proceeds = shares * price
    cost = shares * buy_price
    pnl = round(proceeds - cost, 2)
    pnl_pct = round((price / buy_price - 1.0) * 100.0, 2)
    
    # Delete position from Supabase
    client.table("portfolio_positions").delete().eq("ticker", ticker.upper()).execute()
    
    # Log to trade history
    trade_log = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "buy_reason": buy_reason,
        "sell_price": price,
        "sell_date": date,
        "sell_reason": reason,
        "profit_loss": pnl,
        "percent_return": pnl_pct
    }
    client.table("trade_history").insert(trade_log).execute()

def update_position_price(ticker, current_price):
    # Since current_price is fetched dynamically from FMP quote in main.py, we don't need to write it to Supabase
    pass

def get_trade_history():
    try:
        client = get_supabase_client()
        res = client.table("trade_history").select("*").order("sell_date", desc=True).execute()
        
        trades = []
        for row in res.data:
            trades.append({
                "id": row["id"],
                "ticker": row["ticker"],
                "shares": row["shares"],
                "buy_price": float(row["buy_price"]),
                "buy_date": row["buy_date"],
                "sell_price": float(row["sell_price"]),
                "sell_date": row["sell_date"],
                "profit_loss": float(row["profit_loss"]),
                "percent_return": float(row["percent_return"]),
                "exit_reason": row.get("sell_reason", "Manual Close")
            })
        return trades
    except Exception as e:
        print(f"Error getting trade history from Supabase: {e}")
        return []

def get_daily_triggers():
    try:
        client = get_supabase_client()
        
        # 1. Fetch the unique triggered_at dates
        res_dates = client.table("daily_triggers").select("triggered_at").order("triggered_at", desc=True).execute()
        unique_dates = []
        for row in res_dates.data:
            dt = row["triggered_at"]
            if dt not in unique_dates:
                unique_dates.append(dt)
                if len(unique_dates) == 2:
                    break
                    
        if not unique_dates:
            return {"breakouts": [], "removed": []}
            
        latest_date = unique_dates[0]
        
        # 2. Fetch latest breakouts
        curr_res = client.table("daily_triggers").select("*").eq("triggered_at", latest_date).execute()
        curr_rows = curr_res.data
        
        # 3. Fetch previous day's breakouts to check changes
        prev_tickers = set()
        if len(unique_dates) == 2:
            prev_date = unique_dates[1]
            prev_res = client.table("daily_triggers").select("ticker").eq("triggered_at", prev_date).execute()
            prev_tickers = set(row["ticker"] for row in prev_res.data)
            
        # 3.5 Fetch company_size from watchlist
        curr_tickers_list = [row["ticker"] for row in curr_rows]
        watchlist_map = {}
        if curr_tickers_list:
            watchlist_res = client.table("watchlist").select("ticker, company_size").in_("ticker", curr_tickers_list).execute()
            watchlist_map = {row["ticker"]: row.get("company_size") for row in (watchlist_res.data or [])}

        results = []
        curr_tickers = set()
        for row in curr_rows:
            ticker = row["ticker"]
            curr_tickers.add(ticker)
            change_status = "NEW" if (prev_tickers and ticker not in prev_tickers) else "RETAINED"
            
            results.append({
                "ticker": ticker,
                "close_price": row["close_price"],
                "volume_surge": row["volume_surge"],
                "sma_50": row["sma_50"],
                "rolling_high_52w": row["rolling_high_52w"],
                "pivot_distance_pct": row["pivot_distance_pct"],
                "triggered_at": row["triggered_at"],
                "change_status": change_status,
                "retention_period": row.get("retention_period") or "1d",
                "ai_rating": row.get("ai_rating"),
                "company_size": watchlist_map.get(ticker)
            })
            
        removed_tickers = list(prev_tickers - curr_tickers) if prev_tickers else []
        
        return {
            "breakouts": results,
            "removed": removed_tickers
        }
    except Exception as e:
        print(f"Error getting daily triggers from Supabase: {e}")
        return {"breakouts": [], "removed": []}


def get_historical_triggers(days: int = 30) -> list:
    """Fetch all daily_triggers from the last N days."""
    try:
        client = get_supabase_client()
        cutoff_date = (datetime.datetime.now(ZoneInfo('America/New_York')).date() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        
        # Query daily_triggers
        daily_res = client.table("daily_triggers").select("*").gte("triggered_at", cutoff_date).execute()
        daily = [{**row, "type": "Primary CANSLIM"} for row in (daily_res.data or [])]
        
        return daily
    except Exception as e:
        print(f"Error fetching historical triggers: {e}")
        return []

def reset_portfolio():
    """
    Resets the LOCAL paper-trading state only (SQLite settings).

    CRITICAL SAFETY NOTE: This function intentionally does NOT touch
    portfolio_positions or trade_history in Supabase. Those tables are
    owned and managed exclusively by the execution-agent container.
    Deleting from them here would wipe LIVE production positions.

    If you need to manually clear Supabase positions, do it directly
    in the Supabase dashboard after stopping the execution agent.
    """
    initial = get_setting("initial_balance", "100000.0")
    set_setting("cash_balance", initial)
    print("[reset_portfolio] Local paper-trading state reset. Supabase tables were NOT touched.")

def get_cash_flows():
    try:
        client = get_supabase_client()
        res = client.table("cash_flows").select("*").order("date", desc=True).execute()
        return res.data or []
    except Exception as e:
        print(f"Error getting cash flows from Supabase: {e}")
        return []

def get_account_balances():
    try:
        client = get_supabase_client()
        # Fetch historical values for TWR charting
        res = client.table("account_balances").select("*").order("date", desc=True).execute()
        return res.data or []
    except Exception as e:
        print(f"Error getting account balances from Supabase: {e}")
        return []

# Initialize database on load
init_db()
