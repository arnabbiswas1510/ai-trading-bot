"""
tests/test_rs_percentile.py

Covers the shadow relative-strength ranking added 2026-09-17.

The single most important test in this file is
TestLiveScoringIsUntouched::test_final_score_ignores_shadow_columns. The whole
premise of shipping this feature was that it CANNOT change what the bot trades.
If that test ever fails, the feature has stopped being a shadow and the
provisional-decision register entry (rs-percentile-shadow) is invalid.

See decisions/2026-09-17_rs-percentile-shadow-column.md.
"""

import pytest

from scoring import (
    RS_SHADOW_COLUMNS,
    assign_rs_percentiles,
    compute_final_score,
    compute_rs_excess,
    compute_rs_score,
    rank_percentiles,
)


class TestComputeRsExcess:
    def test_subtracts_benchmark(self):
        assert compute_rs_excess(30.0, 8.0) == 22.0

    def test_is_not_clipped_unlike_rs_score(self):
        # The defect being investigated: compute_rs_score flattens everything
        # above +10% excess to a single value, so these two very different
        # candidates are indistinguishable to the live scorer. The shadow
        # column must keep them apart.
        assert compute_rs_score(120.0, 8.0) == compute_rs_score(19.0, 8.0) == 100
        assert compute_rs_excess(120.0, 8.0) != compute_rs_excess(19.0, 8.0)

    def test_handles_garbage_without_raising(self):
        assert compute_rs_excess(None, 8.0) == 0.0
        assert compute_rs_excess("abc", 8.0) == 0.0


class TestRankPercentiles:
    def test_empty_cohort(self):
        assert rank_percentiles([]) == []

    def test_single_candidate_is_neutral_not_top(self):
        # A cohort of one has no comparison. Returning 99 would fabricate a
        # top-decile signal out of nothing.
        assert rank_percentiles([42.0]) == [50]

    def test_endpoints_span_the_full_range(self):
        out = rank_percentiles([1.0, 2.0, 3.0, 4.0])
        assert out[0] == 1
        assert out[-1] == 99

    def test_is_monotonic_in_value(self):
        vals = [5.0, -3.0, 22.0, 0.5, 11.0]
        out = rank_percentiles(vals)
        pairs = sorted(zip(vals, out))
        ranks = [p[1] for p in pairs]
        assert ranks == sorted(ranks)

    def test_ties_share_a_rank(self):
        out = rank_percentiles([7.0, 7.0, 7.0])
        assert out[0] == out[1] == out[2]

    def test_ties_take_the_average_position(self):
        # Two lowest values tie: they span 1-based ranks 1 and 2, mean 1.5.
        # pct = 1 + 98*(1.5-1)/(3-1) = 25.5 -> 26 (banker-free round via round()).
        out = rank_percentiles([1.0, 1.0, 9.0])
        assert out[0] == out[1] == 26
        assert out[2] == 99

    def test_non_numeric_entries_rank_none_and_do_not_shift_others(self):
        clean = rank_percentiles([1.0, 2.0, 3.0])
        dirty = rank_percentiles([1.0, None, 2.0, "x", 3.0])
        assert dirty[1] is None and dirty[3] is None
        assert [dirty[0], dirty[2], dirty[4]] == clean

    def test_nan_and_inf_are_excluded(self):
        out = rank_percentiles([float("nan"), float("inf"), 1.0, 2.0])
        assert out[0] is None and out[1] is None
        assert out[2] == 1 and out[3] == 99

    def test_all_invalid_returns_all_none(self):
        assert rank_percentiles([None, "x"]) == [None, None]

    def test_output_is_always_within_1_99(self):
        out = rank_percentiles([float(i) for i in range(200)])
        assert all(1 <= v <= 99 for v in out)

    def test_saturated_cohort_still_ranks_nothing(self):
        # Sanity check on the real-world failure mode: if every candidate truly
        # has identical excess return, a percentile cannot help either. This is
        # the honest floor of what the feature can do.
        out = rank_percentiles([12.0] * 8)
        assert len(set(out)) == 1


