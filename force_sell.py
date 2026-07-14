#!/usr/bin/env python3
"""
force_sell.py — Emergency portfolio liquidation tool.

Usage:
    python force_sell.py AAPL          # sell AAPL directly (with confirmation)
    python force_sell.py               # show portfolio menu, pick by number
    python force_sell.py "Force sell NVDA"   # natural language form

What it does:
  1. Connects to the running IB Gateway (as clientId=1 — required for cash accounts).
  2. Looks up the ticker in portfolio_positions (Supabase).
  3. Cancels any existing GTC trailing stop for that ticker.
  4. Places a MARKETABLE LIMIT SELL order (bid-based, fills immediately).
  5. Archives the trade in trade_history and removes from portfolio_positions.
  6. Sends a Telegram notification.

IMPORTANT — Cash account requirement:
  The sell must use clientId=1 (same session that placed the original buy).
  IBKR rejects sells from a different clientId as "potential short position".
  This means the execution-agent must be stopped before running this script:

      docker compose stop execution-agent
      docker exec -it execution-agent python3 force_sell.py [TICKER]
      docker compose start execution-agent

  Or, using docker run directly (agent can remain stopped):
      docker run --rm --network ai-trading-bot_trading_bridge \
        --env-file .env \
        -v /tmp/force_sell.py:/app/force_sell.py \
        ghcr.io/arnabbiswas1510/ai-trading-bot-execution-agent:latest \
        python3 /app/force_sell.py [TICKER]
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

from ib_insync import IB, Stock, Order
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
IB_HOST      = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT      = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 0.07))

# clientId=1: must match the execution-agent's session so IBKR cash account
# recognises the sell as closing a long (not opening a short).
# The execution-agent must be STOPPED before connecting with clientId=1.
CLIENT_ID    = 1

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
    cleaned = re.sub(r'(?i)^force\s+sell\s+', '', raw_arg).strip().upper()
    return cleaned if cleaned else None


def _pick_from_menu(holdings: list) -> str:
    """Display a numbered menu and return the chosen ticker."""
    print("\nCurrent portfolio positions:")
    for i, h in enumerate(holdings, 1):
        ticker    = h["ticker"]
        shares    = h.get("shares", "?")
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


def _cancel_existing_sells(ib: IB, ticker: str):
    """Cancel any open GTC trailing stop or sell orders for this ticker."""
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
        print(f"  ✓ Cancelled {cancelled} existing SELL/stop order(s) for {ticker}")
    return cancelled


def _place_sell(ib: IB, supabase, position: dict, account: str) -> dict | None:
    """
    Place a marketable limit SELL for the given position.

    Uses IBKR delayed price to set the limit floor (ask - 0.5%), ensuring
    the order fills immediately while never selling below 99.5% of last known price.
    Falls back to cost basis if IBKR price is unavailable.

    Returns a result dict on success, None on failure.
    """
    ticker    = position["ticker"]
    shares    = int(position.get("shares", 0))
    buy_price = float(position.get("buy_price", 0))
    buy_date_raw = position.get("buy_date", "")
    buy_reason   = position.get("buy_reason", "manual")

    if shares <= 0:
        print(f"  ✗ Position for {ticker} shows 0 shares — nothing to sell.")
        return None

    print(f"\n  → Qualifying contract for {ticker}...")
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    # Cancel any GTC trailing stop first (avoids IBKR OCA conflict)
    _cancel_existing_sells(ib, ticker)

    # Get IBKR delayed price for limit floor
    from execution_agent import fetch_ibkr_delayed_price
    ibkr_price, price_method = fetch_ibkr_delayed_price(ib, contract)

    if ibkr_price > 0:
        # Marketable limit: 0.5% below last known IBKR price — fills immediately
        limit_price = round(ibkr_price * 0.995, 2)
        print(f"  📡 {ticker} IBKR price: ${ibkr_price:.2f} ({price_method}) → sell limit: ${limit_price:.2f}")
    else:
        # No IBKR quote — use cost basis as floor (safe, will fill above)
        limit_price = round(buy_price * 0.995, 2)
        print(f"  ⚠️ No IBKR price for {ticker} — using cost-based limit: ${limit_price:.2f}")

    print(f"  → Placing MARKETABLE LIMIT SELL: {shares} × {ticker} @ ${limit_price:.2f}...")

    order = Order()
    order.action        = "SELL"
    order.orderType     = "LMT"
    order.totalQuantity = shares
    order.lmtPrice      = limit_price
    order.tif           = "DAY"
    order.account       = account
    order.transmit      = True

    trade = ib.placeOrder(contract, order)

    # Wait up to 60s for fill
    for i in range(60):
        ib.sleep(1)
        if trade.orderStatus.status == "Filled":
            break
        if trade.orderStatus.status in ("Cancelled", "Inactive"):
            msgs = [e.message for e in trade.log if getattr(e, "message", "")]
            print(f"  ✗ Order {trade.orderStatus.status}: {' | '.join(msgs) or 'unknown'}")
            break
        if i > 0 and i % 15 == 0:
            print(f"    … {i}s: filled={trade.orderStatus.filled}, remaining={trade.orderStatus.remaining}")

    if trade.orderStatus.status != "Filled":
        return None

    actual_shares = int(trade.orderStatus.filled)
    fill_price    = round(trade.orderStatus.avgFillPrice, 2)
    proceeds      = round(fill_price * actual_shares, 2)
    profit_loss   = round((fill_price - buy_price) * actual_shares, 2)
    pct_return    = round(((fill_price / buy_price) - 1.0) * 100.0, 2) if buy_price > 0 else 0.0

    print(f"  ✓ FILLED: {actual_shares} shares @ ${fill_price:.2f} | Proceeds: ${proceeds:,.2f} | P&L: ${profit_loss:+,.2f} ({pct_return:+.2f}%)")

    # Archive in trade_history
    try:
        buy_date = datetime.datetime.fromisoformat(buy_date_raw.replace("Z", "+00:00"))
    except Exception:
        buy_date = datetime.datetime.now(datetime.timezone.utc)

    supabase.table("trade_history").insert({
        "ticker":         ticker,
        "shares":         actual_shares,
        "buy_price":      buy_price,
        "buy_date":       buy_date.isoformat(),
        "buy_reason":     buy_reason,
        "sell_price":     fill_price,
        "sell_date":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sell_reason":    "force_sell",
        "profit_loss":    profit_loss,
        "percent_return": pct_return,
    }).execute()

    supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()
    print(f"  ✓ Archived to trade_history, removed from portfolio_positions.")

    _notify(
        f"🔴 *FORCE SELL executed*\n"
        f"Ticker: `{ticker}`\n"
        f"Shares: {actual_shares} @ ${fill_price:.2f}\n"
        f"P&L: ${profit_loss:+,.2f} ({pct_return:+.2f}%)\n"
        f"Reason: Manual force sell"
    )

    return {
        "ticker": ticker, "shares": actual_shares, "fill_price": fill_price,
        "proceeds": proceeds, "pnl": profit_loss, "pct": pct_return,
    }


def main():
    # ── Parse CLI argument ────────────────────────────────────────────────────
    raw_arg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None

    print("=" * 56)
    print("  Force Sell — Emergency Portfolio Liquidation")
    print("=" * 56)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    holdings = _get_portfolio(supabase)

    if not holdings:
        print("\n  Portfolio is empty — nothing to sell.")
        sys.exit(0)

    # Resolve ticker
    requested    = _resolve_ticker(raw_arg, holdings)
    held_tickers = {h["ticker"].upper() for h in holdings}

    if requested and requested in held_tickers:
        position = next(h for h in holdings if h["ticker"].upper() == requested)
    elif requested and requested not in held_tickers:
        print(f"\n  '{requested}' is not in your portfolio.")
        print(f"  Holdings: {', '.join(sorted(held_tickers))}")
        chosen   = _pick_from_menu(holdings)
        position = next(h for h in holdings if h["ticker"].upper() == chosen.upper())
    else:
        chosen   = _pick_from_menu(holdings)
        position = next(h for h in holdings if h["ticker"].upper() == chosen.upper())

    # Confirm
    ticker = position["ticker"]
    shares = position.get("shares", "?")
    print(f"\n  Ready to FORCE SELL: {shares} shares of {ticker}")
    confirm = input("  Type 'yes' to confirm, anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("  Aborted.")
        sys.exit(0)

    # Connect to IB Gateway
    print(f"\n  Connecting to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})...")
    print("  ⚠️  The execution-agent must be STOPPED before connecting as clientId=1.")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"  ✗ Could not connect: {e}")
        print("  Hint: stop the execution-agent first (docker compose stop execution-agent)")
        sys.exit(1)

    # Subscribe to account + verify positions
    acct = next((a for a in ib.managedAccounts() if not a.startswith("DU")),
                ib.managedAccounts()[0] if ib.managedAccounts() else "")
    ib.reqAccountSummary()
    ib.reqPositions()
    ib.sleep(3)

    ibkr_positions = {p.contract.symbol: int(p.position) for p in ib.positions()}
    if ticker not in ibkr_positions:
        print(f"\n  ✗ {ticker} not found in IBKR positions (found: {list(ibkr_positions.keys())})")
        print("  IBKR and Supabase may be out of sync. Check IBKR TWS manually.")
        ib.disconnect()
        sys.exit(1)

    # Use IBKR share count as source of truth
    actual_ibkr_shares = ibkr_positions[ticker]
    if actual_ibkr_shares != shares:
        print(f"  ⚠️  Share count mismatch: Supabase={shares}, IBKR={actual_ibkr_shares}. Using IBKR count.")
        position = dict(position)
        position["shares"] = actual_ibkr_shares

    # Execute
    try:
        result = _place_sell(ib, supabase, position, acct)
    finally:
        ib.disconnect()

    if result:
        print(f"\n  ✅ Force sell complete. Freed ${result['proceeds']:,.2f}.")
        print("  The execution-agent will fill the freed slot at the next buy opportunity.")
    else:
        print(f"\n  ✗ Force sell failed. Check the IB Gateway and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
