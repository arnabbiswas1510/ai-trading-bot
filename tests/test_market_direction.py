"""
test_market_direction.py — Real unit tests for the CANSLIM "M" gate.

Before 2026-08-22 the only coverage of `is_market_bullish()` was in
tests/test_buy_gates.py, which *mocks the function out entirely*. That left the
real logic — including three separate fail-**open** paths — completely untested.
These tests exercise the actual implementation against synthetic FMP payloads.

Contract under test (decisions/2026-08-22_market-direction-gate-spy-qqq.md):
  * BULL requires EVERY benchmark to close more than MARKET_DIRECTION_BUFFER_PCT
    above its SMA-200, AND at least one benchmark's SMA-200 to be non-falling
    over MARKET_DIRECTION_SLOPE_DAYS sessions.
  * Every failure mode is fail-CLOSED (bearish): HTTP error, malformed payload,
    short history, stale data, unhandled exception, no benchmarks configured.
  * MARKET_DIRECTION_FILTER_ENABLED=false is the only bypass.
"""

import datetime
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent  # noqa: E402


# ── Synthetic price history helpers ───────────────────────────────────────────

def _sessions(n: int, end: datetime.date | None = None) -> list[datetime.date]:
    """`n` weekday dates ending at `end` (default: today in NY), oldest first."""
    if end is None:
        end = datetime.datetime.now(execution_agent.ZoneInfo("America/New_York")).date()
    out, cursor = [], end
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= datetime.timedelta(days=1)
    return list(reversed(out))


def make_history(n: int = 260, start: float = 100.0, drift: float = 0.1,
                 final_close: float | None = None,
                 end: datetime.date | None = None) -> list[dict]:
    """FMP-shaped daily rows.

    A constant positive `drift` produces a rising SMA-200 and a close above it;
    a negative drift produces the mirror image. `final_close` overrides only the
    most recent close, which lets a test place price precisely relative to the
    SMA without disturbing the slope.
    """
    dates = _sessions(n, end)
    rows = []
    for i, d in enumerate(dates):
        rows.append({"date": d.isoformat(), "close": round(start + drift * i, 4)})
    if final_close is not None:
        rows[-1]["close"] = final_close
    # FMP returns newest-first; the code must sort. Hand it back reversed so the
    # test would fail if that sort were ever removed.
    return list(reversed(rows))


