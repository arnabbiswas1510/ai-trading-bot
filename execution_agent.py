import os
import sys
import argparse
import datetime
import time
import requests
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order
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
# ── Exit & hold parameters ──────────────────────────────────────────────────
STOP_LOSS_PCT            = float(os.getenv("STOP_LOSS_PCT", 0.07))
PROFIT_TARGET_PCT        = float(os.getenv("PROFIT_TARGET_PCT", 0.25))
POWER_HOLD_GAIN_TRIGGER  = float(os.getenv("POWER_HOLD_GAIN_TRIGGER", 0.20))
POWER_HOLD_DAYS_LIMIT    = int(os.getenv("POWER_HOLD_DAYS_LIMIT", 21))
POWER_HOLD_DURATION_WEEKS = int(os.getenv("POWER_HOLD_DURATION_WEEKS", 8))
COOLING_OFF_DAYS         = int(os.getenv("COOLING_OFF_DAYS", 3))
TRIGGER_LOOKBACK_DAYS    = int(os.getenv("TRIGGER_LOOKBACK_DAYS", 3))
MAX_PIVOT_EXTENSION      = float(os.getenv("MAX_PIVOT_EXTENSION", 0.05))  # skip if price > 5% above pivot

# ── Moving Average Exit parameters ────────────────────────────────────────────
EXIT_MA_TRIGGER_ENABLED  = os.getenv("EXIT_MA_TRIGGER_ENABLED", "true").lower() == "true"
EXIT_MA_TYPE             = os.getenv("EXIT_MA_TYPE", "EMA")
EXIT_MA_WINDOW           = int(os.getenv("EXIT_MA_WINDOW", 21))
EXIT_MA_BUFFER_PCT       = float(os.getenv("EXIT_MA_BUFFER_PCT", 0.01))
EXIT_MA_EOD_ONLY         = os.getenv("EXIT_MA_EOD_ONLY", "true").lower() == "true"


MARKET_DIRECTION_FILTER_ENABLED = os.getenv("MARKET_DIRECTION_FILTER_ENABLED", "true").lower() == "true"
MARKET_DIRECTION_SMA_WINDOW     = int(os.getenv("MARKET_DIRECTION_SMA_WINDOW", 200))
MARKET_DIRECTION_TICKER         = os.getenv("MARKET_DIRECTION_TICKER", "SPY")

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

def fetch_historical_closes_with_dates(ticker: str, window: int) -> list:
    """Fetch historical daily close prices and dates from FMP (oldest first)."""
    # Fetch window * 4 + 20 calendar days to guarantee sufficient trading days
    lookback_days = window * 4 + 20
    to_date = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    from_date = to_date - datetime.timedelta(days=lookback_days)
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={from_date}&to={to_date}"
           f"&apikey={FMP_API_KEY}")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                # Return sorted by date ascending (oldest first)
                return sorted(data, key=lambda x: x["date"])
            else:
                print(f"⚠️ Empty historical data response for {ticker} from FMP.")
        else:
            print(f"⚠️ FMP historical API returned status code {r.status_code} for {ticker}.")
    except Exception as e:
        print(f"❌ Error fetching historical prices for {ticker} from FMP: {e}")
    return []

