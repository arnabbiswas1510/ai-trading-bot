"""Read-only loader for the committed benchmark price dataset.

The dataset lives in <repo>/benchmark_data/ and is committed, so backtests and
strategy benchmarks run offline with no FMP calls and no rate limiting.

    from bardata import daily, symbols, coverage

    bars = daily("NVDA")        # list[dict] — same shape the FMP JSON had
    bars[0]["close"]

Shape is deliberately identical to the raw FMP `/stable/historical-price-eod/full`
response so existing harness code works unchanged.
"""
from __future__ import annotations

import functools
import json
import os

import pyarrow.parquet as pq

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_data")
_ROOT = os.path.normpath(_ROOT)
_DAILY = os.path.join(_ROOT, "daily", "us_equities_daily.parquet")


@functools.lru_cache(maxsize=1)
def _table() -> dict[str, list[dict]]:
    if not os.path.exists(_DAILY):
        raise FileNotFoundError(
            f"Benchmark dataset missing at {_DAILY}. "
            "It is committed to the repo — check out the full tree, or rebuild "
            "with research/fetch_daily.py + research/to_parquet.py."
        )
    t = pq.read_table(_DAILY)
    syms = t.column("symbol").to_pylist()
    dates = t.column("date").to_pylist()
    o = t.column("open").to_pylist()
    h = t.column("high").to_pylist()
    lo = t.column("low").to_pylist()
    c = t.column("close").to_pylist()
    v = t.column("volume").to_pylist()
    w = t.column("vwap").to_pylist()

    out: dict[str, list[dict]] = {}
    for i, s in enumerate(syms):
        out.setdefault(s, []).append({
            "symbol": s,
            "date": dates[i].isoformat(),
            "open": o[i], "high": h[i], "low": lo[i], "close": c[i],
            "volume": v[i], "vwap": w[i],
        })
    return out


def daily(symbol: str, start: str | None = None, end: str | None = None):
    """Daily bars ascending by date, or None if the symbol is not in the dataset.

    Returns None (rather than raising) to match the previous fetch-based helper,
    so callers can skip missing names.
    """
    bars = _table().get(symbol.upper())
    if not bars:
        return None
    if start or end:
        lo_, hi_ = start or "0000", end or "9999"
        bars = [b for b in bars if lo_ <= b["date"] <= hi_]
    return bars or None


def symbols() -> list[str]:
    return sorted(_table())


@functools.lru_cache(maxsize=1)
def manifest() -> dict:
    return json.load(open(os.path.join(_ROOT, "MANIFEST.json")))


def coverage() -> str:
    m = manifest()
    return (f"{m['symbols']} symbols, {m['rows']:,} rows, "
            f"{m['date_min']}..{m['date_max']} ({m['bytes']/1e6:.2f} MB)")


def trading_days(universe: list[str] | None = None) -> list[str]:
    """Sorted union of dates across the given symbols (default: all)."""
    t = _table()
    syms = universe if universe is not None else list(t)
    ds: set[str] = set()
    for s in syms:
        for b in t.get(s.upper(), ()):
            ds.add(b["date"])
    return sorted(ds)


if __name__ == "__main__":
    print(coverage())
    b = daily("NVDA")
    print(f"NVDA {len(b)} bars {b[0]['date']}..{b[-1]['date']} last close {b[-1]['close']}")
