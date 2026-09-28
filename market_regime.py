"""market_regime.py — CANSLIM 'M' (Market Direction) filter.

Extracted from execution_agent.py (2026-09-27 orchestrator split) so the live
daemon file stays small enough for a local LLM to hold in context. Behaviour is
unchanged: these functions are the same code, relocated.

SAFETY INVARIANT (see decisions/2026-09-27_execution-agent-modular-split.md):
every name the test-suite patches on `execution_agent` — the MARKET_DIRECTION_*
constants, FMP_API_KEY, fmp_session and notifier — is referenced here through
`ea.<name>` (live attribute lookup), never bound locally. That keeps
`mock.patch("execution_agent.X")` intercepting these functions after the move,
which a by-name import would silently defeat.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from ib_insync import IB

from execution_agent_ref import ea


def _fetch_market_closes(ticker: str) -> list[tuple[str, float]]:
    """Sorted (date, close) daily history for `ticker`, oldest first.

    Returns an empty list on any transport, status or payload problem so that
    every failure mode reaches the caller identically and is treated as bearish.
    """
    to_date   = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    from_date = to_date - datetime.timedelta(
        days=int((ea.MARKET_DIRECTION_SMA_WINDOW + ea.MARKET_DIRECTION_SLOPE_DAYS) * 1.6) + 60)
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={ea.FMP_API_KEY}")
    r = ea.fmp_session.get(url, timeout=10)
    if r.status_code != 200:
        print(f"⚠️ Market direction: HTTP {r.status_code} for {ticker}.")
        return []
    data = r.json()
    if not isinstance(data, list):
        print(f"⚠️ Market direction: unexpected payload for {ticker}.")
        return []
    rows = []
    for d in data:
        try:
            close = float(d["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close > 0 and d.get("date"):
            rows.append((str(d["date"])[:10], close))
    rows.sort(key=lambda x: x[0])
    return rows


def _index_is_bullish(ticker: str) -> tuple[bool, bool] | None:
    """Per-index verdict as ``(above_sma, slope_ok)``, or None if data is unusable.

    Bullish requires the latest close to sit more than MARKET_DIRECTION_BUFFER_PCT
    above the SMA-200. The buffer is deliberately asymmetric-free: the same
    threshold governs entry and exit, so the gate is a simple line with a
    dead-band rather than a hysteresis loop.
    """
    rows = _fetch_market_closes(ticker)
    needed = ea.MARKET_DIRECTION_SMA_WINDOW + ea.MARKET_DIRECTION_SLOPE_DAYS
    if len(rows) < needed:
        print(f"⚠️ Market direction: only {len(rows)} sessions for {ticker}, "
              f"need {needed}.")
        return None

    last_date = datetime.date.fromisoformat(rows[-1][0])
    today_ny  = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    if (today_ny - last_date).days > ea.MARKET_DIRECTION_MAX_STALE_DAYS:
        print(f"⚠️ Market direction: {ticker} data stale (last {last_date}).")
        return None

    closes = [c for _, c in rows]
    w = ea.MARKET_DIRECTION_SMA_WINDOW
    sma_now  = sum(closes[-w:]) / w
    sma_then = sum(closes[-w - ea.MARKET_DIRECTION_SLOPE_DAYS:
                          -ea.MARKET_DIRECTION_SLOPE_DAYS]) / w
    latest   = closes[-1]

    above = latest > sma_now * (1 + ea.MARKET_DIRECTION_BUFFER_PCT)
    slope_ok = sma_then > 0 and (sma_now - sma_then) / sma_then >= 0
    print(f"📊 {ticker}: ${latest:.2f} vs SMA{w} ${sma_now:.2f} "
          f"(+{ea.MARKET_DIRECTION_BUFFER_PCT * 100:.1f}% buffer) "
          f"{'above' if above else 'below'}, "
          f"SMA{w} slope {ea.MARKET_DIRECTION_SLOPE_DAYS}d "
          f"{'flat/up' if slope_ok else 'down'}")
    return bool(above), bool(slope_ok)


def is_market_bullish() -> bool:
    """CANSLIM 'M' (Market Direction) filter — fail-closed.

    Bullish requires **every** benchmark in MARKET_DIRECTION_TICKERS to close
    above its SMA-200 by MARKET_DIRECTION_BUFFER_PCT, and **at least one** of
    those SMA-200s to be non-falling over MARKET_DIRECTION_SLOPE_DAYS.

    Every failure mode — HTTP error, malformed payload, insufficient history,
    stale data, unhandled exception — returns False. Standing down costs a day
    of opportunity; buying into an undiagnosed bear market costs capital.
    MARKET_DIRECTION_FILTER_ENABLED=false is the only bypass.
    """
    if not ea.MARKET_DIRECTION_FILTER_ENABLED:
        return True
    if not ea.MARKET_DIRECTION_TICKERS:
        print("⚠️ Market direction: no benchmarks configured → BEAR (fail-closed).")
        return False
    try:
        above_all, slope_any = True, False
        for ticker in ea.MARKET_DIRECTION_TICKERS:
            verdict = _index_is_bullish(ticker)
            if verdict is None:
                print(f"⚠️ Market direction: {ticker} unusable → BEAR (fail-closed).")
                return False
            above, slope_ok = verdict
            above_all = above_all and above
            slope_any = slope_any or slope_ok
        bullish = above_all and slope_any
        print(f"📊 Market direction [{'+'.join(ea.MARKET_DIRECTION_TICKERS)}]: "
              f"→ {'BULL ↑' if bullish else 'BEAR ↓'}")
        return bullish
    except Exception as e:
        notifier = ea.notifier
        notifier.notify_exception(f"is_market_bullish() — execution_agent.py", e)
        print(f"⚠️ Market direction check failed: {e}. Defaulting to BEAR (fail-closed).")
        return False


def fetch_ibkr_delayed_price(ib: IB, contract) -> tuple:
    """Fetch the current price for a contract using IBKR delayed market data (type 3).

    Prefers the ask price; falls back to last traded price.
    Always restores live market data mode (type 1) after the call.

    Returns:
        (price: float, method: str) where method is 'ask', 'last', or '' on failure.
        price is 0.0 when no valid price is available.
    """
    ibkr_price   = 0.0
    price_method = ""
    try:
        ib.reqMarketDataType(3)          # Switch to delayed data (free, 15-20 min lag)
        _tickers = ib.reqTickers(contract)
        if _tickers:
            _t    = _tickers[0]
            _ask  = _t.ask  if _t.ask  == _t.ask  and _t.ask  > 0 else 0.0
            _last = _t.last if _t.last == _t.last and _t.last > 0 else 0.0
            _p    = _ask if _ask > 0 else _last
            if _p > 0:
                ibkr_price   = _p
                price_method = "ask" if _ask > 0 else "last"
    except Exception as _de:
        print(f"   ⚠️ IBKR delayed price failed: {_de}")
    finally:
        ib.reqMarketDataType(1)          # Always restore live mode
    return ibkr_price, price_method