class TestAssignRsPercentiles:
    def test_annotates_each_trigger(self):
        triggers = [
            {"ticker": "AAA", "rs_excess_return": 30.0},
            {"ticker": "BBB", "rs_excess_return": 2.0},
            {"ticker": "CCC", "rs_excess_return": 14.0},
        ]
        assign_rs_percentiles(triggers)
        by = {t["ticker"]: t["rs_percentile"] for t in triggers}
        assert by["BBB"] < by["CCC"] < by["AAA"]

    def test_discriminates_where_rs_score_cannot(self):
        # The motivating case, with real numbers from the 2026-09-17 archive:
        # all three saturate rs_score at 100, so the live scorer sees them as
        # identical. The shadow percentile separates them.
        spy = 8.0
        rows = [
            {"ticker": "CDNA", "r12": 121.2},
            {"ticker": "LPG", "r12": 57.0},
            {"ticker": "DHT", "r12": 18.6},
        ]
        for r in rows:
            r["rs_score"] = compute_rs_score(r["r12"], spy)
            r["rs_excess_return"] = compute_rs_excess(r["r12"], spy)
        assert {r["rs_score"] for r in rows} == {100}

        assign_rs_percentiles(rows)
        assert len({r["rs_percentile"] for r in rows}) == 3

    def test_empty_list_is_safe(self):
        assert assign_rs_percentiles([]) == []

    def test_missing_field_does_not_raise(self):
        triggers = [{"ticker": "AAA"}, {"ticker": "BBB"}]
        assign_rs_percentiles(triggers)
        assert all("rs_percentile" in t for t in triggers)

    def test_never_raises_on_malformed_input(self):
        triggers = [{"ticker": "AAA", "rs_excess_return": object()}]
        assign_rs_percentiles(triggers)
        assert triggers[0]["rs_percentile"] is None


class TestLiveScoringIsUntouched:
    """The load-bearing guarantee: this feature cannot change a trade."""

    def test_final_score_ignores_shadow_columns(self):
        # compute_final_score takes exactly five components and none of them is
        # a shadow column. If someone wires rs_percentile in, this signature
        # check fails and the register entry must be revisited.
        import inspect

        params = set(inspect.signature(compute_final_score).parameters)
        assert params == {
            "technical_score", "liquidity_score", "ai_score",
            "sentiment_score", "rs_score",
        }
        assert not params & set(RS_SHADOW_COLUMNS)

    def test_final_score_value_is_unchanged_by_saturation(self):
        # Pin the arithmetic so a future edit to the weights is caught here
        # rather than in production.
        assert compute_final_score(46, 72, 85, 50, 100) == 68

    def test_rs_score_behaviour_is_unchanged(self):
        assert compute_rs_score(30.0, 8.0) == 100     # excess +22 -> clipped
        assert compute_rs_score(8.0, 8.0) == 50       # excess 0
        assert compute_rs_score(3.0, 8.0) == 25       # excess -5  -> 50 + (-5*5)
        assert compute_rs_score(-5.0, 8.0) == 0       # excess -13 -> below band
        assert compute_rs_score(-50.0, 8.0) == 0

    def test_shadow_column_tuple_is_the_expected_three(self):
        assert RS_SHADOW_COLUMNS == (
            "rs_12w_return", "rs_excess_return", "rs_percentile",
        )


class TestArchiveCarriesShadowColumns:
    def test_history_columns_include_the_shadow_three(self):
        import trigger_audit

        for col in RS_SHADOW_COLUMNS:
            assert col in trigger_audit._HISTORY_COLUMNS, (
                f"{col} missing from _HISTORY_COLUMNS — it would be dropped at "
                "archive time and could never be paired with forward returns"
            )
