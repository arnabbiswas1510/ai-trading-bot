import pandas as pd
import numpy as np
import datetime
from database import get_watchlist
from fmp_client import FMPClient

def get_market_direction():
    """
    Analyzes ^GSPC (S&P 500) and ^IXIC (Nasdaq Composite) to determine general market health (M) using FMP.
    Returns a dict with status, moving averages, and current prices.
    """
    fmp = FMPClient()
    if not fmp.is_configured():
        return {
            "status": "FMP API Key Not Configured",
            "score": 0.0,
            "details": ["Please configure your FMP API Key in Settings to run the screener."],
            "indices": {}
        }

    indices = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC"}
    status_summary = []
    total_score = 15
    market_status = "Confirmed Uptrend"
    
    index_data = {}
    
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    for name, symbol in indices.items():
        try:
            df = fmp.get_historical_prices(symbol, start_str, end_str)
            if df.empty:
                # Try fallback index symbol names if needed (e.g. without ^ for some feeds, but FMP standard is ^GSPC)
                continue
                
            close = float(df['Close'].iloc[-1])
            sma50 = float(df['Close'].rolling(50).mean().iloc[-1])
            sma200 = float(df['Close'].rolling(200).mean().iloc[-1])
            
            above_50 = close > sma50
            above_200 = close > sma200
            sma50_above_200 = sma50 > sma200
            
            index_data[name] = {
                "price": round(close, 2),
                "sma50": round(sma50, 2),
                "sma200": round(sma200, 2),
                "above_50": above_50,
                "above_200": above_200,
                "golden_cross": sma50_above_200
            }
            
            if not above_200:
                status_summary.append(f"{name} is below 200-day SMA")
            elif not above_50:
                status_summary.append(f"{name} is below 50-day SMA but above 200-day SMA")
        except Exception as e:
            print(f"Error fetching market direction for {symbol} from FMP: {e}")
            
    num_indices = len(index_data)
    if num_indices > 0:
        below_200_count = sum(1 for data in index_data.values() if not data["above_200"])
        below_50_count = sum(1 for data in index_data.values() if not data["above_50"])
        
        if below_200_count > 0:
            market_status = "Market in Correction"
            total_score = 0
        elif below_50_count > 0:
            market_status = "Uptrend Under Pressure"
            total_score = 5
        else:
            market_status = "Confirmed Uptrend"
            total_score = 15
    else:
        # Fallback if indices couldn't be loaded (e.g., weekend/API limits)
        market_status = "Uptrend Under Pressure"
        total_score = 5
        status_summary.append("Indices data temporarily unavailable. Defaulting to Uptrend Under Pressure.")
            
    return {
        "status": market_status,
        "score": total_score,
        "details": status_summary if status_summary else ["All indices in strong uptrend"],
        "indices": index_data
    }

def calculate_rs_scores(watchlist, historical_data):
    """
    Calculates Relative Strength performance weighted:
    40% recent Q (last 3m), 20% Q2 (3-6m), 20% Q3 (6-9m), 20% Q4 (9-12m)
    """
    rs_raw_scores = {}
    
    for ticker in watchlist:
        if ticker not in historical_data or len(historical_data[ticker]) < 252:
            rs_raw_scores[ticker] = -999.0
            continue
            
        df = historical_data[ticker]
        try:
            p_now = df['Close'].iloc[-1]
            p_3m = df['Close'].iloc[-63]  # approx 63 trading days in 3 months
            p_6m = df['Close'].iloc[-126] # approx 126 trading days in 6 months
            p_9m = df['Close'].iloc[-189] # approx 189 trading days in 9 months
            p_12m = df['Close'].iloc[-252] # 252 trading days in 1 year
            
            perf_3m = (p_now / p_3m) - 1.0
            perf_6m = (p_3m / p_6m) - 1.0
            perf_9m = (p_6m / p_9m) - 1.0
            perf_12m = (p_9m / p_12m) - 1.0
            
            weighted_score = (perf_3m * 0.40) + (perf_6m * 0.20) + (perf_9m * 0.20) + (perf_12m * 0.20)
            rs_raw_scores[ticker] = weighted_score
        except Exception as e:
            print(f"Error calculating RS raw score for {ticker}: {e}")
            rs_raw_scores[ticker] = -999.0
            
    # Convert to percentile ranks (1-99)
    valid_tickers = [t for t, score in rs_raw_scores.items() if score > -900]
    valid_scores = [rs_raw_scores[t] for t in valid_tickers]
    
    rs_percentiles = {}
    if len(valid_scores) > 0:
        for t in watchlist:
            if rs_raw_scores[t] <= -900:
                rs_percentiles[t] = 1.0
                continue
            score = rs_raw_scores[t]
            rank = sum(1 for s in valid_scores if s <= score)
            percentile = (rank / len(valid_scores)) * 99.0
            rs_percentiles[t] = round(max(1.0, percentile), 1)
    else:
        for t in watchlist:
            rs_percentiles[t] = 50.0
            
    return rs_percentiles

