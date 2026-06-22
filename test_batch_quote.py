import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

symbols = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN"]
url = f"https://financialmodelingprep.com/stable/quote?symbol={','.join(symbols)}&apikey={API_KEY}"
res = requests.get(url)
print("Status:", res.status_code)
if res.status_code == 200:
    data = res.json()
    print("Count:", len(data))
    if len(data) > 0:
        print("First item keys:", list(data[0].keys()))
        print("Price:", data[0].get('price'), "AvgVol:", data[0].get('avgVolume'))
