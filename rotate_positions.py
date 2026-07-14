#!/usr/bin/env python3
"""
rotate_positions.py — Sell EGP + WSFS, then buy RSI + NBIX.

Execution order:
  1. Cancel any existing GTC trailing stop sell orders for EGP and WSFS
  2. Place DAY marketable limit SELL orders for EGP and WSFS
  3. Wait up to 60s for both sells to fill
  4. Confirm proceeds and compute buy sizes from available cash
  5. Place marketable limit BUY orders for RSI and NBIX
  6. Place GTC trailing stop for each new position
  7. Update Supabase: archive sells → portfolio_positions, log buys

Run inside the execution-agent container:
    docker exec -it execution-agent python3 /app/rotate_positions.py

What "marketable limit" means:
  SELL: limit price = current_bid - small_buffer  (fills immediately, avoids slippage below)
  BUY:  limit price = current_ask + small_buffer  (fills immediately, caps overpay)
"""

import os
import sys
import datetime
import time as _time
from zoneinfo import ZoneInfo

# ── Load .env if running locally ──────────────────────────────────────────────
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from ib_insync import IB, Stock, Order, LimitOrder
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
IB_HOST       = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT       = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 0.07))
CLIENT_ID     = 79   # unique — does not clash with agent (1) or force_sell (76)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS  = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")

# ── Orders to execute ─────────────────────────────────────────────────────────
SELLS = [
    {"ticker": "WSFS", "shares": 311,  "buy_price": 77.65,  "buy_date": "2026-07-10",
     "limit_price": 77.65},   # marketable — at cost, still profitable
    {"ticker": "EGP",  "shares": 119,  "buy_price": 210.47, "buy_date": "2026-07-09",
     "limit_price": 210.50},  # $0.03 above cost, highly marketable at open
]

