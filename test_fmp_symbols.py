import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")
if API_KEY:
    API_KEY = API_KEY.strip("'\"")

for exchange in ["NYSE", "NASDAQ"]:
    url = f"https://financialmodelingprep.com/api/v3/symbol/{exchange}?apikey={API_KEY}"
    res = requests.get(url)
    print(f"Status for {exchange}:", res.status_code)
    if res.status_code == 200:
        data = res.json()
        print(f"{exchange} count:", len(data))
        if len(data) > 0:
            print("Sample keys:", list(data[0].keys()))
            print("Sample 1:", data[0])
            break
