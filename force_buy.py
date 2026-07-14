"""
force_buy.py — One-off manual buy trigger (bypasses 9:30 AM time gate).

Run this ON THE SERVER when you want to manually execute the buy logic
outside the normal market-open window:

    ssh root@dietpi
    docker exec -it execution-agent python3 force_buy.py

Or via docker run (while agent is stopped):
    docker run --rm --network ai-trading-bot_trading_bridge \\
      --env-file /home/dietpi/docker/ai-trading-bot/.env \\
      ghcr.io/arnabbiswas1510/ai-trading-bot-execution-agent:latest \\
      python3 /app/force_buy.py

All normal buy gates still apply:
  - Trigger must be within TRIGGER_LOOKBACK_DAYS
  - Not already held
  - Not in cooling-off period
  - Cash >= MIN_POSITION_SIZE
  - Price within MAX_PIVOT_EXTENSION of pivot (O'Neil buy zone)

Price source: IBKR delayed quotes (ask + $0.10 marketable limit).
Never uses FMP — FMP returns yesterday's close at market open causing bad fills.
"""

import os
import sys
import datetime
from zoneinfo import ZoneInfo
from supabase import create_client
from ib_insync import IB, Stock, Order
from execution_agent import (
    fetch_ibkr_delayed_price,
    place_trailing_stop,
    cancel_ticker_sell_orders,
)
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
SUPABASE_URL          = os.getenv("SUPABASE_URL")
SUPABASE_KEY          = os.getenv("SUPABASE_KEY")
IB_GATEWAY_HOST       = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_GATEWAY_PORT       = int(os.getenv("IB_GATEWAY_PORT", 4000))
MAX_POSITIONS         = int(os.getenv("MAX_POSITIONS", 4))
MIN_POSITION_SIZE     = float(os.getenv("MIN_POSITION_SIZE", 5000.0))
STOP_LOSS_PCT         = float(os.getenv("STOP_LOSS_PCT", 0.07))
COOLING_OFF_DAYS      = int(os.getenv("COOLING_OFF_DAYS", 3))
TRIGGER_LOOKBACK_DAYS = int(os.getenv("TRIGGER_LOOKBACK_DAYS", 3))
MAX_PIVOT_EXTENSION   = float(os.getenv("MAX_PIVOT_EXTENSION", 0.05))

notifier = TelegramNotifier(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
)


def get_ibkr_price(ib: IB, ticker: str) -> float:
    """
    Fetch price via IBKR delayed market data (same as execution_agent buy path).
    Returns the ask price, falling back to last traded price.
    Never uses FMP — FMP returns yesterday's close at market open.
    """
    try:
        contract = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(contract)
        price, method = fetch_ibkr_delayed_price(ib, contract)
        if price > 0:
            print(f"   📡 {ticker} IBKR delayed price: ${price:.2f} ({method})")
            return price
    except Exception as e:
        print(f"   ⚠️ IBKR price fetch failed for {ticker}: {e}")
    return 0.0


def get_available_cash(ib: IB) -> float:
    """Return settled available funds from IBKR."""
    try:
        for av in ib.accountValues():
            if av.tag == "AvailableFunds" and av.currency == "USD":
                return float(av.value)
    except Exception as e:
        print(f"Warning: could not fetch cash balance: {e}")
    return 0.0


