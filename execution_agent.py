import os
import sys
import argparse
import datetime
import time
import requests
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from ib_insync import IB, Stock, MarketOrder
from telegram_notifier import TelegramNotifier

# Load environment variables
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

FMP_API_KEY = os.getenv("FMP_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
IB_GATEWAY_HOST = os.getenv("IB_GATEWAY_HOST", "localhost")
IB_GATEWAY_PORT = int(os.getenv("IB_GATEWAY_PORT", 7497))

# ── Strategy configuration (set in .env) ──────────────────────────────────────
# Maximum concurrent open positions. Each slot gets an equal share of available cash.
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 4))
# Skip a buy if the computed position size falls below this floor (USD).
MIN_POSITION_SIZE = float(os.getenv("MIN_POSITION_SIZE", 5000.0))
# ── Stale position rotation ────────────────────────────────────────────────────
# Days held before a sideways position is considered eligible for rotation.
STALE_HOLD_DAYS = int(os.getenv("STALE_HOLD_DAYS", 15))
# Maximum gain (decimal) that qualifies as "sideways". 0.03 = within 3% of entry.
STALE_HOLD_MAX_GAIN = float(os.getenv("STALE_HOLD_MAX_GAIN", 0.03))

# ── Telegram notifications ─────────────────────────────────────────────────────
notifier = TelegramNotifier(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
)

# Initialize Supabase client
supabase: Client = None

def get_supabase_client() -> Client:
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase

def get_live_price(ticker: str) -> float:
    """Fetch current price of a ticker from FMP."""
    url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={FMP_API_KEY}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0))
    except Exception as e:
        print(f"❌ Error fetching price for {ticker} from FMP: {e}")
    return 0.0

def get_available_cash(ib: IB) -> float:
    """Query account values for settled cash / buying power in USD."""
    try:
        account_values = ib.accountValues()
        for av in account_values:
            if av.tag == "CashBalance" and av.currency == "USD":
                return float(av.value)
        # Fallback to TotalCashValue
        for av in account_values:
            if av.tag == "TotalCashValue" and av.currency == "USD":
                return float(av.value)
    except Exception as e:
        print(f"❌ Error querying cash balance from IBKR: {e}")
    return 0.0

def handle_mock_sell(ticker: str, price: float, reason: str):
    """Executes a mock sale event directly on Supabase, bypassing IBKR."""
    print(f"🧪 Initiating mock sale for {ticker} at price ${price:.2f} (Reason: {reason})...")
    client = get_supabase_client()
    
    # Fetch existing position
    res = client.table("portfolio_positions").select("*").eq("ticker", ticker.upper()).execute()
    if not res.data:
        print(f"❌ No active position found in Supabase for {ticker.upper()}")
        sys.exit(1)
        
    pos = res.data[0]
    shares = int(pos["shares"])
    buy_price = float(pos["buy_price"])
    buy_date = pos["buy_date"]
    buy_reason = pos.get("buy_reason", "Unknown")
    
    # Calculate returns
    sell_price = price
    profit_loss = round((sell_price - buy_price) * shares, 2)
    percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)
    
    # Insert into trade history
    trade_log = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "buy_reason": buy_reason,
        "sell_price": sell_price,
        "sell_reason": reason,
        "profit_loss": profit_loss,
        "percent_return": percent_return
    }
    
    try:
        # Delete from portfolio
        client.table("portfolio_positions").delete().eq("ticker", ticker.upper()).execute()
        # Insert into history
        client.table("trade_history").insert(trade_log).execute()
        print(f"✅ Mock sale complete! Ticker {ticker} removed and logged to trade_history.")
        print(f"   Return: {percent_return}% | PnL: ${profit_loss:.2f}")
    except Exception as e:
        print(f"❌ Database error during mock sale execution: {e}")
        sys.exit(1)

