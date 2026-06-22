import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_stock_screener():
    url = f"https://financialmodelingprep.com/api/v3/stock-screener?marketCapMoreThan=1000000000&volumeMoreThan=300000&priceMoreThan=15&apikey={API_KEY}"
    print(f"Requesting: {url.replace(API_KEY, 'HIDDEN_API_KEY')}")
    
    response = requests.get(url)
    print(f"Status Code: {response.status_code}")
    
    try:
        data = response.json()
        print("\nFMP Response:")
        import json
        print(json.dumps(data, indent=2))
    except Exception as e:
        print("Error parsing JSON:", e)

if __name__ == "__main__":
    test_stock_screener()
