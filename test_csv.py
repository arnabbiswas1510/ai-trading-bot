import requests

url = "https://www.ishares.com/us/products/239706/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': 'text/csv,application/csv,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}
res = requests.get(url, headers=headers)
print("Status:", res.status_code)
lines = res.text.split('\n')
for i, line in enumerate(lines[:20]):
    print(f"{i}: {line.strip()}")
