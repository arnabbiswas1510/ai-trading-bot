import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ.get("FMP_API_KEY")

endpoints = [
    "/api/v3/russell-1000-constituent",
    "/api/v3/historical/russell_1000_constituent",
    "/stable/russell-1000-constituent"
]

for ep in endpoints:
    url = f"https://financialmodelingprep.com{ep}?apikey={API_KEY}"
    print(f"Trying {ep}...")
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        print(f"Success! Fetched {len(data)} items.")
        if len(data) > 0:
            print(f"First item: {data[0]}")
    else:
        print(f"Failed with status: {res.status_code}")