def reconcile_with_ibkr(ib: IB):
    """
    Full bidirectional reconciliation between IBKR actual positions and Supabase ledger.
    Runs every monitoring cycle (every 15 min during market hours).

    Case 1 — In Supabase, NOT in IBKR:
        Position was closed manually in TWS. Log to trade_history and remove from portfolio.

    Case 2 — In IBKR, NOT in Supabase:
        Position was opened manually in TWS. Insert into portfolio with computed stop/target.

    Case 3 — In both, but share count differs:
        Partial fill or manual adjustment. Update share count in Supabase.
    """
    print("🔄 Running IBKR ↔ Supabase reconciliation...")
    client = get_supabase_client()

    # ── Fetch IBKR positions ────────────────────────────────────────────────
    try:
        ib_raw = ib.positions()
        # Only include equity positions with a positive share count
        ib_map = {
            p.contract.symbol: p
            for p in ib_raw
            if p.contract.secType == "STK" and int(p.position) > 0
        }
    except Exception as e:
        print(f"❌ Could not fetch IBKR positions during reconciliation: {e}")
        return

    ib_tickers = set(ib_map.keys())

    # ── Fetch Supabase positions ────────────────────────────────────────────
    try:
        res = client.table("portfolio_positions").select("*").execute()
        supabase_positions = res.data or []
    except Exception as e:
        print(f"❌ Could not fetch Supabase positions during reconciliation: {e}")
        return

    supabase_map = {p["ticker"]: p for p in supabase_positions}
    supabase_tickers = set(supabase_map.keys())

    changes = 0

    # ── Case 1: In Supabase but NOT in IBKR (manual sell / closed in TWS) ──
    for ticker in supabase_tickers - ib_tickers:
        pos = supabase_map[ticker]
        print(f"   ⚠️  {ticker}: in Supabase but not in IBKR — manual close detected.")

        # Prefer the actual IBKR fill price from execution history.
        # reqExecutions() returns all fills from the current IB Gateway session.
        sell_price = 0.0
        sell_price_source = "unknown"
        try:
            fills = ib.reqExecutions()
            # Find the most recent SLD (sold) execution for this ticker
            sell_fills = [
                f for f in fills
                if f.contract.symbol == ticker and f.execution.side == "SLD"
            ]
            if sell_fills:
                # Sort newest first by execution time string (IB format: 'YYYYMMDD  HH:MM:SS TZ')
                sell_fills.sort(key=lambda f: f.execution.time, reverse=True)
                sell_price = float(sell_fills[0].execution.avgPrice)
                sell_price_source = f"IBKR fill (execId {sell_fills[0].execution.execId})"
        except Exception as ex:
            print(f"        ⚠️  reqExecutions() failed for {ticker}: {ex}")

        # Fallback 1: live FMP quote (price at reconciliation moment, up to 15 min late)
        if sell_price <= 0:
            sell_price = get_live_price(ticker)
            sell_price_source = "FMP live quote (fill not found in current session)"

        # Fallback 2: buy_price (prevents a zero-division or zero-price log entry)
        if sell_price <= 0:
            sell_price = float(pos["buy_price"])
            sell_price_source = "buy_price (no price source available)"

        print(f"        Sell price source: {sell_price_source} → ${sell_price:.2f}")


        shares = int(pos["shares"])
        buy_price = float(pos["buy_price"])
        buy_date = pos["buy_date"]
        buy_reason = pos.get("buy_reason", "Unknown")
        profit_loss = round((sell_price - buy_price) * shares, 2)
        percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)

        trade_log = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date,
            "buy_reason": buy_reason,
            "sell_price": sell_price,
            "sell_reason": "Manual close in IBKR (reconciled)",
            "profit_loss": profit_loss,
            "percent_return": percent_return,
        }
        try:
            client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            client.table("trade_history").insert(trade_log).execute()
            print(f"        ✅ Removed from portfolio, logged to history. PnL: ${profit_loss:+.2f} ({percent_return:+.2f}%)")
            notifier.notify_manual_close(
                ticker=ticker, shares=shares, buy_price=buy_price,
                sell_price=sell_price, sell_price_source=sell_price_source,
                buy_date=buy_date
            )
            changes += 1
        except Exception as e:
            print(f"        ❌ DB error reconciling close for {ticker}: {e}")

    # ── Case 2: In IBKR but NOT in Supabase (manual buy / opened in TWS) ───
    for ticker in ib_tickers - supabase_tickers:
        ib_pos = ib_map[ticker]
        shares = int(ib_pos.position)
        avg_cost = round(float(ib_pos.avgCost), 2)

        if avg_cost <= 0:
            print(f"   ⚠️  {ticker}: in IBKR with zero avg cost — skipping.")
            continue

        print(f"   ⚠️  {ticker}: in IBKR but not in Supabase — manual buy detected.")

        stop_loss = round(avg_cost * 0.93, 2)     # 7% stop
        profit_target = round(avg_cost * 1.25, 2)  # 25% target
        buy_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

        position_data = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": avg_cost,
            "buy_date": buy_date,
            "buy_reason": "Manual IBKR order (reconciled)",
            "stop_loss": stop_loss,
            "profit_target": profit_target,
            "is_power_hold": False,
            "high_water_mark": avg_cost,   # Trailing stop anchor — set to entry price
        }
        try:
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"        ✅ Added to Supabase: {shares} shares @ ${avg_cost} | SL: ${stop_loss} | PT: ${profit_target}")
            changes += 1
        except Exception as e:
            print(f"        ❌ DB error adding {ticker} to Supabase: {e}")

    # ── Case 3: In both, but share count mismatch (partial fill / adjustment)
    for ticker in ib_tickers & supabase_tickers:
        ib_shares = int(ib_map[ticker].position)
        db_shares = int(supabase_map[ticker]["shares"])
        if ib_shares != db_shares:
            print(f"   ⚠️  {ticker}: share count mismatch — IBKR: {ib_shares}, Supabase: {db_shares}. Correcting.")
            try:
                client.table("portfolio_positions").update({"shares": ib_shares}).eq("ticker", ticker).execute()
                print(f"        ✅ Updated to {ib_shares} shares.")
                changes += 1
            except Exception as e:
                print(f"        ❌ DB error updating shares for {ticker}: {e}")

    if changes == 0:
        print("   ✅ Supabase and IBKR are in sync. No changes needed.")
    else:
        print(f"   🔄 Reconciliation complete — {changes} correction(s) applied.")

    # ── Case 4: Sync live IBKR cash balance to Supabase ─────────────────────
    # The backend derives cash dynamically (initial + realized_pnl - open_cost)
    # which doesn't account for deposits, withdrawals, commissions, or dividends.
    # We write the real IBKR CashBalance here so the backend can use it directly.
    # Only upsert if the balance has changed by more than $1 to avoid redundant writes.
    try:
        ibkr_cash = get_available_cash(ib)
        if ibkr_cash > 0:
            new_balance = round(ibkr_cash, 2)

            # Read current stored value before writing
            stored_balance = None
            try:
                res = client.table("account_balances").select("value").eq("key", "ibkr_cash_balance").execute()
                if res.data:
                    stored_balance = float(res.data[0]["value"])
            except Exception:
                pass  # If read fails, proceed with write

            if stored_balance is None or abs(new_balance - stored_balance) > 1.00:
                client.table("account_balances").upsert(
                    {"key": "ibkr_cash_balance", "value": new_balance},
                    on_conflict="key"
                ).execute()
                change_str = f" (was ${stored_balance:,.2f})" if stored_balance is not None else " (first write)"
                print(f"   💰 Cash balance synced from IBKR: ${new_balance:,.2f}{change_str}")
            else:
                print(f"   💰 Cash balance unchanged (${new_balance:,.2f}) — skipping write.")
        else:
            print("   ⚠️  IBKR cash balance returned 0 or negative — skipping cash sync.")
    except Exception as e:
        print(f"   ❌ Could not sync cash balance from IBKR: {e}")



