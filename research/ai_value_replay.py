"""ai_value_replay.py — settle "does the AI actually pick winners?" with an A/B replay.

This harness answers one question and only one: does the AI evaluator's score
(its blended `adjusted_score`/`final_score`) and its grade-D VETO change the SET
of trades the bot takes in a way that improves realised P&L — or is it noise, or
worse, does it cut winners?

It is deliberately different from `exit_rule_replay.py`. That harness takes the
trades the bot ACTUALLY placed and asks whether a different EXIT would have done
better. This one holds the exit fixed (the live Prove-It stop) and varies the
ENTRY SELECTION, so the dollar difference between the two arms is attributable to
the AI and nothing else.

    Arm A  "AI ON"   rank the day's candidates by adjusted_score (AI-blended) and
                     DROP any ai_grade == "D" (the live veto). Fill free slots.
    Arm B  "AI OFF"  rank the same candidates by quality_score (the AI-INDEPENDENT
                     technical/quality score) and apply NO veto. Fill free slots.

    AI contribution = P&L(Arm A) − P&L(Arm B).

DATA SOURCE
-----------
`trigger_decisions` in Supabase — the retained per-day candidate log. Each row is
one candidate the buy loop evaluated on one `decision_date`, carrying BOTH scores
(`quality_score` = AI-independent, `adjusted_score`/`final_score` = AI-blended),
its `ai_grade`, and the reason it was bought or skipped. `daily_triggers` is NOT
usable — it is a rolling table that only holds the current day's handful of rows.

ELIGIBILITY — isolating the AI lever
------------------------------------
The two arms must differ ONLY in the AI. So a candidate enters the shared pool
only if the reason it was (not) taken is either the AI or CAPACITY:

    BOUGHT                         -> in the pool (both arms may take it)
    AI_VETO                        -> in the pool; Arm A drops it, Arm B keeps it
    SLOTS_FULL / INSUFFICIENT_CASH -> in the pool; capacity is RE-DERIVED by the
                                      walk-forward, not taken from the log, because
                                      a slot the log saw full may be free once an
                                      arm's earlier picks have exited

Everything else — COOLING_OFF, ALREADY_HELD, BELOW_PIVOT, SCORE_FLOOR,
EARNINGS_IMMINENT, NO_AI_SCORE — is a NON-AI gate and is excluded from BOTH arms.
SCORE_FLOOR is treated as a shared gate even though it reads the AI-blended score;
inventing a separate quality-only floor for Arm B would add a second moving part
and muddy the attribution. This is the conservative choice and it is noted again
in the printed caveats.

FORWARD OUTCOME
---------------
Every candidate — taken or not — is entered at the OPEN of its `decision_date`
session and replayed forward on 5-minute bars under the live Prove-It stop
(`exit_rule_replay.shipped_proveit`), reusing that module's fetch and simulation
so the exit mechanics are identical to the exit-review harness. A position that
never triggers an exit inside the horizon is marked out at the last close. Both
arms size every slot at the same fixed NOTIONAL, so the dollar delta reflects
SELECTION only, not sizing.

WHAT IT CAN AND CANNOT SAY TODAY
--------------------------------
The book is usually FULL: at the time of writing 130 of 287 logged decisions were
SLOTS_FULL and only 5 were BOUGHT. When capacity is the binding constraint the two
arms rarely diverge, so the headline A/B number is thin and must not be
over-read — the harness prints the count of picks that actually DIFFER, and if
that is near zero the AI contribution is ~$0 by construction, not by evidence.

The highest-signal reading available now is the `--veto-audit`: the forward
outcome of every AI_VETO'd name. It is a direct per-name counterfactual (did the
veto remove a winner or dodge a loser?) and does not depend on slot contention, so
it is trustworthy at a smaller sample than the full A/B. Start there.

USAGE
-----
    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/ai_value_replay.py --insecure --veto-audit   # start here
    python3 research/ai_value_replay.py --insecure                # full A/B
    python3 research/ai_value_replay.py --insecure --json out.json

Reads Supabase and FMP only. Writes nothing anywhere. Bars are cached under /tmp
so re-runs are fast. Requires SUPABASE_URL, SUPABASE_KEY, FMP_API_KEY.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pickle
import sys
from typing import Any

import requests

# Reuse the exit-review harness so the forward exit mechanics are byte-identical
# to the model the operator already trusts for parameter review.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exit_rule_replay import (  # noqa: E402
    Trade,
    fetch_5min,
    shipped_proveit,
    simulate_proveit,
)

# ── Configuration ─────────────────────────────────────────────────────────────
MAX_POSITIONS = 5          # mirror of config.MAX_POSITIONS
NOTIONAL = 20_000.0        # $ per slot, identical across arms — isolates selection
HORIZON_SESSIONS = 30      # forward sessions to let the Prove-It exit resolve
_CAL_DAYS_PER_HORIZON = 48 # calendar days to fetch to cover HORIZON_SESSIONS

# Reason codes that put a candidate in the SHARED pool (see module docstring).
_POOL_REASONS = {"BOUGHT", "AI_VETO", "SLOTS_FULL", "INSUFFICIENT_CASH"}

_CACHE_DIR = "/tmp/ai_value_replay_bars"


# ── Data loading ──────────────────────────────────────────────────────────────
def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} not set — run: set -a && . ~/.config/ai-trading-bot/secrets.env && set +a")
    return v


def load_decisions(verify_tls: bool = True) -> list[dict]:
    """Every row from Supabase `trigger_decisions`, oldest decision first."""
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    resp = requests.get(
        f"{url}/rest/v1/trigger_decisions",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={
            "select": "decision_date,triggered_at,ticker,trigger_type,decision,"
                      "reason_code,ai_grade,quality_score,final_score,"
                      "adjusted_score,price",
            "order": "decision_date.asc",
            "limit": "5000",
        },
        timeout=30,
        verify=verify_tls,
    )
    resp.raise_for_status()
    return resp.json()


# ── Forward-outcome simulation ────────────────────────────────────────────────
def _cache_path(ticker: str, entry_date: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{ticker}_{entry_date}.pkl")


def _fetch_forward_bars(ticker: str, entry_date: str, api_key: str,
                        verify_tls: bool) -> list[dict]:
    """5-minute bars from the entry session forward, cached on disk."""
    path = _cache_path(ticker, entry_date)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
    d0 = dt.date.fromisoformat(entry_date)
    start = dt.datetime.combine(d0, dt.time(0, 0), tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(d0 + dt.timedelta(days=_CAL_DAYS_PER_HORIZON),
                              dt.time(23, 59), tzinfo=dt.timezone.utc)
    # fetch_5min uses requests without a verify kwarg; the corp-cert case is
    # handled by the caller disabling TLS verification process-wide when needed.
    if not verify_tls:
        _disable_tls_verification()
    bars = fetch_5min(ticker, start, end, api_key)
    with open(path, "wb") as fh:
        pickle.dump(bars, fh)
    return bars


_TLS_DISABLED = False


def _disable_tls_verification() -> None:
    """Turn off TLS verification for the whole process (corp cert workaround).

    exit_rule_replay.fetch_5min calls requests.get without a verify= kwarg, so we
    cannot pass it through. Patch the session default once, mirroring how the
    other research harnesses accept --insecure.
    """
    global _TLS_DISABLED
    if _TLS_DISABLED:
        return
    import urllib3
    urllib3.disable_warnings()
    _orig = requests.Session.request

    def _patched(self, method, url, **kw):
        kw.setdefault("verify", False)
        return _orig(self, method, url, **kw)

    requests.Session.request = _patched  # type: ignore[assignment]
    _TLS_DISABLED = True


def _build_trade(ticker: str, bars: list[dict]) -> Trade | None:
    """Synthetic Trade entered at the OPEN of the first session in `bars`."""
    if not bars:
        return None
    entry_open = bars[0]["open"]
    buy_ts = bars[0]["ts"]
    trade_days = sorted({b["date"] for b in bars})
    return Trade(
        ticker=ticker,
        buy_price=entry_open,
        buy_ts=buy_ts,
        sell_price=entry_open,   # unused by simulate_proveit; set to entry
        sell_ts=buy_ts,
        shares=0,
        profit_loss=0.0,
        sell_reason=None,
        bars=bars,
        trade_days=trade_days,
    )


def forward_outcome(ticker: str, entry_date: str, api_key: str,
                    verify_tls: bool) -> dict | None:
    """Replay one candidate forward under the live Prove-It stop.

    Returns {entry, exit_price, exit_date, reason, ret_pct} or None if no bars.
    """
    bars = _fetch_forward_bars(ticker, entry_date, api_key, verify_tls)
    trade = _build_trade(ticker, bars)
    if trade is None:
        return None
    cfg = shipped_proveit()
    sim = simulate_proveit(trade, cfg)
    if sim is None:
        # Never exited inside the horizon → mark out at the last close.
        exit_price = bars[-1]["close"]
        exit_ts = bars[-1]["ts"]
        reason = "horizon_open"
    else:
        exit_price = sim["price"]
        exit_ts = sim.get("ts", bars[-1]["ts"])
        reason = sim["reason"]
    entry = trade.buy_price
    return {
        "entry": entry,
        "exit_price": exit_price,
        "exit_date": exit_ts.date().isoformat(),
        "reason": reason,
        "ret_pct": (exit_price / entry - 1.0) * 100.0,
    }


# ── Eligible-candidate assembly ───────────────────────────────────────────────
def eligible_candidates(rows: list[dict]) -> list[dict]:
    """Rows that belong in the shared A/B pool, de-duplicated per ticker+date.

    A ticker can appear more than once on a date (re-evaluated across cycles); we
    keep the first occurrence, which carries the same scores.
    """
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:
        if r.get("reason_code") not in _POOL_REASONS:
            continue
        if r.get("quality_score") is None:
            continue
        key = (r["ticker"], r["decision_date"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _rank_key(row: dict, arm: str) -> float:
    """Descending-sort key for an arm. Arm A = AI-blended, Arm B = quality only."""
    if arm == "A":
        v = row.get("adjusted_score")
        if v is None:
            v = row.get("final_score")
        return float(v if v is not None else 0)
    return float(row.get("quality_score") or 0)


def _passes_veto(row: dict, arm: str) -> bool:
    """Arm A drops grade D; Arm B keeps everything."""
    if arm == "A":
        return row.get("ai_grade") != "D"
    return True


# ── Walk-forward portfolio simulation ─────────────────────────────────────────
def walk_forward(candidates: list[dict], arm: str, api_key: str,
                 verify_tls: bool, notional: float,
                 outcome_cache: dict) -> dict:
    """Fill up to MAX_POSITIONS slots per day for one arm, oldest date first."""
    by_date: dict[str, list[dict]] = {}
    for c in candidates:
        by_date.setdefault(c["decision_date"], []).append(c)

    open_positions: list[dict] = []   # {ticker, exit_date}
    held_tickers: set[str] = set()
    picks: list[dict] = []

    for date in sorted(by_date):
        # 1. Free slots whose positions have exited strictly before today.
        still_open = []
        for p in open_positions:
            if p["exit_date"] < date:
                held_tickers.discard(p["ticker"])
            else:
                still_open.append(p)
        open_positions = still_open

        free = MAX_POSITIONS - len(open_positions)
        if free <= 0:
            continue

        ranked = sorted(
            (c for c in by_date[date]
             if _passes_veto(c, arm) and c["ticker"] not in held_tickers),
            key=lambda c: _rank_key(c, arm),
            reverse=True,
        )
        for cand in ranked:
            if free <= 0:
                break
            ticker = cand["ticker"]
            if ticker in held_tickers:
                continue
            key = (ticker, date)
            if key not in outcome_cache:
                outcome_cache[key] = forward_outcome(ticker, date, api_key, verify_tls)
            oc = outcome_cache[key]
            if oc is None:
                continue   # no bars available; cannot score this pick
            pnl = notional * oc["ret_pct"] / 100.0
            picks.append({
                "ticker": ticker, "date": date,
                "entry": oc["entry"], "exit_price": oc["exit_price"],
                "exit_date": oc["exit_date"], "reason": oc["reason"],
                "ret_pct": oc["ret_pct"], "pnl": pnl,
                "quality": cand.get("quality_score"),
                "adjusted": cand.get("adjusted_score") or cand.get("final_score"),
                "grade": cand.get("ai_grade"),
            })
            open_positions.append({"ticker": ticker, "exit_date": oc["exit_date"]})
            held_tickers.add(ticker)
            free -= 1

    total = sum(p["pnl"] for p in picks)
    wins = sum(1 for p in picks if p["pnl"] > 0)
    return {"arm": arm, "picks": picks, "total": total,
            "n": len(picks), "wins": wins}


# ── Veto audit (the high-signal current read) ─────────────────────────────────
def veto_audit(rows: list[dict], api_key: str, verify_tls: bool,
               notional: float) -> dict:
    """Forward outcome of every AI_VETO'd candidate — did the veto help or hurt?"""
    seen: set[tuple[str, str]] = set()
    results = []
    for r in rows:
        if r.get("reason_code") != "AI_VETO":
            continue
        key = (r["ticker"], r["decision_date"])
        if key in seen:
            continue
        seen.add(key)
        oc = forward_outcome(r["ticker"], r["decision_date"], api_key, verify_tls)
        if oc is None:
            results.append({"ticker": r["ticker"], "date": r["decision_date"],
                            "quality": r.get("quality_score"),
                            "no_bars": True})
            continue
        results.append({
            "ticker": r["ticker"], "date": r["decision_date"],
            "quality": r.get("quality_score"),
            "grade": r.get("ai_grade"),
            "entry": oc["entry"], "exit_price": oc["exit_price"],
            "exit_date": oc["exit_date"], "reason": oc["reason"],
            "ret_pct": oc["ret_pct"], "pnl": notional * oc["ret_pct"] / 100.0,
            "no_bars": False,
        })
    scored = [r for r in results if not r["no_bars"]]
    total = sum(r["pnl"] for r in scored)
    winners_removed = sum(1 for r in scored if r["pnl"] > 0)
    losers_avoided = sum(1 for r in scored if r["pnl"] <= 0)
    return {"results": results, "scored": scored, "total": total,
            "winners_removed": winners_removed, "losers_avoided": losers_avoided}