def make_response(payload, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


def patch_fmp(per_ticker: dict):
    """Patch fmp_session.get to serve a payload (or response) per ticker."""
    def _get(url, timeout=None):
        for ticker, value in per_ticker.items():
            if f"symbol={ticker}&" in url or url.endswith(f"symbol={ticker}"):
                if isinstance(value, MagicMock):
                    return value
                return make_response(value)
        raise AssertionError(f"unexpected market-direction URL: {url}")
    return patch.object(execution_agent.fmp_session, "get", side_effect=_get)


@pytest.fixture(autouse=True)
def _default_config():
    """Pin the gate's configuration so tests are independent of the environment."""
    with patch.object(execution_agent, "MARKET_DIRECTION_FILTER_ENABLED", True), \
         patch.object(execution_agent, "MARKET_DIRECTION_TICKERS", ["SPY", "QQQ"]), \
         patch.object(execution_agent, "MARKET_DIRECTION_SMA_WINDOW", 200), \
         patch.object(execution_agent, "MARKET_DIRECTION_BUFFER_PCT", 0.01), \
         patch.object(execution_agent, "MARKET_DIRECTION_SLOPE_DAYS", 20), \
         patch.object(execution_agent, "MARKET_DIRECTION_MAX_STALE_DAYS", 5), \
         patch.object(execution_agent, "FMP_API_KEY", "test-key"), \
         patch.object(execution_agent.notifier, "notify_exception", MagicMock()):
        yield


# ── Bullish path ──────────────────────────────────────────────────────────────

class TestBullish:

    def test_both_indices_strong_is_bullish(self):
        """Both above the buffer with rising SMA-200 → BULL."""
        hist = make_history(drift=0.5)
        with patch_fmp({"SPY": hist, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is True

    def test_filter_disabled_bypasses_everything(self):
        """The kill switch must short-circuit before any network call."""
        with patch.object(execution_agent, "MARKET_DIRECTION_FILTER_ENABLED", False), \
             patch.object(execution_agent.fmp_session, "get") as mock_get:
            assert execution_agent.is_market_bullish() is True
            mock_get.assert_not_called()

    def test_slope_needs_only_one_index(self):
        """Slope is OR'd: one flat/rising SMA-200 suffices if both are above buffer.

        QQQ here is above its SMA-200 (a late spike) but its SMA-200 is falling.
        SPY's is rising, so the gate stays open.
        """
        spy = make_history(drift=0.5)
        qqq = make_history(drift=-0.3, final_close=200.0)
        with patch_fmp({"SPY": spy, "QQQ": qqq}):
            assert execution_agent.is_market_bullish() is True


# ── Bearish path ──────────────────────────────────────────────────────────────

class TestBearish:

    def test_one_index_below_sma_blocks(self):
        """`above` is AND'd across benchmarks — one breakdown closes the gate."""
        spy = make_history(drift=0.5)
        qqq = make_history(drift=-0.5)
        with patch_fmp({"SPY": spy, "QQQ": qqq}):
            assert execution_agent.is_market_bullish() is False

    def test_inside_buffer_is_bearish(self):
        """Above the SMA-200 but inside the 1% dead-band → still BEAR.

        This is the case the pre-2026-08-22 rule got wrong: a close 0.5% above
        the SMA-200 counted as a confirmed uptrend.
        """
        hist = make_history(n=260, start=100.0, drift=0.0, final_close=100.5)
        with patch_fmp({"SPY": hist, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False

    def test_above_buffer_is_bullish_boundary(self):
        """Sanity companion to the previous test: 2% above clears a 1% buffer."""
        hist = make_history(n=260, start=100.0, drift=0.0, final_close=102.0)
        with patch_fmp({"SPY": hist, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is True

    def test_all_slopes_falling_blocks(self):
        """Both above the buffer on a spike, but every SMA-200 falling → BEAR."""
        hist = make_history(drift=-0.3, final_close=200.0)
        with patch_fmp({"SPY": hist, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False


# ── Fail-closed paths (the reason this file exists) ───────────────────────────

class TestFailsClosed:

    def test_http_error_is_bearish(self):
        """Previously returned True. A 500 must never authorise buying."""
        hist = make_history(drift=0.5)
        with patch_fmp({"SPY": make_response([], status=500), "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False

    def test_short_history_is_bearish(self):
        """Previously printed 'Defaulting to BULL' and returned True."""
        hist = make_history(drift=0.5)
        with patch_fmp({"SPY": make_history(n=100, drift=0.5), "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False

    def test_malformed_payload_is_bearish(self):
        """A dict where a list is expected (e.g. an FMP error envelope)."""
        hist = make_history(drift=0.5)
        with patch_fmp({"SPY": {"Error Message": "limit reached"}, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False

    def test_stale_data_is_bearish(self):
        """A feed frozen a fortnight ago must not be read as a live uptrend."""
        stale_end = (datetime.datetime.now(
            execution_agent.ZoneInfo("America/New_York")).date()
            - datetime.timedelta(days=14))
        stale = make_history(drift=0.5, end=stale_end)
        with patch_fmp({"SPY": stale, "QQQ": make_history(drift=0.5)}):
            assert execution_agent.is_market_bullish() is False

    def test_exception_is_bearish_and_notified(self):
        with patch.object(execution_agent.fmp_session, "get",
                          side_effect=RuntimeError("boom")):
            assert execution_agent.is_market_bullish() is False
            execution_agent.notifier.notify_exception.assert_called_once()

    def test_no_benchmarks_configured_is_bearish(self):
        """An empty ticker list must not vacuously satisfy the 'all above' test."""
        with patch.object(execution_agent, "MARKET_DIRECTION_TICKERS", []), \
             patch.object(execution_agent.fmp_session, "get") as mock_get:
            assert execution_agent.is_market_bullish() is False
            mock_get.assert_not_called()

    def test_unparseable_closes_are_dropped_then_fail_closed(self):
        """Non-numeric closes are skipped; dropping enough of them starves the SMA."""
        hist = make_history(drift=0.5)
        broken = [dict(row, close="n/a") for row in hist]
        with patch_fmp({"SPY": broken, "QQQ": hist}):
            assert execution_agent.is_market_bullish() is False


# ── Wiring: the gate must actually stop the buy loop ──────────────────────────

class TestGateIsEnforced:

    def test_bearish_market_stands_down(self):
        """run_market_open_buys() must return before reading today's triggers.

        The startup schema guard legitimately touches Supabase first, so this
        asserts on the trigger table specifically rather than on Supabase as a
        whole.
        """
        with patch.object(execution_agent, "is_market_bullish", return_value=False), \
             patch.object(execution_agent, "MARKET_DIRECTION_FILTER_ENABLED", True), \
             patch.object(execution_agent, "supabase") as mock_supabase:
            ib = MagicMock()
            ib.isConnected.return_value = True
            execution_agent.run_market_open_buys(ib)
            tables = [c.args[0] for c in mock_supabase.table.call_args_list if c.args]
            assert "daily_triggers" not in tables
