#!/usr/bin/env python3
"""decision_review.py — the ACTIVE half of the Provisional Decision Register.

Reads decisions/provisional_decisions.json (the single source of truth) and asks
two questions of each active entry:

    1. Is it DUE?        -- has the trigger fired? (closed-trade count / date)
    2. Is it ACTIONABLE? -- do the live preconditions hold right now?

These are deliberately separate. A trigger says "it is time to look at this"; a
precondition says "and the bot is currently in a state where acting is safe". An
entry that is due but blocked is still reported -- it is never silently skipped,
because "we forgot" is the exact failure mode this file exists to remove.

Preconditions are evaluated against LIVE portfolio state, so a work item like the
orchestrator rewrite can declare "only when the book is quiet" and have that
checked on the day rather than assumed months in advance.

This exists because the schedule in AGENTS.md is only a PASSIVE reminder — it
fires when a human happens to read the file. This script is run on a cron by
.github/workflows/decision_review.yml, which opens a persistent GitHub issue the
moment something is due, so a decision can never be silently forgotten.

Usage:
    python3 research/decision_review.py [--insecure] [--json] [--telegram]

    --insecure  skip TLS verification for Supabase (local trust-store issues;
                NOT needed in CI, where the trust store is clean)
    --json      emit the machine-readable result (what the workflow consumes)
    --telegram  also push a summary of DUE items via TELEGRAM_BOT_TOKEN/CHAT_IDS

Exit codes:
    0   ran cleanly, nothing due
    10  ran cleanly, one or more entries are DUE  (the workflow branches on this)
    1   error (registry missing/malformed, Supabase unreachable) — fail LOUD
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo
import sys
import urllib3

import requests

REGISTRY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "decisions", "provisional_decisions.json",
)


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"error: required environment variable {name} is not set")
    return val


def load_registry() -> dict:
    try:
        with open(REGISTRY) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"error: registry not found at {REGISTRY}")
    except json.JSONDecodeError as e:
        sys.exit(f"error: registry is not valid JSON ({e}) — a malformed register "
                 "must fail loud, never be silently skipped")
    if "decisions" not in data or not isinstance(data["decisions"], list):
        sys.exit("error: registry has no 'decisions' array")
    return data


def closed_trade_count(verify_tls: bool) -> int:
    """Number of rows in trade_history — i.e. closed trades, the sample size
    every provisional decision is waiting on."""
    if not verify_tls:
        urllib3.disable_warnings()
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    # Ask PostgREST for an exact count without pulling the rows.
    r = requests.get(
        f"{url}/rest/v1/trade_history",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "count=exact",
            "Range": "0-0",
        },
        params={"select": "id"},
        timeout=30,
        verify=verify_tls,
    )
    if r.status_code not in (200, 206):
        sys.exit(f"error: Supabase returned HTTP {r.status_code}: {r.text[:200]}")
    # PostgREST returns the total in the Content-Range header as "0-0/<total>".
    content_range = r.headers.get("Content-Range", "")
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[1]
        if total.isdigit():
            return int(total)
    # Fallback: length of the returned page (only correct for small tables).
    return len(r.json())


def portfolio_state(verify_tls: bool) -> dict:
    """Live bot state that preconditions are evaluated against.

    Returns open position count and the age in days of the YOUNGEST open
    position -- the two facts that decide whether disruptive work is safe today.
    Youngest is the one that matters: a single day-0 position is sitting in the
    tight Prove-It Phase 1 band, so the book is not quiet regardless of how long
    the other four have been held.

    `youngest_position_age_days` is None when the book is flat, which every
    precondition must treat as "no constraint" rather than "zero".
    """
    url = f"{_env('SUPABASE_URL').rstrip('/')}/rest/v1/portfolio_positions"
    r = requests.get(
        url,
        headers={"apikey": _env("SUPABASE_KEY"),
                 "Authorization": f"Bearer {_env('SUPABASE_KEY')}"},
        params={"select": "ticker,buy_date"},
        timeout=30, verify=verify_tls,
    )
    if r.status_code != 200:
        sys.exit(f"error: Supabase returned HTTP {r.status_code}: {r.text[:200]}")
    rows = r.json()
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()

    ages = []
    for row in rows:
        raw = (row.get("buy_date") or "")[:10]
        try:
            ages.append((today - dt.date.fromisoformat(raw)).days)
        except ValueError:
            # An unparseable buy_date must not silently read as age 0, which
            # would make a stale row look like a fresh position and block work
            # forever. Skip it and let the count still reflect the row.
            continue
    return {
        "open_positions": len(rows),
        "youngest_position_age_days": min(ages) if ages else None,
        "tickers": sorted(r.get("ticker", "?") for r in rows),
    }


def check_preconditions(decision: dict, state: dict) -> tuple[bool, list[str]]:
    """Is it SAFE to action this entry right now, given live bot state?

    Distinct from is_due(). Due means the trigger fired; actionable means the
    conditions the work assumes still hold. An entry with no `preconditions`
    block is always actionable, so every existing parameter review is unaffected.
    """
    pre = decision.get("preconditions") or {}
    if not pre:
        return True, []

    ok, reasons = True, []

    max_open = pre.get("max_open_positions")
    if max_open is not None:
        n = state["open_positions"]
        if n <= max_open:
            reasons.append(f"open positions {n} <= {max_open} ✓")
        else:
            reasons.append(f"open positions {n} > {max_open}")
            ok = False

    min_age = pre.get("min_position_age_days")
    if min_age is not None:
        age = state["youngest_position_age_days"]
        if age is None:
            reasons.append("book is flat — no position-age constraint ✓")
        elif age >= min_age:
            reasons.append(f"youngest position {age}d >= {min_age}d ✓")
        else:
            reasons.append(f"youngest position {age}d < {min_age}d")
            ok = False

    return ok, reasons


def is_due(decision: dict, n_trades: int, today: dt.date) -> tuple[bool, list[str]]:
    """Has the TRIGGER fired? Says nothing about whether acting is safe today."""
    if decision.get("status") != "active":
        return False, ["status is not 'active'"]
    revisit = decision.get("revisit") or {}
    reasons: list[str] = []
    due = True

    min_trades = revisit.get("min_closed_trades")
    if min_trades is not None:
        if n_trades >= min_trades:
            reasons.append(f"trades {n_trades} >= {min_trades} ✓")
        else:
            reasons.append(f"trades {n_trades} < {min_trades} (need {min_trades - n_trades} more)")
            due = False

    not_before = revisit.get("not_before")
    if not_before:
        nb = dt.date.fromisoformat(not_before)
        if today >= nb:
            reasons.append(f"date {today} >= {not_before} ✓")
        else:
            reasons.append(f"date {today} < {not_before}")
            due = False

    if min_trades is None and not not_before:
        reasons.append("no thresholds set — always due")

    return due, reasons


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification for Supabase (local only)")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable result for the workflow")
    ap.add_argument("--telegram", action="store_true",
                    help="push a summary of DUE items to Telegram")
    args = ap.parse_args()

    registry = load_registry()
    n_trades = closed_trade_count(verify_tls=not args.insecure)
    state = portfolio_state(verify_tls=not args.insecure)
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()

    due_items, pending_items = [], []
    for d in registry["decisions"]:
        due, reasons = is_due(d, n_trades, today)
        actionable, pre_reasons = check_preconditions(d, state)
        row = {
            "id": d["id"],
            "title": d.get("title", d["id"]),
            "kind": d.get("kind", "parameter"),
            "status": d.get("status"),
            "revisit": d.get("revisit"),
            "preconditions": d.get("preconditions"),
            "actionable": actionable,
            "precondition_reasons": pre_reasons,
            "review_command": d.get("review_command"),
            "review_questions": d.get("review_questions", []),
            "adr": d.get("adr"),
            "reasons": reasons,
        }
        (due_items if due else pending_items).append(row)

    result = {
        "checked_at": today.isoformat(),
        "closed_trades": n_trades,
        "portfolio": state,
        "due": due_items,
        "pending": [p for p in pending_items if p["status"] == "active"],
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        pf = state["youngest_position_age_days"]
        print(f"Provisional Decision Register — {today}  (closed trades: {n_trades})")
        print(f"Bot state: {state['open_positions']} open position(s)"
              + (f", youngest {pf}d old" if pf is not None else ", book flat")
              + (f"  [{', '.join(state['tickers'])}]" if state["tickers"] else ""))
        print("=" * 72)
        if due_items:
            ready = [i for i in due_items if i["actionable"]]
            blocked = [i for i in due_items if not i["actionable"]]
            if ready:
                print(f"\n⏰ DUE — ready to action ({len(ready)}):")
                for it in ready:
                    print(f"\n  • {it['title']}  [{it['id']}]")
                    for r in it["reasons"] + it["precondition_reasons"]:
                        print(f"      {r}")
                    if it["review_command"]:
                        print(f"      run: {it['review_command']}")
            if blocked:
                print(f"\n🚧 DUE — but BLOCKED by live bot state ({len(blocked)}):")
                for it in blocked:
                    print(f"\n  • {it['title']}  [{it['id']}]")
                    for r in it["precondition_reasons"]:
                        print(f"      {r}")
                    print("      → still tracked; re-check with this same command.")
        else:
            print("\n✓ Nothing due for review.")
        active_pending = result["pending"]
        if active_pending:
            print(f"\n⧗ Tracked, not yet due ({len(active_pending)}):")
            for it in active_pending:
                print(f"  • {it['title']}  [{it['id']}] — {'; '.join(it['reasons'])}")

    if args.telegram and due_items:
        _notify_telegram(due_items, n_trades, today)

    sys.exit(10 if due_items else 0)


def _notify_telegram(due_items: list[dict], n_trades: int, today: dt.date) -> None:
    """Best-effort Telegram ping. Never changes the exit code."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chat_ids:
        print("telegram: not configured, skipping", file=sys.stderr)
        return
    lines = [f"⏰ <b>Decision review due</b> ({today}, {n_trades} closed trades)", ""]
    for it in due_items:
        flag = "" if it["actionable"] else " 🚧 <i>blocked</i>"
        lines.append(f"• <b>{it['title']}</b>{flag}")
        if not it["actionable"]:
            for r in it["precondition_reasons"]:
                lines.append(f"  {r}")
        elif it["review_command"]:
            lines.append(f"  <code>{it['review_command']}</code>")
    text = "\n".join(lines)
    for cid in chat_ids:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": cid, "text": text, "parse_mode": "HTML"},
                timeout=(5, 15),
            )
        except Exception as e:  # noqa: BLE001 — notifications must never raise
            print(f"telegram: send failed for {cid}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
