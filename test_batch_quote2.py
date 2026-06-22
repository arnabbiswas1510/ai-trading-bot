import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

symbols = ["AAPL"] * 50
url = f"https://financialmodelingprep.com/api/v3/quote/{','.join(symbols)}?apikey={API_KEY}"
res = requests.get(url)
print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    print("Fetched:", len(data))