# ── Reporting ─────────────────────────────────────────────────────────────────
def _fmt_usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def report_veto_audit(audit: dict, notional: float) -> None:
    scored = audit["scored"]
    print("=" * 78)
    print("AI VETO AUDIT — forward Prove-It outcome of every grade-D vetoed candidate")
    print("=" * 78)
    print(f"Sizing: {_fmt_usd(notional)} per name (identical, hypothetical)\n")
    if not scored:
        print("No AI_VETO candidates could be scored (no bar data).")
        return
    print(f"{'ticker':8s} {'date':12s} {'qual':>4s} {'entry':>9s} {'exit':>9s} "
          f"{'ret%':>7s} {'P&L':>10s}  reason")
    for r in sorted(scored, key=lambda r: r["date"]):
        print(f"{r['ticker']:8s} {r['date']:12s} {str(r['quality']):>4s} "
              f"{r['entry']:>9.2f} {r['exit_price']:>9.2f} {r['ret_pct']:>7.2f} "
              f"{_fmt_usd(r['pnl']):>10s}  {r['reason']}")
    no_bars = [r for r in audit["results"] if r["no_bars"]]
    if no_bars:
        print(f"\n  ({len(no_bars)} vetoed names had no bar data and were skipped: "
              f"{', '.join(r['ticker'] for r in no_bars)})")
    print("\n" + "-" * 78)
    n = len(scored)
    print(f"Vetoed names scored:       {n}")
    print(f"  would have WON:          {audit['winners_removed']}  "
          f"(the veto REMOVED these)")
    print(f"  would have LOST/flat:    {audit['losers_avoided']}  "
          f"(the veto DODGED these)")
    print(f"\nNet P&L the veto GAVE UP:   {_fmt_usd(audit['total'])} at "
          f"{_fmt_usd(notional)}/name")
    if audit["total"] > 0:
        print("  → Interpretation: the vetoed names were net PROFITABLE, so the "
              "veto COST money\n    (it cut more winners than losers). This is the "
              "'AI cuts winners' failure\n    the register warns about — watch it.")
    elif audit["total"] < 0:
        print("  → Interpretation: the vetoed names were net LOSERS, so the veto "
              "SAVED money.\n    The AI's veto is doing its job on this sample.")
    else:
        print("  → Interpretation: net zero — the veto neither helped nor hurt on "
              "this sample.")


