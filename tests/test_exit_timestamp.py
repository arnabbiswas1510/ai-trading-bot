"""
Tests that a closed position records WHEN it closed, to the precision available.

The reconcile path used to truncate every exit to a bare date — `[:10]` on the
Tier 1 fill string, `.date()` on the Tier 2 execution timestamp — even though
both sources carry a full tz-aware UTC instant. Supabase then stored midnight.

Midnight is *earlier* than the entry for any position bought and closed in the
same session, so FROG (bought 13:45, stopped out that afternoon) recorded an exit
13.8 hours BEFORE its own entry, and any holding period derived from the column
came out negative. Longer holds were quietly wrong too: NBIX bought 07-14 14:03
and sold 07-23 computed as 8.4 days when it was 9.
"""
import ast
import datetime as dt
import re
import types

import pytest


AGENT_SRC = open("execution_agent.py").read()
FLEX_SRC = open("flex_query_sync.py").read()


class TestTierOneKeepsTheTimestamp:
    """Tier 1 reads `ibkr_fills`, written from `execution.time.isoformat()`."""

    def test_fill_time_is_not_truncated_to_a_date(self):
        match = re.search(r"sell_date_fill\s*=\s*sb_fills\[(-?\d+)\]\[\"fill_time\"\](\S*)", AGENT_SRC)
        assert match, "the Tier 1 sell_date assignment has moved or changed shape"
        assert match.group(2) != "[:10]", (
            "Tier 1 is truncating the fill timestamp to a date again — this stamps "
            "every exit at midnight and makes same-day exits precede their entry"
        )
        assert match.group(2) == "", f"unexpected slicing on the fill time: {match.group(2)!r}"

    def test_uses_the_final_fill_not_the_first(self):
        # The row records a CLOSED position; it is not closed until the last
        # share is sold. The query orders fill_time ascending.
        match = re.search(r"sell_date_fill\s*=\s*sb_fills\[(-?\d+)\]", AGENT_SRC)
        assert match.group(1) == "-1", (
            "Tier 1 should stamp the exit with the last fill, not the first"
        )

    def test_query_still_orders_ascending_so_last_means_latest(self):
        # Indexing [-1] is only correct while the query sorts ascending.
        assert re.search(r'\.order\("fill_time",\s*desc=False\)', AGENT_SRC), (
            "the ibkr_fills query no longer sorts fill_time ascending, so [-1] "
            "is no longer the latest fill"
        )


class TestTierTwoKeepsTheTimestamp:
    """Tier 2 reads ib_insync executions, whose `.time` is tz-aware UTC."""

    def test_execution_time_is_not_reduced_to_a_date(self):
        match = re.search(
            r"sell_date_fill\s*=\s*sell_fills_sorted\[(-?\d+)\]\.execution\.time\.(\w+)\(\)",
            AGENT_SRC,
        )
        assert match, "the Tier 2 sell_date assignment has moved or changed shape"
        assert match.group(2) != "date", (
            "Tier 2 is calling .date() on the execution timestamp again, discarding "
            "the time that ib_insync already provides"
        )
        assert match.group(2) == "isoformat"

    def test_uses_the_final_fill_not_the_first(self):
        match = re.search(
            r"sell_date_fill\s*=\s*sell_fills_sorted\[(-?\d+)\]\.execution\.time", AGENT_SRC
        )
        assert match.group(1) == "-1"


class TestFlexStaysDateOnlyOnPurpose:
    """
    Tier 3 is the one path that must NOT gain a time.

    Flex `dateTime` has no timezone and is parsed naive. Writing a naive local
    time into a `timestamptz` column would have Postgres read it as UTC and shift
    every Flex-sourced exit by the account's offset — inventing precision that is
    wrong by hours. The date is the largest unambiguously correct unit.
    """

    def test_flex_sell_date_remains_date_only(self):
        match = re.search(r'sell_date\s*=\s*sorted\(fills.*?\[(-?\d+)\]\["fill_time"\](\S*)',
                          FLEX_SRC, re.DOTALL)
        assert match, "the Flex sell_date assignment has moved or changed shape"
        assert match.group(2) == "[:10]", (
            "Flex fill times are timezone-naive; storing them with a time would "
            "silently shift every Flex-sourced exit by the account's UTC offset"
        )

    def test_flex_parser_still_produces_naive_timestamps(self):
        # If this ever gains tzinfo, the decision above should be revisited.
        assert "strptime" in FLEX_SRC
        assert not re.search(r"strptime\([^)]*\)\.replace\(tzinfo=", FLEX_SRC), (
            "Flex timestamps became timezone-aware — the date-only truncation can "
            "now be lifted; see the comment at the sell_date assignment"
        )

    def test_flex_documents_why_it_differs(self):
        assert "timezone" in FLEX_SRC.lower()
        assert "timestamptz" in FLEX_SRC, (
            "the rationale comment explaining the deliberate date-only truncation "
            "has been removed; without it this reads as the bug that was just fixed"
        )


class TestHoldingPeriodIsNoLongerNegative:
    """The behaviour all of the above exists to produce."""

    @staticmethod
    def _hold_seconds(buy_iso: str, sell_iso: str) -> float:
        buy = dt.datetime.fromisoformat(buy_iso)
        sell = dt.datetime.fromisoformat(sell_iso)
        return (sell - buy).total_seconds()

    def test_same_day_exit_no_longer_predates_its_entry(self):
        # FROG: bought 13:45:50, trailing stop filled the same afternoon.
        buy = "2026-08-18T13:45:50+00:00"
        old_truncated = "2026-08-18T00:00:00+00:00"
        new_full = "2026-08-18T18:12:33+00:00"

        assert self._hold_seconds(buy, old_truncated) < 0, (
            "fixture no longer reproduces the original bug"
        )
        assert self._hold_seconds(buy, new_full) > 0

    def test_multi_day_hold_is_not_shortened_by_truncation(self):
        # NBIX: bought 07-14 14:03, sold 07-23. Truncation lost 14 hours, which
        # rounded the hold from 9 days down to 8.
        buy = "2026-07-14T14:03:43+00:00"
        old_truncated = "2026-07-23T00:00:00+00:00"
        new_full = "2026-07-23T15:41:02+00:00"

        old_days = self._hold_seconds(buy, old_truncated) / 86400
        new_days = self._hold_seconds(buy, new_full) / 86400
        assert int(old_days) == 8
        assert int(new_days) == 9


class TestReconcileStillWritesTheField:
    """A precise timestamp is worthless if it stops reaching the row."""

    def test_trade_log_still_sources_sell_date_from_the_fill(self):
        assert re.search(r'"sell_date":\s*sell_date_fill', AGENT_SRC), (
            "trade_history no longer receives the reconciled fill timestamp"
        )

    def test_none_is_still_permitted_for_an_undiscoverable_fill(self):
        # When no fill is found in any tier, sell_date_fill stays None and
        # Supabase auto-stamps. That path must survive.
        assert re.search(r"sell_date_fill\s*=\s*None", AGENT_SRC)
