"""Convert the fetched FMP JSON bars into the committed Parquet benchmark dataset.

Run from the harness directory containing data/hist_*.json.
Writes into <repo>/benchmark_data/.
"""
import json, glob, os, hashlib, datetime, sys
import pyarrow as pa
import pyarrow.parquet as pq

REPO = "/Users/e193757/Workspace/ai-trading-bot"
OUT = os.path.join(REPO, "benchmark_data")
os.makedirs(os.path.join(OUT, "daily"), exist_ok=True)

SCHEMA = pa.schema([
    ("symbol", pa.dictionary(pa.int32(), pa.string())),
    ("date",   pa.date32()),
    ("open",   pa.float32()),
    ("high",   pa.float32()),
    ("low",    pa.float32()),
    ("close",  pa.float32()),
    ("volume", pa.int64()),
    ("vwap",   pa.float32()),
])

rows = {k: [] for k in ("symbol", "date", "open", "high", "low", "close", "volume", "vwap")}
meta = {}

for p in sorted(glob.glob("data/hist_*.json")):
    sym = os.path.basename(p)[5:-5]
    try:
        d = json.load(open(p))
    except Exception:
        print(f"  skip unreadable {sym}")
        continue
    if not isinstance(d, list) or len(d) < 300:
        print(f"  skip short {sym} ({len(d) if isinstance(d,list) else '?'})")
        continue
    d = sorted(d, key=lambda r: r["date"])
    for r in d:
        rows["symbol"].append(sym)
        rows["date"].append(datetime.date.fromisoformat(r["date"][:10]))
        for f in ("open", "high", "low", "close"):
            rows[f].append(float(r.get(f) or 0.0))
        rows["volume"].append(int(r.get("volume") or 0))
        rows["vwap"].append(float(r.get("vwap") or 0.0))
    meta[sym] = {"rows": len(d), "start": d[0]["date"][:10], "end": d[-1]["date"][:10]}

tbl = pa.Table.from_pydict(rows, schema=SCHEMA)
# Rows are appended in sorted(glob) symbol order, each symbol's bars pre-sorted by
# date, so the table is already ordered by (symbol, date). Arrow cannot sort a
# dictionary-encoded column, so we rely on that construction order.
dest = os.path.join(OUT, "daily", "us_equities_daily.parquet")
pq.write_table(tbl, dest, compression="zstd", compression_level=9,
               use_dictionary=["symbol"], write_statistics=True)

size = os.path.getsize(dest)
digest = hashlib.sha256(open(dest, "rb").read()).hexdigest()
alldates = sorted({m["start"] for m in meta.values()} | {m["end"] for m in meta.values()})

manifest = {
    "dataset": "us_equities_daily",
    "vendor": "financialmodelingprep.com",
    "endpoint": "/stable/historical-price-eod/full",
    "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "bar_interval": "1d",
    "price_basis": "as-reported split/dividend adjusted by vendor at fetch time",
    "symbols": len(meta),
    "rows": tbl.num_rows,
    "date_min": min(m["start"] for m in meta.values()),
    "date_max": max(m["end"] for m in meta.values()),
    "file": "daily/us_equities_daily.parquet",
    "bytes": size,
    "sha256": digest,
    "per_symbol": meta,
}
json.dump(manifest, open(os.path.join(OUT, "MANIFEST.json"), "w"), indent=1, sort_keys=True)

src = sum(os.path.getsize(f) for f in glob.glob("data/hist_*.json"))
print(f"symbols  {len(meta)}")
print(f"rows     {tbl.num_rows:,}")
print(f"range    {manifest['date_min']} .. {manifest['date_max']}")
print(f"source   {src/1e6:.1f} MB JSON")
print(f"parquet  {size/1e6:.2f} MB  ({src/size:.0f}x smaller)")
print(f"sha256   {digest[:16]}...")