def report_ab(arm_a: dict, arm_b: dict, candidates: list[dict],
              notional: float) -> None:
    dates = sorted({c["decision_date"] for c in candidates})
    print("=" * 78)
    print("A/B SELECTION REPLAY — AI ON (Arm A) vs AI OFF (Arm B), exit held fixed")
    print("=" * 78)
    print(f"Eligible candidates: {len(candidates)}   distinct dates: {len(dates)}   "
          f"span: {dates[0]} → {dates[-1]}")
    print(f"Slots: {MAX_POSITIONS}   sizing: {_fmt_usd(notional)}/slot   "
          f"exit: {shipped_proveit().label}\n")

    for res, name in ((arm_a, "Arm A  AI ON  (rank=adjusted_score, veto grade-D)"),
                      (arm_b, "Arm B  AI OFF (rank=quality_score, no veto)")):
        wr = (100.0 * res["wins"] / res["n"]) if res["n"] else 0.0
        print(f"{name}")
        print(f"    picks={res['n']:3d}   winners={res['wins']:3d} ({wr:4.0f}%)   "
              f"total P&L={_fmt_usd(res['total'])}")

    contribution = arm_a["total"] - arm_b["total"]
    print("\n" + "-" * 78)
    print(f"AI CONTRIBUTION = P&L(A) − P&L(B) = {_fmt_usd(contribution)}")

    a_set = {(p["ticker"], p["date"]) for p in arm_a["picks"]}
    b_set = {(p["ticker"], p["date"]) for p in arm_b["picks"]}
    only_a = a_set - b_set
    only_b = b_set - a_set
    print(f"Picks that DIFFER between the arms: {len(only_a | only_b)} "
          f"(only-A={len(only_a)}, only-B={len(only_b)})")
    if not (only_a or only_b):
        print("  → The arms chose the SAME trades. The AI made NO difference to the "
              "SET taken\n    on this sample — contribution is ~$0 by construction, "
              "not by evidence.\n    This is expected while capacity (SLOTS_FULL) is "
              "the binding constraint.")
    else:
        pick_by_key = {(p["ticker"], p["date"]): p
                       for p in arm_a["picks"] + arm_b["picks"]}
        print("\n  Divergent picks (where the AI's footprint actually is):")
        print(f"  {'arm':4s} {'ticker':8s} {'date':12s} {'ret%':>7s} {'P&L':>10s}")
        for key in sorted(only_a, key=lambda k: k[1]):
            p = pick_by_key[key]
            print(f"  {'A':4s} {p['ticker']:8s} {p['date']:12s} "
                  f"{p['ret_pct']:>7.2f} {_fmt_usd(p['pnl']):>10s}")
        for key in sorted(only_b, key=lambda k: k[1]):
            p = pick_by_key[key]
            print(f"  {'B':4s} {p['ticker']:8s} {p['date']:12s} "
                  f"{p['ret_pct']:>7.2f} {_fmt_usd(p['pnl']):>10s}")


