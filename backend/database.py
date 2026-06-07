import sqlite3
import json
import os

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "trading_bot.db"))

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
    
    # 1. Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # 2. Screener Results Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS screener_results (
        ticker TEXT PRIMARY KEY,
        score_c REAL,
        score_a REAL,
        score_n REAL,
        score_s REAL,
        score_l REAL,
        score_i REAL,
        score_m REAL,
        total_score REAL,
        details TEXT, -- JSON string containing detailed metrics
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 3. Portfolio Positions Table (Open paper trades)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE,
        shares INTEGER,
        buy_price REAL,
        buy_date TEXT,
        current_price REAL,
        stop_loss REAL,
        profit_target REAL,
        active INTEGER DEFAULT 1
    )
    """)
    
    # 4. Trade History Table (Closed trades log)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        shares INTEGER,
        buy_price REAL,
        buy_date TEXT,
        sell_price REAL,
        sell_date TEXT,
        profit_loss REAL,
        percent_return REAL,
        exit_reason TEXT
    )
    """)
    
    # Default settings setup
    default_settings = {
        "watchlist": "AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,NFLX,AMD,AVGO,SMCI,ANET,CELH,COIN,ELF",
        "stop_loss_pct": "7.0",
        "profit_target_pct": "25.0",
        "initial_balance": "100000.0",
        "cash_balance": "100000.0"
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
    raw_watchlist = get_setting("watchlist", "")
    if not raw_watchlist:
        return []
    return [t.strip().upper() for t in raw_watchlist.split(",") if t.strip()]

def save_screener_results(results):
    conn = get_db_connection()
    cursor = conn.cursor()
    # First clear older results to avoid stale ratings
    cursor.execute("DELETE FROM screener_results")
    for r in results:
        cursor.execute("""
        INSERT OR REPLACE INTO screener_results 
        (ticker, score_c, score_a, score_n, score_s, score_l, score_i, score_m, total_score, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r['ticker'], 
            r['score_c'], 
            r['score_a'], 
            r['score_n'], 
            r['score_s'], 
            r['score_l'], 
            r['score_i'], 
            r['score_m'], 
            r['total_score'], 
            json.dumps(r['details'])
        ))
    conn.commit()
    conn.close()

def get_screener_results():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM screener_results ORDER BY total_score DESC")
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "ticker": r["ticker"],
            "score_c": r["score_c"],
            "score_a": r["score_a"],
            "score_n": r["score_n"],
            "score_s": r["score_s"],
            "score_l": r["score_l"],
            "score_i": r["score_i"],
            "score_m": r["score_m"],
            "total_score": r["total_score"],
            "details": json.loads(r["details"]),
            "timestamp": r["timestamp"]
        })
    return results

def get_positions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM portfolio_positions WHERE active = 1")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_position(ticker):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM portfolio_positions WHERE ticker = ? AND active = 1", (ticker,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def buy_position(ticker, shares, price, date):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    stop_loss_pct = float(get_setting("stop_loss_pct", 7.0))
    profit_target_pct = float(get_setting("profit_target_pct", 25.0))
    
    stop_loss = price * (1.0 - (stop_loss_pct / 100.0))
    profit_target = price * (1.0 + (profit_target_pct / 100.0))
    
    # Update cash balance
    cash = float(get_setting("cash_balance", 100000.0))
    cost = shares * price
    if cost > cash:
        conn.close()
        raise ValueError("Insufficient cash balance")
        
    set_setting("cash_balance", cash - cost)
    
    cursor.execute("""
    INSERT OR REPLACE INTO portfolio_positions (ticker, shares, buy_price, buy_date, current_price, stop_loss, profit_target, active)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (ticker, shares, price, date, price, stop_loss, profit_target))
    
    conn.commit()
    conn.close()

def sell_position(ticker, price, date, reason):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get current position
    cursor.execute("SELECT * FROM portfolio_positions WHERE ticker = ? AND active = 1", (ticker,))
    pos = cursor.fetchone()
    if not pos:
        conn.close()
        raise ValueError(f"No active position for {ticker}")
        
    shares = pos['shares']
    buy_price = pos['buy_price']
    buy_date = pos['buy_date']
    
    # Calculate proceeds and PnL
    proceeds = shares * price
    cost = shares * buy_price
    pnl = proceeds - cost
    pnl_pct = (price / buy_price - 1.0) * 100.0
    
    # Update cash balance
    cash = float(get_setting("cash_balance", 0.0))
    set_setting("cash_balance", cash + proceeds)
    
    # Delete position (or set active = 0)
    cursor.execute("DELETE FROM portfolio_positions WHERE id = ?", (pos['id'],))
    
    # Save to history
    cursor.execute("""
    INSERT INTO trade_history (ticker, shares, buy_price, buy_date, sell_price, sell_date, profit_loss, percent_return, exit_reason)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, shares, buy_price, buy_date, price, date, pnl, pnl_pct, reason))
    
    conn.commit()
    conn.close()

def update_position_price(ticker, current_price):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE portfolio_positions SET current_price = ? WHERE ticker = ? AND active = 1", (current_price, ticker))
    conn.commit()
    conn.close()

def get_trade_history():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY sell_date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def reset_portfolio():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio_positions")
    cursor.execute("DELETE FROM trade_history")
    conn.commit()
    conn.close()
    
    initial = get_setting("initial_balance", "100000.0")
    set_setting("cash_balance", initial)

# Initialize database on load
init_db()
