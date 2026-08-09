# Fundamental Screener

The first gate into the system. A name that fails here is invisible to everything
downstream — the technical screener never evaluates it, and it can never be bought.

**Source:** `tv_api_screener.py` · **Schedule:** Mon–Fri, 21:00 UTC · **Output:** `watchlist`

---

## Purpose

CAN SLIM is a growth methodology, and the fundamental screen enforces the **C**, **A** and
**I** criteria: current earnings acceleration, sustained annual growth, and enough size and
liquidity for institutional participation.

The screen answers one question — *is this an institutional-quality growth business?* — and
nothing about price structure. Timing is the technical screener's job.

---

## Universe

The entire US-listed common and preferred equity market via TradingView's Scanner API. Not
an index subset. ETFs, mutual funds, and non-primary listings are excluded.

```
POST https://scanner.tradingview.com/america/scan
```

A single server-side request returns only the names satisfying every filter, so no bulk
download or local filtering is required.

---

## Filters

All conditions must hold simultaneously.

| Filter | Threshold | Rationale |
|---|---|---|
| Diluted EPS growth, QoQ | > **20%** | Earnings *acceleration* — the strongest single CAN SLIM signal |
| Diluted EPS growth, YoY TTM | > **25%** | Sustained annual growth, not one favourable quarter |
| Revenue growth, YoY TTM | > **15%** | Real top-line expansion |
| Close | > **$15** | Below this, quality degrades and the AI rating is capped anyway |
| 30-day average volume | > **250,000** | Executable size without moving the market |
| Market cap | > **$300M** | Micro-caps lack the institutional sponsorship CAN SLIM depends on |
| Listing | `is_primary` | Excludes secondary listings and duplicate share classes |
| Sector | **not** Finance, Real Estate, Utilities | See below |

### Why revenue growth is required alongside EPS

EPS growth can be manufactured: cut costs, retire shares, and earnings per share rises while
the business itself does not. The prior filter only required revenue > 0, which blocked
nothing but outright contraction.

Requiring **15% top-line growth** forces the earnings growth to be a consequence of the
business expanding. It is the difference between a growth company and a company with a
growth-looking income statement.

### Why three sectors are excluded

Finance, Real Estate and Utilities are driven primarily by interest rates rather than
earnings acceleration. Their price behaviour does not respond to the CAN SLIM signals this
system trades, so their inclusion adds candidates that the rest of the pipeline is not
equipped to judge. Set `EXCLUDED_SECTORS=""` to disable.

---

## Output

Survivors are written to `watchlist`, sorted by market cap. Typical output is roughly
100 names.

Stored per name: ticker, exchange mappings, currency, FMP symbol, market cap, volume and
sector.

---

## Point-in-time archive

**Every run also appends to `watchlist_history`** — an append-only log keyed by
`(snapshot_date, ticker)` — *before* `watchlist` is truncated.

### Why this exists

`watchlist` is current-state: it is wiped and rewritten on every run. Without an archive,
a name that qualified in 2023 and later deteriorated simply vanishes. Any study run against
`watchlist` can therefore only see names that **still** qualify today — the definition of
survivorship bias.

This has a concrete consequence in this repository. The `research/pass_names.txt` universe
was produced by dumping the live watchlist once, then replaying it backwards over three
years. Its absolute returns are inflated by both survivorship and look-ahead bias and cannot
be read as evidence of screen quality. Paired comparisons remain valid, because the bias
affects both arms equally — but level comparisons do not. See `benchmark_data/README.md`.

`watchlist_history` is what makes an unbiased answer possible in future: it records what the
screen believed **on the day it believed it**.

### Ordering invariant

The archive call must precede the truncate. Reversing them captures nothing, silently —
there is no error, just an empty history. A test enforces the ordering.

Setup: run `migrations/add_watchlist_history.sql` once. It self-seeds from the current
watchlist.

---

## Parameters

| Variable | Default |
|---|---|
| `MIN_QUARTERLY_EPS_GROWTH` | `20` |
| `MIN_ANNUAL_EPS_GROWTH` | `25` |
| `MIN_REVENUE_GROWTH` | `15` |
| `EXCLUDED_SECTORS` | `Finance,Real Estate,Utilities` |

Price, volume, market-cap and listing-type floors are hard-coded in the request body.

---

## Known limitation

The fundamental screen has **never been varied in a backtest**. Every study in this
repository holds it fixed and varies the technical or exit layer. Its thresholds are
therefore justified by O'Neil's published methodology and by reasoning, not by measurement
within this system.

Answering "are these the right thresholds?" empirically requires point-in-time fundamental
data — knowing what a stock's EPS growth was *as reported on that date*, not as restated
since. `watchlist_history` begins accumulating that record going forward; a retrospective
answer would require a paid point-in-time vendor.

This is stated plainly because the alternative — treating an untested filter as validated —
is how unexamined assumptions become permanent.
