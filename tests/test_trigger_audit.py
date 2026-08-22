"""
tests/test_trigger_audit.py

Tests for the point-in-time trigger archive and the buy/skip decision log.

WHY THIS EXISTS
---------------
`daily_triggers` is truncated on every screener run. Each morning the screener
emits N triggers and at most a few are bought (4 slots); the rejected ones are
the control group, and they were being deleted. `trade_history` only contains
candidates already judged good -- selection on the dependent variable.

Two invariants dominate these tests:
  1. The archive must run BEFORE the truncate, or it captures nothing.
  2. The archive must capture the OUTGOING rows, not the incoming ones.
     ai_evaluator.py writes scores back AFTER technical_screener inserts, so
     archiving the incoming rows would store NULL ai_rating/final_score -- which
     would silently defeat the entire purpose.

All Supabase calls are mocked; no network required.
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trigger_audit


def _trigger(ticker="NUE", trigger_type="PRE_BREAKOUT", final_score=71,
             ai_grade="A", triggered_at="2026-08-07"):
    return {
        "ticker": ticker, "close_price": 272.63, "volume_surge": 0.93,
        "sma_50": 246.4, "rolling_high_52w": 280.11, "pivot_distance_pct": -2.67,
        "triggered_at": triggered_at, "retention_period": "1d", "ai_rating": 76,
        "quality_score": 47, "ai_grade": ai_grade, "final_score": final_score,
        "avg_volume_50": 1636197, "technical_score": 47, "rs_score": 100,
        "liquidity_score": 90, "sentiment_score": 55,
        "score_rationale": "NUE shows a solid ATR of 3.33%/day.",
        "atr_pct": 3.33, "est_days_to_target": 8, "adjusted_score": None,
        "failure_penalty": 0, "penalty_reason": None, "trigger_type": trigger_type,
    }


def _sb():
    sb = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    return sb


def _payloads(sb):
    out = []
    for c in sb.table.return_value.upsert.call_args_list:
        out.extend(c.args[0])
    return out


class TestTriggerHistoryPayload:

    def test_captures_ai_scores(self):
        """The scores are the entire point. A stored ticker without them cannot
        be linked back to why the bot rated it as it did."""
        sb = _sb()
        trigger_audit.save_trigger_history(sb, [_trigger()])
        p = _payloads(sb)[0]
        assert p["final_score"] == 71
        assert p["ai_rating"] == 76
        assert p["ai_grade"] == "A"
        assert p["rs_score"] == 100
        assert p["sentiment_score"] == 55
        assert "ATR of 3.33" in p["score_rationale"]

    def test_defaults_null_primary_key_parts(self):
        """trigger_type and triggered_at are PK components; NULL would fail."""
        sb = _sb()
        t = _trigger(); t["trigger_type"] = None; t["triggered_at"] = None
        trigger_audit.save_trigger_history(sb, [t])
        p = _payloads(sb)[0]
        assert p["trigger_type"] == "BREAKOUT"
        assert p["triggered_at"] is not None

    def test_upserts_on_composite_key(self):
        sb = _sb()
        trigger_audit.save_trigger_history(sb, [_trigger()])
        kw = sb.table.return_value.upsert.call_args.kwargs
        assert kw.get("on_conflict") == "triggered_at,ticker,trigger_type"

    def test_never_deletes(self):
        sb = _sb()
        trigger_audit.save_trigger_history(sb, [_trigger()])
        sb.table.return_value.delete.assert_not_called()

    def test_same_ticker_two_trigger_types_both_kept(self):
        """PK includes trigger_type, so BREAKOUT and PRE_BREAKOUT coexist."""
        sb = _sb()
        trigger_audit.save_trigger_history(
            sb, [_trigger(trigger_type="BREAKOUT"),
                 _trigger(trigger_type="PRE_BREAKOUT")])
        assert len(_payloads(sb)) == 2

    def test_empty_writes_nothing(self):
        sb = _sb()
        assert trigger_audit.save_trigger_history(sb, []) == 0
        sb.table.return_value.upsert.assert_not_called()

    def test_non_fatal_on_failure(self):
        sb = _sb()
        sb.table.return_value.upsert.return_value.execute.side_effect = \
            Exception("boom")
        trigger_audit.save_trigger_history(sb, [_trigger()])

    def test_chunks_at_100(self):
        sb = _sb()
        rows = [_trigger(ticker=f"T{i}") for i in range(250)]
        assert trigger_audit.save_trigger_history(sb, rows) == 250
        sizes = [len(c.args[0]) for c in sb.table.return_value.upsert.call_args_list]
        assert sizes == [100, 100, 50]


class TestDecisionLog:

    def test_records_reason_code_and_detail(self):
        sb = _sb()
        trigger_audit.record_trigger_decision(
            sb, _trigger(), "SKIPPED", trigger_audit.AI_VETO, detail="D-grade")
        p = _payloads(sb)[0]
        assert p["decision"] == "SKIPPED"
        assert p["reason_code"] == "AI_VETO"
        assert p["reason_detail"] == "D-grade"

    def test_snapshots_score_at_decision_time(self):
        """Snapshotted rather than joined: the trigger row may be re-scored on a
        later run, which would silently rewrite history."""
        sb = _sb()
        trigger_audit.record_trigger_decision(
            sb, _trigger(final_score=71), "SKIPPED", trigger_audit.SCORE_FLOOR,
            candidate_score=71.0, min_score=75.0)
        p = _payloads(sb)[0]
        assert p["final_score"] == 71
        assert p["candidate_score"] == 71.0
        assert p["min_score"] == 75.0

    def test_capacity_rejections_flagged(self):
        """A name skipped for lack of a slot says nothing about the quality
        model but everything about the cost of MAX_POSITIONS. Analysis must be
        able to separate the two without parsing prose."""
        sb = _sb()
        for code in (trigger_audit.SLOTS_FULL, trigger_audit.INSUFFICIENT_CASH,
                     trigger_audit.SHARES_ZERO):
            sb2 = _sb()
            trigger_audit.record_trigger_decision(sb2, _trigger(), "SKIPPED", code)
            assert _payloads(sb2)[0]["is_capacity"] is True, code

    def test_quality_rejections_not_flagged_as_capacity(self):
        for code in (trigger_audit.AI_VETO, trigger_audit.SCORE_FLOOR,
                     trigger_audit.BELOW_PIVOT, trigger_audit.NO_AI_SCORE):
            sb = _sb()
            trigger_audit.record_trigger_decision(sb, _trigger(), "SKIPPED", code)
            assert _payloads(sb)[0]["is_capacity"] is False, code

    def test_bulk_records_every_trigger(self):
        sb = _sb()
        trigs = [_trigger(ticker=t) for t in ("A", "B", "C")]
        n = trigger_audit.record_decisions_bulk(
            sb, trigs, "SKIPPED", trigger_audit.SLOTS_FULL, slots_free=0)
        assert n == 3
        assert {p["ticker"] for p in _payloads(sb)} == {"A", "B", "C"}
        assert all(p["reason_code"] == "SLOTS_FULL" for p in _payloads(sb))

    def test_decision_date_defaults_to_today(self):
        sb = _sb()
        trigger_audit.record_trigger_decision(sb, _trigger(), "BOUGHT",
                                              trigger_audit.BOUGHT)
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        assert _payloads(sb)[0]["decision_date"] == today

    def test_decision_keyed_by_date_not_trigger_date(self):
        """A trigger can be re-evaluated on several days within the lookback
        window and get a different verdict each day."""
        sb = _sb()
        trigger_audit.record_trigger_decision(
            sb, _trigger(triggered_at="2026-08-05"), "SKIPPED",
            trigger_audit.SLOTS_FULL, decision_date="2026-08-07")
        p = _payloads(sb)[0]
        assert p["decision_date"] == "2026-08-07"
        assert p["triggered_at"] == "2026-08-05"

    def test_upserts_on_composite_key(self):
        sb = _sb()
        trigger_audit.record_trigger_decision(sb, _trigger(), "BOUGHT",
                                              trigger_audit.BOUGHT)
        kw = sb.table.return_value.upsert.call_args.kwargs
        assert kw.get("on_conflict") == "decision_date,ticker,trigger_type"

    def test_non_fatal_on_failure(self):
        """A failure here must never interrupt a live buy cycle."""
        sb = _sb()
        sb.table.return_value.upsert.return_value.execute.side_effect = \
            Exception("connection reset")
        trigger_audit.record_trigger_decision(sb, _trigger(), "BOUGHT",
                                              trigger_audit.BOUGHT)

    def test_missing_table_reports_migration(self, capsys):
        sb = _sb()
        sb.table.return_value.upsert.return_value.execute.side_effect = \
            Exception('relation "trigger_decisions" does not exist')
        trigger_audit.record_trigger_decision(sb, _trigger(), "BOUGHT",
                                              trigger_audit.BOUGHT)
        assert "add_trigger_history.sql" in capsys.readouterr().out


class TestArchiveOrderingInScreener:
    """THE critical invariant, plus the subtler one about WHICH rows."""

    def _run(self, sb, existing):
        import technical_screener
        sb.table.return_value.select.return_value.execute.return_value = \
            MagicMock(data=existing)
        return technical_screener

    def test_archive_precedes_truncate(self):
        order = []
        sb = MagicMock()

        def _table(name):
            t = MagicMock()
            t.upsert.side_effect = lambda *a, **k: (
                order.append(f"upsert:{name}"), MagicMock())[1]
            t.delete.side_effect = lambda *a, **k: (
                order.append(f"delete:{name}"), MagicMock())[1]
            t.select.return_value.execute.return_value = MagicMock(
                data=[_trigger()])
            return t

        sb.table.side_effect = _table

        # Exercise the archive+truncate sequence exactly as the screener does.
        existing = sb.table("daily_triggers").select("*").execute().data
        trigger_audit.save_trigger_history(sb, existing)
        sb.table("daily_triggers").delete().neq("ticker", "X").execute()

        assert order.index("upsert:trigger_history") < order.index("delete:daily_triggers")

    def test_screener_source_archives_before_truncating(self):
        """Guards the real call site, not a reconstruction."""
        import inspect
        import technical_screener
        src = inspect.getsource(technical_screener)
        archive_at = src.index("save_trigger_history")
        truncate_at = src.index('delete().neq("ticker", "DUMMY_NEVER_MATCH")')
        assert archive_at < truncate_at, \
            "archive must appear before the truncate in technical_screener.py"

    def test_archives_outgoing_rows_not_incoming(self):
        """ai_evaluator.py writes scores back AFTER technical_screener inserts.
        Archiving the incoming rows would store NULL scores and silently defeat
        the purpose, so the call site must read the table first."""
        import inspect
        import technical_screener
        src = inspect.getsource(technical_screener)
        i = src.index("save_trigger_history")
        window = src[max(0, i - 600):i]
        assert 'table("daily_triggers").select' in window, \
            "archive must be fed from a SELECT of the existing rows"


class TestDecisionsWiredIntoBuyLoop:
    """The module can be perfect and still record nothing if the buy loop never
    calls it. These drive execution_agent.run_market_open_buys() end to end and
    assert on what actually reached trigger_audit."""

    def _run(self, triggers, holdings, monkeypatch):
        import execution_agent

        client = MagicMock()

        def _table(name):
            t = MagicMock()
            if name == "daily_triggers":
                t.select.return_value.gte.return_value.execute.return_value = \
                    MagicMock(data=list(triggers))
            elif name == "portfolio_positions":
                t.select.return_value.execute.return_value = MagicMock(data=holdings)
            elif name == "trade_history":
                t.select.return_value.eq.return_value.gte.return_value.execute.return_value = \
                    MagicMock(data=[])
            return t

        client.table.side_effect = _table

        calls = []

        def _rec(_c, trigger, decision, reason_code, **kw):
            calls.append({"ticker": trigger.get("ticker"),
                          "decision": decision, "reason": reason_code})
            return 1

        def _bulk(_c, trigs, decision, reason_code, **kw):
            for t in trigs:
                calls.append({"ticker": t.get("ticker"),
                              "decision": decision, "reason": reason_code})
            return len(trigs)

        monkeypatch.setattr(execution_agent.trigger_audit,
                            "record_trigger_decision", _rec)
        monkeypatch.setattr(execution_agent.trigger_audit,
                            "record_decisions_bulk", _bulk)
        monkeypatch.setattr(execution_agent, "get_supabase_client",
                            lambda: client)
        monkeypatch.setattr(execution_agent, "get_margin_loan", lambda ib: 0.0)
        monkeypatch.setattr(execution_agent, "get_own_cash", lambda ib: 100_000.0)
        # The CANSLIM "M" gate is fail-closed and would otherwise stand the buy
        # loop down before any decision row is written. It is exercised for real
        # in tests/test_market_direction.py.
        monkeypatch.setattr(execution_agent, "is_market_bullish", lambda: True)

        execution_agent.run_market_open_buys(MagicMock())
        return calls

    def test_full_portfolio_logs_every_foregone_trigger(self, monkeypatch):
        """The rows that make the opportunity cost of MAX_POSITIONS measurable."""
        import execution_agent
        trigs = [_trigger(ticker=t) for t in ("AAA", "BBB", "CCC")]
        holdings = [{"ticker": f"H{i}"} for i in range(execution_agent.MAX_POSITIONS)]
        calls = self._run(trigs, holdings, monkeypatch)
        assert {c["ticker"] for c in calls} == {"AAA", "BBB", "CCC"}
        assert all(c["reason"] == "SLOTS_FULL" for c in calls)
        assert all(c["decision"] == "SKIPPED" for c in calls)

    def test_ai_veto_is_logged(self, monkeypatch):
        calls = self._run([_trigger(ticker="AAA", ai_grade="D")], [], monkeypatch)
        assert {"ticker": "AAA", "decision": "SKIPPED",
                "reason": "AI_VETO"} in calls

    def test_missing_score_is_logged(self, monkeypatch):
        t = _trigger(ticker="AAA", ai_grade="B")
        t["final_score"] = None
        t["adjusted_score"] = None
        calls = self._run([t], [], monkeypatch)
        assert {"ticker": "AAA", "decision": "SKIPPED",
                "reason": "NO_AI_SCORE"} in calls

    def test_score_floor_rejection_is_logged(self, monkeypatch):
        """The near-miss candidates needed to test whether the floor is set
        anywhere near the right level."""
        t = _trigger(ticker="AAA", ai_grade="B", final_score=1)
        calls = self._run([t], [], monkeypatch)
        assert {"ticker": "AAA", "decision": "SKIPPED",
                "reason": "SCORE_FLOOR"} in calls

    def test_already_held_is_logged(self, monkeypatch):
        calls = self._run([_trigger(ticker="AAA")], [{"ticker": "AAA"}], monkeypatch)
        assert {"ticker": "AAA", "decision": "SKIPPED",
                "reason": "ALREADY_HELD"} in calls
