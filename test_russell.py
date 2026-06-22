import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get('FMP_API_KEY')
if not API_KEY:
    print("NO API KEY")
    exit()

endpoints = [
    "/stable/russell-1000-constituent",
    "/api/v3/russell-1000-constituent",
    "/api/v3/historical/russell_1000_constituent",
    "/stable/sp500-constituent" # Just to verify
]

for ep in endpoints:
    url = f"https://financialmodelingprep.com{ep}?apikey={API_KEY}"
    res = requests.get(url)
    print(f"Endpoint: {ep}")
    print(f"Status: {res.status_code}")
    try:
        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            print(f"Got {len(data)} items. First item: {data[0]}")
        else:
            print("Empty list or dict")
    except:
        print("Not JSON")
    print("-" * 20)
