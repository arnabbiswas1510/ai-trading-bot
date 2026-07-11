#!/usr/bin/env python3
"""
force_sell.py — Emergency portfolio liquidation tool.

Usage:
    python force_sell.py AAPL          # sell AAPL directly
    python force_sell.py               # show portfolio menu, pick by number
    python force_sell.py "Force sell NVDA"   # natural language form

What it does:
  1. Connects to the running IB Gateway.
  2. Looks up the ticker in portfolio_positions (Supabase).
  3. If found, immediately places a market SELL order on IBKR.
  4. Archives the trade in trade_history and removes from portfolio_positions.
  5. Sends a Telegram notification.

The freed cash slot will be picked up by the execution-agent's next 15-min
buy check (run_market_open_buys) — same day if market is open, next morning
at 9:30 AM ET otherwise.

Run inside the execution-agent container:
    docker exec -it execution-agent python3 force_sell.py
Or from the project root (requires local .env with IB_GATEWAY_HOST etc.):
    python force_sell.py [TICKER]
"""

import os
import sys
import re
import datetime
from zoneinfo import ZoneInfo

# ── Load .env if running outside Docker ──────────────────────────────────────
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from ib_insync import IB, Stock, MarketOrder
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
IB_HOST      = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT      = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CLIENT_ID    = 76   # Unique — doesn't clash with execution-agent (uses 1) or tests (77/78)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS  = os.getenv("TELEGRAM_CHAT_IDS", "")


