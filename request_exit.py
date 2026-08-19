#!/usr/bin/env python3
"""
request_exit.py — Queue a Smart OCA Managed Exit for a held position.

WHAT THIS IS
    This writes a row to the `exit_requests` table. It does NOT talk to IBKR.
    The running execution-agent picks the request up on its next 15-minute
    cycle and places an IBKR OCA pair on the position:

        upper leg   LMT  SELL at an optimistic recovery target
        lower leg   TRAIL SELL that ratchets up behind any bounce

    One cancels the other.

WHY IT WORKS THIS WAY
    force_sell.py and managed_exit.py both connect to IBKR as clientId=1 — the
    session that placed the original buys — because a cash account otherwise
    treats the sell as opening a short. That forces you to stop the
    execution-agent, which leaves your ENTIRE portfolio unmonitored while you
    babysit a single exit.

    This script sidesteps that completely: it only touches Supabase, so the
    agent keeps running and places the order itself. Nothing has to be stopped
    and nothing goes unwatched.

WHY THE LOWER LEG IS A TRAIL, NOT A STOP
    With a static stop, a position that rallies most of the way to your limit
    and then fades still exits at the original stop price — you hand back the
    entire move. A trailing stop follows the advance and banks whatever the
    bounce actually delivered. That upside is the only thing that justifies
    waiting instead of selling now.

USAGE
    # Force sell — market exit on the agent's next cycle, agent stays running
    python request_exit.py DELL --now

    # Recover-to-a-level exit (the common case)
    python request_exit.py DELL --limit-abs 489.89 --trail 2.5

    # Wait for breakeven, trail scaled to the stock's own ATR
    python request_exit.py DELL --breakeven --trail auto

    # Target +3% above entry, hard floor 4%, give it 5 trading days
    python request_exit.py NVDA --limit-pct-entry 3 --trail 2 --floor 4 --expires 5

    # Trail only, no upper leg (a plain managed exit)
    python request_exit.py NVDA --no-limit --trail auto

    python request_exit.py --list          # show queued / in-flight requests
    python request_exit.py --cancel DELL   # withdraw a request

TWO DIFFERENT INTENTS
    --now      "get me out."       Market sell, no upper leg, no waiting.
    otherwise  "get me out well."  OCA pair, bounded by a floor and an expiry.

    Both run through the queue, so neither requires stopping the agent. Only
    force_sell.py acts in under 15 minutes, and it is the only one that still
    needs the agent stopped — reserve it for the case where the delay itself
    is the risk.

SAFETY
    While an OCA request is PLACED, the agent SUSPENDS its automated exit rules
    for that ticker (thesis stop, dollar stop, intraday minimiser, EMA-21 exit).
    Those all cancel open SELL orders, which would destroy the OCA. The OCA's
    own hard floor and expiry are what protect the position instead — so do not
    set --floor absurdly wide. (--now exits immediately, so it never suspends
    anything.)
"""

import os
import sys
import argparse
import datetime
from zoneinfo import ZoneInfo

if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from supabase import create_client

NY = ZoneInfo("America/New_York")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ACTIVE = ("PENDING", "PLACED")


def _client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def cmd_list(sb):
    rows = (sb.table("exit_requests").select("*")
            .order("created_at", desc=True).limit(25).execute().data) or []
    if not rows:
        print("No exit requests on record.")
        return
    print(f"{'ID':>4}  {'TICKER':<7} {'STATUS':<10} {'LIMIT':>9} {'TRAIL':>7} "
          f"{'FLOOR':>7} {'EXP':>4}  NOTE")
    print("-" * 78)
    for r in rows:
        lim = r.get("placed_limit_price") or r.get("limit_value")
        lim_s = f"${float(lim):.2f}" if lim else (r.get("limit_mode") or "-")
        tr = r.get("placed_trail_pct") or r.get("stop_value")
        tr_s = f"{float(tr):.2f}%" if tr else (r.get("stop_mode") or "-")
        fl = r.get("hard_floor_pct")
        print(f"{r['id']:>4}  {r['ticker']:<7} {r['status']:<10} {lim_s:>9} {tr_s:>7} "
              f"{(f'{float(fl):.1f}%' if fl else '-'):>7} {str(r.get('expires_after_days') or '-'):>4}  "
              f"{(r.get('note') or r.get('last_error') or '')[:28]}")