def run_market_open_buys(ib: IB):
    """Checks for daily breakout triggers and executes buy orders at market open."""
    print("⏳ Running Market Open Buy checks...")
    client = get_supabase_client()
    
    # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date()
    today_str = today_ny.strftime("%Y-%m-%d")
    recent_date = (today_ny - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    
    try:
        triggers_res = client.table("daily_triggers").select("*").gte("triggered_at", recent_date).execute()
        triggers = triggers_res.data
    except Exception as e:
        print(f"❌ Failed to fetch daily triggers: {e}")
        return

    if not triggers:
        print("😴 No breakouts triggered in the last 3 days. Skipping purchases.")
        return
        
    # Get current holdings in portfolio_positions
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        holdings = portfolio_res.data
        active_tickers = [h["ticker"] for h in holdings]
    except Exception as e:
        print(f"❌ Failed to fetch portfolio positions: {e}")
        return

    # Check portfolio cap limits
    if len(holdings) >= MAX_POSITIONS:
        print(f"🚫 Portfolio is fully invested ({len(holdings)}/{MAX_POSITIONS} positions). Standing down.")
        return
        
    for trigger in triggers:
        ticker = trigger["ticker"]
        
        # Don't buy a stock we already hold
        if ticker in active_tickers:
            continue

        # ── Cooling-off period: skip tickers sold within the last 3 days ────────
        # Prevents re-buying a stock that was just stopped out (trailing stop)
        # before it has had time to form a new valid base.
        # Uses sell_date (the actual trade_history column) not created_at.
        try:
            cooling_cutoff = (today_ny - datetime.timedelta(days=3)).isoformat()
            recent_sell_res = client.table("trade_history").select("ticker").eq("ticker", ticker).gte("sell_date", cooling_cutoff).execute()
            if recent_sell_res.data:
                print(f"   ⏳ {ticker} sold within last 3 days — cooling-off period active. Skipping.")
                continue
        except Exception as cool_err:
            print(f"   ⚠️ Cooling-off check failed for {ticker}: {cool_err} — allowing buy.")
            
        # Verify settled cash is enough for at least one minimum-sized position
        available_cash = get_available_cash(ib)
        print(f"💰 Available Cash Balance in IBKR: ${available_cash:,.2f}")
        if available_cash < MIN_POSITION_SIZE:
            print(f"🚫 Insufficient cash to buy {ticker} (floor: ${MIN_POSITION_SIZE:,.0f}). Skipping.")
            continue

        # Size the position as an equal share of remaining capital across unfilled slots
        remaining_slots = max(1, MAX_POSITIONS - len(portfolio_res.data))
        position_size = available_cash / remaining_slots
        print(f"   Position sizing: ${available_cash:,.2f} cash / {remaining_slots} remaining slot(s) = ${position_size:,.2f} per position")
            
        # Double check active holdings size again (in case we bought one earlier in this loop)
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        if len(portfolio_res.data) >= MAX_POSITIONS:
            print(f"🚫 Portfolio capacity ({MAX_POSITIONS}) reached during loop. Skipping further buys.")
            break

        print(f"🚀 Execution Trigger: Initiating purchase for {ticker}...")

        # Get live price to size shares
        current_price = get_live_price(ticker)
        if current_price <= 0:
            current_price = float(trigger["close_price"])

        shares = int(position_size / current_price)
        if shares <= 0:
            print(f"⚠️ Price of {ticker} (${current_price:.2f}) is too high for the computed position size (${position_size:,.0f}). Skipping.")
            continue
            
        # Place order on IBKR
        try:
            contract = Stock(ticker, 'SMART', 'USD')
            ib.qualifyContracts(contract)
            order = MarketOrder('BUY', shares)
            trade = ib.placeOrder(contract, order)
            
            # Wait a few seconds for execution fill
            print(f"   Waiting for fill on {shares} shares of {ticker}...")
            ib.sleep(3)
            
            fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0
            if fill_price <= 0:
                fill_price = current_price  # fallback
                
            stop_loss = round(fill_price * 0.93, 2)       # 7% Stop
            profit_target = round(fill_price * 1.25, 2)   # 25% Target
            
            # Record position in Supabase
            position_data = {
                "ticker": ticker,
                "shares": shares,
                "buy_price": fill_price,
                "buy_reason": f"CANSLIM Breakout: Vol Surge {trigger['volume_surge']}x",
                "stop_loss": round(fill_price * 0.93, 2),   # Initial floor (entry-based)
                "profit_target": profit_target,
                "is_power_hold": False,
                "high_water_mark": fill_price               # Trailing stop anchor — rises with price
            }
            
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"✅ Successfully bought {shares} shares of {ticker} at ${fill_price:.2f}.")
            print(f"   Stop-Loss: ${stop_loss} | Profit Target: ${profit_target}")
            
            # Notify all configured Telegram recipients
            portfolio_res = client.table("portfolio_positions").select("ticker").execute()
            slot_used = len(portfolio_res.data) if portfolio_res.data else 1
            notifier.notify_buy(
                ticker=ticker, shares=shares, fill_price=fill_price,
                stop_loss=stop_loss, profit_target=profit_target,
                volume_surge=float(trigger.get("volume_surge", 0)),
                pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                slot_used=slot_used, max_slots=MAX_POSITIONS
            )
            
            # Add to local tracker to prevent double buys in this loop
            active_tickers.append(ticker)
            
        except Exception as order_err:
            print(f"❌ Failed to execute order for {ticker}: {order_err}")
            notifier.notify_buy_failure(ticker=ticker, shares=shares, error=order_err)

