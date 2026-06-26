"""
force_buy.py — One-off manual buy trigger (bypasses 9:30 AM time gate).

Run this ON THE SERVER (192.168.1.50) when you want to manually execute the
buy logic outside the normal market-open window:

    ssh root@192.168.1.50
    cd /home/dietpi/docker/ai-trading-bot
    docker exec -it execution-agent python force_buy.py

All normal buy gates still apply:
  - Trigger must be within TRIGGER_LOOKBACK_DAYS
  - Not already held
  - Not in cooling-off period
  - Cash >= MIN_POSITION_SIZE
  - Price within MAX_PIVOT_EXTENSION of pivot (O'Neil buy zone)
"""

import os
import sys
import datetime
from zoneinfo import ZoneInfo
from supabase import create_client
from ib_insync import IB, Stock, MarketOrder
from execution_agent import place_oca_bracket
from telegram_notifier import TelegramNotifier

# ── Load .env if present (for local runs) ─────────────────────────────────────
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

# ── Config ─────────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_KEY         = os.getenv("SUPABASE_KEY")
FMP_API_KEY          = os.getenv("FMP_API_KEY")
IB_GATEWAY_HOST      = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_GATEWAY_PORT      = int(os.getenv("IB_GATEWAY_PORT", 4004))
MAX_POSITIONS        = int(os.getenv("MAX_POSITIONS", 4))
MIN_POSITION_SIZE    = float(os.getenv("MIN_POSITION_SIZE", 5000.0))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT", 0.07))
PROFIT_TARGET_PCT    = float(os.getenv("PROFIT_TARGET_PCT", 0.25))
COOLING_OFF_DAYS     = int(os.getenv("COOLING_OFF_DAYS", 3))
TRIGGER_LOOKBACK_DAYS = int(os.getenv("TRIGGER_LOOKBACK_DAYS", 3))
MAX_PIVOT_EXTENSION  = float(os.getenv("MAX_PIVOT_EXTENSION", 0.05))

notifier = TelegramNotifier(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
)

def get_live_price(ticker: str) -> float:
    import requests
    try:
        r = requests.get(
            f"https://financialmodelingprep.com/api/v3/quote/{ticker}",
            params={"apikey": FMP_API_KEY}, timeout=10
        )
        data = r.json()
        if data and isinstance(data, list):
            return float(data[0].get("price", 0))
    except Exception as e:
        print(f"   Warning: could not get live price for {ticker}: {e}")
    return 0.0

def get_available_cash(ib: IB) -> float:
    try:
        account_values = ib.accountValues()
        for av in account_values:
            if av.tag == "AvailableFunds" and av.currency == "USD":
                return float(av.value)
    except Exception as e:
        print(f"Warning: could not fetch cash balance: {e}")
    return 0.0

