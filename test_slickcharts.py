import pandas as pd
import requests

url = "https://www.slickcharts.com/russell1000"
headers = {'User-Agent': 'Mozilla/5.0'}
response = requests.get(url, headers=headers)
print(response.status_code)
dfs = pd.read_html(response.text)
for df in dfs:
    if 'Symbol' in df.columns:
        tickers = df['Symbol'].tolist()
        print("Found", len(tickers))
        print(tickers[:5])
        break
