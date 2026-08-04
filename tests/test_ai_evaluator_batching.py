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
