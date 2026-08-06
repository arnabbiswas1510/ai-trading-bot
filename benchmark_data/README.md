# Committed benchmark price dataset

Offline, version-controlled market data so strategy benchmarks run **fast, free,
reproducible, and without FMP rate limiting**.

Before this existed, every backtest session re-fetched ~54 MB of JSON from FMP,
took ~20 minutes, hit the daily request cap, and produced results that could not
be compared across sessions because the universe silently varied with whatever
fetches happened to succeed.

## Contents

| file | what |
|---|---|
| `daily/us_equities_daily.parquet` | 313 US equities, daily OHLCV + VWAP, 2023-07-03 .. 2026-08-04 |
| `MANIFEST.json` | provenance, per-symbol row counts and date ranges, SHA-256 of the parquet |

**4.39 MB** on disk — 12x smaller than the equivalent raw JSON (54.1 MB), via
zstd-9 with dictionary-encoded symbols.

## Usage

```python
import sys; sys.path.insert(0, "research")
from bardata import daily, symbols, coverage

print(coverage())              # 313 symbols, 239,661 rows, 2023-07-03..2026-08-04
bars = daily("NVDA")           # list[dict], ascending by date
bars[-1]["close"]
```

`daily()` returns exactly the shape the raw FMP
`/stable/historical-price-eod/full` response had, so existing harness code works
unchanged. Cold load is ~1.2s for the whole dataset; lookups are then in-memory.

## Universe

Union of the two benchmark universes plus SPY:

- `research/pass_names.txt` (79) — names passing the current CAN SLIM fundamental
  screen. The "growth" universe used for most tuning.
- `research/broad_names.txt` (259) — large-cap broad universe, used as an
  out-of-universe robustness check.

Five tickers could not be retrieved (delisted, renamed, or symbol-format issues)
and are absent: `CBRS`, `HONA`, `MDLN`, `MOG.A`, `SPCX`.

## Rebuilding / extending

```bash
cd research
FMP=<key> python3 fetch_daily.py     # resumable; skips anything already cached
python3 to_parquet.py                # rewrites the parquet + MANIFEST.json
```

`fetch_daily.py` is resumable by design — FMP's daily request cap will interrupt
a cold fetch of the full universe, and re-running the next day fills the gaps.
Measured throughput on the current plan: ~3.3 req/s sustained with no 429s, but a
daily cap around a few hundred requests. There are **no bulk endpoints** on this
plan (`batch-quote` and `batch-eod` both return HTTP 402), so it is strictly one
request per symbol.

## Known limitations

Read these before drawing conclusions from anything built on this dataset.

- **Survivorship and look-ahead bias.** `pass_names.txt` is the set of names that
  pass the fundamental screen *today*, replayed backwards over three years.
  Companies that qualified in 2023 and later failed are absent. This biases
  results optimistic. Fixing it properly needs point-in-time fundamentals
  (Norgate, Sharadar, CRSP/Compustat PIT) or forward-accumulated weekly snapshots
  of the live screener output.
- **Vendor-adjusted prices.** Values are split/dividend adjusted *as of the fetch
  date*. FMP restates retroactively, so a future re-fetch will not reproduce
  these bytes exactly. This is why `MANIFEST.json` pins a SHA-256 — cite it
  alongside the code commit when reporting a result.
- **Daily bars only.** Intraday exit rules (the Intraday Loss Minimiser, the
  Armed Trailing Exit, the Day 0-2 kill-switch) cannot be modelled faithfully
  here. Those need 5-minute bars, which are a much larger fetch — see below.
- **Universe composition is a live variable.** Dropping 5 of 80 growth names
  moved headline CAGR by 7.5pp and *reversed* the sign of one config comparison.
  Always pin the universe when comparing results. This dataset exists largely to
  make that possible.

## Not yet included: 5-minute bars

Intraday questions need `historical-chart/5min`, which FMP caps at ~468 bars per
response (~6 trading days). One year for the 79-name growth universe is roughly
**3,300 requests** — many days of budget at the current daily cap, so it is not
worth fetching wholesale.

The practical approach is to fetch 5-minute bars only in windows around breakout
signals, which is where every intraday rule actually binds. That reduces one year
to a few hundred requests. Not yet built.

If intraday work becomes central, the right answer is to change vendor rather
than grind against this cap: **Polygon.io flat files** (bulk S3 minute aggregates,
no per-request limits), **Databento**, or **Norgate** (which also fixes the
survivorship problem above).