def get_fresh_triggers_today(client: Client, active_tickers: list) -> list:
    """
    Returns ticker symbols from today's daily_triggers that are not already held.
    Used by the stale rotation gate to confirm a real replacement opportunity exists
    before rotating out a sideways position.
    """
    tz = ZoneInfo("America/New_York")
    today_str = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")
    try:
        res = client.table("daily_triggers") \
                    .select("ticker") \
                    .gte("triggered_at", today_str) \
                    .execute()
        return [r["ticker"] for r in res.data if r["ticker"] not in active_tickers]
    except Exception:
        return []

def monitor_portfolio_intraday(ib: IB):
    """Monitors open positions, enforcing stop-losses, profit targets, and the 8-week hold rule."""
    print("🔍 Running Intraday Portfolio Monitoring...")
    client = get_supabase_client()
    
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        positions = portfolio_res.data
    except Exception as e:
        print(f"❌ Failed to fetch portfolio positions: {e}")
        return

    if not positions:
        print("😴 No open positions to monitor.")
        return

    # Check actual IBKR positions to ensure we are in sync
    ib_positions = ib.positions()
    ib_tickers = [p.contract.symbol for p in ib_positions]

    # ── Pre-pass: Stale Position Rotation ────────────────────────────────────────
    # Before entering the per-position loop, identify ALL stale candidates and pick
    # the single worst performer. This ensures we always rotate the weakest position
    # rather than whichever happens to appear first in the Supabase results.
    # Rotation only fires when: portfolio is full AND a fresh trigger exists today.
    if len(positions) >= MAX_POSITIONS:
        active_tickers_now = [p["ticker"] for p in positions]
        fresh_triggers = get_fresh_triggers_today(client, active_tickers_now)
        if fresh_triggers:
            stale_candidates = []
            for p in positions:
                if bool(p.get("is_power_hold")):
                    continue  # Power Hold positions are exempt
                try:
                    bd = datetime.datetime.fromisoformat(p["buy_date"].replace('Z', '+00:00'))
                    days = (datetime.datetime.now(datetime.timezone.utc) - bd).days
                    bp = float(p["buy_price"])
                    lp = get_live_price(p["ticker"])
                    if lp <= 0:
                        continue
                    gain = (lp / bp) - 1.0
                    if days >= STALE_HOLD_DAYS and gain < STALE_HOLD_MAX_GAIN:
                        stale_candidates.append((gain, lp, days, p))
                except Exception:
                    continue

            if stale_candidates:
                # Sort ascending by gain — worst performer first
                stale_candidates.sort(key=lambda x: x[0])
                worst_gain, worst_price, worst_days, worst_pos = stale_candidates[0]
                replacement = fresh_triggers[0]
                gain_pct = worst_gain * 100.0
                wticker = worst_pos["ticker"]
                wshares = int(worst_pos["shares"])
                wbuy_price = float(worst_pos["buy_price"])
                wbuy_date = datetime.datetime.fromisoformat(
                    worst_pos["buy_date"].replace('Z', '+00:00'))
                wbuy_reason = worst_pos.get("buy_reason", "Unknown")
                reason = (
                    f"Stale Rotation — held {worst_days}d, "
                    f"only {gain_pct:+.1f}% gain. "
                    f"Freeing slot for: {replacement}"
                )
                print(f"♻️  Stale Rotation: {wticker} ({worst_days}d, {gain_pct:+.1f}%) "
                      f"→ slot freed for {replacement}")
                execute_sell(ib, client, wticker, wshares, wbuy_price,
                             wbuy_date, wbuy_reason, worst_price, reason)
                # execute_sell() already calls notifier.notify_sell() internally
                # Refresh positions list after rotation sell before entering main loop
                try:
                    positions = client.table("portfolio_positions").select("*").execute().data
                    ib_positions = ib.positions()
                    ib_tickers = [p.contract.symbol for p in ib_positions]
                except Exception:
                    pass

    for pos in positions:

        ticker = pos["ticker"]
        shares = int(pos["shares"])
        buy_price = float(pos["buy_price"])
        profit_target = float(pos["profit_target"])
        is_power_hold = bool(pos["is_power_hold"])
        buy_reason = pos.get("buy_reason", "Unknown")
        # Trailing stop: watermark rises with price, never falls. Default to buy_price on first cycle.
        high_water_mark = float(pos.get("high_water_mark") or buy_price)
        
        # Parse dates
        buy_date = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
        
        # 0. Skip positions already reconciled as missing from IBKR
        # (reconcile_with_ibkr handles the full bidirectional sync before this loop runs)
        if ticker not in ib_tickers:
            continue
            
        # Fetch live price
        current_price = get_live_price(ticker)
        if current_price <= 0:
            continue

        # ── Update trailing high-water mark ─────────────────────────────────
        if current_price > high_water_mark:
            high_water_mark = round(current_price, 2)
            try:
                client.table("portfolio_positions").update(
                    {"high_water_mark": high_water_mark}
                ).eq("ticker", ticker).execute()
            except Exception as e:
                print(f"   ⚠️ Could not update high_water_mark for {ticker}: {e}")

        trailing_stop = round(high_water_mark * 0.93, 2)
        print(f"   Monitoring {ticker}: Current: ${current_price:.2f} | Entry: ${buy_price:.2f} | High: ${high_water_mark:.2f} | Trail Stop: ${trailing_stop:.2f} | PT: ${profit_target:.2f}")
        
        # 1. 8-Week Power Holding Rule Check
        # If stock surges 20%+ in less than 21 days from purchase
        days_held = (datetime.datetime.now(datetime.timezone.utc) - buy_date).days
        if not is_power_hold and current_price >= (buy_price * 1.20) and days_held <= 21:
            print(f"🔥 Power Hold Triggered for {ticker}! Surged 20% in {days_held} days.")
            tz = ZoneInfo("America/New_York")
            today_ny = datetime.datetime.now(tz).date()
            expiry_date = (today_ny + datetime.timedelta(weeks=8)).isoformat()
            try:
                client.table("portfolio_positions").update({
                    "is_power_hold": True,
                    "power_hold_expiry": expiry_date
                }).eq("ticker", ticker).execute()
                print(f"   Exempt from 25% target until {expiry_date} (8 weeks hold).")
                gain_pct = ((current_price / buy_price) - 1.0) * 100.0
                notifier.notify_power_hold(
                    ticker=ticker, gain_pct=gain_pct, days_held=days_held,
                    expiry_date=expiry_date, stop_loss=trailing_stop
                )
            except Exception as e:
                print(f"   ❌ Failed to update power hold state: {e}")
                
        # If power hold has expired, deactivate it
        if is_power_hold and pos["power_hold_expiry"]:
            expiry_str = pos["power_hold_expiry"]
            if 'T' in expiry_str:
                expiry_str = expiry_str.split('T')[0]
            expiry = datetime.date.fromisoformat(expiry_str)
            tz = ZoneInfo("America/New_York")
            today_ny = datetime.datetime.now(tz).date()
            if today_ny >= expiry:
                print(f"⏳ Power Hold expired for {ticker}. Restoring standard target.")
                try:
                    client.table("portfolio_positions").update({
                        "is_power_hold": False,
                        "power_hold_expiry": None
                    }).eq("ticker", ticker).execute()
                except Exception as e:
                    print(f"   ❌ Failed to reset power hold state: {e}")

        # 2. Enforce Trailing Stop Loss (7% below high-water mark)
        # Trailing stop rises as price climbs but never falls — locks in gains.
        # Falls back to entry-based -7% if price never exceeded buy price.
        if current_price <= trailing_stop:
            gain_from_entry = ((high_water_mark / buy_price) - 1.0) * 100.0
            if high_water_mark > buy_price:
                reason_str = f"Trailing Stop (-7% from high of ${high_water_mark:.2f}, locked in {gain_from_entry:+.1f}% gain)"
            else:
                reason_str = "Trailing Stop (-7% from entry — position never gained)"
            print(f"🚨 Trailing Stop triggered for {ticker} at ${current_price:.2f} (Stop: ${trailing_stop:.2f}, High: ${high_water_mark:.2f})")
            execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason_str)
            continue
            
        # 3. Enforce 25% Profit Target (Skip if in Power Hold)
        if current_price >= profit_target and not is_power_hold:
            print(f"💰 Profit Target Triggered for {ticker} at ${current_price:.2f} (Target: ${profit_target})!")
            execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, "25% Profit Target")
            continue

