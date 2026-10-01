"""test_unfilled_slots.py — the once-daily "why are slots empty?" summary.

Covers buying.maybe_report_unfilled_slots and its wiring into
run_market_open_buys:

  - fires exactly once when the book has idle slots and nothing has been sent
    today (dedup ledger empty);
  - is a no-op when the summary already went out today (dedup ledger hit);
  - is a no-op when the portfolio is full (no idle slots);
  - reports the single top-level stand-down reason when the market is bearish;
  - aggregates per-trigger skip reasons when the market is open but no candidate
    cleared the gates.
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_portfolio_item,
    make_position, make_trigger,
)
import execution_agent


def _held(n):
    """n stock positions in portfolio_positions (each occupies one slot)."""
    names = ["AAA", "BBB", "CCC", "DDD", "EEE"][:n]
    return [make_position(t) for t in names]


def _run(ib, supabase, is_bullish=True, margin=0.0):
    with patch("execution_agent.supabase", supabase), \
         patch("execution_agent.get_own_cash", return_value=20_000.0), \
         patch("execution_agent.get_margin_loan", return_value=margin), \
         patch("execution_agent.fetch_ibkr_delayed_price", return_value=(0.0, "")), \
         patch("execution_agent.is_market_bullish", return_value=is_bullish), \
         patch("execution_agent.notifier") as mock_notifier, \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return mock_notifier


class TestUnfilledSlotSummary:

    def test_fires_once_when_slots_idle_and_not_yet_sent(self):
        """1 held, 4 idle, nothing sent today → exactly one summary."""
        ib = make_ib_mock(symbols=["AAA"])
        supa = make_supabase_mock(
            daily_triggers=[], portfolio=_held(1), slot_report_sent=False)
        notifier = _run(ib, supa)
        assert notifier.notify_unfilled_slots.call_count == 1
        args = notifier.notify_unfilled_slots.call_args.args
        # (free_slots, max_slots, held_count, body)
        assert args[0] == execution_agent.MAX_POSITIONS - 1
        assert args[2] == 1

    def test_no_duplicate_when_already_sent_today(self):
        """Dedup ledger already has today's row → no summary."""
        ib = make_ib_mock(symbols=["AAA"])
        supa = make_supabase_mock(
            daily_triggers=[], portfolio=_held(1), slot_report_sent=True)
        notifier = _run(ib, supa)
        notifier.notify_unfilled_slots.assert_not_called()

    def test_no_summary_when_portfolio_full(self):
        """MAX_POSITIONS held → zero idle slots → nothing to report."""
        n = execution_agent.MAX_POSITIONS
        ib = make_ib_mock(symbols=["AAA", "BBB", "CCC", "DDD", "EEE"][:n])
        supa = make_supabase_mock(
            daily_triggers=[], portfolio=_held(n), slot_report_sent=False)
        notifier = _run(ib, supa)
        notifier.notify_unfilled_slots.assert_not_called()

    def test_bearish_market_reports_standdown_reason(self):
        """Bearish 'M' gate → single top-level reason mentioning market direction."""
        ib = make_ib_mock(symbols=["AAA"])
        supa = make_supabase_mock(
            daily_triggers=[make_trigger("ZZZ")], portfolio=_held(1),
            slot_report_sent=False)
        notifier = _run(ib, supa, is_bullish=False)
        assert notifier.notify_unfilled_slots.call_count == 1
        body = notifier.notify_unfilled_slots.call_args.args[3]
        assert "arket direction" in body

    def test_aggregates_skip_reasons_when_open(self):
        """Open market, candidates all skipped → bulleted per-reason breakdown."""
        ib = make_ib_mock(symbols=["AAA"])
        decisions = [
            {"ticker": "XYZ", "reason_code": "SCORE_FLOOR", "decision": "SKIPPED"},
            {"ticker": "QRS", "reason_code": "SCORE_FLOOR", "decision": "SKIPPED"},
            {"ticker": "TUV", "reason_code": "EXTENDED_ABOVE_PIVOT", "decision": "SKIPPED"},
        ]
        supa = make_supabase_mock(
            daily_triggers=[make_trigger("SKIPME", final_score=None)],
            portfolio=_held(1),
            trigger_decisions=decisions, slot_report_sent=False)
        notifier = _run(ib, supa)
        assert notifier.notify_unfilled_slots.call_count == 1
        body = notifier.notify_unfilled_slots.call_args.args[3]
        assert "2 below the quality-score" in body
        assert "1 extended too far above the pivot" in body

    def test_no_duplicate_within_process_when_db_latch_write_denied(self):
        """RLS denies the latch UPSERT → the in-process latch still stops a resend.

        Reproduces the 2026-10-01 spam: the daily_notifications SELECT returns
        empty every cycle (RLS hides/denies the write), so the DB latch never
        records the send. Two buy cycles in one process must still yield exactly
        one summary — the second is suppressed by the in-memory backstop.
        """
        ib = make_ib_mock(symbols=["AAA"])
        supa = make_supabase_mock(
            daily_triggers=[], portfolio=_held(1),
            slot_report_sent=False, slot_report_write_denied=True)

        first = _run(ib, supa)
        assert first.notify_unfilled_slots.call_count == 1

        # Same process, same supabase mock (DB latch still empty): a second
        # 15-minute cycle must not re-send.
        second = _run(ib, supa)
        second.notify_unfilled_slots.assert_not_called()