BUYS = [
    {"ticker": "RSI",  "trigger_close": 34.18,  "limit_offset": 0.17,   # limit = $34.35
     "volume_surge": 1.30},
    {"ticker": "NBIX", "trigger_close": 174.10, "limit_offset": 0.65,   # limit = $174.75
     "volume_surge": 1.27},
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def notify(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    try:
        import requests
        for cid in TELEGRAM_CHAT_IDS:
            cid = cid.strip()
            if cid:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
                    timeout=8,
                )
    except Exception as e:
        print(f"  ⚠ Telegram error: {e}")


def get_account(ib: IB) -> str:
    accounts = ib.managedAccounts()
    live = [a for a in accounts if not a.startswith("DU")]
    return live[0] if live else (accounts[0] if accounts else "")


def get_ticker_snapshot(ib: IB, contract) -> tuple[float, float]:
    """Return (bid, ask) for a contract. Falls back to last price if no quote."""
    ib.reqMktData(contract, "", False, False)
    ib.sleep(2)
    ticker = ib.ticker(contract)
    bid = ticker.bid if ticker.bid and ticker.bid > 0 else ticker.last or 0
    ask = ticker.ask if ticker.ask and ticker.ask > 0 else ticker.last or 0
    ib.cancelMktData(contract)
    return round(bid, 2), round(ask, 2)


def cancel_existing_sells(ib: IB, ticker: str) -> int:
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
        print(f"   Cancelled {cancelled} existing SELL order(s) for {ticker}")
    return cancelled


def place_trailing_stop(ib: IB, contract, shares: int, account: str) -> str:
    from ib_insync import Order
    group = f"TS_{contract.symbol}_{int(_time.time())}"
    stop = Order()
    stop.action           = "SELL"
    stop.orderType        = "TRAIL"
    stop.totalQuantity    = shares
    stop.trailingPercent  = round(STOP_LOSS_PCT * 100, 2)
    stop.tif              = "GTC"
    stop.account          = account
    ib.placeOrder(contract, stop)
    print(f"   🛡  Trailing stop placed: {STOP_LOSS_PCT*100:.0f}% trail GTC")
    return group


def wait_for_fill(ib: IB, trade, label: str, timeout: int = 60) -> bool:
    """Wait up to `timeout` seconds for a trade to fill. Returns True on fill."""
    for i in range(timeout):
        ib.sleep(1)
        status = trade.orderStatus.status
        if status == "Filled":
            return True
        if status in ("Cancelled", "Inactive"):
            print(f"   ✗ {label} order {status} after {i+1}s.")
            return False
        if i > 0 and i % 10 == 0:
            filled = trade.orderStatus.filled
            remain = trade.orderStatus.remaining
            print(f"   … {label}: {filled} filled, {remain} remaining ({i}s elapsed)")
    print(f"   ✗ {label}: timed out after {timeout}s. Status: {trade.orderStatus.status}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  ROTATION: Sell EGP + WSFS  →  Buy RSI + NBIX")
    print("=" * 60)

    # ── ONE-TIME DATE GUARD ───────────────────────────────────────────────────
    # This script is a single-use rotation for 2026-07-14 ONLY.
    # It will refuse to run on any other date, even if called manually.
    ALLOWED_DATE = "2026-07-14"
    today_et = datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    if today_et != ALLOWED_DATE:
        print(f"\n⛔  ABORTED — This one-time rotation is only valid on {ALLOWED_DATE}.")
        print(f"    Today is {today_et}. Nothing was traded.")
        sys.exit(0)
    print(f"✅  Date check passed ({today_et}). Proceeding with rotation.\n")
    # ── END DATE GUARD ────────────────────────────────────────────────────────


    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    tz       = ZoneInfo("America/New_York")

    # ── Connect to IB Gateway ─────────────────────────────────────────────────
    print(f"\nConnecting to IB Gateway at {IB_HOST}:{IB_PORT}...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
    except Exception as e:
        print(f"✗ Could not connect to IB Gateway: {e}")
        sys.exit(1)

    account = get_account(ib)
    print(f"✅ Connected. Account: {account}\n")

    # ── PHASE 1: SELL EGP and WSFS ────────────────────────────────────────────
    print("─" * 60)
    print("PHASE 1 — Selling EGP and WSFS")
    print("─" * 60)

    sell_proceeds = 0.0
    sell_results  = []

    for s in SELLS:
        ticker = s["ticker"]
        shares = s["shares"]

        print(f"\n[{ticker}] Preparing sell order ({shares} shares)...")

        # Cancel any existing GTC trailing stop for this ticker
        cancel_existing_sells(ib, ticker)

        contract = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(contract)

        # Get live bid for a tighter marketable limit
        bid, ask = get_ticker_snapshot(ib, contract)
        if bid > 0:
            limit_price = round(max(bid, s["limit_price"]), 2)
            print(f"   Live bid: ${bid:.2f}  →  Using limit: ${limit_price:.2f}")
        else:
            limit_price = s["limit_price"]
            print(f"   No live quote — using preset limit: ${limit_price:.2f}")

        order = LimitOrder("SELL", shares, limit_price)
        order.tif     = "DAY"
        order.account = account

        trade = ib.placeOrder(contract, order)
        print(f"   ✅ SELL order placed: {shares} × {ticker} @ LIMIT ${limit_price:.2f}")

        filled = wait_for_fill(ib, trade, f"SELL {ticker}", timeout=90)

        if filled:
            fill_px  = round(trade.orderStatus.avgFillPrice, 2)
            filled_q = int(trade.orderStatus.filled)
            proceeds = round(fill_px * filled_q, 2)
            sell_proceeds += proceeds

            pnl  = round((fill_px - s["buy_price"]) * filled_q, 2)
            pct  = round((fill_px / s["buy_price"] - 1) * 100, 2)
            print(f"   ✅ FILLED: {filled_q} shares @ ${fill_px:.2f}  |  Proceeds: ${proceeds:,.2f}  |  P&L: ${pnl:+,.2f} ({pct:+.2f}%)")

            # Archive in trade_history
            supabase.table("trade_history").insert({
                "ticker":         ticker,
                "shares":         filled_q,
                "buy_price":      s["buy_price"],
                "buy_date":       s["buy_date"],
                "buy_reason":     "CANSLIM Breakout [daily_triggers]",
                "sell_price":     fill_px,
                "sell_date":      datetime.datetime.now(tz).isoformat(),
                "sell_reason":    "manual_rotation",
                "profit_loss":    pnl,
                "percent_return": pct,
            }).execute()

            # Remove from portfolio_positions
            supabase.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            print(f"   ✅ Archived to trade_history, removed from portfolio.")

            sell_results.append({"ticker": ticker, "shares": filled_q, "fill_price": fill_px, "pnl": pnl, "pct": pct})
            notify(
                f"🔴 *SOLD {ticker}*\n"
                f"{filled_q} shares @ ${fill_px:.2f}\n"
                f"P&L: ${pnl:+,.2f} ({pct:+.2f}%)\n"
                f"_Rotation: buying RSI + NBIX next_"
            )
        else:
            print(f"   ✗ SELL for {ticker} did NOT fill. Aborting rotation to avoid unbalanced state.")
            ib.disconnect()
            sys.exit(1)

    print(f"\nTotal sell proceeds: ${sell_proceeds:,.2f}")

    # ── PHASE 2: BUY RSI and NBIX ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("PHASE 2 — Buying RSI and NBIX")
    print("─" * 60)

    # Split proceeds equally between 2 positions
    position_size = sell_proceeds / len(BUYS)
    print(f"Position size per buy: ${position_size:,.2f}\n")

    buy_results = []
    for b in BUYS:
        ticker = b["ticker"]

        contract = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(contract)

        # Get live ask for tighter marketable limit
        bid, ask = get_ticker_snapshot(ib, contract)
        if ask > 0:
            limit_price = round(ask + 0.05, 2)   # $0.05 above ask — highly marketable
            print(f"[{ticker}] Live ask: ${ask:.2f}  →  Limit: ${limit_price:.2f}")
        else:
            limit_price = round(b["trigger_close"] + b["limit_offset"], 2)
            print(f"[{ticker}] No live quote — preset limit: ${limit_price:.2f}")

        shares = int(position_size / limit_price)
        if shares <= 0:
            print(f"   ✗ Cannot compute share count for {ticker}. Skipping.")
            continue

        order = LimitOrder("BUY", shares, limit_price)
        order.tif     = "DAY"
        order.account = account

        trade = ib.placeOrder(contract, order)
        print(f"   ✅ BUY order placed: {shares} × {ticker} @ LIMIT ${limit_price:.2f}")

        filled = wait_for_fill(ib, trade, f"BUY {ticker}", timeout=90)

        if filled:
            fill_px  = round(trade.orderStatus.avgFillPrice, 2)
            filled_q = int(trade.orderStatus.filled)
            stop_px  = round(fill_px * (1 - STOP_LOSS_PCT), 2)
            print(f"   ✅ FILLED: {filled_q} shares @ ${fill_px:.2f}  |  Stop: ${stop_px:.2f}")

            # Write to portfolio_positions
            supabase.table("portfolio_positions").insert({
                "ticker":     ticker,
                "shares":     filled_q,
                "buy_price":  fill_px,
                "stop_loss":  stop_px,
                "buy_reason": "CANSLIM Breakout [daily_triggers] — manual rotation",
                "buy_source": "daily_triggers",
                "hwm_date":   datetime.datetime.now(tz).date().isoformat(),
                "oca_group":  None,
            }).execute()

            # Place trailing stop
            oca = place_trailing_stop(ib, contract, filled_q, account)
            supabase.table("portfolio_positions").update({"oca_group": oca}).eq("ticker", ticker).execute()

            buy_results.append({"ticker": ticker, "shares": filled_q, "fill_price": fill_px, "stop": stop_px})
            notify(
                f"🟢 *BOUGHT {ticker}*\n"
                f"{filled_q} shares @ ${fill_px:.2f}\n"
                f"Stop: ${stop_px:.2f} (7% trail)\n"
                f"_Rotation from EGP/WSFS_"
            )
        else:
            print(f"   ✗ BUY for {ticker} did NOT fill within 90s.")
            print(f"      → Check IBKR TWS / TWS app — the order may still be pending.")
            notify(
                f"⚠️ *BUY {ticker} NOT FILLED*\n"
                f"Limit order for {shares} shares @ ${limit_price:.2f} did not fill within 90s.\n"
                f"Check IBKR TWS. Sells already completed."
            )

    # ── Summary ───────────────────────────────────────────────────────────────
    ib.disconnect()

    print("\n" + "=" * 60)
    print("ROTATION COMPLETE — SUMMARY")
    print("=" * 60)
    print("\nSOLD:")
    for r in sell_results:
        print(f"  {r['ticker']:6s}  {r['shares']} shares @ ${r['fill_price']:.2f}  P&L: ${r['pnl']:+,.2f} ({r['pct']:+.2f}%)")
    print("\nBOUGHT:")
    for r in buy_results:
        print(f"  {r['ticker']:6s}  {r['shares']} shares @ ${r['fill_price']:.2f}  Trail stop: ${r['stop']:.2f}")

    print(f"\nTotal sell proceeds: ${sell_proceeds:,.2f}")
    if buy_results:
        total_deployed = sum(r["shares"] * r["fill_price"] for r in buy_results)
        print(f"Total deployed:      ${total_deployed:,.2f}")
        print(f"Cash remainder:      ${sell_proceeds - total_deployed:,.2f}")

    print("\n✅ Portfolio updated in Supabase. Trailing stops active in IBKR.")


if __name__ == "__main__":
    main()
