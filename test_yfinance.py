import pandas as pd
import yfinance as yf

def test_yfinance():
    print("Testing chunked bulk download...")
    tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "GME", "AMC", "PLTR", "SOFI"]
    df = yf.download(tickers, period="1d", progress=False, ignore_tz=True)
    
    valid_tickers = []
    if not df.empty:
        for t in tickers:
            try:
                # yfinance returns Close with Tickers as columns under 'Close' if multiple tickers
                if 'Close' in df.columns and t in df['Close'].columns:
                    price = float(df['Close'][t].iloc[-1])
                    vol = float(df['Volume'][t].iloc[-1])
                    if price >= 10.0 and vol >= 100000:
                        valid_tickers.append(t)
            except Exception as e:
                pass
                
    print(f"Valid technical tickers: {valid_tickers}")
    
    # Test TTL Caching
    print("\nTesting TTL Cache & Income Statements...")
    for t in valid_tickers[:2]:
        print(f"Analyzing {t}...")
        ticker_obj = yf.Ticker(t)
        q_stmt = ticker_obj.quarterly_income_stmt
        if 'Basic EPS' in q_stmt.index:
            eps_row = q_stmt.loc['Basic EPS'].dropna()
            print(f"  {t} trailing EPS: {eps_row.tolist()[:4]}")
        else:
            print(f"  {t} missing Basic EPS")

if __name__ == "__main__":
    test_yfinance()
