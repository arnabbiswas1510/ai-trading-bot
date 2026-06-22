import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_stable_growth():
    ticker = "UBER"
    url = f"https://financialmodelingprep.com/stable/financial-growth?symbol={ticker}&limit=4&apikey={API_KEY}"
    res = requests.get(url)
    print("Status:", res.status_code)
    try:
        print("JSON:", res.json())
    except:
        print("Text:", res.text)

if __name__ == "__main__":
    test_stable_growth()
