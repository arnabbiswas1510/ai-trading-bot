"""
tests/test_trigger_outcomes.py

Tests for the weekly forward-return backfill.

WHY THIS MATTERS
----------------
This job produces the numbers that will be used to judge whether final_score
predicts anything. A silent error here does not crash — it yields a plausible
but wrong answer, which is worse than no answer, because it would be acted on.

The two invariants that carry the most risk:
  1. Entry is the NEXT session's OPEN. Measuring from the trigger close would
     credit the strategy with an overnight gap it never captured, flattering
     every result invisibly.
  2. A partially-elapsed window must never be recorded as a complete one.
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backfill_trigger_outcomes as bto


def _bars(closes, start="2026-01-01", opens=None, highs=None, lows=None):
    """Ascending daily bars on consecutive calendar days."""
    d = datetime.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({
            "date": (d + datetime.timedelta(days=i)).isoformat(),
            "open": (opens[i] if opens else c),
            "high": (highs[i] if highs else c),
            "low": (lows[i] if lows else c),
            "close": c,
        })
    return out


class TestEntryReference:
    """The single most consequential choice in this file."""

    def test_entry_is_next_session_open_not_trigger_close(self):
        # Trigger day closes at 100; next session GAPS UP and opens at 110.
        bars = _bars([100, 110, 121], start="2026-01-01",
                     opens=[100, 110, 110])
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["entry_ref_price"] == 110.0
        assert res["entry_ref_date"] == "2026-01-02"
        # From the 110 open, day 1 closes 110 -> 0%. Measuring from the 100
        # close would have reported +10% the bot never captured.
        assert res["fwd_1d_pct"] == 0.0

    def test_returns_none_when_no_session_follows(self):
        bars = _bars([100], start="2026-01-01")
        assert bto.compute_outcomes(bars, "2026-01-01") is None

    def test_returns_none_when_trigger_date_not_covered(self):
        bars = _bars([100, 101], start="2026-01-01")
        assert bto.compute_outcomes(bars, "2026-06-01") is None

    def test_falls_back_to_close_when_open_missing(self):
        bars = _bars([100, 105], start="2026-01-01")
        for b in bars:
            b["open"] = 0
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["entry_ref_price"] == 105.0


class TestForwardReturns:

    def test_horizons_measured_from_entry_bar(self):
        # Trigger day, then entry session opening at 100 and compounding +1%/day.
        # fwd_1d is the ENTRY session's own close (session 1 of holding).
        closes = [50] + [100 * (1.01 ** i) for i in range(21)]
        bars = _bars(closes, start="2026-01-01",
                     opens=[50, 100] + closes[2:])
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["entry_ref_price"] == 100.0
        assert res["fwd_1d_pct"] == pytest.approx(0.0, abs=0.01)
        assert res["fwd_5d_pct"] == pytest.approx(4.06, abs=0.02)
        assert res["fwd_20d_pct"] == pytest.approx(20.81, abs=0.05)

    def test_incomplete_horizons_are_none_not_zero(self):
        """A missing horizon must be NULL. Zero would be read as 'flat'."""
        bars = _bars([100, 100, 102], start="2026-01-01")
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["fwd_1d_pct"] is not None
        assert res["fwd_5d_pct"] is None
        assert res["fwd_20d_pct"] is None

    def test_outcome_bars_counts_sessions_held(self):
        bars = _bars([100] * 6, start="2026-01-01")
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["outcome_bars"] == 5


class TestPathMetrics:

    def test_max_gain_and_drawdown_include_entry_bar_exclude_trigger_bar(self):
        """The entry session IS held, so its range counts. The trigger session
        is not held, so its range must not."""
        bars = _bars([100, 100, 100, 100],
                     start="2026-01-01",
                     opens=[100, 100, 100, 100],
                     highs=[999, 110, 105, 100],   # 999 on trigger day must not count
                     lows=[1, 95, 90, 100])        # 1 on trigger day must not count
        res = bto.compute_outcomes(bars, "2026-01-01")
        assert res["max_gain_20d_pct"] == pytest.approx(10.0, abs=0.01)
        assert res["max_drawdown_20d_pct"] == pytest.approx(-10.0, abs=0.01)

    def test_ever_above_entry_true_when_high_exceeds(self):
        bars = _bars([100, 98, 97], start="2026-01-01",
                     opens=[100, 100, 97], highs=[100, 103, 97])
        assert bto.compute_outcomes(bars, "2026-01-01")["ever_above_entry"] is True

    def test_ever_above_entry_false_for_never_worked(self):
        """The failed-breakout signature the Thesis Stop targets."""
        bars = _bars([100, 97, 95, 93], start="2026-01-01",
                     opens=[100, 100, 96, 94], highs=[100, 100, 96, 94])
        assert bto.compute_outcomes(bars, "2026-01-01")["ever_above_entry"] is False


class TestBenchmarkAlpha:
    """A raw +5% in a +5% market is not edge."""

    def test_alpha_is_return_minus_benchmark(self):
        # Session 20 of holding is index 19 after entry: stock 119 (+19%),
        # benchmark 109.5 (+9.5%).
        closes = [50] + [100 + i for i in range(21)]
        bars = _bars(closes, start="2026-01-01",
                     opens=[50, 100] + closes[2:])
        bench_closes = [50] + [100 + (i * 0.5) for i in range(21)]
        bench = _bars(bench_closes, start="2026-01-01",
                      opens=[50, 100] + bench_closes[2:])
        res = bto.compute_outcomes(bars, "2026-01-01", bench)
        assert res["fwd_20d_pct"] == pytest.approx(19.0, abs=0.1)
        assert res["bench_fwd_20d_pct"] == pytest.approx(9.5, abs=0.1)
        assert res["alpha_20d_pct"] == pytest.approx(9.5, abs=0.1)

    def test_alpha_none_without_benchmark(self):
        closes = [50] + [100 + i for i in range(21)]
        bars = _bars(closes, start="2026-01-01", opens=[50, 100] + closes[2:])
        res = bto.compute_outcomes(bars, "2026-01-01", None)
        assert res.get("alpha_20d_pct") is None

    def test_market_beating_move_can_be_negative_alpha(self):
        """Up 5% while SPY is up 10% is underperformance, and must read so."""
        closes = [50] + [100 + (i * 0.25) for i in range(21)]
        bars = _bars(closes, start="2026-01-01", opens=[50, 100] + closes[2:])
        bench_closes = [50] + [100 + (i * 0.5) for i in range(21)]
        bench = _bars(bench_closes, start="2026-01-01",
                      opens=[50, 100] + bench_closes[2:])
        res = bto.compute_outcomes(bars, "2026-01-01", bench)
        assert res["fwd_20d_pct"] > 0
        assert res["alpha_20d_pct"] < 0


class TestIncompleteWindowsNotWritten:

    def _client(self, pending):
        c = MagicMock()
        q = c.table.return_value.select.return_value.lte.return_value
        q.is_.return_value.order.return_value.execute.return_value = \
            MagicMock(data=pending)
        q.order.return_value.execute.return_value = MagicMock(data=pending)
        return c

    def test_short_window_is_skipped_not_written(self, monkeypatch, capsys):
        """Recording a 3-day window as a 20-day result would silently corrupt
        every downstream conclusion."""
        pending = [{"triggered_at": "2026-01-01", "ticker": "AAA",
                    "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        client = self._client(pending)

        monkeypatch.setattr(bto, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(bto, "SUPABASE_KEY", "k")
        monkeypatch.setattr(bto, "FMP_API_KEY", "k")
        monkeypatch.setattr(bto, "create_client", lambda *a, **k: client)
        monkeypatch.setattr(bto, "fetch_prices",
                            lambda *a, **k: _bars([100, 101, 102], "2026-01-01"))

        assert bto.run() == 0
        client.table.return_value.update.assert_not_called()
        assert "skipped: 1" in capsys.readouterr().out

    def test_complete_window_is_written(self, monkeypatch):
        pending = [{"triggered_at": "2026-01-01", "ticker": "AAA",
                    "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        client = self._client(pending)

        monkeypatch.setattr(bto, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(bto, "SUPABASE_KEY", "k")
        monkeypatch.setattr(bto, "FMP_API_KEY", "k")
        monkeypatch.setattr(bto, "create_client", lambda *a, **k: client)
        monkeypatch.setattr(bto, "fetch_prices",
                            lambda *a, **k: _bars([100] * 25, "2026-01-01"))

        assert bto.run() == 0
        client.table.return_value.update.assert_called()
        written = client.table.return_value.update.call_args.args[0]
        assert written["outcomes_computed_at"] is not None
        assert written["outcome_bars"] >= bto.MIN_BARS_REQUIRED

    def test_dry_run_writes_nothing(self, monkeypatch):
        pending = [{"triggered_at": "2026-01-01", "ticker": "AAA",
                    "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        client = self._client(pending)

        monkeypatch.setattr(bto, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(bto, "SUPABASE_KEY", "k")
        monkeypatch.setattr(bto, "FMP_API_KEY", "k")
        monkeypatch.setattr(bto, "create_client", lambda *a, **k: client)
        monkeypatch.setattr(bto, "fetch_prices",
                            lambda *a, **k: _bars([100] * 25, "2026-01-01"))

        assert bto.run(dry_run=True) == 0
        client.table.return_value.update.assert_not_called()


class TestResumability:

    def test_selects_only_unmeasured_rows_by_default(self, monkeypatch):
        client = MagicMock()
        chain = client.table.return_value.select.return_value.lte.return_value
        chain.is_.return_value.order.return_value.execute.return_value = \
            MagicMock(data=[])
        bto.fetch_pending(client)
        chain.is_.assert_called_once_with("outcomes_computed_at", "null")

    def test_force_recomputes_measured_rows(self, monkeypatch):
        client = MagicMock()
        chain = client.table.return_value.select.return_value.lte.return_value
        chain.order.return_value.execute.return_value = MagicMock(data=[])
        bto.fetch_pending(client, force=True)
        chain.is_.assert_not_called()

    def test_only_considers_settled_triggers(self):
        client = MagicMock()
        client.table.return_value.select.return_value.lte.return_value \
            .is_.return_value.order.return_value.execute.return_value = \
            MagicMock(data=[])
        bto.fetch_pending(client)
        cutoff = client.table.return_value.select.return_value.lte.call_args.args[1]
        expected = (bto._today_ny()
                    - datetime.timedelta(days=bto.SETTLE_DAYS)).isoformat()
        assert cutoff == expected


class TestFetchRobustness:

    def test_sorts_newest_first_response_ascending(self):
        """FMP returns newest-first; every downstream calculation assumes
        ascending order, so an unsorted response would invert the results."""
        payload = {"historical": [
            {"date": "2026-01-03", "close": 3, "open": 3, "high": 3, "low": 3},
            {"date": "2026-01-01", "close": 1, "open": 1, "high": 1, "low": 1},
            {"date": "2026-01-02", "close": 2, "open": 2, "high": 2, "low": 2},
        ]}
        resp = MagicMock(status_code=200)
        resp.json.return_value = payload
        sess = MagicMock()
        sess.get.return_value = resp
        with patch.object(bto, "FMP_API_KEY", "k"):
            bars = bto.fetch_prices("AAA", "2026-01-01", "2026-01-03", sess)
        assert [b["date"] for b in bars] == ["2026-01-01", "2026-01-02", "2026-01-03"]

    def test_failure_returns_empty_not_raise(self):
        sess = MagicMock()
        sess.get.side_effect = Exception("network down")
        with patch.object(bto, "FMP_API_KEY", "k"), patch.object(bto.time, "sleep"):
            assert bto.fetch_prices("AAA", "2026-01-01", "2026-01-03", sess) == []

    def test_missing_env_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(bto, "FMP_API_KEY", None)
        assert bto.run() == 1