def scan_ticker(ticker_symbol, rs_rating, market_m, hist_df=None):
    """
    Evaluates a single ticker against C, A, N, S, L, I, M using FMP.
    Returns a dict with overall scores and details.
    """
    fmp = FMPClient()
    if not fmp.is_configured():
        raise ValueError("FMP API Key is not configured.")

    # Get quote details
    quote = fmp.get_quote(ticker_symbol)
    
    # Get historical data
    if hist_df is None or hist_df.empty:
        try:
            end_dt = datetime.datetime.now()
            start_dt = end_dt - datetime.timedelta(days=365)
            hist_df = fmp.get_historical_prices(ticker_symbol, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        except Exception as e:
            print(f"Error downloading FMP history for {ticker_symbol}: {e}")
            hist_df = pd.DataFrame()
            
    if hist_df.empty:
        return {
            "ticker": ticker_symbol,
            "total_score": 0.0,
            "score_c": 0.0, "score_a": 0.0, "score_n": 0.0, "score_s": 0.0,
            "score_l": 0.0, "score_i": 0.0, "score_m": 0.0,
            "details": {"error": "No price history available from FMP"}
        }
        
    details = {}
    score_c = 0.0
    score_a = 0.0
    score_n = 0.0
    score_s = 0.0
    score_l = 0.0
    score_i = 0.0
    score_m = market_m["score"]
    
    # Get current price from quote or fallback to historical
    current_price = float(quote.get("price") or hist_df['Close'].iloc[-1])
    details['current_price'] = round(current_price, 2)
    
    # ------------------
    # C - Current Earnings (Max 15)
    # ------------------
    details['c_growth_yoy'] = 0.0
    details['c_rev_growth_yoy'] = 0.0
    details['c_eps_acceleration'] = False
    
    try:
        q_inc = fmp.get_income_statements(ticker_symbol, period="quarter", limit=5)
        if q_inc and len(q_inc) >= 5:
            # FMP returns quarterly data descending by date.
            # q0 = q_inc[0], q1 = q_inc[1], q4 (YoY) = q_inc[4]
            eps_q0 = float(q_inc[0].get("epsdiluted") or q_inc[0].get("eps") or 0)
            eps_q4 = float(q_inc[4].get("epsdiluted") or q_inc[4].get("eps") or 0)
            
            eps_q1 = float(q_inc[1].get("epsdiluted") or q_inc[1].get("eps") or 0)
            eps_q5 = float(q_inc[5].get("epsdiluted") or q_inc[5].get("eps") or 0) if len(q_inc) >= 6 else None
            
            if eps_q4 > 0:
                yoy_growth = (eps_q0 - eps_q4) / eps_q4
                details['c_growth_yoy'] = round(yoy_growth * 100.0, 1)
                if yoy_growth >= 0.25:
                    score_c += 8.0
                    score_c += min(4.0, (yoy_growth - 0.25) * 10.0)
                elif yoy_growth > 0:
                    score_c += max(0.0, yoy_growth * 32.0)
                    
                # Check acceleration
                if eps_q5 is not None and eps_q5 > 0:
                    prev_yoy_growth = (eps_q1 - eps_q5) / eps_q5
                    if yoy_growth > prev_yoy_growth:
                        details['c_eps_acceleration'] = True
                        score_c += 2.0
            
            rev_q0 = float(q_inc[0].get("revenue") or 0)
            rev_q4 = float(q_inc[4].get("revenue") or 0)
            if rev_q4 > 0:
                rev_growth = (rev_q0 - rev_q4) / rev_q4
                details['c_rev_growth_yoy'] = round(rev_growth * 100.0, 1)
                if rev_growth >= 0.25:
                    score_c += 3.0
                elif rev_growth > 0:
                    score_c += max(0.0, rev_growth * 12.0)
    except Exception as e:
        print(f"Error evaluating C for {ticker_symbol}: {e}")
        details['c_notes'] = "Failed to parse FMP quarterly statements"
        
    score_c = min(15.0, round(score_c, 1))
    
    # ------------------
    # A - Annual Earnings (Max 15)
    # ------------------
    details['a_eps_growth_cagr'] = 0.0
    details['a_roe'] = 0.0
    
    try:
        a_inc = fmp.get_income_statements(ticker_symbol, period="annual", limit=3)
        a_bal = fmp.get_balance_sheets(ticker_symbol, period="annual", limit=3)
        
        if a_inc and len(a_inc) >= 3:
            eps_y0 = float(a_inc[0].get("epsdiluted") or a_inc[0].get("eps") or 0)
            eps_y2 = float(a_inc[2].get("epsdiluted") or a_inc[2].get("eps") or 0)
            if eps_y2 > 0 and eps_y0 > 0:
                cagr = (eps_y0 / eps_y2) ** (0.5) - 1.0
                details['a_eps_growth_cagr'] = round(cagr * 100.0, 1)
                if cagr >= 0.20:
                    score_a += 8.0
                    if cagr >= 0.25:
                        score_a += 2.0
                elif cagr > 0:
                    score_a += max(0.0, cagr * 40.0)
                    
            # ROE calculation
            net_income = float(a_inc[0].get("netIncome") or 0)
            if a_bal and len(a_bal) > 0:
                equity = float(a_bal[0].get("totalStockholdersEquity") or a_bal[0].get("totalEquity") or 0)
                if equity > 0:
                    roe = net_income / equity
                    details['a_roe'] = round(roe * 100.0, 1)
                    if roe >= 0.17:
                        score_a += 5.0
                    elif roe > 0:
                        score_a += max(0.0, roe * 29.4)
    except Exception as e:
        print(f"Error evaluating A for {ticker_symbol}: {e}")
        details['a_notes'] = "Failed to parse FMP annual financials"
        
    score_a = min(15.0, round(score_a, 1))
    
    # ------------------
    # N - New Catalyst / Price High (Max 15)
    # ------------------
    try:
        # Distance to 52-week High (from FMP Quote or historical)
        max_52w = float(quote.get("yearHigh") or hist_df['Close'].max())
        dist_to_high = (max_52w - current_price) / max_52w
        details['n_52w_high'] = round(max_52w, 2)
        details['n_pct_from_high'] = round(dist_to_high * 100.0, 1)
        
        # Calculate moving averages
        sma50 = float(quote.get("priceAvg50") or hist_df['Close'].rolling(50).mean().iloc[-1])
        sma200 = float(quote.get("priceAvg200") or hist_df['Close'].rolling(200).mean().iloc[-1])
        details['sma50'] = round(sma50, 2)
        details['sma200'] = round(sma200, 2)
        
        if dist_to_high <= 0.15:
            score_n += 10.0
            if dist_to_high <= 0.05:
                score_n += 2.0
        elif dist_to_high <= 0.25:
            score_n += 5.0
            
        if current_price > sma50:
            score_n += 2.0
        if current_price > sma200:
            score_n += 1.0
    except Exception as e:
        print(f"Error evaluating N for {ticker_symbol}: {e}")
        
    score_n = min(15.0, round(score_n, 1))
    
    # ------------------
    # S - Supply and Demand (Max 15)
    # ------------------
    try:
        # Accumulation vs Distribution days
        avg_vol_50 = float(quote.get("avgVolume") or hist_df['Volume'].rolling(50).mean().iloc[-1])
        details['s_avg_volume'] = int(avg_vol_50)
        
        recent_df = hist_df.tail(20).copy()
        recent_df['pct_change'] = recent_df['Close'].pct_change()
        
        accumulation_days = 0
        distribution_days = 0
        
        for idx in range(1, len(recent_df)):
            row = recent_df.iloc[idx]
            vol = float(row['Volume'])
            chg = float(row['pct_change'])
            
            if vol > avg_vol_50 * 1.1:
                if chg > 0.005:
                    accumulation_days += 1
                elif chg < -0.005:
                    distribution_days += 1
                    
        details['s_acc_days'] = accumulation_days
        details['s_dist_days'] = distribution_days
        
        if accumulation_days > distribution_days:
            score_s += 8.0
            score_s += min(4.0, (accumulation_days - distribution_days) * 1.0)
        elif accumulation_days == distribution_days:
            score_s += 4.0
            
        # Float / Shares outstanding
        market_cap = quote.get("marketCap")
        price = quote.get("price")
        shares_out = int(market_cap / price) if market_cap and price else None
        if shares_out:
            details['s_shares_outstanding'] = shares_out
            if shares_out < 150_000_000:
                score_s += 3.0
            elif shares_out < 500_000_000:
                score_s += 1.5
        else:
            details['s_shares_outstanding'] = None
    except Exception as e:
        print(f"Error evaluating S for {ticker_symbol}: {e}")
        
    score_s = min(15.0, round(score_s, 1))
    
    # ------------------
    # L - Relative Strength (Max 15)
    # ------------------
    details['l_rs_rating'] = rs_rating
    
    if rs_rating >= 80:
        score_l += 10.0
        if rs_rating >= 90:
            score_l += 3.0
    elif rs_rating >= 60:
        score_l += 5.0
        
    # Check S&P 500 comparison
    try:
        end_dt = datetime.datetime.now()
        start_dt = end_dt - datetime.timedelta(days=100)
        sp_df = fmp.get_historical_prices("^GSPC", start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        if not sp_df.empty:
            sp_perf = (sp_df['Close'].iloc[-1] / sp_df['Close'].iloc[0]) - 1.0
            stock_perf = (hist_df['Close'].iloc[-1] / hist_df['Close'].iloc[-63]) - 1.0 if len(hist_df) >= 63 else 0.0
            if stock_perf > sp_perf:
                score_l += 2.0
    except Exception as e:
        print(f"Error evaluating relative index performance for {ticker_symbol}: {e}")
        
    score_l = min(15.0, round(score_l, 1))
    
    # ------------------
    # I - Institutional Sponsorship (Max 10)
    # ------------------
    details['i_held_percent_inst'] = 0.0
    try:
        market_cap = quote.get("marketCap")
        price = quote.get("price")
        shares_out = int(market_cap / price) if market_cap and price else None
        if shares_out:
            inst_pct = fmp.get_institutional_holdings_percentage(ticker_symbol, shares_out)
            details['i_held_percent_inst'] = round(inst_pct, 1)
            
            if 30.0 <= inst_pct <= 85.0:
                score_i = 10.0
            elif 10.0 <= inst_pct < 30.0 or 85.0 < inst_pct <= 95.0:
                score_i = 6.0
            else:
                score_i = 2.0
        else:
            details['i_held_percent_inst'] = None
            score_i = 5.0
    except Exception as e:
        print(f"Error evaluating I for {ticker_symbol}: {e}")
        score_i = 5.0
        
    score_i = min(10.0, round(score_i, 1))
    
    # ------------------
    # Summary
    # ------------------
    total_score = score_c + score_a + score_n + score_s + score_l + score_i + score_m
    total_score = min(100.0, round(total_score, 1))
    
    return {
        "ticker": ticker_symbol,
        "score_c": score_c,
        "score_a": score_a,
        "score_n": score_n,
        "score_s": score_s,
        "score_l": score_l,
        "score_i": score_i,
        "score_m": score_m,
        "total_score": total_score,
        "details": details
    }

def run_canslim_screener():
    """
    Scans the entire watchlist, updates scores in the SQLite database, and returns results using FMP.
    """
    watchlist = get_watchlist()
    if not watchlist:
        return []
        
    fmp = FMPClient()
    if not fmp.is_configured():
        raise ValueError("FMP API Key is not configured. Go to settings to set it.")

    # Get Market direction (M)
    market_m = get_market_direction()
    
    # Download daily history for all stocks (1y)
    historical_data = {}
    print(f"Downloading FMP historical data for {len(watchlist)} tickers...")
    
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    for ticker in watchlist:
        try:
            df = fmp.get_historical_prices(ticker, start_str, end_str)
            if not df.empty:
                historical_data[ticker] = df
        except Exception as e:
            print(f"Failed FMP download for {ticker}: {e}")
            
    # Calculate RS ratings
    rs_ratings = calculate_rs_scores(watchlist, historical_data)
    
    results = []
    for ticker in watchlist:
        try:
            rs_score = rs_ratings.get(ticker, 50.0)
            hist = historical_data.get(ticker, pd.DataFrame())
            scan_res = scan_ticker(ticker, rs_score, market_m, hist_df=hist)
            results.append(scan_res)
        except Exception as e:
            print(f"Critical error scanning {ticker}: {e}")
            
    return results