def _place_buy(
    ib: IB,
    client,               # Supabase client
    trigger: dict,        # daily_triggers row
    position_size: float, # dollars to deploy
    held_set: set,        # set of currently-held tickers (updated in-place on buy)
    acct: str,            # IBKR account string
    tz,                   # ZoneInfo for local timestamps
    interactive: bool = True,  # ask for confirmation before placing order
) -> dict | None:
    """
    Place a marketable limit BUY order for a trigger.

    Uses IBKR delayed ask + $0.10 as the limit price (highly marketable,
    caps overpay vs a plain market order).

    Returns a result dict on success, None on skip/fail.
    """
    ticker     = trigger["ticker"]
    pivot      = float(trigger.get("close_price", 0))
    buy_reason = f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger.get('volume_surge', 'N/A')}x"

    if ticker in held_set:
        print(f"   Skip {ticker}: already held.")
        return None

    # ── Price ────────────────────────────────────────────────────────────────
    ibkr_price = get_ibkr_price(ib, ticker)
    if ibkr_price <= 0:
        ibkr_price = pivot
        print(f"   ⚠️ No IBKR price for {ticker} — using prev close ${ibkr_price:.2f}")

    # ── Pivot extension check (O'Neil buy zone) ───────────────────────────────
    if pivot > 0:
        extension_pct = (ibkr_price - pivot) / pivot
        if extension_pct > MAX_PIVOT_EXTENSION:
            print(f"   ⛔ {ticker} is {extension_pct*100:.1f}% above pivot ${pivot:.2f} "
                  f"— extended beyond {MAX_PIVOT_EXTENSION*100:.0f}% buy zone. Skipping.")
            return None
        print(f"   ✅ {ticker} within buy zone: {extension_pct*100:.1f}% above pivot ${pivot:.2f}")

    # ── Share count ───────────────────────────────────────────────────────────
    limit_price = round(ibkr_price + 0.10, 2)   # marketable limit: ask + $0.10
    shares = int(position_size / limit_price)
    if shares <= 0:
        print(f"   Skip {ticker}: price ${limit_price:.2f} too high for position size ${position_size:,.0f}.")
        return None

    if position_size < MIN_POSITION_SIZE:
        print(f"   Skip {ticker}: position size ${position_size:,.0f} below minimum ${MIN_POSITION_SIZE:,.0f}.")
        return None

    # ── Confirmation ──────────────────────────────────────────────────────────
    if interactive:
        confirm = input(
            f"\n   BUY {shares} shares of {ticker} @ limit ${limit_price:.2f} "
            f"(~${shares * limit_price:,.0f}, 7% trail stop from fill)? [y/N] "
        ).strip().lower()
        if confirm != "y":
            print("   Skipped by user.")
            return None

    # ── Place order ───────────────────────────────────────────────────────────
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    order = Order()
    order.action        = "BUY"
    order.orderType     = "LMT"
    order.totalQuantity = shares
    order.lmtPrice      = limit_price
    order.tif           = "DAY"
    order.account       = acct
    order.transmit      = True

    trade = ib.placeOrder(contract, order)
    print(f"   ✅ BUY order placed: {shares} × {ticker} @ LIMIT ${limit_price:.2f}")

    # Wait for fill (up to 90s)
    for i in range(90):
        ib.sleep(1)
        if trade.orderStatus.status == "Filled":
            break
        if trade.orderStatus.status in ("Cancelled", "Inactive"):
            msgs = [e.message for e in trade.log if getattr(e, "message", "")]
            print(f"   ✗ BUY {ticker} {trade.orderStatus.status}: {' | '.join(msgs) or 'unknown'}")
            return None
        if i > 0 and i % 15 == 0:
            print(f"    … {i}s: filled={trade.orderStatus.filled}, remaining={trade.orderStatus.remaining}")

    if trade.orderStatus.status != "Filled":
        print(f"   ✗ BUY {ticker}: not filled after 90s. Status: {trade.orderStatus.status}")
        return None

    fill_price    = round(trade.orderStatus.avgFillPrice, 2)
    actual_shares = int(trade.orderStatus.filled)
    stop_loss     = round(fill_price * (1 - STOP_LOSS_PCT), 2)

    print(f"   ✅ FILLED: {actual_shares} shares @ ${fill_price:.2f} | Stop: ${stop_loss:.2f}")

    # ── Supabase ──────────────────────────────────────────────────────────────
    try:
        # Upsert to handle any pre-existing stale record
        existing = client.table("portfolio_positions").select("ticker").eq("ticker", ticker).execute()
        if existing.data:
            client.table("portfolio_positions").delete().eq("ticker", ticker).execute()

        client.table("portfolio_positions").insert({
            "ticker":     ticker,
            "shares":     actual_shares,
            "buy_price":  fill_price,
            "stop_loss":  stop_loss,
            "buy_reason": buy_reason,
            "buy_source": "daily_triggers",
            "hwm_date":   datetime.datetime.now(tz).date().isoformat(),
            "oca_group":  None,
            # ── Entry conviction snapshot (all 5-component scores) ──────────────
            # Mirrors execution_agent.py exactly so manual/rotation buys show
            # the same data in the Open Positions UI as automated buys.
            "entry_quality_score":    trigger.get("quality_score"),
            "entry_ai_rating":        trigger.get("ai_rating"),
            "entry_ai_grade":         trigger.get("ai_grade"),
            "entry_final_score":      trigger.get("final_score"),
            "entry_technical_score":  trigger.get("technical_score"),
            "entry_liquidity_score":  trigger.get("liquidity_score"),
            "entry_rs_score":         trigger.get("rs_score"),
            "entry_sentiment_score":  trigger.get("sentiment_score"),
            "entry_atr_pct":          trigger.get("atr_pct"),
            "entry_est_days_target":  trigger.get("est_days_to_target"),
            "entry_score_rationale":  trigger.get("score_rationale"),
        }).execute()

    except Exception as e:
        print(f"   ⚠️ Supabase insert error for {ticker}: {e}")


    # ── Trailing stop ─────────────────────────────────────────────────────────
    try:
        cancel_ticker_sell_orders(ib, ticker)
        oca = place_trailing_stop(ib, contract, actual_shares, STOP_LOSS_PCT)
        client.table("portfolio_positions").update({"oca_group": oca}).eq("ticker", ticker).execute()
    except Exception as e:
        print(f"   ⚠️ Trailing stop failed for {ticker}: {e} — self-healing will re-place.")

    # ── Notify ────────────────────────────────────────────────────────────────
    notifier.notify_buy(
        ticker=ticker, shares=actual_shares, fill_price=fill_price,
        stop_loss=stop_loss, profit_target=None,
        volume_surge=float(trigger.get("volume_surge", 0)),
        pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
        slot_used=0, max_slots=MAX_POSITIONS,
    )

    held_set.add(ticker)
    return {
        "ticker": ticker, "shares": actual_shares, "fill_price": fill_price,
        "stop": stop_loss,
    }