def cmd_cancel(sb, ticker):
    rows = (sb.table("exit_requests").select("*")
            .eq("ticker", ticker).in_("status", list(ACTIVE)).execute().data) or []
    if not rows:
        print(f"No active exit request for {ticker}.")
        return
    for r in rows:
        sb.table("exit_requests").update({
            "status": "CANCELLED",
            "note": "cancelled via request_exit.py",
            "updated_at": datetime.datetime.now(NY).isoformat(),
        }).eq("id", r["id"]).execute()
        print(f"✓ Cancelled exit request #{r['id']} for {ticker} (was {r['status']}).")
    if any(r["status"] == "PLACED" for r in rows):
        print("\n⚠ The OCA orders are still live at IBKR. The agent will restore its")
        print("  normal trailing stop on the next cycle, which cancels them. If you")
        print("  need them gone right now, cancel them in TWS.")


def main():
    ap = argparse.ArgumentParser(
        description="Queue a Smart OCA Managed Exit (agent places the orders).")
    ap.add_argument("ticker", nargs="?", help="ticker to exit")
    ap.add_argument("--list", action="store_true", help="show requests and exit")
    ap.add_argument("--cancel", metavar="TICKER", help="cancel the active request for TICKER")

    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit-abs", type=float, metavar="PRICE",
                   help="upper leg at an absolute price")
    g.add_argument("--limit-pct-entry", type=float, metavar="PCT",
                   help="upper leg at entry price %%+PCT (e.g. 3 = 3%% above entry)")
    g.add_argument("--limit-pct-price", type=float, metavar="PCT",
                   help="upper leg at PCT above the price when placed")
    g.add_argument("--breakeven", action="store_true",
                   help="upper leg at the entry price (default)")
    g.add_argument("--no-limit", action="store_true",
                   help="no upper leg — trailing exit only")
    g.add_argument("--now", action="store_true",
                   help="MARKET exit on the next agent cycle — a force sell routed "
                        "through the queue, so the agent never has to be stopped")

    ap.add_argument("--trail", default="auto",
                    help="lower leg trailing percent, or 'auto' to scale to ATR (default: auto)")
    ap.add_argument("--floor", type=float, default=None, metavar="PCT",
                    help="market-exit if price falls PCT%% below the placement price")
    ap.add_argument("--expires", type=int, default=None, metavar="DAYS",
                    help="market-exit after DAYS trading days if neither leg filled")
    ap.add_argument("--note", default=None)
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    sb = _client()

    if args.list:
        cmd_list(sb); return
    if args.cancel:
        cmd_cancel(sb, args.cancel.upper()); return
    if not args.ticker:
        ap.error("name a ticker, or use --list / --cancel")

    ticker = args.ticker.upper()

    holdings = {h["ticker"].upper(): h for h in
                (sb.table("portfolio_positions").select("*").execute().data or [])}
    if ticker not in holdings:
        print(f"✗ {ticker} is not in the portfolio.")
        print(f"  Holdings: {', '.join(sorted(holdings)) or '(none)'}")
        sys.exit(1)
    pos = holdings[ticker]

    existing = (sb.table("exit_requests").select("id,status")
                .eq("ticker", ticker).in_("status", list(ACTIVE)).execute().data) or []
    if existing:
        print(f"✗ {ticker} already has an active exit request "
              f"(#{existing[0]['id']}, {existing[0]['status']}).")
        print(f"  Cancel it first:  python request_exit.py --cancel {ticker}")
        sys.exit(1)

    # ── Resolve the upper leg intent ─────────────────────────────────────────
    if args.now:
        limit_mode, limit_value = "NONE", None
    elif args.no_limit:
        limit_mode, limit_value = "NONE", None
    elif args.limit_abs is not None:
        limit_mode, limit_value = "ABS", args.limit_abs
    elif args.limit_pct_entry is not None:
        limit_mode, limit_value = "PCT_FROM_ENTRY", args.limit_pct_entry
    elif args.limit_pct_price is not None:
        limit_mode, limit_value = "PCT_FROM_PRICE", args.limit_pct_price
    else:
        limit_mode, limit_value = "BREAKEVEN", None

    if args.now:
        stop_mode, stop_value = "MARKET", None
    elif str(args.trail).lower() == "auto":
        stop_mode, stop_value = "ATR_AUTO", None
    else:
        stop_mode, stop_value = "TRAIL_PCT", float(args.trail)

    entry  = float(pos["buy_price"])
    shares = int(pos["shares"])

    print("=" * 66)
    print(f"  {'MARKET Exit' if args.now else 'Smart OCA Managed Exit'} — {ticker}")
    print("=" * 66)
    print(f"  Holding      {shares} sh @ ${entry:.2f}  (cost ${shares*entry:,.2f})")

    if args.now:
        print("  Action       SELL AT MARKET on the agent's next cycle (within 15 min)")
        print()
        print("  This is a force sell. It does NOT wait for a bounce and has no")
        print("  upper leg — use it when you want out, not when you want out well.")
        print("  The agent stays running, so the rest of the portfolio keeps its")
        print("  stops. If you need the fill in under 15 minutes, force_sell.py")
        print("  is still the only tool that acts immediately.")
    else:
        if limit_mode == "ABS":
            px = float(limit_value)
            print(f"  Upper (LMT)  ${px:.2f}   P&L if filled ${shares*(px-entry):+,.2f} "
                  f"({(px/entry-1)*100:+.2f}%)")
        elif limit_mode == "BREAKEVEN":
            print(f"  Upper (LMT)  ${entry:.2f} (breakeven)   P&L if filled $0.00")
        elif limit_mode == "NONE":
            print("  Upper (LMT)  none — trailing exit only")
        else:
            print(f"  Upper (LMT)  {limit_mode} {limit_value:+.2f}% (resolved at placement)")
        print(f"  Lower        {stop_mode}" + (f" {stop_value:.2f}%" if stop_value else
              f" (scaled to ATR {pos.get('entry_atr_pct') or '?'}%)"))
        if args.floor is not None:
            print(f"  Hard floor   {args.floor:.2f}% below the placement price")
        if args.expires is not None:
            print(f"  Expiry       {args.expires} trading day(s), then market exit")
        print()
        print("  While this is active the agent SUSPENDS its automated exit rules for")
        print(f"  {ticker} — the OCA plus the floor/expiry backstops govern it instead.")

    if not args.yes:
        if input("\n  Type 'yes' to queue this exit: ").strip().lower() != "yes":
            print("  Aborted."); sys.exit(0)

    payload = {
        "ticker": ticker,
        "limit_mode": limit_mode,
        "limit_value": limit_value,
        "stop_mode": stop_mode,
        "stop_value": stop_value,
        "hard_floor_pct": args.floor,
        "status": "PENDING",
        "requested_by": os.getenv("USER", "manual"),
        "note": args.note,
    }
    if args.expires is not None:
        payload["expires_after_days"] = args.expires

    try:
        res = sb.table("exit_requests").insert(payload).execute()
    except Exception as e:
        if "42P01" in str(e) or "PGRST205" in str(e):
            print("\n✗ exit_requests table not found.")
            print("  Apply migrations/add_exit_requests.sql in the Supabase SQL editor first.")
            sys.exit(1)
        raise

    rid = (res.data or [{}])[0].get("id", "?")
    print(f"\n  ✅ Queued as exit request #{rid}.")
    print("  The execution-agent will place the OCA on its next cycle (within 15 min,")
    print("  or from 09:45 ET if the market is currently closed).")
    print(f"  Track it with:  python request_exit.py --list")


if __name__ == "__main__":
    main()
