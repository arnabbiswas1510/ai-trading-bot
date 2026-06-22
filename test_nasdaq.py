import requests
import pandas as pd

def test_nasdaq():
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/"
    }
    
    print("Fetching from Nasdaq API...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            rows = data.get('data', {}).get('rows', [])
            print(f"Successfully retrieved {len(rows)} records.")
            if rows:
                print("Sample:", rows[0])
        else:
            print(f"Failed: {response.text[:200]}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_nasdaq()
