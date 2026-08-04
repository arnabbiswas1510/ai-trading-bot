#!/usr/bin/env python3
"""
managed_exit.py — Exit named positions at the best price available today,
instead of dumping them at whatever the current print happens to be.

WHY THIS EXISTS
    force_sell.py liquidates immediately with a marketable limit. That is the
    right tool for an emergency, but it guarantees you sell at the current
    price — which is very often a local trough, because the impulse to exit
    usually arrives *after* a drop.

    This tool instead rides the position's high-water mark for the rest of the
    session and exits on a pullback from that high. You still exit today, but at
    a price anchored to the day's best level rather than the moment you decided.

MECHANISM
    1. Places an IBKR native trailing stop (TRAIL) on each named ticker. IBKR
       tracks the high-water mark tick-by-tick, which is far finer-grained than
       any polling this script could do, and it keeps working even if this
       script is killed.
    2. Polls until one of three things happens:
         - the trail fires  -> position exits near the session high
         - the hard floor breaks -> exit immediately, do not wait (drawdown cap)
         - the deadline passes -> force a market exit so you are flat today
    3. Archives to trade_history and clears portfolio_positions, exactly like
       the normal sell path.

    The hard floor is what makes this safe to leave running: without it, a name
    that gaps down and keeps sliding would sit unsold until the deadline.

CHOOSING THE TRAIL
    --trail auto (default) scales the trail to the stock's own volatility, using
    ATR_TRAIL_FRACTION of entry_atr_pct, clamped to [MIN, MAX]. A fixed tight
    trail is actively harmful on a volatile name: a 0.6% trail on a stock with a
    2.9% daily range fires on the first tick of noise, which just reproduces
    "sell immediately" with extra steps.

USAGE
    python managed_exit.py NBIX CPAY
    python managed_exit.py --all
    python managed_exit.py NBIX --trail 1.5 --deadline 15:30
    python managed_exit.py NBIX --dry-run          # show the plan, place nothing

CASH ACCOUNT REQUIREMENT (same constraint as force_sell.py)
    Sells must use clientId=1, the session that placed the buys, or IBKR treats
    them as opening a short. The execution-agent must therefore be STOPPED:

        docker compose stop execution-agent
        docker exec -it execution-agent python3 managed_exit.py NBIX
        docker compose start execution-agent

    Leaving the agent running would also fight this script for control of the
    stop orders.
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

from ib_insync import IB, Stock, Order, MarketOrder
from supabase import create_client

NY = ZoneInfo("America/New_York")

IB_HOST      = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT      = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CLIENT_ID    = 1

# Trail sizing. The trail must clear the stock's normal intraday noise or it
# fires instantly and defeats the whole point of the tool.
ATR_TRAIL_FRACTION = float(os.getenv("MANAGED_EXIT_ATR_FRACTION", 0.40))
MIN_TRAIL_PCT      = float(os.getenv("MANAGED_EXIT_MIN_TRAIL_PCT", 0.008))   # 0.8%
MAX_TRAIL_PCT      = float(os.getenv("MANAGED_EXIT_MAX_TRAIL_PCT", 0.030))   # 3.0%
DEFAULT_ATR_PCT    = float(os.getenv("MANAGED_EXIT_DEFAULT_ATR_PCT", 2.0))

# Hard floor: give up on riding the high if the position falls this far below
# the price at which it was armed. Caps the cost of being patient.
FLOOR_PCT          = float(os.getenv("MANAGED_EXIT_FLOOR_PCT", 0.020))       # 2.0%

DEFAULT_DEADLINE   = os.getenv("MANAGED_EXIT_DEADLINE", "15:50")
POLL_SECONDS       = int(os.getenv("MANAGED_EXIT_POLL_SECONDS", 30))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS  = os.getenv("TELEGRAM_CHAT_IDS", "")


def _notify(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    try:
        import requests
        for chat_id in TELEGRAM_CHAT_IDS.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id.strip(), "text": msg, "parse_mode": "HTML"},
                timeout=8,
            )
    except Exception as e:
        print(f"  ⚠ Telegram notification failed: {e}")


def resolve_trail_pct(position: dict, requested: str) -> tuple[float, str]:
    """
    Returns (trail_pct, explanation).

    'auto' scales to the stock's own ATR so the trail sits outside normal noise.
    Anything else is parsed as a literal percentage.
    """
    if requested != "auto":
        return float(requested) / 100.0, f"fixed {float(requested):.2f}%"

    atr_pct = position.get("entry_atr_pct")
    source = "entry_atr_pct"
    if not atr_pct or float(atr_pct) <= 0:
        atr_pct, source = DEFAULT_ATR_PCT, "default (no ATR on record)"
    atr_pct = float(atr_pct)

    raw = (atr_pct / 100.0) * ATR_TRAIL_FRACTION
    trail = max(MIN_TRAIL_PCT, min(MAX_TRAIL_PCT, raw))
    note = f"auto: {ATR_TRAIL_FRACTION:.0%} of {atr_pct:.2f}% ATR ({source})"
    if trail != raw:
        note += f", clamped to {trail*100:.2f}%"
    return trail, note


def parse_deadline(hhmm: str) -> datetime.datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    now = datetime.datetime.now(NY)
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def current_price(ib: IB, contract, fallback: float) -> tuple[float, str]:
    try:
        from execution_agent import fetch_ibkr_delayed_price
        px, method = fetch_ibkr_delayed_price(ib, contract)
        if px and px > 0:
            return float(px), method
    except Exception as e:
        print(f"    ⚠ IBKR price lookup failed: {e}")
    try:
        from execution_agent import get_live_price
        px = get_live_price(contract.symbol)
        if px and px > 0:
            return float(px), "FMP"
    except Exception:
        pass
    return fallback, "stale fallback"


def cancel_sells(ib: IB, ticker: str) -> int:
    cancelled = 0
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.action == "SELL"
                and trade.orderStatus.status not in ("Filled", "Cancelled", "Inactive")):
            try:
                ib.cancelOrder(trade.order)
                cancelled += 1
            except Exception:
                pass
    if cancelled:
        ib.sleep(1)
    return cancelled


def place_trail(ib: IB, contract, shares: int, trail_pct: float, account: str):
    """Native IBKR trailing stop. IBKR tracks the high-water mark internally."""
    order = Order()
    order.action        = "SELL"
    order.orderType     = "TRAIL"
    order.totalQuantity = shares
    order.trailingPercent = round(trail_pct * 100, 4)
    order.tif           = "DAY"
    order.account       = account
    order.transmit      = True
    return ib.placeOrder(contract, order)


def market_exit(ib: IB, contract, shares: int, account: str):
    cancel_sells(ib, contract.symbol)
    ib.sleep(1)
    order = MarketOrder("SELL", shares)
    order.account = account
    trade = ib.placeOrder(contract, order)
    for _ in range(30):
        ib.sleep(2)
        if trade.orderStatus.status == "Filled":
            return trade
        if trade.orderStatus.status in ("Cancelled", "Inactive"):
            return trade
    return trade


def archive(supabase, position: dict, shares: int, fill_price: float, reason: str):
    ticker    = position["ticker"]
    buy_price = float(position.get("buy_price", 0))
    pnl = round((fill_price - buy_price) * shares, 2)
    pct = round(((fill_price / buy_price) - 1.0) * 100.0, 2) if buy_price else 0.0
    try:
        buy_date = datetime.datetime.fromisoformat(
            str(position.get("buy_date", "")).replace("Z", "+00:00"))
    except Exception:
        buy_date = datetime.datetime.now(datetime.timezone.utc)

    supabase.table("trade_history").insert({
        "ticker": ticker, "shares": shares, "buy_price": buy_price,
        "buy_date": buy_date.isoformat(),
        "buy_reason": position.get("buy_reason", "manual"),
        "sell_price": fill_price,
        "sell_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sell_reason": reason, "profit_loss": pnl, "percent_return": pct,
    }).execute()
    supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()

    print(f"  ✓ {ticker} exited @ ${fill_price:.2f} | P&L ${pnl:+,.2f} ({pct:+.2f}%) | {reason}")
    _notify(f"🔵 <b>{ticker}</b> managed exit @ ${fill_price:.2f}\n"
            f"P&L ${pnl:+,.2f} ({pct:+.2f}%)\n{reason}")
    return pnl


def main():
    ap = argparse.ArgumentParser(description="Exit positions at the session high-water mark.")
    ap.add_argument("tickers", nargs="*", help="tickers to exit")
    ap.add_argument("--all", action="store_true", help="exit every open position")
    ap.add_argument("--trail", default="auto",
                    help="trailing stop percent, or 'auto' to scale to ATR (default: auto)")
    ap.add_argument("--deadline", default=DEFAULT_DEADLINE,
                    help=f"force a market exit at this NY time (default: {DEFAULT_DEADLINE})")
    ap.add_argument("--floor-pct", type=float, default=FLOOR_PCT * 100,
                    help="abandon the ride and exit at once if price falls this %% below the arming price")
    ap.add_argument("--no-floor", action="store_true", help="disable the hard floor")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, place no orders")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    if not args.tickers and not args.all:
        ap.error("name at least one ticker, or pass --all")
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set."); sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    holdings = supabase.table("portfolio_positions").select("*").execute().data or []
    if not holdings:
        print("Portfolio is empty — nothing to exit."); sys.exit(0)

    by_ticker = {h["ticker"].upper(): h for h in holdings}
    wanted = list(by_ticker) if args.all else [t.upper() for t in args.tickers]

    missing = [t for t in wanted if t not in by_ticker]
    if missing:
        print(f"✗ Not in portfolio: {', '.join(missing)}")
        print(f"  Holdings: {', '.join(sorted(by_ticker))}")
        sys.exit(1)

    deadline = parse_deadline(args.deadline)
    now = datetime.datetime.now(NY)
    print("=" * 68)
    print("  Managed Exit — ride the session high, then exit")
    print("=" * 68)
    print(f"  Now {now:%H:%M} NY | deadline {deadline:%H:%M} NY")
    if deadline <= now:
        print(f"  ⚠ Deadline {args.deadline} has already passed — positions will exit at market immediately.")

    plan = []
    for t in wanted:
        pos = by_ticker[t]
        trail, note = resolve_trail_pct(pos, args.trail)
        plan.append((pos, trail))
        print(f"    {t:<6} {pos['shares']:>5} sh  entry ${float(pos['buy_price']):.2f}  "
              f"trail {trail*100:.2f}%  ({note})")
    if not args.no_floor:
        print(f"  Hard floor: exit at once if price drops {args.floor_pct:.2f}% below the arming price.")

    if args.dry_run:
        print("\n  --dry-run: no orders placed."); return
    if not args.yes:
        if input("\n  Type 'yes' to arm these exits: ").strip().lower() != "yes":
            print("  Aborted."); sys.exit(0)

    print(f"\n  Connecting to IB Gateway {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})...")
    print("  ⚠ The execution-agent must be STOPPED, or it will fight this script for the stops.")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"  ✗ Could not connect: {e}")
        print("  Hint: docker compose stop execution-agent")
        sys.exit(1)

    try:
        account = os.getenv("IBKR_ACCOUNT") or next(
            (a for a in ib.managedAccounts() if not a.startswith("DU")),
            (ib.managedAccounts() or [""])[0])
        ib.reqPositions(); ib.sleep(3)
        ibkr_pos = {p.contract.symbol: int(p.position) for p in ib.positions()}

        live = []
        for pos, trail in plan:
            t = pos["ticker"].upper()
            if t not in ibkr_pos or ibkr_pos[t] <= 0:
                print(f"  ✗ {t} not held at IBKR (found {list(ibkr_pos)}) — skipping.")
                continue
            shares = ibkr_pos[t]
            if shares != int(pos.get("shares", 0)):
                print(f"  ⚠ {t}: Supabase says {pos.get('shares')}, IBKR says {shares}. Using IBKR.")
            contract = Stock(t, "SMART", "USD")
            ib.qualifyContracts(contract)
            px, method = current_price(ib, contract, float(pos["buy_price"]))
            cancel_sells(ib, t)
            place_trail(ib, contract, shares, trail, account)
            floor = None if args.no_floor else px * (1 - args.floor_pct / 100.0)
            print(f"  ✓ {t}: armed {trail*100:.2f}% trail at ${px:.2f} ({method})"
                  + (f", floor ${floor:.2f}" if floor else ""))
            try:
                supabase.table("portfolio_positions").update({
                    "exit_armed": True,
                    "exit_armed_at": datetime.datetime.now(NY).isoformat(),
                    "exit_armed_reason": f"managed_exit {trail*100:.2f}% trail",
                    "exit_armed_price": round(px, 4),
                }).eq("ticker", t).execute()
            except Exception as e:
                print(f"    ⚠ could not record exit_armed for {t}: {e}")
            live.append({"pos": pos, "contract": contract, "shares": shares,
                         "floor": floor, "trail": trail})

        if not live:
            print("\n  Nothing armed."); return

        _notify(f"🕒 Managed exit armed on {', '.join(x['pos']['ticker'] for x in live)}\n"
                f"Riding the session high until {deadline:%H:%M} NY.")

        print(f"\n  Monitoring every {POLL_SECONDS}s until filled or {deadline:%H:%M} NY. Ctrl-C to stop.")
        while live:
            ib.sleep(POLL_SECONDS)
            now = datetime.datetime.now(NY)
            still = []
            for item in live:
                t = item["pos"]["ticker"].upper()
                ib.reqPositions(); ib.sleep(1)
                held = {p.contract.symbol: int(p.position) for p in ib.positions()}
                if held.get(t, 0) <= 0:
                    fills = [f for f in ib.fills() if f.contract.symbol == t]
                    fp = float(fills[-1].execution.price) if fills else 0.0
                    if fp <= 0:
                        fp, _ = current_price(ib, item["contract"], float(item["pos"]["buy_price"]))
                    archive(supabase, item["pos"], item["shares"], fp, "managed_exit: trailing stop")
                    continue

                px, _ = current_price(ib, item["contract"], float(item["pos"]["buy_price"]))
                if item["floor"] and px <= item["floor"]:
                    print(f"  ⚠ {t}: ${px:.2f} broke floor ${item['floor']:.2f} — exiting now.")
                    tr = market_exit(ib, item["contract"], item["shares"], account)
                    if tr.orderStatus.status == "Filled":
                        archive(supabase, item["pos"], int(tr.orderStatus.filled),
                                round(tr.orderStatus.avgFillPrice, 2),
                                "managed_exit: hard floor breached")
                        continue
                    print(f"  ✗ {t}: floor exit did not fill ({tr.orderStatus.status})")
                elif now >= deadline:
                    print(f"  ⏰ {t}: deadline reached — exiting at market.")
                    tr = market_exit(ib, item["contract"], item["shares"], account)
                    if tr.orderStatus.status == "Filled":
                        archive(supabase, item["pos"], int(tr.orderStatus.filled),
                                round(tr.orderStatus.avgFillPrice, 2),
                                "managed_exit: deadline")
                        continue
                    print(f"  ✗ {t}: deadline exit did not fill ({tr.orderStatus.status})")
                else:
                    print(f"    {t}: ${px:.2f}  (trail {item['trail']*100:.2f}% active"
                          + (f", floor ${item['floor']:.2f}" if item["floor"] else "") + ")")
                still.append(item)
            live = still
        print("\n  ✅ All requested positions exited.")
    except KeyboardInterrupt:
        print("\n  Interrupted. NOTE: the IBKR trailing stops remain live and will")
        print("  still fire on their own. Cancel them in TWS if that is not what you want.")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
