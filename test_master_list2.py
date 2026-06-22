import os
import requests
from dotenv import load_dotenv
import yahoo_fin.stock_info as si

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

nasdaq_tickers = si.tickers_nasdaq()
all_tickers = [t.replace('-', '.') for t in nasdaq_tickers if type(t) == str][:1000]

chunk = all_tickers[:100]
url = f"https://financialmodelingprep.com/stable/quote?symbol={','.join(chunk)}&apikey={API_KEY}"
res = requests.get(url)
print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    print("Fetched:", len(data))
    liquid = 0
    for q in data:
        price = q.get('price', 0)
        avg_vol = q.get('avgVolume', 0)
        if price is not None and avg_vol is not None and price > 15 and avg_vol > 400000:
            liquid += 1
    print("Liquid in chunk:", liquid)
else:
    print(res.text[:200])
