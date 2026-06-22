import os
import requests
from dotenv import load_dotenv
import yahoo_fin.stock_info as si
import math
import asyncio

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

nasdaq_tickers = si.tickers_nasdaq()
other_tickers = si.tickers_other()
all_tickers = list(set(nasdaq_tickers + other_tickers))
all_tickers = [t.replace('-', '.') for t in all_tickers if type(t) == str]
print(f"Total tickers: {len(all_tickers)}")

chunk_size = 500
liquid_tickers = []

for i in range(0, len(all_tickers), chunk_size):
    chunk = all_tickers[i:i+chunk_size]
    url = f"https://financialmodelingprep.com/stable/quote?symbol={','.join(chunk)}&apikey={API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        for q in data:
            price = q.get('price', 0)
            avg_vol = q.get('avgVolume', 0)
            if price is not None and avg_vol is not None:
                if price > 15 and avg_vol > 400000:
                    liquid_tickers.append(q.get('symbol'))

print(f"Liquid tickers: {len(liquid_tickers)}")
if len(liquid_tickers) > 0:
    print("Sample:", liquid_tickers[:10])
