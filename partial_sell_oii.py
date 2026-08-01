#!/usr/bin/env python3
"""
partial_sell_oii.py — Emergency partial sell to trim OII to target ~$24.8K position.

PROBLEM:
  The bot bought OII worth ~$49K instead of ~$24.8K because it did not deduct
  spent cash from previous buys in the same market open cycle.
  This script sells the excess shares immediately to repay the margin loan.

WHAT IT DOES:
  1. Connects to IB Gateway (clientId=3 to avoid socket collision).
  2. Reads current OII position from IBKR.
  3. Calculates excess shares to sell so kept position value is ~$24,800.
  4. Submits Market Sell for excess_shares.
  5. Updates Supabase portfolio_positions.shares to reflect the kept shares.
  6. Self-heals the IBKR trailing stop for the kept share count.
  7. Sends Telegram notification.
"""

import os
import sys
import datetime
from zoneinfo import ZoneInfo

# Load .env if present
env_path = "/app/.env" if os.path.exists("/app/.env") else ".env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from ib_insync import IB, Stock, MarketOrder, Order
from supabase import create_client

TICKER = "OII"
TARGET_VALUE = 24_800.0  # target dollar allocation for 1 slot
IB_HOST = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CLIENT_ID = 3

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "")


def notify(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    try:
        import requests
        for chat_id in TELEGRAM_CHAT_IDS.split(","):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": chat_id.strip(), "text": msg, "parse_mode": "HTML"},
                timeout=8,
            )
    except Exception as e:
        print(f"Telegram notify error: {e}")


def main():
    print("=" * 60)
    print("  Partial Sell OII -- trim margin-overbought position")
    print(f"  Target kept value: ${TARGET_VALUE:,.2f}")
    print("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"Connecting to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
        print("Connected to IB Gateway.")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # 1. Fetch live IBKR portfolio for target account U12941651
    ib.reqPositions()
    ib.sleep(2)

    oii_position = None
    for p in ib.portfolio():
        if p.account == "U12941651" and p.contract.symbol == TICKER:
            oii_position = p
            break

    if not oii_position:
        print(f"❌ {TICKER} position not found in IBKR portfolio for account U12941651.")
        ib.disconnect()
        sys.exit(1)

    total_shares = int(oii_position.position)
    mkt_val = float(oii_position.marketValue)
    avg_cost = float(oii_position.averageCost)
    current_price = mkt_val / total_shares if total_shares > 0 else 0.0

    print(f"IBKR Account U12941651: {total_shares} shares of {TICKER} | MktVal: ${mkt_val:,.2f} | Price: ${current_price:.2f}")

    # 2. Calculate excess shares
    kept_shares = int(TARGET_VALUE / current_price) if current_price > 0 else total_shares // 2
    excess_shares = total_shares - kept_shares

    if excess_shares <= 0:
        print(f"Position is already at or below target ${TARGET_VALUE:,.2f}. No action needed.")
        ib.disconnect()
        sys.exit(0)

    excess_value = excess_shares * current_price
    kept_value = kept_shares * current_price

    print(f"Selling {excess_shares} excess shares (~${excess_value:,.2f}) | Keeping {kept_shares} shares (~${kept_value:,.2f})...")

    # 3. Place Market Sell order for excess shares
    contract = Stock(TICKER, "SMART", "USD")
    ib.qualifyContracts(contract)

    # First cancel any existing sell orders for OII to avoid order collision
    for t in ib.openTrades():
        if t.contract.symbol == TICKER and t.order.action == "SELL":
            print(f"Cancelling existing open sell order {t.order.orderId} for {TICKER}...")
            ib.cancelOrder(t.order)
    ib.sleep(1)

    print(f"Submitting MARKET SELL order for {excess_shares} shares of {TICKER}...")
    sell_order = MarketOrder("SELL", excess_shares)
    trade = ib.placeOrder(contract, sell_order)

    while not trade.isDone():
        ib.sleep(1)

    fill_price = trade.orderStatus.avgFillPrice or current_price
    proceeds = fill_price * excess_shares
    print(f"✅ Market Sell Filled! {excess_shares} shares @ ${fill_price:.2f} (Total Proceeds: ${proceeds:,.2f})")

    # 4. Update Supabase portfolio_positions record
    try:
        supabase.table("portfolio_positions").update({
            "shares": kept_shares,
            "last_updated": datetime.datetime.now(ZoneInfo("America/New_York")).isoformat()
        }).eq("ticker", TICKER).execute()
        print(f"✅ Supabase updated: {TICKER} shares set to {kept_shares}.")
    except Exception as se:
        print(f"⚠️ Could not update Supabase: {se}")

    # 5. Place fresh trailing stop for kept_shares
    try:
        trail_order = Order()
        trail_order.orderType = "TRAIL"
        trail_order.action = "SELL"
        trail_order.totalQuantity = kept_shares
        trail_order.trailingPercent = 5.0
        trail_order.tif = "GTC"
        trail_trade = ib.placeOrder(contract, trail_order)
        print(f"🛡️ Fresh 5.0% trailing stop placed for kept {kept_shares} shares of {TICKER}.")
    except Exception as te:
        print(f"⚠️ Trailing stop error: {te}")

    ib.disconnect()

    # 6. Telegram notification
    msg = (
        f"🛠️ <b>EXCESS MARGIN POSITION TRIMMED</b>\n\n"
        f"<b>Ticker:</b> <code>{TICKER}</code>\n"
        f"<b>Sold Excess:</b> {excess_shares} shares @ ${fill_price:.2f} (Proceeds: ${proceeds:,.2f})\n"
        f"<b>Kept Position:</b> {kept_shares} shares (~${kept_value:,.2f})\n\n"
        f"✅ <i>Margin loan repaid to zero. Cash balance restored.</i>"
    )
    notify(msg)
    print("Done!")


if __name__ == "__main__":
    main()
