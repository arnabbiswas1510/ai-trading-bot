from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import datetime
import json
import asyncio
import pandas as pd

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

class TradeOrder(BaseModel):
    ticker: str
    shares: int

class SellOrder(BaseModel):
    ticker: str
    reason: str

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
        
        # Calculate cash balance dynamically based on initial balance, trade history, and active positions
        realized_pnl = sum(t["profit_loss"] for t in history)
        open_cost = sum(p["shares"] * p["buy_price"] for p in positions)
        cash = initial + realized_pnl - open_cost
        
        fmp = FMPClient()
        portfolio_value = cash
        updated_positions = []
        
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
                
            value = pos['shares'] * pos['current_price']
            portfolio_value += value
            
            pnl = value - (pos['shares'] * pos['buy_price'])
            pnl_pct = (pos['current_price'] / pos['buy_price'] - 1.0) * 100.0
            
            pos['value'] = round(value, 2)
            pos['pnl'] = round(pnl, 2)
            pos['pnl_pct'] = round(pnl_pct, 2)
            updated_positions.append(pos)
            
        unrealized_pnl = portfolio_value - (cash + sum(pos['shares'] * pos['buy_price'] for pos in positions))
        total_pnl = portfolio_value - initial
        total_pnl_pct = (portfolio_value / initial - 1.0) * 100.0
        
        win_rate = 0.0
        if history:
            wins = sum(1 for t in history if t['profit_loss'] > 0)
            win_rate = (wins / len(history)) * 100.0
            
        return {
            "summary": {
                "initial_balance": round(initial, 2),
                "cash_balance": round(cash, 2),
                "portfolio_value": round(portfolio_value, 2),
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

@app.post("/api/portfolio/buy")
def buy_stock(order: TradeOrder):
    try:
        ticker = order.ticker.strip().upper()
        fmp = FMPClient()
        if not fmp.is_configured():
            raise HTTPException(status_code=400, detail="FMP API Key is not configured. Please set it in Settings.")
            
        quote = fmp.get_quote(ticker)
        if not quote or "price" not in quote:
            raise HTTPException(status_code=400, detail=f"Invalid ticker or no price data: {ticker}")
            
        price = float(quote['price'])
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        db.buy_position(ticker, order.shares, price, date_str)
        return {"status": "success", "message": f"Bought {order.shares} shares of {ticker} at ${price:.2f}"}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/portfolio/sell")
def sell_stock(order: SellOrder):
    try:
        ticker = order.ticker.strip().upper()
        fmp = FMPClient()
        if not fmp.is_configured():
            raise HTTPException(status_code=400, detail="FMP API Key is not configured. Please set it in Settings.")
            
        quote = fmp.get_quote(ticker)
        if not quote or "price" not in quote:
            raise HTTPException(status_code=400, detail=f"Invalid ticker or no price data: {ticker}")
            
        price = float(quote['price'])
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        db.sell_position(ticker, price, date_str, order.reason)
        return {"status": "success", "message": f"Sold {ticker} at ${price:.2f} due to: {order.reason}"}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trades")
def get_trades():
    try:
        return db.get_trade_history()
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
def reset_portfolio_endpoint():
    try:
        db.reset_portfolio()
        return {"status": "success", "message": "Portfolio and trade history reset successfully"}
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
