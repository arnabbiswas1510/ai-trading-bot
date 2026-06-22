import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

endpoints = [
    "/api/v3/stock/list",
    "/api/v3/available-traded/list",
    "/stable/stock/list"
]

for ep in endpoints:
    url = f"https://financialmodelingprep.com{ep}?apikey={API_KEY}"
    res = requests.get(url)
    print(f"Status for {ep}:", res.status_code)
    if res.status_code == 200:
        data = res.json()
        print(f"Count:", len(data))
        if len(data) > 0:
            print("Sample keys:", list(data[0].keys()))
            print("Sample 1:", data[0])
