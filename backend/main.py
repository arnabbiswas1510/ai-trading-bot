from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import datetime
import json
import asyncio
import pandas as pd
from zoneinfo import ZoneInfo

import database as db
import screener
import backtester
from fmp_client import FMPClient

app = FastAPI(title="CAN SLIM Trading Bot API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all origins for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------
# Pydantic Schemas
# -----------------
class SettingsUpdate(BaseModel):
    watchlist: str
    stop_loss_pct: float
    profit_target_pct: float
    initial_balance: float
    fmp_api_key: Optional[str] = ""

class BacktestRequest(BaseModel):
    tickers: Optional[List[str]] = None
    start_date: str
    end_date: str
    initial_capital: float
    stop_loss_pct: float
    profit_target_pct: float
    max_positions: int

# -----------------
# Background Scheduler
# -----------------
def check_and_run_weekly_watchlist():
    """Checks if more than 7 days have passed since the last watchlist generation, and runs it if so."""
    try:
        fmp = FMPClient()
        if not fmp.is_configured():
            print("[Scheduler] FMP API Key is not configured yet. Skipping weekly watchlist check.")
            return
            
        last_run = db.get_setting("last_watchlist_gen_time", "")
        should_run = False
        if not last_run:
            should_run = True
        else:
            try:
                last_dt = datetime.datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")
                # If it has been more than 7 days, trigger it
                if (datetime.datetime.now() - last_dt).days >= 7:
                    should_run = True
            except Exception:
                should_run = True
                
        if should_run:
            print("[Scheduler] Running automatic weekly watchlist generation...")
            symbols = fmp.run_screener_watchlist()
            if symbols:
                watchlist_str = ",".join(symbols)
                db.set_setting("watchlist", watchlist_str)
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.set_setting("last_watchlist_gen_time", now_str)
                print(f"[Scheduler] Watchlist updated with {len(symbols)} tickers.")
            else:
                print("[Scheduler] FMP Stock Screener returned no symbols or failed.")
    except Exception as e:
        print(f"[Scheduler] Error running weekly watchlist generation: {e}")

async def periodic_watchlist_scheduler():
    while True:
        try:
            check_and_run_weekly_watchlist()
        except Exception as e:
            print(f"[Scheduler] Error in periodic loop: {e}")
        # Wait 1 hour between checks
        await asyncio.sleep(3600)

@app.on_event("startup")
async def startup_event():
    # Start the periodic weekly check loop in the background
    asyncio.create_task(periodic_watchlist_scheduler())

# -----------------
# Routes
# -----------------

@app.get("/api/version")
def get_version():
    """
    Returns build metadata for the currently deployed image.
    GIT_COMMIT and BUILD_TIME are injected as Docker build args → env vars.
    The UI displays these so you can confirm exactly what code is running.
    """
    return {
        "git_commit":  os.getenv("GIT_COMMIT", "unknown"),
        "build_time":  os.getenv("BUILD_TIME",  "unknown"),
        "service":     "can-slim-trading-bot",
    }

@app.get("/api/market")
def get_market_health():
    try:
        return screener.get_market_direction()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/screener/run")
def run_screener():
    try:
        results = screener.run_canslim_screener()
        db.save_screener_results(results)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/screener/auto-watchlist")
def auto_generate_watchlist():
    try:
        fmp = FMPClient()
        if not fmp.is_configured():
            raise HTTPException(status_code=400, detail="FMP API Key is not configured. Please set it in Settings.")
            
        print("Running manual watchlist auto-generation...")
        symbols = fmp.run_screener_watchlist()
        if not symbols:
            raise HTTPException(status_code=500, detail="FMP stock screener returned no symbols or failed.")
            
        watchlist_str = ",".join(symbols)
        db.set_setting("watchlist", watchlist_str)
        
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.set_setting("last_watchlist_gen_time", now_str)
        
        return {
            "status": "success",
            "message": f"Successfully auto-generated watchlist with {len(symbols)} tickers.",
            "watchlist": watchlist_str
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/screener/results")
def get_screener_results():
    try:
        return db.get_screener_results()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/portfolio")
def get_portfolio():
    try:
        initial = float(db.get_setting("initial_balance", 100000.0))
        positions = db.get_positions()
        history = db.get_trade_history()
        
        # Calculate cash balance:
        # Prefer the real IBKR cash balance synced by the execution agent (accounts for
        # deposits, withdrawals, commissions, dividends). Fall back to the derived formula
        # (initial + realized_pnl - open_cost) when no synced value is available yet.
        realized_pnl = sum(t["profit_loss"] for t in history)
        open_cost = sum(p["shares"] * p["buy_price"] for p in positions)
        computed_cash = initial + realized_pnl - open_cost

        cash = computed_cash  # default
        try:
            supabase = db.get_supabase_client()
            res = supabase.table("account_balances").select("ibkr_cash_balance").order("date", desc=True).limit(1).execute()
            if res.data and res.data[0].get("ibkr_cash_balance") is not None:
                cash = float(res.data[0]["ibkr_cash_balance"])
        except Exception:
            pass  # silently fall back to computed value

        
        fmp = FMPClient()
        portfolio_value = cash
        updated_positions = []

        # ── Enrich positions with company_name from the watchlist table ──────────
        # The watchlist table holds the most recent screener snapshot which includes
        # the human-readable company name (e.g. "NVIDIA Corporation" for NVDA).
        # We fetch all tickers in one round-trip to avoid N+1 queries.
        company_name_map = {}
        if positions:
            try:
                tickers = [p["ticker"] for p in positions]
                wl_res = db.get_supabase_client() \
                    .table("watchlist") \
                    .select("ticker, company_name") \
                    .in_("ticker", tickers) \
                    .order("created_at", desc=True) \
                    .execute()
                # Keep only the first (most recent) row per ticker
                for row in (wl_res.data or []):
                    t = row["ticker"]
                    if t not in company_name_map and row.get("company_name"):
                        company_name_map[t] = row["company_name"]
            except Exception as ex:
                print(f"Could not fetch company names from watchlist: {ex}")
        
        for pos in positions:
            ticker = pos['ticker']
            try:
                if fmp.is_configured():
                    # Get current price
                    quote = fmp.get_quote(ticker)
                    if quote and "price" in quote:
                        current_price = float(quote['price'])
                        db.update_position_price(ticker, current_price)
                        pos['current_price'] = current_price
            except Exception as ex:
                print(f"Could not update live price for {ticker}: {ex}")
                
            # Fall back to buy_price when FMP hasn't refreshed yet (e.g. positions just bought)
            # This prevents a 500 crash and shows 0% PnL until the next price cycle.
            display_price = pos['current_price'] or pos['buy_price']
            value = pos['shares'] * display_price
            portfolio_value += value
            
            pnl = value - (pos['shares'] * pos['buy_price'])
            pnl_pct = (display_price / pos['buy_price'] - 1.0) * 100.0
            
            pos['current_price'] = display_price   # ensure it's never None in response
            pos['value'] = round(value, 2)
            pos['pnl'] = round(pnl, 2)
            pos['pnl_pct'] = round(pnl_pct, 2)
            # Attach company name — falls back to ticker if watchlist has no entry
            pos['company_name'] = company_name_map.get(ticker, ticker)
            updated_positions.append(pos)
            
        unrealized_pnl = portfolio_value - (cash + sum(pos['shares'] * pos['buy_price'] for pos in positions))
        total_pnl = portfolio_value - initial
        total_pnl_pct = (portfolio_value / initial - 1.0) * 100.0
        invested_value = portfolio_value - cash
        
        win_rate = 0.0
        if history:
            wins = sum(1 for t in history if t['profit_loss'] > 0)
            win_rate = (wins / len(history)) * 100.0
            
        return {
            "summary": {
                "initial_balance": round(initial, 2),
                "cash_balance": round(cash, 2),
                "portfolio_value": round(portfolio_value, 2),
                "invested_value": round(invested_value, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2),
                "win_rate": round(win_rate, 2),
                "total_trades": len(history)
            },
            "positions": updated_positions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trades")
def get_trades():
    try:
        return db.get_trade_history()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cash_flows")
def get_cash_flows():
    try:
        return db.get_cash_flows()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/account_balances")
def get_account_balances():
    try:
        return db.get_account_balances()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/{ticker}/approve-rotation")
async def approve_rotation(ticker: str):
    """
    User approved a Tier 1 or Tier 2 rotation recommendation.

    Flow (immediate execution):
      1. Validate market hours (9:30 AM – 4:00 PM ET).
      2. Verify recommendation is still set (idempotency guard).
      3. Stop execution-agent container to free clientId=1.
      4. Run rotate_positions.py <TICKER> via docker exec.
      5. Restart execution-agent in the finally block (always).
      6. Clear rotation_recommendation in Supabase.

    Returns JSON with status, stdout, and stderr for the UI to display.
    """
    import subprocess
    from zoneinfo import ZoneInfo as _ZI

    ticker = ticker.upper().strip()
    now_et = datetime.datetime.now(_ZI("America/New_York"))

    # Guard: market hours only
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if not (market_open <= now_et <= market_close):
        raise HTTPException(
            status_code=400,
            detail=f"Market is closed (current ET time: {now_et.strftime('%H:%M')}). Rotation not executed."
        )

    # Idempotency guard: only proceed if recommendation is still set
    try:
        supabase = db.get_supabase_client()
        res = supabase.table("portfolio_positions") \
            .select("rotation_recommendation") \
            .eq("ticker", ticker) \
            .execute()
        if not res.data:
            raise HTTPException(status_code=404, detail=f"{ticker} not found in portfolio.")
        rec = res.data[0].get("rotation_recommendation")
        if not rec:
            raise HTTPException(
                status_code=409,
                detail=f"{ticker} has no pending rotation recommendation (may have already been executed or dismissed)."
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase check failed: {e}")

    COMPOSE_DIR = "/home/dietpi/docker/ai-trading-bot"
    stdout_log, stderr_log = "", ""

    try:
        # Step 1: Stop execution agent to free clientId=1
        subprocess.run(
            ["docker", "compose", "stop", "execution-agent"],
            cwd=COMPOSE_DIR, check=True, timeout=30,
            capture_output=True
        )

        # Step 2: Run rotation script for this ticker
        result = subprocess.run(
            ["docker", "compose", "run", "--rm",
             "-e", f"ROTATE_TICKER={ticker}",
             "execution-agent",
             "python3", "/app/rotate_positions.py", ticker],
            cwd=COMPOSE_DIR, timeout=180,
            capture_output=True, text=True
        )
        stdout_log = result.stdout
        stderr_log = result.stderr

        if result.returncode != 0:
            raise RuntimeError(
                f"rotate_positions.py exited with code {result.returncode}.\n"
                f"stderr: {stderr_log[-2000:]}"
            )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Always restart the execution agent, even if rotation failed
        subprocess.run(
            ["docker", "compose", "start", "execution-agent"],
            cwd=COMPOSE_DIR, timeout=30,
            capture_output=True
        )

    # Step 3: Clear the recommendation in Supabase
    try:
        supabase.table("portfolio_positions") \
            .update({"rotation_recommendation": None}) \
            .eq("ticker", ticker).execute()
    except Exception:
        pass  # non-fatal — rotation already executed

    return {
        "status":  "rotated",
        "ticker":  ticker,
        "stdout":  stdout_log[-3000:] if stdout_log else "",
        "stderr":  stderr_log[-500:]  if stderr_log else "",
    }


@app.post("/api/portfolio/{ticker}/dismiss-rotation")
def dismiss_rotation(ticker: str):
    """
    User dismissed the rotation recommendation.
    Clears rotation_recommendation in Supabase so the UI stops showing the alert.
    The position continues to be monitored — the recommendation may reappear
    at the next EOD cycle if conditions still hold.
    """
    ticker = ticker.upper().strip()
    try:
        supabase = db.get_supabase_client()
        supabase.table("portfolio_positions") \
            .update({"rotation_recommendation": None}) \
            .eq("ticker", ticker).execute()
        return {"status": "dismissed", "ticker": ticker}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





@app.get("/api/breakouts")
def get_breakouts():
    try:
        return db.get_daily_triggers()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/backtest")
def run_backtest_simulation(req: BacktestRequest):
    try:
        tickers = req.tickers
        if not tickers:
            tickers = db.get_watchlist()
            
        if not tickers:
            raise HTTPException(status_code=400, detail="Watchlist is empty. Cannot run backtest.")
            
        results = backtester.run_backtest(
            tickers=tickers,
            start_date_str=req.start_date,
            end_date_str=req.end_date,
            initial_capital=req.initial_capital,
            stop_loss_pct=req.stop_loss_pct,
            profit_target_pct=req.profit_target_pct,
            max_positions=req.max_positions
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/settings")
def get_settings():
    try:
        watchlist = db.get_setting("watchlist", "")
        stop_loss_pct = float(db.get_setting("stop_loss_pct", 7.0))
        profit_target_pct = float(db.get_setting("profit_target_pct", 25.0))
        initial_balance = float(db.get_setting("initial_balance", 100000.0))
        cash_balance = float(db.get_setting("cash_balance", 100000.0))
        fmp_api_key = db.get_setting("fmp_api_key", "")
        last_watchlist_gen_time = db.get_setting("last_watchlist_gen_time", "")
        
        return {
            "watchlist": watchlist,
            "stop_loss_pct": stop_loss_pct,
            "profit_target_pct": profit_target_pct,
            "initial_balance": initial_balance,
            "cash_balance": cash_balance,
            "fmp_api_key": fmp_api_key,
            "last_watchlist_gen_time": last_watchlist_gen_time
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/settings")
def update_settings(settings: SettingsUpdate):
    try:
        db.set_setting("watchlist", settings.watchlist)
        db.set_setting("stop_loss_pct", settings.stop_loss_pct)
        db.set_setting("profit_target_pct", settings.profit_target_pct)
        db.set_setting("initial_balance", settings.initial_balance)
        if settings.fmp_api_key is not None:
            db.set_setting("fmp_api_key", settings.fmp_api_key.strip())
        return {"status": "success", "message": "Settings updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/settings/reset")
def reset_portfolio_endpoint(confirm: str = ""):
    """
    Reset local paper-trading state only.
    Requires confirm=RESET_CONFIRMED query param as a safety guard against
    accidental or automated (scanner) triggers.
    NOTE: This endpoint does NOT affect live Supabase portfolio_positions — 
          those are managed exclusively by the execution-agent.
    """
    if confirm != "RESET_CONFIRMED":
        raise HTTPException(
            status_code=403,
            detail="Reset requires ?confirm=RESET_CONFIRMED query parameter."
        )
    try:
        db.reset_portfolio()
        return {"status": "success", "message": "Local paper-trading state reset. Live positions untouched."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock-history/{ticker}")
def get_stock_history(ticker: str):
    try:
        fmp = FMPClient()
        if not fmp.is_configured():
            raise HTTPException(status_code=400, detail="FMP API Key is not configured.")
            
        end_dt = datetime.datetime.now()
        start_dt = end_dt - datetime.timedelta(days=365)
        df = fmp.get_historical_prices(ticker.strip().upper(), start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for ticker {ticker}")
            
        history = []
        df['SMA50'] = df['Close'].rolling(50).mean()
        df['SMA200'] = df['Close'].rolling(200).mean()
        
        for date, row in df.iterrows():
            history.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row['Open']), 2),
                "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
                "volume": int(row['Volume']),
                "sma50": round(float(row['SMA50']), 2) if not pd.isna(row['SMA50']) else None,
                "sma200": round(float(row['SMA200']), 2) if not pd.isna(row['SMA200']) else None,
            })
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/benchmark_returns")
def get_benchmark_returns(from_date: str = None, to_date: str = None):
    """
    Return price stats for S&P 500 (SPY), Nasdaq 100 (QQQ), Russell 2000 (IWM),
    and S&P 500 Equal Weight (RSP) over the given date range.
    Each benchmark includes: period_return, ann_return, volatility, max_drawdown,
    and a daily_normalized series (starting at 100) for charting.
    """
    BENCHMARKS = [
        {"symbol": "SPY", "name": "S&P 500"},
        {"symbol": "QQQ", "name": "Nasdaq 100"},
        {"symbol": "IWM", "name": "Russell 2000"},
        {"symbol": "RSP", "name": "S&P 500 EW"},
    ]

    today = datetime.datetime.now(ZoneInfo("America/New_York")).date()
    if not to_date:
        to_date = today.isoformat()
    if not from_date:
        from_date = f"{today.year}-01-01"

    fmp = FMPClient()
    if not fmp.is_configured():
        return {"benchmarks": [], "error": "FMP API key not configured — set it in Settings."}

    results = []
    for b in BENCHMARKS:
        try:
            df = fmp.get_historical_prices(b["symbol"], from_date, to_date)
            if df.empty or len(df) < 2:
                results.append({"symbol": b["symbol"], "name": b["name"], "return": None, "error": "Insufficient data"})
                continue

            df = df.sort_index()  # oldest first

            start_price = float(df["Close"].iloc[0])
            end_price   = float(df["Close"].iloc[-1])
            n_days_cal  = max((df.index[-1] - df.index[0]).days, 1)

            period_return = (end_price / start_price - 1) * 100
            ann_return    = ((end_price / start_price) ** (365.0 / n_days_cal) - 1) * 100

            # Annualized volatility — std of daily returns * sqrt(252)
            daily_rets = df["Close"].pct_change().dropna()
            volatility = float(daily_rets.std()) * (252 ** 0.5) * 100

            # Max drawdown — largest peak-to-trough decline
            rolling_max  = df["Close"].cummax()
            drawdowns    = (df["Close"] - rolling_max) / rolling_max
            max_drawdown = float(drawdowns.min()) * 100  # negative number

            # Normalized price series for chart (start = 100)
            daily_normalized = [
                {"date": idx.strftime("%Y-%m-%d"), "value": round(float(close) / start_price * 100, 4)}
                for idx, close in zip(df.index, df["Close"])
            ]

            results.append({
                "symbol":           b["symbol"],
                "name":             b["name"],
                "return":           round(period_return, 2),
                "ann_return":       round(ann_return, 2),
                "volatility":       round(volatility, 2),
                "max_drawdown":     round(max_drawdown, 2),
                "daily_normalized": daily_normalized,
                "start_price":      round(start_price, 2),
                "end_price":        round(end_price, 2),
                "from_date":        df.index[0].strftime("%Y-%m-%d"),
                "to_date":          df.index[-1].strftime("%Y-%m-%d"),
            })
        except Exception as e:
            results.append({"symbol": b["symbol"], "name": b["name"], "return": None, "error": str(e)})

    return {"benchmarks": results, "from_date": from_date, "to_date": to_date}


import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Mount the React frontend built assets if they exist
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")

if os.path.exists(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        
    @app.get("/{catchall:path}")
    def serve_frontend(catchall: str):
        if catchall.startswith("api"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        index_path = os.path.join(frontend_dist, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Frontend index.html not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
