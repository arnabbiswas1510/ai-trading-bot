"""
tests/test_watchlist_history.py

Tests for the append-only point-in-time watchlist archive.

WHY THIS EXISTS
---------------
run_screener() truncates the entire `watchlist` table on every run. A name that
qualified months ago and later deteriorated therefore leaves no trace, which is
exactly why the fundamental screen cannot currently be backtested: the only
universe file available (research/pass_names.txt) is a single present-day
snapshot replayed backwards, carrying survivorship and look-ahead bias.

`watchlist_history` fixes that going forward. The single most important
invariant guarded here is ORDERING — the archive must be written BEFORE the
truncate, or it captures nothing.

All Supabase and TradingView calls are mocked; no network required.
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tv_api_screener


WATCHLIST_ONLY_FIELDS = {"tv_exchange", "ib_exchange", "currency",
                         "fmp_ticker", "created_at"}


def _row(ticker="NVDA", retention="3d"):
    """A row as it exists in `inserts` — i.e. after retention/created_at are added."""
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Inc.",
        "q_eps_growth": 35.9,
        "a_eps_growth": 110.3,
        "revenue_growth": 70.6,
        "analyst_rating": "Buy",
        "float_shares": 23269606800,
        "roe": 114.2,
        "company_size": "Large",
        "price": 223.96,
        "tv_exchange": "NASDAQ",
        "ib_exchange": "SMART",
        "currency": "USD",
        "fmp_ticker": ticker,
        "retention_period": retention,
        "created_at": "2026-08-09T00:00:00+00:00",
    }


def _extras(ticker="NVDA"):
    return {ticker: {"market_cap": 5.4e12, "volume": 105666402.0,
                     "sector": "Electronic Technology"}}


def _sb():
    sb = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    return sb


def _upsert_payloads(sb):
    """All payloads passed to .upsert(), flattened."""
    out = []
    for c in sb.table.return_value.upsert.call_args_list:
        out.extend(c.args[0])
    return out


class TestPayloadShape:

    def test_writes_to_watchlist_history_table(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        assert call("watchlist_history") in sb.table.call_args_list

    def test_excludes_watchlist_only_routing_fields(self):
        """`inserts` carries fields with no column here; an unknown key would
        fail the write, so the allow-list must drop them."""
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        payload = _upsert_payloads(sb)[0]
        assert not (WATCHLIST_ONLY_FIELDS & set(payload)), \
            f"leaked watchlist-only fields: {WATCHLIST_ONLY_FIELDS & set(payload)}"

    def test_carries_fundamental_metrics(self):
        """Storing raw metrics is the point: it lets alternative screen
        thresholds be re-cut offline without a point-in-time data vendor."""
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        p = _upsert_payloads(sb)[0]
        assert p["q_eps_growth"] == 35.9
        assert p["a_eps_growth"] == 110.3
        assert p["revenue_growth"] == 70.6
        assert p["roe"] == 114.2
        assert p["float_shares"] == 23269606800

    def test_carries_retention_period(self):
        """Directly testable as a buy gate: do names qualifying many runs
        running outperform fresh entrants?"""
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row(retention="7d")], _extras())
        assert _upsert_payloads(sb)[0]["retention_period"] == "7d"

    def test_merges_research_extras(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        p = _upsert_payloads(sb)[0]
        assert p["market_cap"] == 5.4e12
        assert p["volume"] == 105666402.0
        assert p["sector"] == "Electronic Technology"

    def test_missing_extras_degrade_to_none(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], {})
        p = _upsert_payloads(sb)[0]
        assert p["sector"] is None and p["market_cap"] is None

    def test_tags_source(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        assert _upsert_payloads(sb)[0]["source"] == "tv_api_screener"

    def test_skips_rows_without_ticker(self):
        sb = _sb()
        bad = _row(); bad.pop("ticker")
        n = tv_api_screener.save_watchlist_history(sb, [bad, _row("AMD")], _extras("AMD"))
        assert n == 1
        assert _upsert_payloads(sb)[0]["ticker"] == "AMD"


class TestSnapshotDate:

    def test_defaults_to_today_utc(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        assert _upsert_payloads(sb)[0]["snapshot_date"] == today

    def test_explicit_date_is_used(self):
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras(),
                                               snapshot_date="2026-01-15")
        assert _upsert_payloads(sb)[0]["snapshot_date"] == "2026-01-15"

    def test_all_rows_share_one_snapshot_date(self):
        sb = _sb()
        rows = [_row("NVDA"), _row("AMD"), _row("MU")]
        tv_api_screener.save_watchlist_history(sb, rows, {})
        dates = {p["snapshot_date"] for p in _upsert_payloads(sb)}
        assert len(dates) == 1


class TestIdempotence:

    def test_upserts_on_composite_primary_key(self):
        """A same-day re-run must overwrite, not duplicate."""
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        kwargs = sb.table.return_value.upsert.call_args.kwargs
        assert kwargs.get("on_conflict") == "snapshot_date,ticker"

    def test_never_deletes(self):
        """Append-only. A delete here would reintroduce the very data loss this
        table exists to prevent."""
        sb = _sb()
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        sb.table.return_value.delete.assert_not_called()


class TestChunking:

    def test_chunks_at_100(self):
        sb = _sb()
        rows = [_row(f"T{i}") for i in range(250)]
        n = tv_api_screener.save_watchlist_history(sb, rows, {})
        assert n == 250
        sizes = [len(c.args[0]) for c in sb.table.return_value.upsert.call_args_list]
        assert sizes == [100, 100, 50]


class TestNonFatal:
    """A research feature must never be able to break live screening."""

    def test_write_failure_does_not_raise(self):
        sb = _sb()
        sb.table.return_value.upsert.return_value.execute.side_effect = \
            Exception("connection reset")
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())

    def test_missing_table_reports_migration(self, capsys):
        sb = _sb()
        sb.table.return_value.upsert.return_value.execute.side_effect = \
            Exception('relation "watchlist_history" does not exist')
        tv_api_screener.save_watchlist_history(sb, [_row()], _extras())
        assert "add_watchlist_history.sql" in capsys.readouterr().out

    def test_empty_rows_writes_nothing(self):
        sb = _sb()
        assert tv_api_screener.save_watchlist_history(sb, [], {}) == 0
        sb.table.return_value.upsert.assert_not_called()


class TestArchiveHappensBeforeTruncate:
    """THE critical invariant. `watchlist` is wiped every run; if the archive
    ran after the wipe it would capture nothing at all."""

    def _run(self, sb):
        tv_rows = [{
            "s": "NASDAQ:NVDA",
            "d": ["NVDA", "NVIDIA Corporation", 35.9, 110.3, 70.6, 1.0,
                  23269606800, 114.2, 5.4e12, 223.96, 105666402,
                  "Electronic Technology"],
        }]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": tv_rows, "totalCount": 1}

        with patch.object(tv_api_screener, "SUPABASE_URL", "https://x.supabase.co"), \
             patch.object(tv_api_screener, "SUPABASE_KEY", "key"), \
             patch.object(tv_api_screener, "create_client", return_value=sb), \
             patch("tv_api_screener.requests.post", return_value=resp):
            tv_api_screener.run_screener()

    def test_history_upsert_precedes_watchlist_delete(self):
        order = []
        sb = MagicMock()

        def _table(name):
            t = MagicMock()
            t.upsert.side_effect = lambda *a, **k: (
                order.append(f"upsert:{name}"), MagicMock())[1]
            t.delete.side_effect = lambda *a, **k: (
                order.append(f"delete:{name}"), MagicMock())[1]
            t.insert.side_effect = lambda *a, **k: (
                order.append(f"insert:{name}"), MagicMock())[1]
            t.select.return_value.in_.return_value.execute.return_value = \
                MagicMock(data=[])
            return t

        sb.table.side_effect = _table
        self._run(sb)

        assert "upsert:watchlist_history" in order, f"archive never ran: {order}"
        assert "delete:watchlist" in order, f"truncate never ran: {order}"
        assert order.index("upsert:watchlist_history") < order.index("delete:watchlist"), \
            f"archive ran AFTER truncate — it would capture nothing: {order}"

    def test_sector_reaches_history_end_to_end(self):
        sb = _sb()
        sb.table.return_value.select.return_value.in_.return_value.execute.return_value = \
            MagicMock(data=[])
        self._run(sb)
        payloads = _upsert_payloads(sb)
        assert payloads, "nothing archived"
        assert payloads[0]["sector"] == "Electronic Technology"
        assert payloads[0]["volume"] == 105666402.0

    def test_watchlist_insert_payload_unchanged_by_research_fields(self):
        """Research extras must not leak into the `watchlist` insert — those
        columns do not exist there and the run would fail with PGRST204."""
        sb = _sb()
        sb.table.return_value.select.return_value.in_.return_value.execute.return_value = \
            MagicMock(data=[])
        self._run(sb)
        for c in sb.table.return_value.insert.call_args_list:
            for rec in c.args[0]:
                assert "sector" not in rec
                assert "market_cap" not in rec
                assert "volume" not in rec
