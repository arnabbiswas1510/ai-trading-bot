# ADR: Point-in-time watchlist history — make the fundamental screen backtestable

**Date:** 2026-08-09
**Status:** Accepted
**Relates to:** 2026-08-09 (thesis stop, and its correction), 2026-08-05 (benchmark dataset)

---

## Context

`tv_api_screener.py` wipes the entire `watchlist` table on every run:

```python
supabase.table("watchlist").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()
```

A stock that qualified months ago and later deteriorated is deleted without a
trace. **No record of what the screen returned on any past date exists.**

The consequence surfaced while investigating whether entry selection could be
improved. Every technical entry filter tested (pivot clearance, base tightness,
RS confirmation, volume surge) helped one universe and hurt the other, so none
shipped. The tempting conclusion was "the edge must be in the fundamental
screen instead" — supported by baseline CAGR of 45.2 on the screener-passing
universe versus 22.2 on the broad one.

That conclusion was wrong, and the reason is this data loss. `pass_names.txt`
is a single snapshot of the names passing the screen **today**, replayed
backwards over three years. Companies that passed in 2023 and later failed are
absent, because they were deleted. `benchmark_data/README.md` documents this as
survivorship and look-ahead bias. The 45.2-vs-22.2 gap is confounded by
construction and cannot be read as screen skill.

So the largest untested component of the strategy is also the one component
that is **structurally impossible** to test with the data being kept.

## Decision

Add `watchlist_history`: an **append-only, never-pruned** table holding one
immutable row per `(snapshot_date, ticker)`, written on every screener run
**before** the truncate.

- `watchlist` keeps its current-state semantics and is **not modified** — the
  live trading pipeline reads it and must not change.
- The write is a `upsert(on_conflict="snapshot_date,ticker")`, so a same-day
  re-run overwrites rather than duplicating.
- The write is **non-fatal**. A research feature must never be able to break
  live screening, so all failures are logged and swallowed.

### Store the raw metrics, not just the tickers

`q_eps_growth`, `a_eps_growth`, `revenue_growth`, `roe`, `float_shares`,
`analyst_rating`, `company_size`, `price`, `retention_period` — plus
`market_cap`, `volume` and `sector`, which the screener already fetches or
filters on but previously discarded.

This is the highest-leverage part of the change. Keeping the metrics means
alternative screen definitions can be re-cut **offline and retroactively**
without a point-in-time fundamentals vendor (Norgate, Sharadar, Compustat PIT).
Storing only tickers would force a vendor purchase to ask any threshold question.

`sector` required adding one column to the TradingView request. It was already
used as a filter, so it costs nothing; it was appended at index 11 so indices
0–10 are untouched, and it is read defensively (`row[11] if len(row) > 11`) so
a response without it degrades to a null rather than dropping the run. Verified
against the live API: 106 names returned, 15 distinct sectors, zero nulls.

## What this makes answerable

1. **Does the screen add anything at all?** Forward returns of screen-passing
   names versus a matched broad sample, using only names known at the time.
2. **Is EPS > 20% QoQ / 25% YoY right?** The actual growth figure is stored per
   name, so thresholds can be re-cut offline.
3. **Does `retention_period` predict?** Do names qualifying many runs running
   outperform fresh entrants? Directly usable as a buy gate, and currently
   unanswerable.
4. **Which metric carries the weight?** Rank forward returns against each field.

## Consequences

**Positive**
- The screen becomes testable point-in-time, without survivorship bias.
- No vendor purchase needed for threshold work.
- Zero risk to live trading: `watchlist` semantics unchanged, archive write is
  non-fatal and happens before any mutation.

**Negative / limits**
- **Forward-only.** It repairs nothing retroactively and does not fix the
  existing backtest. Roughly 6 months (~26 snapshots x ~100 names) gives a first
  read on questions 1 and 3; threshold tuning realistically wants 12 months.
- Conclusions from the first year will be regime-limited.
- Unbounded growth, deliberately — pruning would recreate the exact problem this
  table exists to solve. At ~100 rows/run this is negligible.
- The migration seeds today's rows from the current `watchlist`, tagged
  `source='seed_from_watchlist'`. Those lack `sector`/`market_cap`/`volume`
  since `watchlist` has no such columns.

## Pre-existing issue observed, NOT addressed here

`backend/database.py::save_screener_results()` also writes to `watchlist`, with
a *different* retention model (replace current ISO week, prune beyond 56 days)
and a *different* column set (`total_score`, `rs_rating`, `sma50`,
`n_pct_from_high`, `s_acc_days`, `s_dist_days`) — none of which exist in the
live table, which was verified to have only the `tv_api_screener` columns.

Two writers with incompatible schemas and incompatible retention models are
pointed at one table. The dashboard path is wrapped in try/except and therefore
likely fails silently today. Out of scope for this change; recorded so it is not
rediscovered from scratch.

## Files

- `migrations/add_watchlist_history.sql` — table, indexes, comments, seed
- `tv_api_screener.py` — `save_watchlist_history()`; `sector` column; research
  extras carried in a parallel map so they cannot leak into the `watchlist`
  insert (an unknown column there fails the run with PGRST204)
- `tests/test_watchlist_history.py` — 20 tests, including a mutation-verified
  guard that the archive precedes the truncate
- `docs/fundamental_screener.md` — data flow updated