def main():
    print("=" * 55)
    print("   FORCE BUY — Manual buy trigger (no time gate)")
    print("=" * 55)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    tz = ZoneInfo("America/New_York")
    today = datetime.datetime.now(tz).date()
    lookback_date = (today - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).isoformat()
    cooloff_date  = (today - datetime.timedelta(days=COOLING_OFF_DAYS)).isoformat()

    # Fetch state
    positions  = client.table("portfolio_positions").select("*").execute().data or []
    held       = [p["ticker"] for p in positions]
    stock_count = sum(1 for p in positions)
    free_slots = MAX_POSITIONS - stock_count

    recent_sells = client.table("trade_history").select("ticker,sell_date") \
                         .gte("sell_date", cooloff_date).execute().data or []
    cooled = [r["ticker"] for r in recent_sells]

    triggers = client.table("daily_triggers").select("*") \
                     .gte("triggered_at", lookback_date) \
                     .order("triggered_at", desc=True).execute().data or []

    print(f"\nDate         : {today}")
    print(f"Free slots   : {free_slots}/{MAX_POSITIONS}")
    print(f"Held         : {held or '(none)'}")
    print(f"Cooling-off  : {cooled or '(none)'}")
    print(f"Triggers ({len(triggers)})  : {[t['ticker'] for t in triggers]}")

    if free_slots == 0:
        print("\n⛔ Portfolio is fully invested with stocks. No buys possible.")
        return

    if not triggers:
        print(f"\n⛔ No triggers in last {TRIGGER_LOOKBACK_DAYS} days. Nothing to buy.")
        return

    # Connect to IBKR
    print(f"\nConnecting to IB Gateway at {IB_GATEWAY_HOST}:{IB_GATEWAY_PORT}...")
    ib = IB()
    try:
        ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=10)
        print("✅ Connected to IBKR Gateway.")
    except Exception as e:
        print(f"❌ Failed to connect to IBKR Gateway: {e}")
        sys.exit(1)

    if triggers:
        new_trigger_count = sum(1 for t in triggers if t["ticker"] not in held)
            # Refresh held after ETF sell
            positions  = client.table("portfolio_positions").select("*").execute().data or []
            held       = [p["ticker"] for p in positions]

    available_cash = get_available_cash(ib)
    print(f"Available cash: ${available_cash:,.2f}")

    slot_count = free_slots
    position_size = available_cash / slot_count if slot_count > 0 else 0

    bought = 0
    for trigger in triggers:
        if bought >= free_slots:
            break

        ticker = trigger["ticker"]
        print(f"\n--- Evaluating {ticker} (triggered {trigger['triggered_at']}) ---")

        if ticker in held:
            print(f"   Skip: already held.")
            continue
        if ticker in cooled:
            print(f"   Skip: in cooling-off period.")
            continue
        if position_size < MIN_POSITION_SIZE:
            print(f"   Skip: position size ${position_size:,.0f} below minimum ${MIN_POSITION_SIZE:,.0f}.")
            break

        current_price = get_live_price(ticker)
        if current_price <= 0:
            current_price = float(trigger["close_price"])

        pivot_price = float(trigger["close_price"])
        extension_pct = (current_price - pivot_price) / pivot_price if pivot_price > 0 else 0
        if extension_pct > MAX_PIVOT_EXTENSION:
            print(f"   ⛔ {ticker} is {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} — extended. Skip.")
            continue
        print(f"   ✅ Within buy zone: {extension_pct*100:.1f}% above pivot ${pivot_price:.2f}")

        shares = int(position_size / current_price)
        if shares <= 0:
            print(f"   Skip: price ${current_price:.2f} too high for position size ${position_size:,.0f}.")
            continue

        stop_loss     = round(current_price * (1 - STOP_LOSS_PCT), 2)
        profit_target = round(current_price * (1 + PROFIT_TARGET_PCT), 2)
        buy_reason    = f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger.get('volume_surge', 'N/A')}x"

        confirm = input(f"\n   BUY {shares} shares of {ticker} @ ~${current_price:.2f} "
                        f"(stop: ${stop_loss}, target: ${profit_target})? [y/N] ").strip().lower()
        if confirm != "y":
            print("   Skipped by user.")
            continue

        try:
            contract = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(contract)
            order = MarketOrder("BUY", shares)
            trade = ib.placeOrder(contract, order)

            # Wait for fill — IBKR sometimes shows 'Cancelled' briefly when
            # it replaces an order due to TIF preset change (Error 10349).
            # Verify by checking the actual portfolio instead of trade status.
            ib.sleep(5)

            # Check if the position now exists in IBKR portfolio
            ib_positions = {p.contract.symbol: p for p in ib.portfolio()}
            if ticker not in ib_positions:
                ib.sleep(3)  # one more wait
                ib_positions = {p.contract.symbol: p for p in ib.portfolio()}

            if ticker in ib_positions:
                ib_pos = ib_positions[ticker]
                fill_price = round(ib_pos.averageCost, 2)
                actual_shares = int(ib_pos.position)

                client.table("portfolio_positions").insert({
                    "ticker":          ticker,
                    "shares":          actual_shares,
                    "buy_price":       fill_price,
                    "high_water_mark": fill_price,
                    "stop_loss":       round(fill_price * (1 - STOP_LOSS_PCT), 2),
                    "profit_target":   round(fill_price * (1 + PROFIT_TARGET_PCT), 2),
                    "buy_reason":      buy_reason,
                    "buy_source":      "daily_triggers",
                    "is_power_hold":   False,
                    "oca_group":       None,  # will be updated after bracket placement below
                }).execute()

                # Place OCA bracket immediately — trailing stop + limit sell.
                # Store the group name so execution_agent self-healing won't
                # double-place on the next monitor cycle.
                try:
                    _oca = place_oca_bracket(ib, contract, actual_shares, fill_price,
                                             PROFIT_TARGET_PCT, STOP_LOSS_PCT,
                                             parent_order_id=trade.order.orderId)
                    client.table("portfolio_positions").update(
                        {"oca_group": _oca}
                    ).eq("ticker", ticker).execute()
                except Exception as _oca_err:
                    print(f"   ⚠️ OCA bracket placement failed: {_oca_err} — self-healing will re-place.")

                actual_stop   = round(fill_price * (1 - STOP_LOSS_PCT), 2)
                actual_target = round(fill_price * (1 + PROFIT_TARGET_PCT), 2)
                print(f"   ✅ Confirmed fill: {actual_shares} shares of {ticker} @ ${fill_price:.2f}")
                print(f"   Stop: ${actual_stop}  |  Target: ${actual_target}")
                notifier.notify_buy(
                    ticker=ticker, shares=actual_shares, fill_price=fill_price,
                    stop_loss=actual_stop, profit_target=actual_target,
                    volume_surge=float(trigger.get("volume_surge", 0)),
                    pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                    slot_used=len(held) + bought + 1, max_slots=MAX_POSITIONS
                )
                bought += 1
                held.append(ticker)
            else:
                print(f"   ⚠️ {ticker}: order placed but not detected in IBKR portfolio after 8s.")
                print(f"      The execution-agent's reconcile_with_ibkr() will sync it on the next cycle.")

        except Exception as e:
            print(f"   ❌ Order failed for {ticker}: {e}")

    ib.disconnect()
    print(f"\nDone. {bought} position(s) opened.")

if __name__ == "__main__":
    main()