def print_caveats(n_candidates: int) -> None:
    print("\n" + "=" * 78)
    print("CAVEATS — read before believing any number above")
    print("=" * 78)
    print(
        "• Sample is tiny and single-regime. Every candidate is from one ~6-week\n"
        "  window; a result carried by one or two names is noise, which is why the\n"
        "  per-pick and per-veto deltas are printed rather than hidden in a mean.\n"
        "• Capacity, not the AI, is usually the binding constraint (SLOTS_FULL was\n"
        "  the modal skip reason). When the book is full the arms cannot diverge, so\n"
        "  the headline A/B contribution understates AND obscures the AI's effect.\n"
        "  The --veto-audit is the more trustworthy current read.\n"
        "• Forward outcomes are simulated at a fixed notional per slot and enter at\n"
        "  the session OPEN; real fills, sizing and commissions differ. These cancel\n"
        "  in the A−B delta but not in the absolute totals.\n"
        "• SCORE_FLOOR is applied as a shared gate (it reads the AI-blended score),\n"
        "  so Arm B is not given a separate quality-only floor. This is conservative\n"
        "  and slightly narrows Arm B's freedom.\n"
        f"• {n_candidates} eligible candidates scored. Below ~30 distinct TAKEN\n"
        "  trades per arm this cannot discriminate — see the register entry\n"
        "  ai-value-ab-replay (due 2026-12-01 / 90 closed trades)."
    )


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="AI-on/AI-off entry-selection A/B replay.")
    ap.add_argument("--insecure", action="store_true",
                    help="disable TLS verification (corporate cert).")
    ap.add_argument("--veto-audit", action="store_true",
                    help="only report the forward outcome of AI_VETO'd names.")
    ap.add_argument("--notional", type=float, default=NOTIONAL,
                    help=f"$ per slot (default {NOTIONAL:.0f}).")
    ap.add_argument("--json", metavar="PATH", help="write machine-readable results.")
    args = ap.parse_args()

    verify_tls = not args.insecure
    if args.insecure:
        _disable_tls_verification()
    api_key = _env("FMP_API_KEY")

    print("Loading trigger_decisions from Supabase …", file=sys.stderr)
    rows = load_decisions(verify_tls)
    print(f"  {len(rows)} decision rows loaded.", file=sys.stderr)

    if args.veto_audit:
        print("Simulating forward outcomes for vetoed names …", file=sys.stderr)
        audit = veto_audit(rows, api_key, verify_tls, args.notional)
        report_veto_audit(audit, args.notional)
        print_caveats(audit and len(audit["scored"]) or 0)
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({"mode": "veto_audit", "audit": audit}, fh, indent=2)
            print(f"\nWrote {args.json}")
        return

    candidates = eligible_candidates(rows)
    print(f"Simulating forward outcomes for {len(candidates)} eligible candidates "
          f"(cached under {_CACHE_DIR}) …", file=sys.stderr)
    outcome_cache: dict = {}
    arm_a = walk_forward(candidates, "A", api_key, verify_tls, args.notional, outcome_cache)
    arm_b = walk_forward(candidates, "B", api_key, verify_tls, args.notional, outcome_cache)
    report_ab(arm_a, arm_b, candidates, args.notional)
    print_caveats(len(candidates))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"mode": "ab", "arm_a": arm_a, "arm_b": arm_b}, fh, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
