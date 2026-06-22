import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_negative_base():
    ticker = "UBER"
    
    url = f"https://financialmodelingprep.com/api/v3/financial-growth/{ticker}?period=quarter&limit=4&apikey={API_KEY}"
    res = requests.get(url).json()
    print("Growth:", res)

    url2 = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}?period=quarter&limit=4&apikey={API_KEY}"
    res2 = requests.get(url2).json()
    if isinstance(res2, list) and len(res2) >= 2:
        curr_eps = res2[0].get("eps", 0)
        prev_eps = res2[1].get("eps", 0)
        print(f"Income Statement EPS: Current={curr_eps}, Previous={prev_eps}")

if __name__ == "__main__":
    test_negative_base()
