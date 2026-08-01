#!/usr/bin/env python3
"""
partial_sell_trv.py — Emergency partial sell to trim TRV to target ~$25K position.

PROBLEM:
  The bot bought TRV worth ~$60K instead of ~$25K because it used borrowed margin
  cash. We want to sell the excess (~$35K worth) immediately as a marketable limit
  order (fills at/above current ask).

WHAT IT DOES:
  1. Connects to IB Gateway (clientId=2 — execution-agent uses clientId=1, so we
     use a different ID to avoid conflicts. The execution-agent can stay running.)
  2. Reads current TRV position from IBKR (source of truth for share count).
  3. Reads TRV from Supabase to get buy_price for P&L calculation.
  4. Fetches live IBKR delayed ask price for TRV.
  5. Calculates: excess_shares = total_shares - floor(TARGET_VALUE / current_price)
  6. Places a DAY LIMIT SELL for excess_shares at buy_price + $0.01.
     This is a PROFIT-ENSURING limit: the order only fills when TRV is trading
     above cost basis. It sits active all day and fills immediately once TRV
     ticks into profit. We never sell at a loss.
  7. Updates Supabase portfolio_positions.shares to reflect the kept shares.
  8. Sends Telegram notification.

USAGE (run on the server):
  ssh root@dietpi
  cd /home/dietpi/docker/ai-trading-bot
  docker exec -it execution-agent python3 partial_sell_trv.py

Or without stopping the agent (uses clientId=2):
  docker exec execution-agent python3 /app/partial_sell_trv.py

IMPORTANT:
  - The execution-agent does NOT need to be stopped (we use clientId=2).
  - This only sells the EXCESS shares; the kept position stays in Supabase.
  - The existing trailing stop covers ALL shares — after this script runs,
    you may want to manually adjust the trailing stop in TWS to cover only
    the kept share count.
"""

import os
import sys
import datetime
from zoneinfo import ZoneInfo

# -- Load .env if running outside Docker ----------------------------------------
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from ib_insync import IB, Stock, Order
from supabase import create_client

# -- Config ---------------------------------------------------------------------
TICKER        = "TRV"
TARGET_VALUE  = 25_000.0       # dollars we WANT to keep in TRV

IB_HOST       = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT       = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")

# clientId=2 avoids conflicting with the execution-agent (clientId=1).
# The agent can keep running while this script executes.
CLIENT_ID     = 2

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
        print(f"  Warning: Telegram notification failed: {e}")


def _get_ibkr_price(ib: IB, contract) -> tuple:
    """
    Fetch IBKR delayed market data: tries ask first, then last, then close.
    Returns (price, method_label).
    """
    ib.reqMarketDataType(3)   # 3 = delayed
    ticker_obj = ib.reqMktData(contract, "", False, False)
    ib.sleep(4)               # allow delayed snapshot to arrive

    ask   = getattr(ticker_obj, "ask",   None)
    last  = getattr(ticker_obj, "last",  None)
    close = getattr(ticker_obj, "close", None)

    if ask and ask > 0:
        ib.cancelMktData(contract)
        return round(float(ask), 2), "delayed ask"
    if last and last > 0:
        ib.cancelMktData(contract)
        return round(float(last), 2), "delayed last"
    if close and close > 0:
        ib.cancelMktData(contract)
        return round(float(close), 2), "delayed close"

    ib.cancelMktData(contract)
    return 0.0, "unavailable"