def main():
    print("=" * 55)
    print("   FORCE BUY — Manual buy trigger (no time gate)")
    print("=" * 55)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    tz     = ZoneInfo("America/New_York")
    today  = datetime.datetime.now(tz).date()

    lookback_date = (today - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).isoformat()
    cooloff_date  = (today - datetime.timedelta(days=COOLING_OFF_DAYS)).isoformat()

    # Fetch state
    positions   = client.table("portfolio_positions").select("*").execute().data or []
    held        = set(p["ticker"] for p in positions)
    stock_count = len(positions)
    free_slots  = MAX_POSITIONS - stock_count

    recent_sells = client.table("trade_history").select("ticker,sell_date") \
                         .gte("sell_date", cooloff_date).execute().data or []
    cooled = set(r["ticker"] for r in recent_sells)

    triggers = client.table("daily_triggers").select("*") \
                     .gte("triggered_at", lookback_date) \
                     .execute().data or []
    triggers.sort(key=lambda x: x.get("final_score") or x.get("quality_score") or 0, reverse=True)

    # Filter out cooled and already held
    eligible = [t for t in triggers if t["ticker"] not in held and t["ticker"] not in cooled]

    print(f"\nDate         : {today}")
    print(f"Free slots   : {free_slots}/{MAX_POSITIONS}")
    print(f"Held         : {sorted(held) or '(none)'}")
    print(f"Cooling-off  : {sorted(cooled) or '(none)'}")
    print(f"Eligible ({len(eligible)}) : {[t['ticker'] for t in eligible]}")

    if free_slots == 0:
        print("\n⛔ Portfolio is fully invested. No buys possible.")
        return

    if not eligible:
        print(f"\n⛔ No eligible triggers in last {TRIGGER_LOOKBACK_DAYS} days.")
        return

    # Connect to IBKR
    print(f"\nConnecting to IB Gateway at {IB_GATEWAY_HOST}:{IB_GATEWAY_PORT}...")
    ib = IB()
    try:
        ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
        print("✅ Connected to IBKR Gateway.")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    acct = next((a for a in ib.managedAccounts() if not a.startswith("DU")),
                ib.managedAccounts()[0] if ib.managedAccounts() else "")

    available_cash = get_available_cash(ib)
    print(f"Available cash: ${available_cash:,.2f}")
    position_size = available_cash / free_slots if free_slots > 0 else 0

    bought = 0
    for trigger in eligible:
        if bought >= free_slots:
            break
        print(f"\n--- Evaluating {trigger['ticker']} (score: {trigger.get('final_score', '?')}) ---")
        result = _place_buy(
            ib=ib, client=client, trigger=trigger,
            position_size=position_size, held_set=held,
            acct=acct, tz=tz, interactive=True,
        )
        if result:
            bought += 1

    ib.disconnect()
    print(f"\nDone. {bought} position(s) opened.")


if __name__ == "__main__":
    main()