def execute_sell(ib: IB, client: Client, ticker: str, shares: int, buy_price: float, buy_date, buy_reason: str, current_price: float, reason: str):
    """Executes a market sell order on IBKR and archives the transaction in Supabase."""
    try:
        # Place sell order
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        order = MarketOrder('SELL', shares)
        trade = ib.placeOrder(contract, order)
        
        print(f"   Placing market sell order for {shares} shares of {ticker}...")
        ib.sleep(3)
        
        fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0
        if fill_price <= 0:
            fill_price = current_price
            
        profit_loss = round((fill_price - buy_price) * shares, 2)
        percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
        
        # Log to trade history
        trade_log = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date.isoformat(),
            "buy_reason": buy_reason,
            "sell_price": fill_price,
            "sell_reason": reason,
            "profit_loss": profit_loss,
            "percent_return": percent_return
        }
        
        # Database transaction
        client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
        client.table("trade_history").insert(trade_log).execute()
        
        print(f"✅ Closed Position: Sold {shares} shares of {ticker} at ${fill_price:.2f}.")
        print(f"   PnL: ${profit_loss} ({percent_return}%) | Reason: {reason}")
        notifier.notify_sell(
            ticker=ticker, shares=shares, buy_price=buy_price,
            buy_date=buy_date.isoformat(), fill_price=fill_price, reason=reason
        )
        
    except Exception as e:
        print(f"❌ Error executing sell order for {ticker}: {e}")
        notifier.notify_exception(f"execute_sell({ticker}) — execution_agent.py", e)

