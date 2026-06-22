import os
import requests
from dotenv import load_dotenv

load_dotenv('.env')
API_KEY = os.environ.get("FMP_API_KEY")

def test_bulk():
    url = f"https://financialmodelingprep.com/api/v4/income-statement-bulk?year=2024&period=quarter&apikey={API_KEY}"
    res = requests.get(url)
    print("Status:", res.status_code)
    try:
        data = res.json()
        print(f"Total records in bulk: {len(data)}")
        if len(data) > 0:
            print("Sample:", data[0])
    except Exception as e:
        print("Error parsing json:", e)

if __name__ == "__main__":
    test_bulk()
