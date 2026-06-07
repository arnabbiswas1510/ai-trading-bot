from fmp_client import FMPClient
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def run_backtest(tickers, start_date_str, end_date_str, initial_capital=100000.0, stop_loss_pct=7.0, profit_target_pct=25.0, max_positions=5):
    """
    Runs a historical simulation of a breakout CAN SLIM-inspired technical strategy using FMP data.
    """
    
    # Parse dates
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    
    fmp = FMPClient()
    if not fmp.is_configured():
        raise ValueError("FMP API Key is not configured. Go to settings to set it.")
        
    # Download S&P 500 for market filter
    sp500_df = fmp.get_historical_prices("^GSPC", start_date_str, end_date_str)
    if sp500_df.empty:
        raise ValueError("Could not download index data (^GSPC) from FMP.")
    sp500_df['SMA200'] = sp500_df['Close'].rolling(200).mean()
    
    # Download data for all tickers
    data = {}
    for t in tickers:
        try:
            # Download extra historical days (approx 1 year prior) to compute 200 SMA right from start_date
            dl_start = (start_dt - timedelta(days=365)).strftime("%Y-%m-%d")
            df = fmp.get_historical_prices(t, dl_start, end_date_str)
            if not df.empty:
                # Calculate indicators
                df['SMA50'] = df['Close'].rolling(50).mean()
                df['SMA200'] = df['Close'].rolling(200).mean()
                df['VolSMA50'] = df['Volume'].rolling(50).mean()
                df['High20'] = df['High'].rolling(20).max().shift(1) # previous 20-day high
                
                # Filter to only the requested backtest window
                df = df.loc[start_date_str:end_date_str]
                data[t] = df
        except Exception as e:
            print(f"Error fetching data for backtest of {t}: {e}")
            
    # Combine all trading dates
    all_dates = sorted(list(sp500_df.index))
    
    # Portfolio State
    cash = initial_capital
    positions = {} # ticker: {shares, buy_price, buy_date, stop_loss, profit_target}
    trades = []
    equity_history = []
    
    # Simulation loop
    for current_date in all_dates:
        date_str = current_date.strftime("%Y-%m-%d")
        
        # 1. Update/Close active positions first (Exit checks)
        tickers_to_close = []
        for ticker, pos in positions.items():
            if ticker not in data or current_date not in data[ticker].index:
                continue
                
            day_data = data[ticker].loc[current_date]
            high = float(day_data['High'])
            low = float(day_data['Low'])
            close = float(day_data['Close'])
            sma50 = float(day_data['SMA50'])
            
            # Check Stop Loss
            if low <= pos['stop_loss']:
                # Sold at stop loss
                exit_price = pos['stop_loss']
                pnl = (exit_price - pos['buy_price']) * pos['shares']
                pct = (exit_price / pos['buy_price'] - 1.0) * 100.0
                trades.append({
                    "ticker": ticker,
                    "shares": pos['shares'],
                    "buy_price": round(pos['buy_price'], 2),
                    "buy_date": pos['buy_date'],
                    "sell_price": round(exit_price, 2),
                    "sell_date": date_str,
                    "profit_loss": round(pnl, 2),
                    "percent_return": round(pct, 2),
                    "exit_reason": "Stop Loss"
                })
                cash += pos['shares'] * exit_price
                tickers_to_close.append(ticker)
                
            # Check Profit Target
            elif high >= pos['profit_target']:
                # Sold at profit target
                exit_price = pos['profit_target']
                pnl = (exit_price - pos['buy_price']) * pos['shares']
                pct = (exit_price / pos['buy_price'] - 1.0) * 100.0
                trades.append({
                    "ticker": ticker,
                    "shares": pos['shares'],
                    "buy_price": round(pos['buy_price'], 2),
                    "buy_date": pos['buy_date'],
                    "sell_price": round(exit_price, 2),
                    "sell_date": date_str,
                    "profit_loss": round(pnl, 2),
                    "percent_return": round(pct, 2),
                    "exit_reason": "Profit Target"
                })
                cash += pos['shares'] * exit_price
                tickers_to_close.append(ticker)
                
            # Trailing Exit: Close below 50-day moving average
            elif close < sma50:
                exit_price = close
                pnl = (exit_price - pos['buy_price']) * pos['shares']
                pct = (exit_price / pos['buy_price'] - 1.0) * 100.0
                trades.append({
                    "ticker": ticker,
                    "shares": pos['shares'],
                    "buy_price": round(pos['buy_price'], 2),
                    "buy_date": pos['buy_date'],
                    "sell_price": round(exit_price, 2),
                    "sell_date": date_str,
                    "profit_loss": round(pnl, 2),
                    "percent_return": round(pct, 2),
                    "exit_reason": "Closed Below 50 MA"
                })
                cash += pos['shares'] * exit_price
                tickers_to_close.append(ticker)
                
        for t in tickers_to_close:
            positions.pop(t)
            
        # 2. Check Market Condition (M)
        market_bullish = True
        if current_date in sp500_df.index:
            sp_close = float(sp500_df.loc[current_date]['Close'])
            sp_sma200 = float(sp500_df.loc[current_date]['SMA200'])
            market_bullish = sp_close > sp_sma200
            
        # 3. Scan for new entries if we have empty slots
        if market_bullish and len(positions) < max_positions:
            available_slots = max_positions - len(positions)
            candidates = []
            
            # Find all breakout setups on this day
            for ticker in tickers:
                if ticker in positions:
                    continue
                if ticker not in data or current_date not in data[ticker].index:
                    continue
                    
                df = data[ticker]
                # We need data up to yesterday to check breakout triggers today
                loc_idx = df.index.get_loc(current_date)
                if loc_idx < 1:
                    continue
                    
                day_data = df.iloc[loc_idx]
                prev_day = df.iloc[loc_idx - 1]
                
                close = float(day_data['Close'])
                high = float(day_data['High'])
                vol = float(day_data['Volume'])
                sma50 = float(day_data['SMA50'])
                sma200 = float(day_data['SMA200'])
                vol_sma = float(day_data['VolSMA50'])
                prev_high20 = float(day_data['High20'])
                
                # Check indicators availability
                if pd.isna(sma50) or pd.isna(sma200) or pd.isna(vol_sma) or pd.isna(prev_high20):
                    continue
                    
                # Technical breakout rules:
                # - High breaks above yesterday's 20-day high
                # - Close > SMA50 and Close > SMA200
                # - Volume today > 1.4x of 50-day average volume
                is_breakout = high > prev_high20
                is_above_ma = close > sma50 and close > sma200
                is_high_volume = vol > vol_sma * 1.4
                
                if is_breakout and is_above_ma and is_high_volume:
                    # Score candidates by how close they are to 52w high (relative strength proxy)
                    max_52w = df['Close'].iloc[max(0, loc_idx-252):loc_idx+1].max()
                    distance_from_high = (max_52w - close) / max_52w
                    candidates.append((ticker, close, distance_from_high))
            
            # Sort candidates by proximity to 52w high (closest first)
            candidates.sort(key=lambda x: x[2])
            
            # Buy candidates up to available slots
            for ticker, buy_price, _ in candidates[:available_slots]:
                # Position Sizing: Equal allocation of the portfolio size
                allocation = (cash + sum(pos['shares'] * data[pos_t].loc[current_date]['Close'] for pos_t, pos in positions.items())) / max_positions
                # Ensure we don't spend more cash than we have
                allocation = min(allocation, cash)
                
                if allocation < 500: # ignore tiny trades
                    continue
                    
                shares = int(allocation // buy_price)
                if shares <= 0:
                    continue
                    
                stop_loss = buy_price * (1.0 - (stop_loss_pct / 100.0))
                profit_target = buy_price * (1.0 + (profit_target_pct / 100.0))
                
                positions[ticker] = {
                    "shares": shares,
                    "buy_price": buy_price,
                    "buy_date": date_str,
                    "stop_loss": stop_loss,
                    "profit_target": profit_target
                }
                cash -= shares * buy_price
                
        # 4. Record Daily Equity Value
        current_equity = cash
        for ticker, pos in positions.items():
            if ticker in data and current_date in data[ticker].index:
                curr_price = float(data[ticker].loc[current_date]['Close'])
                current_equity += pos['shares'] * curr_price
            else:
                current_equity += pos['shares'] * pos['buy_price']
                
        equity_history.append({
            "date": date_str,
            "equity": round(current_equity, 2),
            "cash": round(cash, 2)
        })
        
    # Summarize Results
    final_equity = equity_history[-1]['equity'] if equity_history else initial_capital
    total_return_pct = ((final_equity / initial_capital) - 1.0) * 100.0
    
    # Calculate Max Drawdown
    equity_series = pd.Series([h['equity'] for h in equity_history])
    drawdown = 0.0
    if not equity_series.empty:
        rolling_max = equity_series.cummax()
        drawdowns = (equity_series - rolling_max) / rolling_max
        drawdown = float(drawdowns.min() * 100.0)
        
    # Calculate Win Rate
    wins = [t for t in trades if t['profit_loss'] > 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    
    # Calculate S&P 500 Buy & Hold return
    sp_start_val = float(sp500_df['Close'].iloc[0])
    sp_end_val = float(sp500_df['Close'].iloc[-1])
    sp_return_pct = ((sp_end_val / sp_start_val) - 1.0) * 100.0
    
    return {
        "summary": {
            "initial_capital": round(initial_capital, 2),
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown": round(drawdown, 2),
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(trades) - len(wins),
            "win_rate": round(win_rate, 2),
            "sp500_return_pct": round(sp_return_pct, 2)
        },
        "trades": trades,
        "equity_curve": equity_history
    }