def main():
    print("=" * 60)
    print("  Partial Sell TRV -- trim margin-over-bought position")
    print(f"  Target kept value: ${TARGET_VALUE:,.0f}")
    print("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("x SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 1. Get Supabase position (for buy_price / P&L reference)
    sb_res = supabase.table("portfolio_positions").select("*").eq("ticker", TICKER).execute()
    if not sb_res.data:
        print(f"x No position found in Supabase for {TICKER}.")
        sys.exit(1)
    sb_pos    = sb_res.data[0]
    buy_price = float(sb_pos.get("buy_price", 0))
    print(f"\n  Supabase: {sb_pos.get('shares')} shares @ buy price ${buy_price:.2f}")

    # 2. Connect to IBKR
    print(f"\n  Connecting to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=15)
        print("  Connected to IB Gateway.")
    except Exception as e:
        print(f"  x Could not connect: {e}")
        sys.exit(1)

    # 3. Verify IBKR position
    try:
        ib.reqPositions()
        ib.sleep(2)
    except Exception:
        pass

    ibkr_positions = {p.contract.symbol: int(p.position) for p in ib.positions()}
    if TICKER not in ibkr_positions:
        print(f"\n  x {TICKER} not found in IBKR positions.")
        print(f"     Found: {list(ibkr_positions.keys())}")
        ib.disconnect()
        sys.exit(1)

    total_shares = ibkr_positions[TICKER]
    print(f"  IBKR: {total_shares} shares held")

    # 4. Get live IBKR price
    contract = Stock(TICKER, "SMART", "USD")
    ib.qualifyContracts(contract)

    live_price, price_method = _get_ibkr_price(ib, contract)
    if live_price <= 0:
        print(f"\n  x Could not get live IBKR price for {TICKER}.")
        print("    Cannot calculate excess shares safely. Aborting.")
        ib.disconnect()
        sys.exit(1)

    print(f"  {TICKER} live price: ${live_price:.2f} ({price_method})")

    # 5. Calculate excess shares
    current_total_value = total_shares * live_price
    shares_to_keep      = int(TARGET_VALUE / live_price)    # floor: never keep more than target
    shares_to_sell      = total_shares - shares_to_keep
    excess_value        = shares_to_sell * live_price
    kept_value          = shares_to_keep * live_price

    print(f"\n  Current position : {total_shares} shares x ${live_price:.2f} = ${current_total_value:,.2f}")
    print(f"  Target keep      : {shares_to_keep} shares x ${live_price:.2f} = ${kept_value:,.2f}")
    print(f"  --> SELL (excess): {shares_to_sell} shares approximately ${excess_value:,.2f}")

    if shares_to_sell <= 0:
        print(f"\n  No excess shares to sell -- position is already at or below ${TARGET_VALUE:,.0f}.")
        ib.disconnect()
        sys.exit(0)

    # Profit-ensuring limit: sell only at buy_price + $0.01.
    # This guarantees no loss on the excess shares.
    # The order is a DAY limit -- it sits active all session and fills
    # immediately once TRV ticks above the entry cost.
    limit_price = round(buy_price + 0.01, 2)
    print(f"  Buy price (cost basis) : ${buy_price:.2f}")
    print(f"  Limit sell price       : ${limit_price:.2f}  (buy_price + $0.01 -- profit-ensuring)")
    if live_price >= limit_price:
        print(f"  TRV is already above limit (${live_price:.2f} >= ${limit_price:.2f}) -- order will fill immediately.")
    else:
        print(f"  TRV is currently below limit (${live_price:.2f} < ${limit_price:.2f}) -- order will wait to fill.")

    # 6. Confirm
    print(f"  {'─'*50}")
    print(f"  ORDER SUMMARY")
    print(f"  Action : SELL {shares_to_sell} shares of {TICKER}")
    print(f"  Type   : DAY LIMIT (profit-ensuring)")
    print(f"  Price  : ${limit_price:.2f}  (buy_price + $0.01 -- never sells at a loss)")
    print(f"  Keeping: {shares_to_keep} shares approximately ${kept_value:,.2f}")
    print(f"  Note   : Order stays active all day; fills as soon as TRV > ${limit_price:.2f}")
    print(f"  {'─'*50}")
    confirm = input("\n  Type 'yes' to place the order, anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("  Aborted.")
        ib.disconnect()
        sys.exit(0)

    # 7. Get IBKR account
    accounts = ib.managedAccounts()
    acct = next((a for a in accounts if not a.startswith("DU")), accounts[0] if accounts else "")
    print(f"  Using account: {acct}")

    # 8. Place the SELL order
    order = Order()
    order.action        = "SELL"
    order.orderType     = "LMT"
    order.totalQuantity = shares_to_sell
    order.lmtPrice      = limit_price
    order.tif           = "DAY"
    order.account       = acct
    order.transmit      = True

    print(f"\n  --> Placing SELL {shares_to_sell} x {TICKER} @ LMT ${limit_price:.2f}...")
    trade = ib.placeOrder(contract, order)
    print(f"  Order submitted (orderId={trade.order.orderId})")

    # Wait up to 90s for fill
    print("  Waiting for fill (up to 90s)...")
    for i in range(90):
        ib.sleep(1)
        status = trade.orderStatus.status
        if status == "Filled":
            break
        if status in ("Cancelled", "Inactive"):
            msgs = [e.message for e in trade.log if getattr(e, "message", "")]
            print(f"  x Order {status}: {' | '.join(msgs) or 'unknown'}")
            ib.disconnect()
            sys.exit(1)
        if i > 0 and i % 15 == 0:
            print(f"    ... {i}s elapsed | filled={trade.orderStatus.filled} "
                  f"remaining={trade.orderStatus.remaining} status={status}")

    if trade.orderStatus.status != "Filled":
        print(f"\n  WARNING: Order not filled after 90s. Status: {trade.orderStatus.status}")
        print(f"     Filled: {trade.orderStatus.filled} / {shares_to_sell} shares")
        print("     The order remains ACTIVE in IBKR (DAY order).")
        print("     Check TWS -- it will fill when TRV trades at/above your limit.")
        # Don't update Supabase yet since fill is partial/pending
        ib.disconnect()
        sys.exit(0)

    # 9. Record the fill
    fill_price      = round(trade.orderStatus.avgFillPrice, 2)
    filled_shares   = int(trade.orderStatus.filled)
    proceeds        = round(fill_price * filled_shares, 2)
    profit_loss     = round((fill_price - buy_price) * filled_shares, 2)
    pct_return      = round(((fill_price / buy_price) - 1.0) * 100.0, 2) if buy_price > 0 else 0.0
    remaining_shares = total_shares - filled_shares

    print(f"\n  FILLED: {filled_shares} shares @ ${fill_price:.2f}")
    print(f"     Proceeds  : ${proceeds:,.2f}")
    print(f"     P&L       : ${profit_loss:+,.2f} ({pct_return:+.2f}%)")
    print(f"     Remaining : {remaining_shares} shares approximately ${remaining_shares * fill_price:,.2f}")

    # 10. Update Supabase -- reduce share count (keep position row)
    try:
        supabase.table("portfolio_positions").update({
            "shares": remaining_shares,
        }).eq("ticker", TICKER).execute()
        print(f"  Supabase updated: {TICKER} shares --> {remaining_shares}")
    except Exception as e:
        print(f"  WARNING: Supabase update failed: {e}")
        print("      Manually update portfolio_positions.shares to:", remaining_shares)

    # 11. Log to trade_history (partial sell record)
    try:
        supabase.table("trade_history").insert({
            "ticker":         TICKER,
            "shares":         filled_shares,
            "buy_price":      buy_price,
            "buy_date":       sb_pos.get("buy_date", ""),
            "buy_reason":     sb_pos.get("buy_reason", ""),
            "sell_price":     fill_price,
            "sell_date":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sell_reason":    "partial_sell -- margin cash over-buy correction",
            "profit_loss":    profit_loss,
            "percent_return": pct_return,
        }).execute()
        print(f"  Partial sell logged to trade_history.")
    except Exception as e:
        print(f"  WARNING: trade_history insert failed: {e}")

    # 12. Notify
    _notify(
        f"*PARTIAL SELL executed* -- margin correction\n"
        f"Ticker: `{TICKER}`\n"
        f"Sold: {filled_shares} shares @ ${fill_price:.2f}\n"
        f"Proceeds: ${proceeds:,.2f}\n"
        f"P&L on sold portion: ${profit_loss:+,.2f} ({pct_return:+.2f}%)\n"
        f"Remaining: {remaining_shares} shares approx ${remaining_shares * fill_price:,.2f}\n"
        f"Reason: Over-bought with margin cash -- trimmed to ~$25K target"
    )

    ib.disconnect()

    print(f"\n  Done.")
    print(f"  ACTION REQUIRED: The trailing stop in IBKR still covers the original")
    print(f"  {total_shares} shares. Open TWS and adjust it to cover only {remaining_shares} shares,")
    print(f"  or wait for the execution-agent's self-healing to re-place it at the next cycle.")


if __name__ == "__main__":
    main()
