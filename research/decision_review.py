#!/usr/bin/env python3
"""decision_review.py — the ACTIVE half of the Provisional Decision Register.

Reads decisions/provisional_decisions.json (the single source of truth) and asks
one question of each active decision: *is it due for review yet?* A decision is
due once the live closed-trade count has reached its `min_closed_trades` AND the
current date is on/after its `not_before` floor (either threshold may be null,
in which case it does not gate).

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
    10  ran cleanly, one or more decisions are DUE  (the workflow branches on this)
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


def is_due(decision: dict, n_trades: int, today: dt.date) -> tuple[bool, list[str]]:
    """A decision is due only when EVERY threshold it declares is satisfied."""
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
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()

    due_items, pending_items = [], []
    for d in registry["decisions"]:
        due, reasons = is_due(d, n_trades, today)
        row = {
            "id": d["id"],
            "title": d.get("title", d["id"]),
            "status": d.get("status"),
            "revisit": d.get("revisit"),
            "review_command": d.get("review_command"),
            "review_questions": d.get("review_questions", []),
            "adr": d.get("adr"),
            "reasons": reasons,
        }
        (due_items if due else pending_items).append(row)

    result = {
        "checked_at": today.isoformat(),
        "closed_trades": n_trades,
        "due": due_items,
        "pending": [p for p in pending_items if p["status"] == "active"],
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Provisional Decision Register — {today}  (closed trades: {n_trades})")
        print("=" * 72)
        if due_items:
            print(f"\n⏰ DUE FOR REVIEW ({len(due_items)}):")
            for it in due_items:
                print(f"\n  • {it['title']}  [{it['id']}]")
                for r in it["reasons"]:
                    print(f"      {r}")
                if it["review_command"]:
                    print(f"      run: {it['review_command']}")
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
        lines.append(f"• <b>{it['title']}</b>")
        if it["review_command"]:
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
