# Commit a benchmark price dataset instead of re-fetching from FMP

- **Date:** 2026-08-05
- **Status:** Accepted

## Problem

Every strategy-benchmarking session began by re-downloading three years of daily
bars from FMP, one HTTP request per symbol. This was slow (~20 minutes), hit the
vendor's daily request cap partway through, and — worst of all — was **not
reproducible**.

The reproducibility failure was not theoretical. During this session an
interrupted fetch produced a 75-name universe instead of 80. That single
difference moved headline CAGR by 7.5pp and **reversed the sign of a config
comparison**, which would have led to the opposite recommendation on the EMA-21
exit. See `2026-08-05_paired-bootstrap-fix-noise-floor.md`.

Compounding this, the analysis harness itself lived only in scratch session
storage, so each session rebuilt it from scratch and could not verify earlier
numbers.

## Decision

Commit the market data and the harness to the repository.

```
benchmark_data/
  daily/us_equities_daily.parquet   313 symbols, 239,661 rows, 2023-07-03..2026-08-04
  MANIFEST.json                     provenance, per-symbol coverage, SHA-256
  README.md                         usage + limitations
research/
  bardata.py        read-only loader; returns the exact shape the FMP JSON had
  breakout_bt.py    breakout detection + indicators (now reads bardata, not HTTP)
  port_sim.py       slot-constrained portfolio simulation
  boot_fixed.py     paired stationary block bootstrap (+ the old buggy method)
  rerun.py          config comparison table
  fetch_daily.py    resumable vendor fetch (only needed to extend the dataset)
  to_parquet.py     JSON -> parquet + manifest
  pass_names.txt / broad_names.txt   the two pinned universes
```

## Format

**Parquet, zstd-9, dictionary-encoded symbol column, float32 prices.**

| | size |
|---|---|
| raw vendor JSON | 54.1 MB |
| committed parquet | **4.39 MB** (12x smaller) |

4.39 MB is comfortably within normal git limits, so no Git LFS or external object
store is needed. Cold load of the entire dataset is ~1.2s; subsequent lookups are
in-memory. A full 10-config comparison with 2000 bootstrap replicates each now
runs end to end in **20 seconds with zero network calls**, against ~20 minutes and
a rate-limit wall before.

float32 was chosen over float64 because ~7 significant digits is far beyond what
matters for backtest prices and it roughly halves the numeric payload. Verified:
the parquet-backed harness reproduces the previously published +27.7% baseline
exactly.

## Why not the alternatives

- **Keep re-fetching** — the status quo that caused the defect above.
- **Commit the raw JSON** — 54 MB of poorly-compressing text, 12x the size, with
  no schema or type guarantees.
- **Git LFS / DVC / object store** — real answers at tens of GB. At 4.39 MB they
  add setup and a second source of truth for no benefit.
- **SQLite** — workable, but parquet is columnar, compresses far better, and is
  directly queryable by DuckDB/pandas/Arrow without a schema migration story.

## Reproducibility contract

`MANIFEST.json` pins a SHA-256 of the parquet. A benchmark result should be cited
as **(code commit, dataset SHA-256, config)**. This matters because FMP restates
split/dividend adjustments retroactively — a future re-fetch will *not* reproduce
these bytes, and silently comparing across vendor restatements is exactly the
class of bug this decision exists to prevent.

## What is deliberately excluded

**5-minute intraday bars.** FMP caps `historical-chart/5min` at ~468 bars per
response (~6 trading days), so one year for the 79-name growth universe is roughly
3,300 requests — many days of budget at the current cap. There are no bulk
endpoints on this plan (`batch-quote` and `batch-eod` both return HTTP 402).

Consequence: intraday rules — the Intraday Loss Minimiser, the Armed Trailing
Exit, and the Day 0-2 kill-switch — still cannot be modelled faithfully. When that
becomes the priority, the two options are (a) fetch 5-minute bars only in windows
around breakout signals, cutting one year to a few hundred requests, or (b) change
vendor: Polygon.io flat files (bulk S3 minute aggregates, no per-request limits),
Databento, or Norgate.

**Point-in-time fundamentals.** The growth universe is still the set of names
passing the screen *today*, replayed backwards — survivorship and look-ahead bias
that this dataset does not fix and that makes absolute returns optimistic. The
real fix is a PIT fundamentals vendor (Norgate, Sharadar, CRSP/Compustat) or
accumulating weekly snapshots of the live screener output going forward.

Both limitations are documented in `benchmark_data/README.md` so they are not
rediscovered later.

## Housekeeping

`graphify-out/cache/` and graphify's dated backup snapshot directories are now
gitignored. A graphify version bump from 0.9.24 to 0.9.28 during this session
deleted and recreated 103 cached AST files, which is pure churn with no
information content. The working graph (`graph.json`, `graph.html`,
`GRAPH_REPORT.md`, `manifest.json`, label files) remains tracked.