def _notify(msg: str):
    """Fire-and-forget Telegram notification."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    try:
        import requests
        for chat_id in TELEGRAM_CHAT_IDS.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id.strip(), "text": msg, "parse_mode": "Markdown"},
                timeout=8,
            )
    except Exception as e:
        print(f"  ⚠ Telegram notification failed: {e}")


def _get_portfolio(supabase):
    """Return list of stock positions from Supabase portfolio_positions."""
    res = supabase.table("portfolio_positions").select("*").execute()
    return res.data or []


def _resolve_ticker(raw_arg: str | None, holdings: list) -> str | None:
    """
    Parse the ticker from a raw CLI argument.
    Handles:
      - 'AAPL'
      - 'Force sell AAPL'
      - 'force sell aapl'
    Returns the ticker in uppercase, or None if not parseable.
    """
    if not raw_arg:
        return None
    # Strip 'force sell' prefix (case-insensitive)
    cleaned = re.sub(r'(?i)^force\s+sell\s+', '', raw_arg).strip().upper()
    return cleaned if cleaned else None


def _pick_from_menu(holdings: list) -> str:
    """Display a numbered menu and return the chosen ticker."""
    print("\nCurrent portfolio positions:")
    for i, h in enumerate(holdings, 1):
        ticker = h["ticker"]
        shares = h.get("shares", "?")
        buy_price = h.get("buy_price", 0)
        print(f"  {i}. {ticker:6s}  {shares} shares @ ${buy_price:.2f}")

    while True:
        try:
            raw = input(f"\nEnter number (1–{len(holdings)}) to force sell, or 'q' to quit: ").strip()
            if raw.lower() == 'q':
                print("Aborted.")
                sys.exit(0)
            idx = int(raw) - 1
            if 0 <= idx < len(holdings):
                return holdings[idx]["ticker"]
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"  Invalid — enter a number between 1 and {len(holdings)}.")


def _place_sell(ib: IB, supabase, position: dict) -> bool:
    """
    Place an immediate market sell order for the given position dict.
    Archives in trade_history and removes from portfolio_positions on success.
    Returns True on success.
    """
    ticker    = position["ticker"]
    shares    = int(position.get("shares", 0))
    buy_price = float(position.get("buy_price", 0))
    buy_date_raw = position.get("buy_date", "")
    buy_reason   = position.get("buy_reason", "manual")

    if shares <= 0:
        print(f"  ✗ Position for {ticker} shows 0 shares — nothing to sell.")
        return False

    print(f"\n  → Qualifying contract for {ticker}...")
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    # Get current price for P&L reporting
    from execution_agent import fetch_ibkr_delayed_price
    current_price, _ = fetch_ibkr_delayed_price(ib, contract)
    if current_price <= 0:
        current_price = buy_price   # fallback for P&L display only

    print(f"  → Placing MARKET SELL for {shares} shares of {ticker}...")
    order = MarketOrder("SELL", shares)
    order.tif     = "DAY"
    order.account = next(
        (a for a in ib.managedAccounts() if not a.startswith("DU")),
        ib.managedAccounts()[0] if ib.managedAccounts() else ""
    )
    trade = ib.placeOrder(contract, order)

    # Wait up to 30s for fill
    for _ in range(30):
        ib.sleep(1)
        if trade.orderStatus.status == "Filled":
            break
        if trade.orderStatus.status in ("Cancelled", "Inactive"):
            break

    actual_shares = int(trade.orderStatus.filled)
    fill_price    = round(trade.orderStatus.avgFillPrice, 2) if trade.orderStatus.avgFillPrice else current_price

    if actual_shares == 0:
        msgs = [e.message for e in trade.log if getattr(e, "message", "")]
        print(f"  ✗ Order not filled. Reason: {' | '.join(msgs) or 'unknown'}")
        return False

    # P&L
    profit_loss    = round((fill_price - buy_price) * actual_shares, 2)
    percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2) if buy_price > 0 else 0.0

    print(f"  ✓ Sold {actual_shares} shares of {ticker} at ${fill_price:.2f}")
    print(f"    P&L: ${profit_loss:+,.2f}  ({percent_return:+.1f}%)")

    # ── Archive in trade_history ──────────────────────────────────────────────
    try:
        buy_date = datetime.datetime.fromisoformat(buy_date_raw.replace("Z", "+00:00"))
    except Exception:
        buy_date = datetime.datetime.now(datetime.timezone.utc)

    trade_record = {
        "ticker":         ticker,
        "shares":         actual_shares,
        "buy_price":      buy_price,
        "buy_date":       buy_date.isoformat(),
        "buy_reason":     buy_reason,
        "sell_price":     fill_price,
        "sell_date":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sell_reason":    "force_sell",
        "profit_loss":    profit_loss,
        "percent_return": percent_return,
    }
    supabase.table("trade_history").insert(trade_record).execute()

    # ── Remove from portfolio_positions ──────────────────────────────────────
    supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()
    print(f"  ✓ Archived trade and removed {ticker} from portfolio_positions.")

    # ── Telegram ─────────────────────────────────────────────────────────────
    _notify(
        f"🔴 *FORCE SELL executed*\n"
        f"Ticker: `{ticker}`\n"
        f"Shares: {actual_shares} @ ${fill_price:.2f}\n"
        f"P&L: ${profit_loss:+,.2f} ({percent_return:+.1f}%)\n"
        f"Reason: Manual force sell\n"
        f"_The bot will buy the next highest-scored breakout at the next opportunity._"
    )
    return True


def main():
    # ── Parse CLI argument ────────────────────────────────────────────────────
    raw_arg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None

    print("=" * 56)
    print("  Force Sell — Emergency Portfolio Liquidation")
    print("=" * 56)

    # ── Validate env ─────────────────────────────────────────────────────────
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    # ── Connect to Supabase ───────────────────────────────────────────────────
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    holdings = _get_portfolio(supabase)

    if not holdings:
        print("\n  Portfolio is empty — nothing to sell.")
        sys.exit(0)

    # ── Resolve ticker ────────────────────────────────────────────────────────
    requested = _resolve_ticker(raw_arg, holdings)
    held_tickers = {h["ticker"].upper() for h in holdings}

    if requested and requested in held_tickers:
        position = next(h for h in holdings if h["ticker"].upper() == requested)
    elif requested and requested not in held_tickers:
        print(f"\n  '{requested}' is not in your portfolio.")
        position_dict = {h["ticker"].upper(): h for h in holdings}
        print(f"  Holdings: {', '.join(sorted(held_tickers))}")
        chosen = _pick_from_menu(holdings)
        position = next(h for h in holdings if h["ticker"].upper() == chosen.upper())
    else:
        # No argument given — show menu
        chosen = _pick_from_menu(holdings)
        position = next(h for h in holdings if h["ticker"].upper() == chosen.upper())

    # ── Confirm ───────────────────────────────────────────────────────────────
    ticker = position["ticker"]
    shares = position.get("shares", "?")
    print(f"\n  Ready to FORCE SELL: {shares} shares of {ticker}")
    confirm = input("  Type 'yes' to confirm, anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("  Aborted.")
        sys.exit(0)

    # ── Connect to IB Gateway ─────────────────────────────────────────────────
    print(f"\n  Connecting to IB Gateway at {IB_HOST}:{IB_PORT}...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"  ✗ Could not connect to IB Gateway: {e}")
        sys.exit(1)

    # ── Execute ───────────────────────────────────────────────────────────────
    try:
        success = _place_sell(ib, supabase, position)
    finally:
        ib.disconnect()

    if success:
        print(f"\n  ✅ Force sell complete. The bot will fill the freed slot at the next opportunity.")
    else:
        print(f"\n  ✗ Force sell failed. Check the IB Gateway and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
