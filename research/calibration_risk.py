"""Pure, descriptive risk statistics for frozen calibration replay results.

No networking, selection gates, or changes to replay decisions live here. Session
end marks are recorded samples, not a claim to official exchange closing prices.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import statistics
from bisect import bisect_left
from zoneinfo import ZoneInfo

from market_calendar import session_bounds


NY = ZoneInfo("America/New_York")
METRICS = (
    "sharpe", "sortino", "calmar", "annualized_return_pct",
    "annualized_volatility_pct", "session_max_drawdown_pct",
    "max_sampled_drawdown_pct", "total_return_pct", "daily_window_return_pct",
    "worst_day_pct", "best_day_pct", "var_95_pct", "expected_shortfall_95_pct",
    "profit_factor", "win_rate_pct", "expectancy_usd",
    "session_max_drawdown", "max_sampled_drawdown",
    "max_sampled_underwater_days",
)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("missing or nonnumeric value")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite value")
    return value


def _stamp(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return result


def _iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _drawdown(values):
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def _reference(reference):
    """Validate the signed, frozen reference without trusting its availability."""
    try:
        if not isinstance(reference, dict):
            raise ValueError("no frozen Treasury reference")
        unsigned = {k: v for k, v in reference.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, str(reference.get("sha256", ""))):
            raise ValueError("Treasury reference digest mismatch")
        if reference.get("schema_version") != 1:
            raise ValueError("unsupported Treasury reference schema")
        if reference.get("status") != "available":
            raise ValueError("frozen Treasury reference unavailable")
        if not isinstance(reference.get("source"), str) or not reference["source"]:
            raise ValueError("missing Treasury source")
        if _stamp(reference["retrieved_at"]).utcoffset() != dt.timedelta(0):
            raise ValueError("Treasury retrieval timestamp must be UTC")
        if reference.get("error") is not None:
            raise ValueError("available Treasury reference carries an error")
        if not isinstance(reference.get("urls"), list):
            raise ValueError("missing Treasury provenance URLs")
        observations = {}
        for row in reference["observations"]:
            day = dt.date.fromisoformat(row["date"])
            if day in observations:
                raise ValueError("duplicate Treasury observation date")
            observations[day] = _number(row["annual_yield_pct"])
        if not observations:
            raise ValueError("no Treasury observations")
        return sorted(observations), observations, None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return [], {}, str(exc)


def _daily_series(data, curve):
    """Match terminal events to their actual recorded equity-curve samples."""
    events = data.get("events", [])
    by_timestamp = {}
    for index, row in enumerate(curve):
        by_timestamp.setdefault(_stamp(row["timestamp"]), []).append(index)
    aligned = len(curve) == len(events) + 1 and all(
        _stamp(row["timestamp"]) == _stamp(event["timestamp"])
        for row, event in zip(curve[1:], events)
    )
    terminal = {}
    sessions = set()
    previous = None
    for index, event in enumerate(events):
        day = dt.date.fromisoformat(event["session"])
        now = _stamp(event["timestamp"])
        bounds = session_bounds(day)
        if (now.astimezone(NY).date() != day or not bounds
                or not bounds[0] <= now <= bounds[1]):
            raise ValueError("event timestamp inconsistent with exchange session")
        if previous is not None and now < previous:
            raise ValueError("events are not chronological")
        previous = now
        sessions.add(day)
        if event["type"] != "end_mark":
            continue
        matches = by_timestamp.get(now, [])
        if not matches:
            raise ValueError(f"missing end_mark equity for {day}")
        if aligned:
            mark = curve[index + 1]
        elif len(matches) == 1:
            mark = curve[matches[0]]
        else:
            raise ValueError(f"ambiguous end_mark equity timestamp for {day}")
        equity = _number(mark["equity"])
        if equity <= 0:
            raise ValueError("nonpositive session-final equity")
        terminal[day] = dict(session=day.isoformat(), timestamp=_iso(now), equity=equity)
    if sessions - terminal.keys():
        raise ValueError("missing end_mark for an observed session")
    days = sorted(terminal)
    for left, right in zip(days, days[1:]):
        day = left + dt.timedelta(days=1)
        while day < right:
            if session_bounds(day):
                raise ValueError(f"missing consecutive exchange session {day}")
            day += dt.timedelta(days=1)
    return [terminal[day] for day in days]


def _trades(data, run, result, put, reject):
    names = ("profit_factor", "win_rate_pct", "expectancy_usd")
    try:
        if "position_sales" not in run:
            raise ValueError("position-sale ledger unavailable")
        initial = run.get("initial_positions", data.get("initial_positions", []))
        carried = {(p["ticker"], p["buy_date"]) for p in initial}
        seed = data.get("initial_state", {}).get("timestamp")
        seed_day = _stamp(seed).astimezone(NY).date() if seed else None
        groups, active = [], {}
        for sale in run["position_sales"]:
            key = (sale["ticker"], sale["buy_date"])
            net = _number(sale["net_profit_loss"])
            if not isinstance(sale["partial"], bool):
                raise ValueError("position-sale completion flag missing")
            if key not in active:
                bought = dt.date.fromisoformat(sale["buy_date"][:10])
                group = dict(ticker=key[0], buy_date=key[1], sales_count=0,
                             completed=False, realized_net_on_recorded_sales_usd=0.0,
                             carried_in=key in carried or bool(seed_day and bought < seed_day))
                groups.append(group)
                active[key] = group
            group = active[key]
            group["sales_count"] += 1
            group["realized_net_on_recorded_sales_usd"] = _number(
                group["realized_net_on_recorded_sales_usd"] + net)
            if not sale["partial"]:
                group["completed"] = True
                del active[key]
                carried.discard(key)
        eligible = [g["realized_net_on_recorded_sales_usd"]
                    for g in groups if g["completed"] and not g["carried_in"]]
        result["trades"] = {
            "scope": "Completed positions opened within this replay; partial sales merged. "
                     "Carried-in positions excluded from trade ratios. Their displayed realized "
                     "net is only the recorded sales at original cost basis, not lifetime P&L "
                     "and not mark-to-market P&L since the window began.",
            "completed_positions": sum(g["completed"] for g in groups),
            "eligible_positions": len(eligible),
            "carried_in_positions": sum(g["carried_in"] for g in groups),
            "groups": groups,
        }
        if not eligible:
            raise ValueError("no completed non-carried positions")
        gains = math.fsum(p for p in eligible if p > 0)
        losses = -math.fsum(p for p in eligible if p < 0)
        put("win_rate_pct", lambda: 100 * sum(p > 0 for p in eligible) / len(eligible))
        put("expectancy_usd", lambda: statistics.mean(eligible))
        if losses:
            put("profit_factor", lambda: gains / losses)
        else:
            reject(("profit_factor",), "no realized losses; profit-factor denominator is zero")
        if len(eligible) < 30:
            result["warnings"].append("Fewer than 30 eligible completed positions: trade ratios "
                                      "are highly uncertain, not evidence of a reliable edge.")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        reject(names, f"Trade metrics unavailable: {exc}")


def calculate_risk(data, run, reference):
    """Return finite JSON metrics and explicit unavailability, without network I/O."""
    result = {
        "methodology": {
            "version": 1, "annualization_sessions": 252,
            "daily_sampling": "Last recorded end_mark per session, matched to equity_curve; "
                              "sampled terminal marks, NOT official exchange closes.",
            "opening_interval": "Excluded: N session-final marks supply N-1 close-to-close returns.",
            "risk_free": "Frozen historical 3-month US Treasury annual yield is a cash-return "
                         "proxy, not realized Treasury fund returns. Latest observation STRICTLY "
                         "BEFORE interval start session; maximum age 7 calendar days.",
            "risk_free_accrual": "Simple ACT/365: annual_yield_pct / 100 * calendar_days / 365.",
            "sharpe": "Mean daily excess / sample stdev daily excess * sqrt(252).",
            "sortino": "Mean daily excess / sqrt(mean(min(excess,0)^2)) * sqrt(252); "
                       "denominator includes every return, not just losing returns.",
            "calmar": "Geometric annualized return / maximum drawdown over the SAME "
                      "session-final equity series; first terminal mark is the starting value.",
            "drawdown": "Positive peak-to-trough loss; full-window sampled drawdown includes "
                        "initial equity and every intraday mark. Unobserved paths are unknown.",
            "historical_tail": "With at least 20 returns: 95% daily VaR is the negative "
                               "nearest-rank 5th-percentile return; expected shortfall averages "
                               "the worst ceil(0.05*n) returns. Signed losses, not forecasts.",
            "annualized_growth": "252-period geometric extrapolation, NOT observed annual profit.",
            "missing_data": "No skipped return pairs and no zero risk-free substitutions.",
            "trade_scope": "Completed, non-carried positions; recorded partial sales merged; "
                           "net_profit_loss already includes replay transaction costs.",
        },
        "sample": {"sessions": 0, "daily_returns": 0, "start": None, "end": None},
        "daily_equity": [], "daily_returns": [],
        "metrics": dict.fromkeys(METRICS),
        "unavailable": {},
        "warnings": ["Descriptive research only: these metrics do not approve or select a strategy.",
                     "Sampled drawdowns can miss deeper losses between observations."],
    }

    def reject(names, reason):
        for name in names:
            result["metrics"][name] = None
            result["unavailable"][name] = reason

    def put(name, compute):
        try:
            result["metrics"][name] = _number(compute())
            result["unavailable"].pop(name, None)
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            reject((name,), f"Undefined or nonfinite calculation: {exc}")

    reject(METRICS, "insufficient observations")
    dates, yields, reference_error = _reference(reference)
    result["reference"] = {
        "status": "unavailable" if reference_error else "available",
        "sha256": reference.get("sha256") if isinstance(reference, dict)
                  and isinstance(reference.get("sha256"), str) else None,
        "error": reference_error,
    }
    curve = run.get("equity_curve", [])
    full_names = ("max_sampled_drawdown_pct", "max_sampled_drawdown",
                  "max_sampled_underwater_days", "total_return_pct")
    try:
        initial = _number(run.get("initial_equity"))
        marks = [_number(row["equity"]) for row in curve]
        if initial <= 0 or not marks or any(v <= 0 for v in marks):
            raise ValueError("missing or nonpositive full-window equity")
        timestamps = [_stamp(row["timestamp"]) for row in curve]
        if any(a > b for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError("equity curve is not chronological")
        seed = data.get("initial_state", {}).get("timestamp")
        start = _stamp(seed) if seed else timestamps[0]
        if start > timestamps[0]:
            raise ValueError("initial snapshot follows equity curve")
        values = [initial] + marks
        dd = _drawdown(values)
        put("max_sampled_drawdown", lambda: dd)
        put("max_sampled_drawdown_pct", lambda: dd * 100)
        put("total_return_pct", lambda: (marks[-1] / initial - 1) * 100)
        peak, peak_at, duration, underwater = initial, start, 0.0, False
        for now, equity in zip(timestamps, marks):
            if equity < peak or underwater:
                duration = max(duration, (now - peak_at).total_seconds())
            if equity >= peak:
                peak, peak_at = equity, now
            underwater = equity < peak
        put("max_sampled_underwater_days", lambda: duration / 86400)
        result["sample"].update(window_start=_iso(start), window_end=_iso(timestamps[-1]),
                                intraday_marks=len(marks))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        reject(full_names, f"Full-window equity unavailable: {exc}")

    daily_names = tuple(name for name in METRICS if name not in full_names
                        and name not in ("profit_factor", "win_rate_pct", "expectancy_usd"))
    try:
        daily = _daily_series(data, curve)
        result["daily_equity"] = daily
        result["sample"].update(
            sessions=len(daily), daily_returns=max(0, len(daily) - 1),
            start=daily[0]["timestamp"] if daily else None,
            end=daily[-1]["timestamp"] if daily else None,
            start_session=daily[0]["session"] if daily else None,
            end_session=daily[-1]["session"] if daily else None,
        )
        returns, excess = [], []
        rf_errors = []
        for left, right in zip(daily, daily[1:]):
            raw = _number(right["equity"] / left["equity"] - 1)
            if raw <= -1:
                raise ValueError("daily equity ratio underflow")
            returns.append(raw)
            start = dt.date.fromisoformat(left["session"])
            calendar_days = (dt.date.fromisoformat(right["session"]) - start).days
            row = dict(start_session=left["session"], session=right["session"],
                       start_timestamp=left["timestamp"], end_timestamp=right["timestamp"],
                       calendar_days=calendar_days, **{"return": raw},
                       rf_return=None, excess_return=None, treasury_date=None,
                       annual_yield_pct=None, treasury_age_days=None, rf_unavailable=None)
            rate_error = reference_error
            index = bisect_left(dates, start) - 1
            if not rate_error and index < 0:
                rate_error = f"no Treasury observation strictly before {start}"
            if not rate_error:
                observed = dates[index]
                age = (start - observed).days
                row.update(treasury_date=observed.isoformat(), annual_yield_pct=yields[observed],
                           treasury_age_days=age)
                if age > 7:
                    rate_error = f"Treasury observation stale by {age} calendar days at {start}"
                else:
                    try:
                        rate = _number(yields[observed] / 100 * calendar_days / 365)
                        daily_excess = _number(raw - rate)
                        row.update(rf_return=rate, excess_return=daily_excess)
                        excess.append(daily_excess)
                    except (ValueError, OverflowError) as exc:
                        rate_error = f"nonfinite Treasury accrual: {exc}"
            if rate_error:
                row["rf_unavailable"] = rate_error
                rf_errors.append(rate_error)
            result["daily_returns"].append(row)
        n = len(returns)
        if daily:
            dd = _drawdown([row["equity"] for row in daily])
            put("session_max_drawdown", lambda: dd)
            put("session_max_drawdown_pct", lambda: dd * 100)
        if n:
            log_growth = math.fsum(math.log1p(r) for r in returns)
            put("daily_window_return_pct", lambda: math.expm1(log_growth) * 100)
            put("annualized_return_pct", lambda: math.expm1(log_growth * 252 / n) * 100)
            put("worst_day_pct", lambda: min(returns) * 100)
            put("best_day_pct", lambda: max(returns) * 100)
            if dd and result["metrics"]["annualized_return_pct"] is not None:
                put("calmar", lambda: result["metrics"]["annualized_return_pct"] / 100 / dd)
            else:
                reject(("calmar",), "zero session drawdown or unavailable annualized growth")
        if n < 2:
            reject(("sharpe", "sortino", "annualized_volatility_pct"),
                   "at least 2 close-to-close returns required for dispersion")
        else:
            put("annualized_volatility_pct", lambda: statistics.stdev(returns) * math.sqrt(252) * 100)
            if rf_errors:
                reject(("sharpe", "sortino"), "; ".join(dict.fromkeys(rf_errors)))
            else:
                mean = statistics.mean(excess)
                deviation = statistics.stdev(excess)
                downside = math.hypot(*(min(r, 0) for r in excess)) / math.sqrt(n)
                if deviation:
                    put("sharpe", lambda: mean / deviation * math.sqrt(252))
                else:
                    reject(("sharpe",), "zero sample variance of excess returns")
                if downside:
                    put("sortino", lambda: mean / downside * math.sqrt(252))
                else:
                    reject(("sortino",), "no negative excess returns; downside denominator is zero")
        if n >= 20:
            tail = sorted(returns)[:math.ceil(0.05 * n)]
            put("var_95_pct", lambda: -tail[-1] * 100)
            put("expected_shortfall_95_pct", lambda: -statistics.mean(tail) * 100)
            result["warnings"].append("Historical 95% tail metrics use a plain empirical sample "
                                      f"of {len(tail)} worst daily return(s), not a confidence bound "
                                      "or a forecast of future losses.")
        else:
            reject(("var_95_pct", "expected_shortfall_95_pct"),
                   "at least 20 close-to-close returns required for empirical daily tail metrics")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RuntimeError,
            ImportError) as exc:
        reject(daily_names, f"Session series unavailable: {exc}")

    n = result["sample"]["daily_returns"]
    if n < 252:
        result["warnings"].append(f"Only {n} daily returns: annualized figures extrapolate "
                                  "a sample shorter than 252 sessions; they are not observed annual profit.")
    if n < 30:
        result["warnings"].append(f"Only {n} daily returns: short-sample estimates are highly "
                                  "uncertain and do not establish statistical confidence.")
    _trades(data, run, result, put, reject)
    return result
