import pandas as pd
import requests
import io

url = "https://www.ishares.com/us/products/239706/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
print("Status:", response.status_code)
if response.status_code == 200:
    lines = response.text.split('\n')
    for i, line in enumerate(lines[:20]):
        print(f"{i}: {line.strip()}")
