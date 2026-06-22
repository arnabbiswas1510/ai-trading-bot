import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_active_trading_list():
    # Test stock/list
    url_stock_list = f"https://financialmodelingprep.com/api/v3/stock/list?apikey={API_KEY}"
    print(f"Testing: /api/v3/stock/list")
    res1 = requests.get(url_stock_list)
    print(f"Status: {res1.status_code}")
    print(res1.text[:200])
    print("-" * 50)
    
    # Test available-traded/list
    url_available = f"https://financialmodelingprep.com/api/v3/available-traded/list?apikey={API_KEY}"
    print(f"Testing: /api/v3/available-traded/list")
    res2 = requests.get(url_available)
    print(f"Status: {res2.status_code}")
    print(res2.text[:200])

if __name__ == "__main__":
    test_active_trading_list()
