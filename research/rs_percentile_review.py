#!/usr/bin/env python3
"""rs_percentile_review.py — answers the questions the shadow RS experiment asked.

This is the `review_command` for the `rs-percentile-shadow` entry in
decisions/provisional_decisions.json, due 2026-10-19. It reads trigger_history
and reports whether the shadow `rs_percentile` column ranks candidates any better
than the saturated `rs_score` it shadows.

The experiment and its caveats are recorded in
decisions/2026-09-17_rs-percentile-shadow-column.md. Read that first — in
particular, the prior evidence points NEGATIVE (more relative strength predicted
WORSE forward returns), which is the opposite of the naive fix, so a negative
result here is a real finding and not a failed experiment.

Usage:
    python3 research/rs_percentile_review.py [--insecure] [--json]

    --insecure  skip TLS verification for Supabase (local trust-store issues)
    --json      emit machine-readable output

Exit codes:
    0   ran cleanly and produced a verdict
    2   ran cleanly but the sample is too thin to interpret (see DISTINCT DATES)
    1   error — fail LOUD rather than quietly reporting nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import requests
import urllib3

FWD_COLS = ("fwd_5d_pct", "fwd_20d_pct", "alpha_20d_pct", "max_gain_20d_pct",
            "max_drawdown_20d_pct")

# The headline outcome. alpha_20d_pct (return in excess of the benchmark over the
# same window) is deliberately preferred over raw fwd_20d_pct for this question:
# relative strength is itself a relative measure, so scoring it against an
# absolute return would let a broad market rally masquerade as stock selection.
PRIMARY = "alpha_20d_pct"

# Below this many distinct trigger dates the sample is one cohort wearing a
# disguise. The original -0.68 correlation came from 16 rows that all shared a
# single date; reporting a number from 2 dates would repeat that mistake with
# more decimal places.
MIN_DISTINCT_DATES = 5

# Columns added by migrations/add_rs_percentile.sql. Used only to turn a raw
# PostgREST 400 into an instruction a human can act on a month from now.
SHADOW_COLS = ("rs_percentile", "rs_excess_return", "rs_12w_return")


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"error: required environment variable {name} is not set. "
                 "Load it with: set -a && . ~/.config/ai-trading-bot/secrets.env && set +a")
    return val


def fetch_rows(insecure: bool) -> list:
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cols = ("ticker,triggered_at,rs_score,rs_percentile,rs_excess_return,"
            "rs_12w_return,final_score,trigger_type," + ",".join(FWD_COLS))
    out, offset, page = [], 0, 1000
    while True:
        try:
            r = requests.get(
                f"{url}/rest/v1/trigger_history",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params={"select": cols, "offset": offset, "limit": page},
                timeout=30, verify=not insecure,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            body = getattr(e.response, "text", "") or ""
            if any(c in body for c in SHADOW_COLS):
                sys.exit(
                    "error: trigger_history has no rs_percentile/rs_excess_return "
                    "columns yet.\n"
                    "       Apply migrations/add_rs_percentile.sql in the Supabase "
                    "SQL Editor, then wait for\n"
                    "       the screener to write a few cohorts before reviewing.\n"
                    f"       (server said: {body.strip()[:200]})")
            sys.exit(f"error: could not reach Supabase ({e})")
        batch = r.json()
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def terciles(pairs):
    """Split (rank, outcome) pairs into bottom/middle/top third by rank."""
    if len(pairs) < 6:
        return None
    s = sorted(pairs, key=lambda p: p[0])
    c = len(s) // 3
    return (
        [p[1] for p in s[:c]],
        [p[1] for p in s[c:len(s) - c]],
        [p[1] for p in s[len(s) - c:]],
    )


def analyse(rows: list) -> dict:
    usable = [r for r in rows if _num(r.get("rs_percentile")) is not None]
    res = {
        "total_rows": len(rows),
        "rows_with_percentile": len(usable),
    }

    labelled = {c: [r for r in usable if _num(r.get(c)) is not None] for c in FWD_COLS}
    core = labelled[PRIMARY]
    dates = sorted({r.get("triggered_at") for r in core if r.get("triggered_at")})
    res["labelled_primary"] = len(core)
    res["distinct_dates"] = len(dates)
    res["date_range"] = [dates[0], dates[-1]] if dates else None
    res["thin"] = len(dates) < MIN_DISTINCT_DATES

    # Q2 — the sign, which is the only thing the decision hinges on.
    res["correlations"] = {}
    for c in FWD_COLS:
        sub = labelled[c]
        res["correlations"][c] = {
            "n": len(sub),
            "corr_percentile": _pearson([_num(r["rs_percentile"]) for r in sub],
                                        [_num(r[c]) for r in sub]),
            "corr_rs_score": _pearson(
                [_num(r["rs_score"]) for r in sub if _num(r.get("rs_score")) is not None],
                [_num(r[c]) for r in sub if _num(r.get("rs_score")) is not None]),
        }

    # Q3 — does it discriminate INSIDE the saturated group? This is the whole
    # question. If rs_score == 100 rows show no spread across percentile
    # terciles, the percentile adds nothing and the entry resolves as no-effect.
    sat = [r for r in core if _num(r.get("rs_score")) == 100]
    res["saturated_rows"] = len(sat)
    t = terciles([(_num(r["rs_percentile"]), _num(r[PRIMARY])) for r in sat])
    if t:
        bot, mid, top = t
        res["saturated_terciles"] = {
            "bottom_mean": _mean(bot), "bottom_n": len(bot),
            "middle_mean": _mean(mid), "middle_n": len(mid),
            "top_mean": _mean(top), "top_n": len(top),
            "spread_top_minus_bottom": (_mean(top) - _mean(bot)) if bot and top else None,
        }
    else:
        res["saturated_terciles"] = None

    # Q4 — is it carried by one date? Drop the largest contributor and re-check.
    by_date = defaultdict(list)
    for r in core:
        by_date[r.get("triggered_at")].append(r)
    res["per_date"] = {
        d: {"n": len(v), "mean_primary": _mean([_num(x[PRIMARY]) for x in v])}
        for d, v in sorted(by_date.items())
    }
    if len(by_date) > 1:
        biggest = max(by_date, key=lambda d: len(by_date[d]))
        rest = [r for r in core if r.get("triggered_at") != biggest]
        res["jackknife"] = {
            "dropped_date": biggest,
            "dropped_n": len(by_date[biggest]),
            "corr_without": _pearson([_num(r["rs_percentile"]) for r in rest],
                                     [_num(r[PRIMARY]) for r in rest]),
        }
    else:
        res["jackknife"] = None

    return res


def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:+.{nd}f}"


def report(res: dict) -> None:
    print("Shadow rs_percentile review — decisions/2026-09-17_rs-percentile-shadow-column.md")
    print("=" * 76)
    print(f"trigger_history rows                 : {res['total_rows']}")
    print(f"  carrying rs_percentile             : {res['rows_with_percentile']}")
    print(f"  ALSO carrying {PRIMARY:<21}: {res['labelled_primary']}")
    print(f"  spanning DISTINCT trigger dates    : {res['distinct_dates']}  {res['date_range']}")
    print()

    if res["thin"]:
        print(f"⚠️  Fewer than {MIN_DISTINCT_DATES} distinct dates. This is effectively ONE")
        print("    cohort, which is exactly the flaw that made the original -0.68")
        print("    correlation uninterpretable. Report n, change nothing, come back later.")
        print()

    print("Q2 — SIGN of the relationship (the decision hinges on this alone)")
    print("-" * 76)
    for c in FWD_COLS:
        d = res["correlations"][c]
        print(f"  {c:<21} n={d['n']:<5} corr(percentile)={_fmt(d['corr_percentile'])}"
              f"   corr(rs_score)={_fmt(d['corr_rs_score'])}")
    print(f"  Headline outcome is {PRIMARY} (benchmark-relative) — see module docstring.")
    print("  Prior at decision time: corr(rs_score, fwd_20d) = -0.68 on ONE cohort.")
    print()

    print("Q3 — does the percentile discriminate INSIDE the saturated group?")
    print("-" * 76)
    print(f"  rows with rs_score == 100 and a labelled outcome: {res['saturated_rows']}")
    st = res["saturated_terciles"]
    if not st:
        print("  Too few saturated rows to form terciles — unanswered.")
    else:
        print(f"  bottom third  n={st['bottom_n']:<4} mean = {_fmt(st['bottom_mean'], 2)}%")
        print(f"  middle third  n={st['middle_n']:<4} mean = {_fmt(st['middle_mean'], 2)}%")
        print(f"  top third     n={st['top_n']:<4} mean = {_fmt(st['top_mean'], 2)}%")
        print(f"  spread (top - bottom)            = {_fmt(st['spread_top_minus_bottom'], 2)}pp")
        print("  A spread near zero means the percentile adds NOTHING the live")
        print("  scorer was missing -> resolve the register entry as 'no effect'.")
    print()

    print("Q4 — is any result carried by a single date?")
    print("-" * 76)
    for d, v in res["per_date"].items():
        print(f"  {d}  n={v['n']:<4} mean {PRIMARY} = {_fmt(v['mean_primary'], 2)}%")
    jk = res["jackknife"]
    if jk:
        print(f"  dropping {jk['dropped_date']} (n={jk['dropped_n']}): "
              f"corr = {_fmt(jk['corr_without'])}")
        print("  If the SIGN flips here, nothing has been measured.")
    print()

    print("Next step")
    print("-" * 76)
    print("  NEGATIVE and robust -> the implied change is an RS CEILING (reject")
    print("     over-extended leaders), NOT percentile promotion. Check it against")
    print("     the 2026-09-09 buy-price drift guard, which already removed most")
    print("     pre-era gap-chasing, before proposing anything.")
    print("  POSITIVE and robust -> re-rank historical cohorts offline and show")
    print("     which ticker would have won each slot BEFORE touching live code.")
    print("  FLAT -> resolve the entry; the saturation was real but inert.")
    print()
    print("  Then: append to `history` in decisions/provisional_decisions.json,")
    print("  set last_reviewed, and close the GitHub issue.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = fetch_rows(a.insecure)
    res = analyse(rows)

    if a.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        report(res)

    sys.exit(2 if res["thin"] else 0)


if __name__ == "__main__":
    main()
