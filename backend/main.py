from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import yfinance as yf
import datetime
import json

import database as db
import screener
import backtester

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

@app.get("/api/screener/results")
def get_screener_results():
    try:
        return db.get_screener_results()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/portfolio")
def get_portfolio():
    try:
        cash = float(db.get_setting("cash_balance", 100000.0))
        initial = float(db.get_setting("initial_balance", 100000.0))
        positions = db.get_positions()
        
        # Update current prices in real-time
        portfolio_value = cash
        updated_positions = []
        
        for pos in positions:
            ticker = pos['ticker']
            try:
                # Get current price
                stock = yf.Ticker(ticker)
                # fast price download
                price_df = stock.history(period="1d")
                if not price_df.empty:
                    current_price = float(price_df['Close'].iloc[-1])
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
        
        history = db.get_trade_history()
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
        # Fetch current price
        stock = yf.Ticker(ticker)
        df = stock.history(period="1d")
        if df.empty:
            raise HTTPException(status_code=400, detail=f"Invalid ticker: {ticker}")
            
        price = float(df['Close'].iloc[-1])
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
        # Fetch current price
        stock = yf.Ticker(ticker)
        df = stock.history(period="1d")
        if df.empty:
            raise HTTPException(status_code=400, detail=f"Invalid ticker: {ticker}")
            
        price = float(df['Close'].iloc[-1])
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
        
        return {
            "watchlist": watchlist,
            "stop_loss_pct": stop_loss_pct,
            "profit_target_pct": profit_target_pct,
            "initial_balance": initial_balance,
            "cash_balance": cash_balance
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
        stock = yf.Ticker(ticker.strip().upper())
        df = stock.history(period="1y")
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for ticker {ticker}")
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # Resample or format history
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
