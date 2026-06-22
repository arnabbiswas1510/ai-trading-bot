import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_user_script():
    url = "https://financialmodelingprep.com/api/v3/stock-screener"
    # Wait, the user literally provided "https://financialmodelingprep.com"
    # I'll use exactly their URL first, but I will also add the endpoint they missed
    # Actually, I'll write EXACTLY what they asked to test.
    
    user_url = "https://financialmodelingprep.com"
    params = {
        "marketCapMoreThan": 1000000000,    # $1B minimum
        "volumeMoreThan": 300000,          # High liquidity
        "priceMoreThan": 15,                # Eliminate penny stocks
        "isEtf": "false",
        "isActivelyTrading": "true",
        "apikey": API_KEY
    }

    print(f"Testing URL: {user_url}")
    response = requests.get(user_url, params=params)

    if response.status_code == 200:
        try:
            candidates = response.json()
            print(f"Successfully retrieved {len(candidates)} active stock candidates.")
        except Exception as e:
            print(f"Status is 200, but failed to parse JSON: {e}")
            print(f"Response starts with: {response.text[:100]}")
    else:
        print(f"Error {response.status_code}: {response.text}")

if __name__ == "__main__":
    test_user_script()