def main_loop():
    """Main daemon loop running inside the Docker container."""
    print("==================================================")
    print("       CANSLIM Local Trade Execution Agent        ")
    print("==================================================")
    print(f"Connecting to IB Gateway at {IB_GATEWAY_HOST}:{IB_GATEWAY_PORT}...")
    
    ib = IB()
    try:
        ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
        print("✅ Connected to IBKR Gateway successfully!")
    except Exception as e:
        print(f"❌ Failed to connect to IBKR Gateway: {e}")
        print("   Ensure the ib-gateway container is running and API ports are open.")
        sys.exit(1)
        
    while True:
        try:
            tz = ZoneInfo("America/New_York")
            now = datetime.datetime.now(tz)
            # If market is open (9:30 AM - 4:00 PM EST, Mon-Fri)
            # 1-5 represents Monday to Friday
            if now.weekday() < 5:
                # 1. Market Open Check: Trigger buys between 9:30 AM and 9:45 AM
                # Check if it is within the market open window
                if now.hour == 9 and 30 <= now.minute <= 45:
                    reconcile_with_ibkr(ib)   # Sync before placing any new buys
                    run_market_open_buys(ib)
                    # Sleep 15 minutes to avoid duplicate runs during the open window
                    time.sleep(900)
                    continue

                # 2. Intraday monitoring during market hours
                if (now.hour == 9 and now.minute > 45) or (10 <= now.hour < 16):
                    reconcile_with_ibkr(ib)   # Sync every 15 min — catches manual TWS trades
                    monitor_portfolio_intraday(ib)
                    # Check every 15 minutes
                    time.sleep(900)
                    continue
                    
            # Outside market hours: check once an hour
            print(f"😴 Market is closed. Checking in 1 hour... (Current Time: {now.strftime('%H:%M:%S')})")
            time.sleep(3600)
            
        except KeyboardInterrupt:
            print("\nShutting down execution agent.")
            ib.disconnect()
            break
        except Exception as loop_err:
            print(f"❌ Error in main execution loop: {loop_err}")
            notifier.notify_exception("main_loop() — execution_agent.py", loop_err)
            time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CANSLIM Local execution agent CLI.")
    parser.add_argument("--mock-sell", type=str, help="Mock close a position in Supabase (e.g. AAPL)")
    parser.add_argument("--price", type=float, help="Mock sale price (required with --mock-sell)")
    parser.add_argument("--reason", type=str, default="Mock exit", help="Mock sale reason")
    
    args = parser.parse_args()
    
    if args.mock_sell:
        if not args.price:
            print("❌ Error: --price is required when mocking a sale.")
            sys.exit(1)
        handle_mock_sell(args.mock_sell, args.price, args.reason)
    else:
        if not FMP_API_KEY:
            print("❌ Error: FMP_API_KEY environment variable is not set.")
            sys.exit(1)
        main_loop()
