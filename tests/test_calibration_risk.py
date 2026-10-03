"""Hand-computed, offline risk statistics and invalid-data regression cases."""
import copy
import datetime as dt
import hashlib
import json
import math
import statistics

import pytest

from market_calendar import session_bounds
from research.calibration_risk import calculate_risk


def signed(reference):
    reference = copy.deepcopy(reference)
    reference.pop("sha256", None)
    reference["sha256"] = hashlib.sha256(json.dumps(
        reference, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    return reference


def reference(rows=None, status="available"):
    return signed({
        "schema_version": 1, "status": status, "source": "US Treasury test fixture",
        "retrieved_at": "2026-10-03T20:00:00Z",
        "observations": rows if rows is not None else [
            {"date": f"2026-09-{day:02d}", "annual_yield_pct": 3.65}
            for day in range(1, 31)
        ],
        "urls": ["https://home.treasury.gov/test-fixture"],
        "error": None if status == "available" else "offline",
    })


def campaign(equities, days=None, initial=100.0, intraday=None):
    if days is None:
        days, day = [], dt.date(2026, 9, 21)
        while len(days) < len(equities):
            if session_bounds(day):
                days.append(day.isoformat())
            day += dt.timedelta(days=1)
    first = session_bounds(days[0])[0].isoformat()
    data = {"initial_state": {"timestamp": first}, "events": [], "initial_positions": []}
    run = {"initial_equity": initial, "initial_positions": [], "position_sales": [],
           "equity_curve": [{"timestamp": first, "equity": initial, "cash": initial}]}
    for i, (day, equity) in enumerate(zip(days, equities)):
        if intraday is not None:
            stamp = (session_bounds(day)[0] + dt.timedelta(hours=1)).isoformat()
            data["events"].append({"type": "monitor", "session": day, "timestamp": stamp})
            run["equity_curve"].append({"timestamp": stamp, "equity": intraday[i], "cash": 0})
        stamp = (session_bounds(day)[1] - dt.timedelta(minutes=10)).isoformat()
        data["events"].append({"type": "end_mark", "session": day, "timestamp": stamp})
        run["equity_curve"].append({"timestamp": stamp, "equity": equity, "cash": 0})
    return data, run


def calc(equities, **kwargs):
    return calculate_risk(*campaign(equities, **kwargs), reference())


def sale(ticker, net, partial=False, buy_date="2026-09-21"):
    return {"ticker": ticker, "buy_date": buy_date, "sell_date": "2026-09-23T19:00:00Z",
            "shares": 10, "partial": partial, "net_profit_loss": net}


def test_hand_computed_sharpe_sortino_calmar_and_window():
    result = calc([110, 121, 108.9, 119.79], initial=100)
    values = [.1, -.1, .1]
    excess = [r - .0001 for r in values]
    m = result["metrics"]
    assert result["sample"]["sessions"] == 4
    assert result["sample"]["daily_returns"] == 3
    assert m["sharpe"] == pytest.approx(statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252))
    downside = math.sqrt(sum(min(v, 0) ** 2 for v in excess) / 3)
    assert m["sortino"] == pytest.approx(statistics.mean(excess) / downside * math.sqrt(252))
    assert m["annualized_volatility_pct"] == pytest.approx(statistics.stdev(values) * math.sqrt(252) * 100)
    growth = (1.1 * .9 * 1.1) ** (252 / 3) - 1
    assert m["annualized_return_pct"] == pytest.approx(growth * 100)
    assert m["calmar"] == pytest.approx(growth / .1)
    assert m["session_max_drawdown_pct"] == pytest.approx(10)
    assert m["session_max_drawdown"] == pytest.approx(.1)
    assert m["total_return_pct"] == pytest.approx(19.79)
    assert m["daily_window_return_pct"] == pytest.approx(8.9)
    assert m["worst_day_pct"] == pytest.approx(-10)
    assert m["best_day_pct"] == pytest.approx(10)
    assert result["daily_returns"][0]["rf_return"] == pytest.approx(.0001)
    assert result["daily_returns"][0]["treasury_date"] == "2026-09-20"
    assert any("statistical confidence" in s for s in result["warnings"])
    assert any("not observed annual profit" in s for s in result["warnings"])


def test_intraday_marks_are_not_independent_daily_returns_or_calmar_drawdown():
    normal = calc([100, 110, 99, 105, 106])
    noisy = calc([100, 110, 99, 105, 106], intraday=[100, 180, 40, 80, 105])
    assert noisy["sample"]["daily_returns"] == 4
    assert noisy["daily_returns"] == normal["daily_returns"]
    for name in ("sharpe", "sortino", "calmar", "annualized_return_pct", "session_max_drawdown_pct"):
        assert noisy["metrics"][name] == normal["metrics"][name]
    assert noisy["metrics"]["max_sampled_drawdown_pct"] == pytest.approx((1 - 40 / 180) * 100)
    assert noisy["metrics"]["max_sampled_drawdown_pct"] > normal["metrics"]["max_sampled_drawdown_pct"]
    assert "NOT official" in noisy["methodology"]["daily_sampling"]
    assert noisy["daily_equity"][0]["timestamp"].endswith("19:50:00Z")


def test_last_end_mark_and_occurrence_matching_not_last_intraday_mark():
    data, run = campaign([100, 101, 102])
    # A seed and first end mark may share a timestamp; alignment must use the event sample.
    data["initial_state"]["timestamp"] = data["events"][0]["timestamp"]
    run["equity_curve"][0]["timestamp"] = data["events"][0]["timestamp"]
    run["equity_curve"][0]["equity"] = 50
    extra = copy.deepcopy(data["events"][1])
    extra["timestamp"] = (session_bounds(extra["session"])[1] - dt.timedelta(minutes=5)).isoformat()
    data["events"].insert(2, extra)
    run["equity_curve"].insert(3, {"timestamp": extra["timestamp"], "equity": 110})
    result = calculate_risk(data, run, reference())
    assert [r["equity"] for r in result["daily_equity"]] == [100, 110, 102]
    assert result["sample"]["daily_returns"] == 2


def test_weekend_and_exchange_holiday_use_calendar_day_accrual_and_prior_date():
    days = ["2026-09-04", "2026-09-08", "2026-09-09"]  # Labor Day on Monday.
    data, run = campaign([100, 102, 101], days=days)
    frozen = reference([
        {"date": "2026-09-03", "annual_yield_pct": 3.65},
        {"date": "2026-09-04", "annual_yield_pct": 7.30},
        {"date": "2026-09-08", "annual_yield_pct": 99.0},
    ])
    result = calculate_risk(data, run, frozen)
    first, second = result["daily_returns"]
    assert first["calendar_days"] == 4
    assert first["rf_return"] == pytest.approx(.0004)
    assert first["treasury_date"] == "2026-09-03"
    assert second["rf_return"] == pytest.approx(.0002)
    assert second["treasury_date"] == "2026-09-04"
    assert result["metrics"]["sharpe"] is not None


@pytest.mark.parametrize("rows,reason", [
    ([{"date": "2026-09-21", "annual_yield_pct": 5}], "strictly before"),
    ([{"date": "2026-09-13", "annual_yield_pct": 5}], "stale"),
    ([], "no Treasury observations"),
])
def test_missing_or_stale_rate_invalidates_ratios_without_dropping_pairs(rows, reason):
    result = calculate_risk(*campaign([100, 101, 99]), reference(rows))
    assert len(result["daily_returns"]) == 2
    assert result["metrics"]["sharpe"] is None
    assert result["metrics"]["sortino"] is None
    assert reason in result["unavailable"]["sharpe"]
    assert any(row["rf_return"] is None for row in result["daily_returns"])
    assert result["metrics"]["annualized_volatility_pct"] is not None
    assert result["metrics"]["calmar"] is not None


def test_seven_day_age_allowed_but_next_pair_not_silently_discarded():
    frozen = reference([{"date": "2026-09-14", "annual_yield_pct": 3.65}])
    result = calculate_risk(*campaign([100, 101, 99]), frozen)
    assert result["daily_returns"][0]["rf_return"] == pytest.approx(.0001)
    assert result["daily_returns"][1]["rf_return"] is None
    assert result["metrics"]["sharpe"] is None
    assert result["sample"]["daily_returns"] == 2


@pytest.mark.parametrize("change", [
    lambda r: r["observations"][0].update(annual_yield_pct=99),
    lambda r: r.update(sha256="bad"),
])
def test_digest_tampering_fails_closed(change):
    frozen = reference()
    change(frozen)
    result = calculate_risk(*campaign([100, 101, 99]), frozen)
    assert "digest mismatch" in result["unavailable"]["sharpe"]
    assert all(row["rf_return"] is None for row in result["daily_returns"])
    assert result["metrics"]["annualized_volatility_pct"] is not None


@pytest.mark.parametrize("frozen", [None, reference(status="unavailable")])
def test_unavailable_reference_does_not_substitute_zero(frozen):
    result = calculate_risk(*campaign([100, 101, 99]), frozen)
    assert result["metrics"]["sharpe"] is None
    assert result["metrics"]["sortino"] is None
    assert all(row["rf_return"] is None for row in result["daily_returns"])


def test_missing_session_gaps_are_not_treated_as_one_daily_return():
    result = calc([100, 105, 103], days=["2026-09-21", "2026-09-23", "2026-09-24"])
    assert result["metrics"]["sharpe"] is None
    assert result["metrics"]["calmar"] is None
    assert "missing consecutive exchange session" in result["unavailable"]["calmar"]
    assert result["metrics"]["max_sampled_drawdown_pct"] is not None


@pytest.mark.parametrize("mutation,reason", [
    ("missing_mark", "missing end_mark equity"),
    ("partial_session", "missing end_mark for"),
    ("wrong_session", "inconsistent with exchange session"),
    ("outside_hours", "inconsistent with exchange session"),
    ("nonpositive", "nonpositive session-final equity"),
    ("nan", "nonfinite"),
])
def test_invalid_session_series_unavailable(mutation, reason):
    data, run = campaign([100, 101, 99])
    if mutation == "missing_mark":
        run["equity_curve"].pop(2)
    elif mutation == "partial_session":
        data["events"][1]["type"] = "monitor"
    elif mutation == "wrong_session":
        data["events"][1]["session"] = "2026-09-23"
    elif mutation == "outside_hours":
        data["events"][1]["timestamp"] = "2026-09-22T23:00:00Z"
    elif mutation == "nonpositive":
        run["equity_curve"][2]["equity"] = 0
    elif mutation == "nan":
        run["equity_curve"][2]["equity"] = float("nan")
    result = calculate_risk(data, run, reference())
    assert result["metrics"]["sharpe"] is None
    assert reason in result["unavailable"]["sharpe"]
    json.dumps(result, allow_nan=False)


def test_calendar_early_close_and_dst_timestamps():
    days = ["2026-11-25", "2026-11-27", "2026-11-30"]
    frozen = reference([{"date": "2026-11-24", "annual_yield_pct": 3.65}])
    result = calculate_risk(*campaign([100, 101, 99], days=days), frozen)
    assert result["sample"]["daily_returns"] == 2
    assert result["daily_equity"][1]["timestamp"] == "2026-11-27T17:50:00Z"
    assert result["daily_returns"][0]["calendar_days"] == 2
    data, run = campaign([100, 101, 99], days=days)
    data["events"][1]["timestamp"] = "2026-11-27T15:00:00-05:00"
    assert "inconsistent" in calculate_risk(data, run, frozen)["unavailable"]["sharpe"]


def test_zero_variance_zero_drawdown_and_no_downside_are_undefined_not_infinite():
    zero = reference([{"date": "2026-09-20", "annual_yield_pct": 0}])
    result = calculate_risk(*campaign([100, 100, 100]), zero)
    assert result["metrics"]["annualized_volatility_pct"] == 0
    assert result["metrics"]["session_max_drawdown_pct"] == 0
    assert result["metrics"]["sharpe"] is None
    assert result["metrics"]["sortino"] is None
    assert result["metrics"]["calmar"] is None
    assert "zero sample variance" in result["unavailable"]["sharpe"]
    winners = calculate_risk(*campaign([100, 102, 105]), zero)
    assert winners["metrics"]["sharpe"] is not None
    assert winners["metrics"]["sortino"] is None
    json.dumps(winners, allow_nan=False)


def test_initial_seed_drawdown_separate_and_underwater_includes_recovery():
    result = calc([80, 90, 100], initial=100)
    assert result["metrics"]["max_sampled_drawdown_pct"] == pytest.approx(20)
    assert result["metrics"]["session_max_drawdown_pct"] == 0
    assert result["metrics"]["calmar"] is None
    duration = (dt.datetime.fromisoformat(result["sample"]["window_end"].replace("Z", "+00:00"))
                - dt.datetime.fromisoformat(result["sample"]["window_start"].replace("Z", "+00:00")))
    assert result["metrics"]["max_sampled_underwater_days"] == pytest.approx(duration.total_seconds() / 86400)
    assert calc([100, 100, 100])["metrics"]["max_sampled_underwater_days"] == 0


@pytest.mark.parametrize("count", [1, 2])
def test_insufficient_daily_dispersion_samples(count):
    result = calc([100, 101][:count])
    assert result["sample"]["daily_returns"] == count - 1
    for name in ("sharpe", "sortino", "annualized_volatility_pct"):
        assert result["metrics"][name] is None
        assert "at least 2" in result["unavailable"][name]
    assert result["metrics"]["var_95_pct"] is None
    assert "at least 20" in result["unavailable"]["var_95_pct"]


def test_empirical_var_expected_shortfall_minimum_and_full_tail():
    returns = [-.20, -.10] + [.01] * 38
    equities = [100]
    for value in returns:
        equities.append(equities[-1] * (1 + value))
    result = calc(equities)
    assert result["metrics"]["var_95_pct"] == pytest.approx(10)
    assert result["metrics"]["expected_shortfall_95_pct"] == pytest.approx(15)
    assert any("2 worst" in w for w in result["warnings"])
    assert calc(equities[:20])["metrics"]["var_95_pct"] is None
    assert calc(equities[:21])["metrics"]["var_95_pct"] == pytest.approx(20)


def test_partial_sales_grouped_and_unfinished_excluded():
    data, run = campaign([100, 101, 99])
    run["position_sales"] = [
        sale("WIN", 30, partial=True), sale("LOSS", -25), sale("WIN", 20),
        sale("OPEN", -500, partial=True), sale("EVEN", 0),
    ]
    result = calculate_risk(data, run, reference())
    assert result["trades"]["eligible_positions"] == 3
    assert result["metrics"]["profit_factor"] == pytest.approx(2)
    assert result["metrics"]["win_rate_pct"] == pytest.approx(100 / 3)
    assert result["metrics"]["expectancy_usd"] == pytest.approx(25 / 3)
    win = next(g for g in result["trades"]["groups"] if g["ticker"] == "WIN")
    assert win["sales_count"] == 2
    assert win["realized_net_on_recorded_sales_usd"] == 50


@pytest.mark.parametrize("profits,factor,rate,expectancy", [
    ([-10, -20], 0, 0, -15),
    ([10, 20], None, 100, 15),
    ([0, 0], None, 0, 0),
])
def test_loss_only_all_winners_and_break_even(profits, factor, rate, expectancy):
    data, run = campaign([100, 101, 99])
    run["position_sales"] = [sale(str(i), net) for i, net in enumerate(profits)]
    result = calculate_risk(data, run, reference())
    assert result["metrics"]["profit_factor"] == factor
    assert result["metrics"]["win_rate_pct"] == rate
    assert result["metrics"]["expectancy_usd"] == expectancy
    json.dumps(result, allow_nan=False)


def test_carried_positions_not_claimed_as_full_lifetime_trades():
    data, run = campaign([100, 101, 99])
    run["initial_positions"] = [{"ticker": "OLD", "buy_date": "2026-09-01"}]
    run["position_sales"] = [sale("OLD", 500, partial=True, buy_date="2026-09-01"),
                             sale("OLD", 1000, buy_date="2026-09-01"), sale("NEW", -20)]
    result = calculate_risk(data, run, reference())
    assert result["trades"]["carried_in_positions"] == 1
    assert result["trades"]["completed_positions"] == 2
    assert result["trades"]["eligible_positions"] == 1
    assert result["metrics"]["expectancy_usd"] == -20
    assert result["trades"]["groups"][0]["realized_net_on_recorded_sales_usd"] == 1500
    assert "not lifetime" in result["trades"]["scope"]


def test_same_day_reentry_is_not_merged_with_already_completed_position():
    data, run = campaign([100, 101, 99])
    run["position_sales"] = [sale("ONE", 20, partial=True), sale("ONE", 30), sale("ONE", -10)]
    result = calculate_risk(data, run, reference())
    assert result["trades"]["eligible_positions"] == 2
    assert result["metrics"]["win_rate_pct"] == 50
    assert result["metrics"]["profit_factor"] == 5


def test_finite_json_with_extreme_growth_and_missing_inputs():
    result = calc([1, 1e150, 1e300], initial=1)
    assert result["metrics"]["annualized_return_pct"] is None
    assert "nonfinite" in result["unavailable"]["annualized_return_pct"]
    json.dumps(result, allow_nan=False)
    empty = calculate_risk({}, {}, None)
    assert all(value is None for value in empty["metrics"].values())
    assert set(empty["metrics"]) == set(empty["unavailable"])
    json.dumps(empty, allow_nan=False)


def test_no_mutation_and_every_null_metric_has_reason():
    data, run = campaign([100, 101, 99])
    frozen = reference()
    before = copy.deepcopy((data, run, frozen))
    result = calculate_risk(data, run, frozen)
    assert (data, run, frozen) == before
    assert all(name in result["unavailable"] for name, value in result["metrics"].items()
               if value is None)
    assert all(name not in result["unavailable"] for name, value in result["metrics"].items()
               if value is not None)
    json.dumps(result, allow_nan=False)
