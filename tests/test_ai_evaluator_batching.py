"""
Regression tests for the AI evaluator batching / completeness logic.

Background: ai_evaluator.py used to send every trigger in a single prompt. With
~30 tickers gpt-4o-mini returned only the first few and last few entries and
silently dropped the middle ("lost in the middle"), leaving those daily_triggers
rows with a NULL final_score. Those rows then slipped through the buy gate via a
quality_score fallback, bypassing all AI guardrails.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test_key")
os.environ.setdefault("OPENAI_API_KEY", "test_key")

import ai_evaluator  # noqa: E402


def _triggers(n):
    return [{"ticker": f"TK{i:02d}", "close_price": 100.0} for i in range(n)]


def _rating():
    return {"rating": 80, "sentiment": 70, "rationale": "ok"}


class TestBatching:

    def test_all_tickers_rated_across_multiple_batches(self):
        trigs = _triggers(30)
        seen_batches = []

        def fake_call(prompt):
            asked = [t["ticker"] for t in trigs if f"- {t['ticker']}:" in prompt]
            seen_batches.append(asked)
            return {t: _rating() for t in asked}

        with patch.object(ai_evaluator, "call_ai_batch", side_effect=fake_call), \
             patch.object(ai_evaluator, "AI_BATCH_SIZE", 8):
            ratings, missing = ai_evaluator.evaluate_triggers(trigs, {}, {}, "")

        assert missing == []
        assert len(ratings) == 30
        # 30 tickers / batch of 8 -> 4 requests, never one giant prompt
        assert len(seen_batches) == 4
        assert all(len(b) <= 8 for b in seen_batches)

    def test_middle_dropped_tickers_are_retried_and_recovered(self):
        """Simulates the exact production failure: model drops middle entries."""
        trigs = _triggers(8)
        calls = {"n": 0}

        def fake_call(prompt):
            calls["n"] += 1
            asked = [t["ticker"] for t in trigs if f"- {t['ticker']}:" in prompt]
            if calls["n"] == 1:
                # first attempt: return only head and tail, drop the middle
                asked = asked[:2] + asked[-1:]
            return {t: _rating() for t in asked}

        with patch.object(ai_evaluator, "call_ai_batch", side_effect=fake_call), \
             patch.object(ai_evaluator, "AI_BATCH_SIZE", 8), \
             patch.object(ai_evaluator, "AI_BATCH_RETRIES", 1):
            ratings, missing = ai_evaluator.evaluate_triggers(trigs, {}, {}, "")

        assert calls["n"] == 2, "should have retried the dropped tickers"
        assert missing == []
        assert len(ratings) == 8

    def test_persistently_missing_tickers_are_reported(self):
        trigs = _triggers(4)

        def fake_call(prompt):
            asked = [t["ticker"] for t in trigs if f"- {t['ticker']}:" in prompt]
            # model never returns these two, no matter how often we ask
            return {t: _rating() for t in asked if t not in ("TK02", "TK03")}

        with patch.object(ai_evaluator, "call_ai_batch", side_effect=fake_call), \
             patch.object(ai_evaluator, "AI_BATCH_SIZE", 4), \
             patch.object(ai_evaluator, "AI_BATCH_RETRIES", 1):
            ratings, missing = ai_evaluator.evaluate_triggers(trigs, {}, {}, "")

        assert sorted(missing) == ["TK02", "TK03"]
        assert len(ratings) == 2

    def test_api_failure_on_one_batch_does_not_lose_other_batches(self):
        trigs = _triggers(16)

        def fake_call(prompt):
            asked = [t["ticker"] for t in trigs if f"- {t['ticker']}:" in prompt]
            if "TK00" in asked:
                raise RuntimeError("OpenAI 500")
            return {t: _rating() for t in asked}

        with patch.object(ai_evaluator, "call_ai_batch", side_effect=fake_call), \
             patch.object(ai_evaluator, "AI_BATCH_SIZE", 8), \
             patch.object(ai_evaluator, "AI_BATCH_RETRIES", 0):
            ratings, missing = ai_evaluator.evaluate_triggers(trigs, {}, {}, "")

        # first batch failed entirely, second batch still recorded
        assert len(missing) == 8
        assert len(ratings) == 8

    def test_ticker_case_and_whitespace_drift_is_tolerated(self):
        trigs = _triggers(3)

        def fake_call(prompt):
            asked = [t["ticker"] for t in trigs if f"- {t['ticker']}:" in prompt]
            return {f"  {t.lower()} ": _rating() for t in asked}

        with patch.object(ai_evaluator, "call_ai_batch", side_effect=fake_call), \
             patch.object(ai_evaluator, "AI_BATCH_SIZE", 8):
            ratings, missing = ai_evaluator.evaluate_triggers(trigs, {}, {}, "")

        assert missing == []
        assert len(ratings) == 3


class TestPromptCompleteness:

    def test_prompt_demands_an_entry_for_every_ticker(self):
        trigs = _triggers(5)
        prompt = ai_evaluator.build_prompt(trigs, {}, {}, "")
        assert "exactly 5 entries" in prompt
        for t in trigs:
            assert t["ticker"] in prompt
        assert "Required tickers:" in prompt

    def test_prompt_only_contains_its_own_batch(self):
        trigs = _triggers(10)
        prompt = ai_evaluator.build_prompt(trigs[:4], {}, {}, "")
        assert "- TK00:" in prompt
        assert "- TK03:" in prompt
        assert "- TK04:" not in prompt


class TestTradeHistoryLearning:

    def test_build_trade_history_index_groups_and_orders(self):
        rows = [
            {"ticker": "NVDA", "percent_return": -3.2},
            {"ticker": "nvda", "percent_return": 2.1},
            {"ticker": "AAPL", "percent_return": -1.0},
        ]
        idx = ai_evaluator.build_trade_history_index(rows)
        assert idx["NVDA"] == [-3.2, 2.1]
        assert idx["AAPL"] == [-1.0]

    def test_history_penalty_applies_on_repeated_recent_losses(self):
        idx = {"TSLA": [-4.5, -2.0, 1.0]}
        penalty, reason = ai_evaluator.compute_trade_history_penalty("TSLA", idx)
        assert penalty > 0
        assert reason is not None
        assert "loser" in reason

    def test_history_penalty_is_zero_without_recent_loss_pattern(self):
        idx = {"MSFT": [1.2, 0.8, 2.4]}
        penalty, reason = ai_evaluator.compute_trade_history_penalty("MSFT", idx)
        assert penalty == 0
        assert reason is None


class TestNextEarningsExtraction:
    """`_next_earnings_from_rows` picks the earliest UPCOMING earnings date from
    FMP `/stable/earnings` rows (which are newest-first and mix past + future)."""

    def _today(self):
        import datetime
        return datetime.date(2026, 9, 28)

    def test_picks_earliest_future_date(self):
        rows = [
            {"symbol": "AAPL", "date": "2026-10-29", "epsActual": None},
            {"symbol": "AAPL", "date": "2026-07-30", "epsActual": 2.02},
            {"symbol": "AAPL", "date": "2027-01-28", "epsActual": None},
        ]
        assert ai_evaluator._next_earnings_from_rows(rows, self._today()) == "2026-10-29"

    def test_date_equal_to_today_counts_as_upcoming(self):
        rows = [{"date": "2026-09-28"}, {"date": "2026-06-01"}]
        assert ai_evaluator._next_earnings_from_rows(rows, self._today()) == "2026-09-28"

    def test_all_past_returns_none(self):
        rows = [{"date": "2026-07-30"}, {"date": "2026-04-30"}]
        assert ai_evaluator._next_earnings_from_rows(rows, self._today()) is None

    def test_empty_or_malformed_rows_return_none(self):
        assert ai_evaluator._next_earnings_from_rows([], self._today()) is None
        assert ai_evaluator._next_earnings_from_rows(None, self._today()) is None
        assert ai_evaluator._next_earnings_from_rows(
            [{"date": None}, {"nodate": 1}, {"date": "bad"}], self._today()) is None


class TestNewsAndEarningsEndpoints:
    """The AI was news-blind because the legacy /api/v3/stock_news endpoint now
    403s; the fetchers must call the supported `stable` endpoints and fail soft."""

    def test_news_uses_stable_endpoint(self):
        captured = {}

        class _Resp:
            status_code = 200
            def json(self): return [{"title": "Headline A"}, {"title": ""}]

        def _fake_get(url, timeout=8):
            captured["url"] = url
            return _Resp()

        with patch("ai_evaluator.FMP_API_KEY", "k"), \
             patch("ai_evaluator.requests.get", _fake_get):
            out = ai_evaluator.fetch_news_headlines("AAPL")
        assert "/stable/news/stock" in captured["url"]
        assert "symbols=AAPL" in captured["url"]
        assert out == ["Headline A"]

    def test_news_non_200_returns_empty(self):
        class _Resp:
            status_code = 403
            def json(self): return []
        with patch("ai_evaluator.FMP_API_KEY", "k"), \
             patch("ai_evaluator.requests.get", return_value=_Resp()):
            assert ai_evaluator.fetch_news_headlines("AAPL") == []

    def test_earnings_uses_stable_symbol_endpoint(self):
        captured = {}

        class _Resp:
            status_code = 200
            def json(self): return [{"date": "2099-01-01", "epsActual": None}]

        def _fake_get(url, timeout=8):
            captured["url"] = url
            return _Resp()

        with patch("ai_evaluator.FMP_API_KEY", "k"), \
             patch("ai_evaluator.requests.get", _fake_get):
            out = ai_evaluator.fetch_next_earnings_date("AAPL")
        assert "/stable/earnings" in captured["url"]
        assert "symbol=AAPL" in captured["url"]
        assert out == "2099-01-01"

    def test_earnings_failure_returns_none(self):
        with patch("ai_evaluator.FMP_API_KEY", "k"), \
             patch("ai_evaluator.requests.get", side_effect=RuntimeError("boom")):
            assert ai_evaluator.fetch_next_earnings_date("AAPL") is None