def calculate_sma(closes: list, window: int) -> float | None:
    """Compute Simple Moving Average."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window

def calculate_ema(closes: list, window: int) -> float | None:
    """Compute Exponential Moving Average."""
    if len(closes) < window:
        return None
    alpha = 2 / (window + 1)
    # Start with SMA of the first 'window' closes
    ema = sum(closes[:window]) / window
    # Apply recursive EMA formula to subsequent closes
    for price in closes[window:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return ema

def get_ma_value(ticker: str, current_price: float, ma_type: str, window: int) -> float | None:
    """Calculate moving average value, appending current_price if today's EOD bar isn't finalized."""
    hist = fetch_historical_closes_with_dates(ticker, window)
    if not hist:
        print(f"⚠️ No history found for {ticker}; cannot calculate {ma_type}-{window}.")
        return None
        
    history_dates = [h["date"] for h in hist]
    closes = [float(h["close"]) for h in hist]
    
    # Resolve today's date in New York time
    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")
    
    # If the latest date in FMP history is before today, append current_price to represent today's close
    if history_dates and history_dates[-1] < today_ny:
        closes.append(current_price)
        
    if ma_type.upper() == "SMA":
        return calculate_sma(closes, window)
    else:
        return calculate_ema(closes, window)

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

# ─────────────────────────────────────────────────────────────────────────────
# IBKR Order Management Helpers
# ─────────────────────────────────────────────────────────────────────────────

def TrailingStopOrder(action: str, totalQuantity: float,
                     trailingPercent: float = None,
                     trailStopPrice: float = None, **kwargs) -> Order:
    """
    Factory for IBKR TRAIL order type.
    `ib_insync` 0.9.x does not export a TrailingStopOrder helper,
    but the underlying Order dataclass supports it via orderType='TRAIL'.
    """
    o = Order()
    o.action = action
    o.orderType = 'TRAIL'
    o.totalQuantity = totalQuantity
    if trailingPercent is not None:
        o.trailingPercent = trailingPercent
    if trailStopPrice is not None:
        o.trailStopPrice = trailStopPrice
    for k, v in kwargs.items():
        setattr(o, k, v)
    return o

def place_oca_bracket(ib: IB, contract, shares: int, buy_price: float,
                      profit_target_pct: float, stop_loss_pct: float,
                      submit_limit_order: bool = False,
                      high_water_mark: float = None,
                      parent_order_id: int = None) -> str:
    """
    Places a GTC OCA bracket for an open stock position:
      • TrailingStopOrder  — trails {stop_loss_pct}% below the high-water mark.
      • LimitOrder         — sells at +{profit_target_pct}% from buy_price.
                             Omitted during the initial 21-day Power Hold qualification window.

    Both orders share the same OCA group so IBKR cancels one when the other fills.
    Returns the OCA group name (informational).
    """
    import time as _time
    oca_group = f"OCA_{contract.symbol}_{int(_time.time())}"

    # Trailing stop — IBKR adjusts the stop level as price climbs
    trail_stop_price = None
    if high_water_mark:
        trail_stop_price = round(high_water_mark * (1 - stop_loss_pct), 2)
        
    stop = TrailingStopOrder('SELL', shares, trailingPercent=round(stop_loss_pct * 100, 2),
                             trailStopPrice=trail_stop_price,
                             ocaGroup=oca_group, ocaType=1)
    if parent_order_id:
        stop.parentId = parent_order_id
    stop.tif = 'GTC'
    ib.placeOrder(contract, stop)
    print(f"   🛡️  IBKR trailing stop placed: {stop_loss_pct*100:.0f}% trail (OCA: {oca_group})")

    if submit_limit_order:
        profit_target = round(buy_price * (1 + profit_target_pct), 2)
        limit = LimitOrder('SELL', shares, profit_target,
                           ocaGroup=oca_group, ocaType=1)
        if parent_order_id:
            limit.parentId = parent_order_id
        limit.tif = 'GTC'
        ib.placeOrder(contract, limit)
        print(f"   💰 IBKR limit sell placed: ${profit_target:.2f} "
              f"(+{profit_target_pct*100:.0f}%) (OCA: {oca_group})")

    return oca_group


def cancel_ticker_sell_orders(ib: IB, ticker: str) -> int:
    """Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sells)."""
    cancelled = 0
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.action == 'SELL'
                and trade.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')):
            try:
                ib.cancelOrder(trade.order)
                cancelled += 1
            except Exception:
                pass
    if cancelled:
        print(f"   🗑️  Cancelled {cancelled} open SELL order(s) for {ticker}")
    return cancelled


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
        # Use ib.portfolio() instead of ib.positions(): portfolio() is always
        # populated on connection whereas positions() relies on a subscription
        # that may not have fired yet, causing false "in sync" results.
        ib_raw = ib.portfolio()
        # Check for short positions and alert
        for p in ib_raw:
            if p.contract.secType == "STK" and int(p.position) < 0:
                msg = f"🚨 SHORT POSITION DETECTED: {p.contract.symbol} has {int(p.position)} shares. Close this immediately in TWS!"
                print(msg)
                try:
                    notifier.notify_error(msg)
                except Exception:
                    pass

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

    # ── Safety guard: empty IBKR response while Supabase has positions ──────
    # ib.portfolio() transiently returns [] when account data hasn't finished
    # loading (e.g. after an internal reconnect). Without this guard, Case 1
    # would delete every Supabase position on a false "not in IBKR" signal.
    if not ib_tickers and supabase_tickers:
        print(f"   ⚠️  IBKR returned empty portfolio but Supabase has "
              f"{len(supabase_tickers)} position(s) — skipping reconcile to "
              f"prevent false deletion. Will retry next cycle.")
        return

    candidates_to_delete = supabase_tickers - ib_tickers
    changes = 0
    net_trade_cash = 0.0

    # ── Case 1: In Supabase but NOT in IBKR ─────────────────────────────────
    # IBKR is the single source of truth: it manages trailing stops and limit
    # sells via GTC OCA bracket. Any position missing from IBKR portfolio was
    # legitimately closed (trailing stop fired, limit sell hit, or TWS manual
    # close). Guard 1 above (empty portfolio) is the only transient-glitch
    # guard needed.
    for ticker in candidates_to_delete:
        pos = supabase_map[ticker]
        print(f"   ✅ {ticker}: position closed in IBKR — archiving to trade_history.")

        # ── Determine sell price from IBKR execution history ─────────────
        sell_price = 0.0
        sell_price_source = "unknown"
        has_sld_fill = False
        try:
            fills = ib.reqExecutions()
            sell_fills = [
                f for f in fills
                if f.contract.symbol == ticker and f.execution.side == "SLD"
            ]
            if sell_fills:
                sell_fills.sort(key=lambda f: f.execution.time, reverse=True)
                sell_price = float(sell_fills[0].execution.avgPrice)
                sell_price_source = f"IBKR fill (execId {sell_fills[0].execution.execId})"
                has_sld_fill = True
        except Exception as ex:
            print(f"        ⚠️  reqExecutions() failed for {ticker}: {ex}")

        # If no SLD fill (e.g. manual TWS close), do a single double-check
        # to rule out a transient partial portfolio read.
        if not has_sld_fill:
            ib.sleep(3)
            _ib_recheck = {
                p.contract.symbol: p for p in ib.portfolio()
                if p.contract.secType == "STK" and int(p.position) > 0
            }
            if ticker in _ib_recheck:
                print(f"        ⚠️  {ticker} reappeared on double-check — skipping (transient IBKR glitch).")
                continue
            print(f"        ℹ️  No SLD fill but position confirmed gone from IBKR — archiving.")

        # Cancel any remaining OCA orders for this ticker (cleanup)
        cancel_ticker_sell_orders(ib, ticker)


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
            net_trade_cash += (sell_price * shares)
        except Exception as e:
            print(f"        ❌ DB error reconciling close for {ticker}: {e}")

    # ── Case 2: In IBKR but NOT in Supabase (manual buy / opened in TWS) ───
    for ticker in ib_tickers - supabase_tickers:
        ib_pos = ib_map[ticker]
        shares = int(ib_pos.position)
        avg_cost = round(float(ib_pos.averageCost), 2)   # PortfolioItem uses averageCost

        if avg_cost <= 0:
            print(f"   ⚠️  {ticker}: in IBKR with zero avg cost — skipping.")
            continue

        print(f"   ⚠️  {ticker}: in IBKR but not in Supabase — manual buy detected.")

        stop_loss = round(avg_cost * (1 - STOP_LOSS_PCT), 2)
        profit_target = round(avg_cost * (1 + PROFIT_TARGET_PCT), 2)
        buy_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

        position_data = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": avg_cost,
            "buy_date": buy_date,
            "buy_reason": "Manual IBKR order (reconciled)",
            "buy_source": "daily_triggers",   # Bug fix: always set buy_source to prevent NULL
            "stop_loss": stop_loss,
            "profit_target": profit_target,
            "is_power_hold": False,
            "high_water_mark": avg_cost,   # Trailing stop anchor — set to entry price
        }
        try:
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"        ✅ Added to Supabase: {shares} shares @ ${avg_cost} | SL: ${stop_loss} | PT: ${profit_target}")
            changes += 1
            net_trade_cash -= (avg_cost * shares)
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

    # ── Case 4: Sync live IBKR cash balance to Supabase & Detect Deposits ───
    try:
        ibkr_cash = get_available_cash(ib)
        if ibkr_cash > 0:
            new_balance = round(ibkr_cash, 2)
            tz = ZoneInfo("America/New_York")
            today_str = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")

            # Read current stored value before writing
            stored_balance = None
            try:
                res = client.table("account_balances").select("value").eq("key", "ibkr_cash_balance").order("date", desc=True).limit(1).execute()
                if res.data:
                    stored_balance = float(res.data[0]["value"])
            except Exception:
                pass  # If read fails, proceed with write

            # Detect external deposits. We use the net_trade_cash accumulated above.
            if stored_balance is not None:
                expected_cash = stored_balance + net_trade_cash
                cash_diff = new_balance - expected_cash
                
                if cash_diff > 500.0:
                    print(f"   💸 External deposit detected! Expected: ${expected_cash:,.2f}, Actual: ${new_balance:,.2f} (+${cash_diff:,.2f})")
                    client.table("cash_flows").insert({
                        "date": today_str,
                        "amount": cash_diff,
                        "description": "Auto-detected Deposit"
                    }).execute()
                elif cash_diff < -500.0:
                    print(f"   💸 External withdrawal detected! Expected: ${expected_cash:,.2f}, Actual: ${new_balance:,.2f} (${cash_diff:,.2f})")
                    client.table("cash_flows").insert({
                        "date": today_str,
                        "amount": cash_diff,
                        "description": "Auto-detected Withdrawal"
                    }).execute()

            # Always record today's snapshot
            client.table("account_balances").upsert(
                {"date": today_str, "key": "ibkr_cash_balance", "value": new_balance},
            ).execute()
            
            # Also record positions value and total value for TWR
            positions_value = 0.0
            for ticker, p in ib_map.items():
                price = get_live_price(ticker)
                if price <= 0:
                    price = float(p.averageCost)
                positions_value += int(p.position) * price

            total_value = new_balance + positions_value

            client.table("account_balances").upsert(
                {"date": today_str, "key": "ibkr_positions_value", "value": round(positions_value, 2)},
            ).execute()
            client.table("account_balances").upsert(
                {"date": today_str, "key": "ibkr_total_value", "value": round(total_value, 2)},
            ).execute()

            change_str = f" (was ${stored_balance:,.2f})" if stored_balance is not None else " (first write)"
            print(f"   💰 Cash balance synced from IBKR: ${new_balance:,.2f}{change_str}")
            print(f"   📊 Portfolio Value snapshot: Cash ${new_balance:,.2f} + Positions ${positions_value:,.2f} = Total ${total_value:,.2f}")
        else:
            print("   ⚠️  IBKR cash balance returned 0 or negative — skipping cash sync.")
    except Exception as e:
        print(f"   ❌ Could not sync cash balance from IBKR: {e}")


def is_market_bullish() -> bool:
    """
    CANSLIM 'M' (Market Direction) filter.
    Returns True  if MARKET_DIRECTION_TICKER (SPY) is above its SMA{window} — bull market.
    Returns False if below — bear market: idle slots hold pure cash.
    Fails open (returns True) to avoid unintended cash locks if the API is unavailable.
    """
    if not MARKET_DIRECTION_FILTER_ENABLED:
        return True
    try:
        to_date   = datetime.datetime.now(ZoneInfo('America/New_York')).date()
        from_date = to_date - datetime.timedelta(days=MARKET_DIRECTION_SMA_WINDOW + 100)
        url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
               f"?symbol={MARKET_DIRECTION_TICKER}&from={from_date}&to={to_date}"
               f"&apikey={FMP_API_KEY}")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return True
        data = r.json()
        if not isinstance(data, list) or len(data) < MARKET_DIRECTION_SMA_WINDOW:
            print(f"⚠️ Not enough history for {MARKET_DIRECTION_TICKER} SMA{MARKET_DIRECTION_SMA_WINDOW}. Defaulting to BULL.")
            return True
        closes  = [float(d["close"]) for d in sorted(data, key=lambda x: x["date"])]
        latest  = closes[-1]
        sma     = sum(closes[-MARKET_DIRECTION_SMA_WINDOW:]) / MARKET_DIRECTION_SMA_WINDOW
        bullish = latest > sma
        print(f"📊 Market direction [{MARKET_DIRECTION_TICKER}]: "
              f"${latest:.2f} vs SMA{MARKET_DIRECTION_SMA_WINDOW} ${sma:.2f} "
              f"→ {'BULL ↑' if bullish else 'BEAR ↓'}")
        return bullish
    except Exception as e:
        print(f"⚠️ Market direction check failed: {e}. Defaulting to BULL.")
        return True

def run_market_open_buys(ib: IB):
    """Checks for daily breakout triggers and executes buy orders at market open."""
    print("⏳ Running Market Open Buy checks...")
    client = get_supabase_client()
    
    # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date()
    today_str = today_ny.strftime("%Y-%m-%d")
    recent_date = (today_ny - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    
    try:
        triggers_res = client.table("daily_triggers").select("*").gte("triggered_at", recent_date).execute()
        triggers = triggers_res.data
    except Exception as e:
        print(f"❌ Failed to fetch daily triggers: {e}")
        return

    if not triggers:
        print(f"😴 No primary breakouts in the last {TRIGGER_LOOKBACK_DAYS} days.")
        
    # Get current holdings in portfolio_positions
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        holdings = portfolio_res.data
        active_tickers = [h["ticker"] for h in holdings]
    except Exception as e:
        print(f"❌ Failed to fetch portfolio positions: {e}")
        return

    # ── Pre-pass: Stale Position Rotation ────────────────────────────────────────
    # Before checking MAX_POSITIONS, if portfolio is full AND we have fresh triggers,
    # sell the worst sideways position to free up a slot.
    fresh_triggers = [t["ticker"] for t in triggers if t["ticker"] not in active_tickers]
    if len(holdings) >= MAX_POSITIONS and fresh_triggers:
        stale_candidates = []
        for p in holdings:
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
            stale_candidates.sort(key=lambda x: (1, x[0]))
            worst_gain, worst_price, worst_days, worst_pos = stale_candidates[0]
            replacement = fresh_triggers[0]
            gain_pct = worst_gain * 100.0
            wticker = worst_pos["ticker"]
            wshares = int(worst_pos["shares"])
            wbuy_price = float(worst_pos["buy_price"])
            wbuy_date = datetime.datetime.fromisoformat(worst_pos["buy_date"].replace('Z', '+00:00'))
            wbuy_reason = worst_pos.get("buy_reason", "Unknown")
            reason = (
                f"Stale Rotation — held {worst_days}d, "
                f"only {gain_pct:+.1f}% gain. "
                f"Freeing slot for: {replacement}"
            )
            print(f"♻️  Stale Rotation: {wticker} ({worst_days}d, {gain_pct:+.1f}%) "
                  f"→ slot freed for {replacement}")
            cancel_ticker_sell_orders(ib, wticker)
            ib.sleep(1)
            execute_sell(ib, client, wticker, wshares, wbuy_price,
                         wbuy_date, wbuy_reason, worst_price, reason)
            
            # Refresh positions
            try:
                portfolio_res = client.table("portfolio_positions").select("*").execute()
                holdings = portfolio_res.data
                active_tickers = [h["ticker"] for h in holdings]
            except Exception as e:
                print(f"❌ Failed to refresh portfolio positions after rotation: {e}")
                return

    # Check portfolio cap.
    stock_holdings = holdings
    if len(stock_holdings) >= MAX_POSITIONS:
        print(f"❌ Portfolio is fully invested with {len(stock_holdings)} stock positions. Standing down.")
        return

    for trigger in triggers:
        ticker = trigger["ticker"]
        
        # Don't buy a stock we already hold
        if ticker in active_tickers:
            continue

        # ── Cooling-off period: skip tickers sold within the last 3 days ────────
        # Prevents re-buying a stock that was just stopped out (trailing stop)
        try:
            cooling_cutoff = (today_ny - datetime.timedelta(days=COOLING_OFF_DAYS)).isoformat()
            recent_sell_res = client.table("trade_history").select("ticker").eq("ticker", ticker).gte("sell_date", cooling_cutoff).execute()
            if recent_sell_res.data:
                print(f"   ⏳ {ticker} sold within last {COOLING_OFF_DAYS} days — cooling-off period active. Skipping.")
                continue
        except Exception as cool_err:
            print(f"   ⚠️ Cooling-off check failed for {ticker}: {cool_err} — allowing buy.")
            
        # Size the position as an equal share of remaining capital across unfilled slots
        stock_held_count = len(holdings)
        remaining_slots = max(1, MAX_POSITIONS - stock_held_count)
        available_cash = get_available_cash(ib)
        print(f"💰 Available Cash Balance in IBKR: ${available_cash:,.2f}")
        position_size = available_cash / remaining_slots
        print(f"   Position sizing: ${available_cash:,.2f} cash / {remaining_slots} remaining slot(s) = ${position_size:,.2f} per position")

        # Double check active holdings size again (in case we bought one earlier in this loop)
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        holdings = portfolio_res.data or []
        stock_held_count_loop = len(holdings)
        if stock_held_count_loop >= MAX_POSITIONS:
            print(f"🚫 Portfolio capacity ({MAX_POSITIONS} stocks) reached during loop. Skipping further buys.")
            break

        if available_cash < MIN_POSITION_SIZE:
            print(f"🚫 Insufficient cash to buy {ticker} (floor: ${MIN_POSITION_SIZE:,.0f}). Skipping.")
            continue
            
        # Buy reason tags the trigger source
        buy_reason = f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x"
        buy_source = "daily_triggers"

        print(f"🚀 Execution Trigger: Initiating purchase for {ticker}...")

        # Get live price to size shares
        current_price = get_live_price(ticker)
        if current_price <= 0:
            current_price = float(trigger["close_price"])

        # ── CANSLIM pivot extension check ────────────────────────────────────
        pivot_price = float(trigger["close_price"])
        extension_pct = (current_price - pivot_price) / pivot_price if pivot_price > 0 else 0
        if extension_pct > MAX_PIVOT_EXTENSION:
            print(f"   ⛔ {ticker} is {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
                  f"— extended beyond {MAX_PIVOT_EXTENSION*100:.0f}% buy zone. Skipping.")
            continue
        print(f"   ✅ {ticker} within buy zone: {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
              f"(max {MAX_PIVOT_EXTENSION*100:.0f}%)")

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

            # Verify fill via ib.portfolio() (NOT trade.orderStatus) to avoid ghost
            # positions from Error 10349 (IB cancels-and-resubmits on TIF change).
            print(f"   Waiting for fill on {shares} shares of {ticker}...")
            ib.sleep(5)
            ib_map = {p.contract.symbol: p for p in ib.portfolio()}
            if ticker not in ib_map:
                ib.sleep(3)  # one more wait
                ib_map = {p.contract.symbol: p for p in ib.portfolio()}

            if ticker not in ib_map:
                print(f"   ⚠️ {ticker} not found in IBKR portfolio after 8s — order may not have filled. Skipping Supabase insert.")
                notifier.notify_buy_failure(ticker=ticker, shares=shares,
                    error="Not confirmed in IBKR portfolio after 8s")
                continue

            ib_pos = ib_map[ticker]
            fill_price = round(ib_pos.averageCost, 2)
            actual_shares = int(ib_pos.position)
            
            # Record position in Supabase
            position_data = {
                "ticker":          ticker,
                "shares":          actual_shares,
                "buy_price":       fill_price,
                "buy_reason":      f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
                "buy_source":      buy_source,
                "stop_loss":       round(fill_price * (1 - STOP_LOSS_PCT), 2),
                "profit_target":   round(fill_price * (1 + PROFIT_TARGET_PCT), 2),
                "is_power_hold":   False,
                "high_water_mark": fill_price
            }
            
            client.table("portfolio_positions").insert(position_data).execute()
            stop_loss     = round(fill_price * (1 - STOP_LOSS_PCT), 2)
            profit_target = round(fill_price * (1 + PROFIT_TARGET_PCT), 2)
            print(f"✅ Successfully bought {actual_shares} shares of {ticker} at ${fill_price:.2f}.")
            print(f"   Stop-Loss: ${stop_loss} | Profit Target: ${profit_target}")

            # Place IBKR GTC OCA bracket: trailing stop + limit sell at profit target.
            # IBKR manages both orders server-side — no stop/target polling needed in code.
            # Store the OCA group name in Supabase so self-healing can verify the
            # exact order pair (prevents double-placement if openTrades() is briefly stale).
            try:
                _oca_group = place_oca_bracket(ib, contract, actual_shares, fill_price,
                                               PROFIT_TARGET_PCT, STOP_LOSS_PCT,
                                               parent_order_id=trade.order.orderId)
                client.table("portfolio_positions").update(
                    {"oca_group": _oca_group}
                ).eq("ticker", ticker).execute()
            except Exception as _oca_err:
                print(f"   ⚠️ OCA bracket placement failed for {ticker}: {_oca_err} "
                      f"— self-healing will re-place on next monitor cycle.")
            
            # Notify all configured Telegram recipients
            portfolio_res = client.table("portfolio_positions").select("ticker").execute()
            slot_used = len(portfolio_res.data) if portfolio_res.data else 1
            notifier.notify_buy(
                ticker=ticker, shares=actual_shares, fill_price=fill_price,
                stop_loss=round(fill_price * (1 - STOP_LOSS_PCT), 2),
                profit_target=round(fill_price * (1 + PROFIT_TARGET_PCT), 2),
                volume_surge=float(trigger.get("volume_surge", 0)),
                pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                slot_used=slot_used, max_slots=MAX_POSITIONS
            )
            
            # Add to local tracker to prevent double buys in this loop
            active_tickers.append(ticker)
            holdings = portfolio_res.data or []
            
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
    # Use ib.portfolio() (not ib.positions()) — portfolio() is always populated
    # on connection; positions() is subscription-based and may be empty.
    ib_map_monitor = {p.contract.symbol: p for p in ib.portfolio()}
    ib_tickers = list(ib_map_monitor.keys())

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

        trailing_stop = round(high_water_mark * (1 - STOP_LOSS_PCT), 2)
        print(f"   Monitoring {ticker}: Current: ${current_price:.2f} | Entry: ${buy_price:.2f} "
              f"| High: ${high_water_mark:.2f} | IBKR Trail: {STOP_LOSS_PCT*100:.0f}% | PT: ${profit_target:.2f}")

        # ── Self-healing: ensure IBKR OCA bracket exists for this position ──
        # GTC orders survive gateway restarts at IBKR's servers but may be
        # absent for positions opened before this feature or after a full
        # account reset. Re-place them automatically.
        #
        # Uses the stored oca_group to check for the EXACT order pair rather
        # than any sell order — prevents double-placement if openTrades() is
        # briefly stale right after a new buy.
        
        days_held = (datetime.datetime.now(datetime.timezone.utc) - buy_date).days
        should_have_limit = days_held > POWER_HOLD_DAYS_LIMIT and not is_power_hold
        
        _stored_oca = pos.get("oca_group")
        _open_sells = [
            t for t in ib.openTrades()
            if t.contract.symbol == ticker
            and t.order.action == 'SELL'
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')
        ]
        
        expected_orders = 2 if should_have_limit else 1
        
        if _stored_oca:
            # Precise check: look for our specific OCA group
            _matching_oca = [t for t in _open_sells if t.order.ocaGroup == _stored_oca]
            _needs_heal = len(_matching_oca) < expected_orders
        else:
            # No stored group yet (old position or Power Hold standalone trail)
            _needs_heal = len(_open_sells) < expected_orders

        if _needs_heal:
            _heal_label = f"OCA group '{_stored_oca}'" if _stored_oca else "SELL orders"
            print(f"   🔧 {ticker}: Missing limit order or {_heal_label} in IBKR — re-placing bracket (self-healing).")
            try:
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _heal_contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_heal_contract)
                _new_oca = place_oca_bracket(ib, _heal_contract, shares, buy_price,
                                             PROFIT_TARGET_PCT, STOP_LOSS_PCT,
                                             submit_limit_order=should_have_limit,
                                             high_water_mark=high_water_mark)
                # Update stored group so next cycle checks the new pair
                client.table("portfolio_positions").update(
                    {"oca_group": _new_oca}
                ).eq("ticker", ticker).execute()
            except Exception as _heal_err:
                print(f"   ⚠️ Self-healing OCA placement failed for {ticker}: {_heal_err}")

        # 1. 8-Week Power Holding Rule Check
        # If stock surges 20%+ in less than 21 days from purchase
        if not is_power_hold and current_price >= (buy_price * (1 + POWER_HOLD_GAIN_TRIGGER)) and days_held <= POWER_HOLD_DAYS_LIMIT:
            print(f"🔥 Power Hold Triggered for {ticker}! Surged {POWER_HOLD_GAIN_TRIGGER*100:.0f}% in {days_held} days.")
            tz = ZoneInfo("America/New_York")
            today_ny = datetime.datetime.now(tz).date()
            expiry_date = (today_ny + datetime.timedelta(weeks=POWER_HOLD_DURATION_WEEKS)).isoformat()
            try:
                client.table("portfolio_positions").update({
                    "is_power_hold": True,
                    "power_hold_expiry": expiry_date
                }).eq("ticker", ticker).execute()
                print(f"   Exempt from {PROFIT_TARGET_PCT*100:.0f}% target until {expiry_date} ({POWER_HOLD_DURATION_WEEKS} weeks hold).")
                gain_pct = ((current_price / buy_price) - 1.0) * 100.0
                notifier.notify_power_hold(
                    ticker=ticker, gain_pct=gain_pct, days_held=days_held,
                    expiry_date=expiry_date, stop_loss=trailing_stop
                )
                # Cancel OCA bracket and re-place only the trailing stop 
                # using the high_water_mark to preserve the trail.
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _ph_contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_ph_contract)
                _ph_stop = TrailingStopOrder('SELL', shares,
                                             trailingPercent=STOP_LOSS_PCT * 100,
                                             trailStopPrice=round(high_water_mark * (1 - STOP_LOSS_PCT), 2))
                _ph_stop.tif = 'GTC'
                ib.placeOrder(_ph_contract, _ph_stop)
                # Clear oca_group: standalone trailing stop has no OCA pair.
                client.table("portfolio_positions").update(
                    {"oca_group": None}
                ).eq("ticker", ticker).execute()
                print(f"   🛡️  Trailing stop re-placed (no 25% limit during Power Hold).")
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
                    # Re-place full OCA bracket: trailing stop + 25% limit sell.
                    cancel_ticker_sell_orders(ib, ticker)
                    ib.sleep(1)
                    _exp_contract = Stock(ticker, 'SMART', 'USD')
                    ib.qualifyContracts(_exp_contract)
                    _exp_oca = place_oca_bracket(ib, _exp_contract, shares, buy_price,
                                                 PROFIT_TARGET_PCT, STOP_LOSS_PCT,
                                                 submit_limit_order=True,
                                                 high_water_mark=high_water_mark)
                    # Record the new OCA group so self-healing tracks the right pair.
                    client.table("portfolio_positions").update(
                        {"oca_group": _exp_oca}
                    ).eq("ticker", ticker).execute()
                    print(f"   💰 OCA bracket re-placed (trailing stop + 25% limit) after Power Hold expiry.")
                except Exception as e:
                    print(f"   ❌ Failed to reset power hold state: {e}")

        # Stop-loss and profit-target enforcement removed from code.
        # IBKR manages both via the GTC OCA bracket placed at buy time:
        #   • TrailingStopOrder — trails STOP_LOSS_PCT% below the high-water mark.
        #   • LimitOrder        — sells at +PROFIT_TARGET_PCT% from buy_price.
        # reconcile_with_ibkr() (Case 1) detects the close next cycle and archives
        # the position to trade_history with the IBKR fill price.

        # ── Moving Average Exit Check ─────────────────────────────────────────
        if EXIT_MA_TRIGGER_ENABLED:
            is_ma_window = True
            if EXIT_MA_EOD_ONLY:
                tz = ZoneInfo("America/New_York")
                now_ny = datetime.datetime.now(tz)
                # Check if we are between 3:45 PM and 4:00 PM ET
                is_ma_window = (now_ny.hour == 15 and now_ny.minute >= 45)
                
            if is_ma_window:
                ma_val = get_ma_value(ticker, current_price, EXIT_MA_TYPE, EXIT_MA_WINDOW)
                if ma_val is not None:
                    threshold = ma_val * (1 - EXIT_MA_BUFFER_PCT)
                    if current_price < threshold:
                        reason = (
                            f"{EXIT_MA_TYPE}-{EXIT_MA_WINDOW} Exit — Price ${current_price:.2f} "
                            f"below MA ${ma_val:.2f} with {EXIT_MA_BUFFER_PCT*100:.1f}% buffer (${threshold:.2f})"
                        )
                        print(f"🚨 {ticker} breached Moving Average exit! {reason}")
                        execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason)

                        continue

def execute_sell(ib: IB, client: Client, ticker: str, shares: int, buy_price: float, buy_date, buy_reason: str, current_price: float, reason: str) -> bool:
    """Executes a market sell order on IBKR and archives the transaction in Supabase.

    CRITICAL INVARIANT: Supabase position is ONLY deleted after confirming via
    ib.portfolio() that the position is truly gone from IBKR. This prevents phantom
    deletions when market orders are cancelled/rejected (e.g. paper trading no-data).
    """
    try:
        # Cancel any open OCA orders (trailing stop + limit) before placing
        # explicit sell (stale rotation) to avoid duplicate fills.
        cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)

        # Place sell order
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        order = MarketOrder('SELL', shares)
        trade = ib.placeOrder(contract, order)
        
        print(f"   Placing market sell order for {shares} shares of {ticker}...")
        
        # Wait up to 30 seconds for fill
        for _ in range(15):
            ib.sleep(2)
            if trade.orderStatus.status == 'Filled':
                break

        # ── CRITICAL: verify fill via ib.portfolio() BEFORE touching Supabase ──
        # MarketOrders can be cancelled (e.g. paper-trading no live market data)
        # without raising a Python exception. We MUST confirm the position is
        # actually gone from IBKR before removing it from Supabase.
        ib_after = {
            p.contract.symbol: p for p in ib.portfolio()
            if p.contract.secType == "STK" and int(p.position) > 0
        }
        if ticker in ib_after:
            print(f"   ⚠️  SELL NOT CONFIRMED: {ticker} still in IBKR portfolio after sell attempt.")
            print(f"       Order status: {trade.orderStatus.status}. Cancelling order — Supabase record PRESERVED.")
            try:
                ib.cancelOrder(trade.order)
            except Exception:
                pass
            return False  # ← EXIT WITHOUT DELETING FROM SUPABASE

        # Sell confirmed — position is gone from IBKR
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
        
        # Database transaction — only reached after confirmed IBKR fill
        client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
        client.table("trade_history").insert(trade_log).execute()
        
        print(f"✅ Closed Position: Sold {shares} shares of {ticker} at ${fill_price:.2f}.")
        print(f"   PnL: ${profit_loss} ({percent_return}%) | Reason: {reason}")
        notifier.notify_sell(
            ticker=ticker, shares=shares, buy_price=buy_price,
            buy_date=buy_date.isoformat(), fill_price=fill_price, reason=reason
        )
        return True
        
    except Exception as e:
        print(f"❌ Error executing sell order for {ticker}: {e}")
        notifier.notify_exception(f"execute_sell({ticker}) — execution_agent.py", e)
        return False

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
        
    _buy_ran_today: str = ""   # tracks date string of last successful buy run

    while True:
        try:
            tz = ZoneInfo("America/New_York")
            now = datetime.datetime.now(tz)
            today_str = now.strftime("%Y-%m-%d")

            if now.weekday() < 5:
                # SENTINEL: if /app/run_buys_now.txt exists, force-run buy logic immediately
                if os.path.exists("/app/run_buys_now.txt"):
                    os.remove("/app/run_buys_now.txt")
                    print("🎯 Force buy sentinel detected — running run_market_open_buys NOW")
                    reconcile_with_ibkr(ib)
                    run_market_open_buys(ib)
                    time.sleep(900)
                    continue

                is_market_open = (
                    (now.hour == 9 and now.minute >= 30)
                    or (10 <= now.hour < 16)
                )

                # 1. Daily Buy Check (No Window Restriction)
                # Ensure the buy check happens exactly once per day during market hours.
                if is_market_open and _buy_ran_today != today_str:
                    _buy_ran_today = today_str
                    reconcile_with_ibkr(ib)   # Sync before placing any new buys
                    run_market_open_buys(ib)
                    time.sleep(900)
                    continue

                # 2. Intraday monitoring during market hours
                if is_market_open:
                    reconcile_with_ibkr(ib)   # Sync every 15 min — catches manual TWS trades
                    monitor_portfolio_intraday(ib)
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
