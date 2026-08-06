"""Fetch 3y daily EOD bars for the full benchmark universe into data/hist_{sym}.json.

Uses curl via subprocess (TLS-intercepting proxy breaks Python HTTPS clients) and
a small thread pool. Measured headroom on this key: ~3.3 req/s sustained, no 429s.
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

FMP = os.environ["FMP"]
START, END = "2023-07-01", "2026-08-04"
WORKERS = 6

names = set()
for f in ("pass_names.txt", "broad_names.txt"):
    names |= {x.strip() for x in open(f) if x.strip()}
names.add("SPY")
names = sorted(names)

os.makedirs("data", exist_ok=True)


def fetch(sym):
    p = f"data/hist_{sym}.json"
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            if isinstance(d, list) and len(d) >= 300:
                return sym, len(d), "cached"
        except Exception:
            pass
    url = (f"https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={sym}&from={START}&to={END}&apikey={FMP}")
    for attempt in range(3):
        out = subprocess.run(["curl", "-s", "-m", "90", url],
                             capture_output=True, text=True).stdout
        try:
            d = json.loads(out)
        except json.JSONDecodeError:
            continue
        if isinstance(d, list) and len(d) >= 300:
            open(p, "w").write(out)
            return sym, len(d), "ok"
        if isinstance(d, dict):
            return sym, 0, f"err:{str(d)[:60]}"
    return sym, 0, "fail"


with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    results = list(ex.map(fetch, names))

ok = [r for r in results if r[2] in ("ok", "cached")]
bad = [r for r in results if r not in ok]
print(f"fetched={len(ok)}/{len(names)}  failed={len(bad)}")
for s, n, st in bad:
    print(f"  {s:8} {st}")
