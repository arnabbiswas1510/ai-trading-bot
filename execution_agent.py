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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

fmp_session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504, 429], connect=3, read=3)
fmp_session.mount('https://', HTTPAdapter(max_retries=retries))
fmp_session.mount('http://', HTTPAdapter(max_retries=retries))

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
# ── Exit & hold parameters ──────────────────────────────────────────────────
STOP_LOSS_PCT            = float(os.getenv("STOP_LOSS_PCT", 0.07))
# Days without a new high-water mark before a position is considered plateaued.
# If portfolio is full and a fresh breakout exists at 3:45pm, the most-stalled
# position (largest days since HWM) is rotated out.
PLATEAU_DAYS             = int(os.getenv("PLATEAU_DAYS", 10))
COOLING_OFF_DAYS         = int(os.getenv("COOLING_OFF_DAYS", 3))
MIN_POSITION_SIZE        = float(os.getenv("MIN_POSITION_SIZE", 5000.0))
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

# Global unhandled exception hook
def global_exception_handler(exctype, value, tb):
    if issubclass(exctype, KeyboardInterrupt):
        sys.__excepthook__(exctype, value, tb)
        return
    import traceback
    tb_str = "".join(traceback.format_exception(exctype, value, tb))
    print(f"CRITICAL: Unhandled exception caught by global hook:\n{tb_str}")
    notifier.notify_exception("GLOBAL UNCAUGHT EXCEPTION", value)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

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
        res = fmp_session.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0))
    except Exception as e:
        notifier.notify_exception(f"get_live_price() — execution_agent.py", e)
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
        r = fmp_session.get(url, timeout=10)
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
        notifier.notify_exception(f"fetch_historical_closes_with_dates() — execution_agent.py", e)
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
        notifier.notify_exception(f"get_available_cash() — execution_agent.py", e)
        print(f"❌ Error querying cash balance from IBKR: {e}")
    return 0.0

# ─────────────────────────────────────────────────────────────────────────────
# IBKR Order Management Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_ibkr_account(ib: IB) -> str:
    """
    Returns the configured IBKR account.
    Defaults to the Paper Trading account ('DU...') if multiple exist.
    Can be overridden with the IBKR_ACCOUNT environment variable.
    """
    accounts = ib.managedAccounts()
    if not accounts:
        raise ValueError("No IBKR accounts found for this login.")
        
    env_account = os.getenv("IBKR_ACCOUNT")
    if env_account and env_account in accounts:
        return env_account
        
    # Default to Paper account if available
    paper_accounts = [acc for acc in accounts if acc.startswith('DU')]
    if paper_accounts:
        return paper_accounts[0]
        
    return accounts[0]

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

def place_trailing_stop(ib: IB, contract, shares: int, stop_loss_pct: float) -> str:
    """
    Places a GTC Trailing Stop for an open stock position.
    Trails stop_loss_pct% below the running peak price.

    IBKR tracks the high-water mark internally (tick-by-tick) — no HWM
    parameter is needed. Winners run freely until the stop fires or EOD
    plateau rotation acts.

    Returns an order group label (informational).
    """
    import time as _time
    group = f"TS_{contract.symbol}_{int(_time.time())}"

    stop = TrailingStopOrder('SELL', shares,
                             trailingPercent=round(stop_loss_pct * 100, 2))
    stop.tif = 'GTC'
    stop.account = get_ibkr_account(ib)
    ib.placeOrder(contract, stop)
    print(f"   🛡️  IBKR trailing stop placed: {stop_loss_pct*100:.0f}% trail")
    return group


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
        notifier.notify_exception(f"handle_mock_sell() — execution_agent.py", e)
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

    # ── Sync live IBKR cash balance to Supabase (Do this FIRST) ────────
    try:
        ibkr_cash = get_available_cash(ib)
        if ibkr_cash > 0:
            new_balance = round(ibkr_cash, 2)
            tz = ZoneInfo("America/New_York")
            today_str = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")

            # Calculate positions value and total value for TWR
            ib_port = []
            try: ib_port = ib.portfolio()
            except Exception: pass
            
            positions_value = 0.0
            for p in ib_port:
                if p.contract.secType == "STK" and int(p.position) > 0:
                    price = get_live_price(p.contract.symbol)
                    if price <= 0: price = float(p.averageCost)
                    positions_value += int(p.position) * price

            total_value = new_balance + positions_value

            # Always record today's snapshot with the new time-series schema
            client.table("account_balances").upsert(
                {
                    "date": today_str,
                    "ibkr_cash_balance": new_balance,
                    "ibkr_positions_value": round(positions_value, 2),
                    "ibkr_total_value": round(total_value, 2)
                }
            ).execute()
            print(f"   💰 Cash balance synced from IBKR: ${new_balance:,.2f}")
    except Exception as e:
        notifier.notify_exception("reconcile_with_ibkr() cash sync — execution_agent.py", e)
        print(f"   ❌ Could not sync cash balance from IBKR: {e}")

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
        notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
        print(f"❌ Could not fetch IBKR positions during reconciliation: {e}")
        return

    ib_tickers = set(ib_map.keys())

    # ── Fetch Supabase positions ────────────────────────────────────────────
    try:
        res = client.table("portfolio_positions").select("*").execute()
        supabase_positions = res.data or []
    except Exception as e:
        notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
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
            notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", ex)
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

        sell_reason = "Manual close in IBKR (reconciled)"
        if has_sld_fill:
            sell_reason = "IBKR order filled (reconciled)"

        trade_log = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date,
            "buy_reason": buy_reason,
            "sell_price": sell_price,
            "sell_reason": sell_reason,
            "profit_loss": profit_loss,
            "percent_return": percent_return,
        }
        try:
            # Delete from portfolio FIRST, independently of trade history
            client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            changes += 1
            print(f"        ✅ Removed {ticker} from Supabase portfolio.")
            
            try:
                # Then try to insert to trade_history
                client.table("trade_history").insert(trade_log).execute()
                print(f"        ✅ Logged to history. PnL: ${profit_loss:+.2f} ({percent_return:+.2f}%)")
                notifier.notify_manual_close(
                    ticker=ticker, shares=shares, buy_price=buy_price,
                    sell_price=sell_price, sell_price_source=sell_price_source,
                    buy_date=buy_date
                )
            except Exception as e:
                notifier.notify_exception(f"reconcile_with_ibkr() (trade_history insert) — execution_agent.py", e)
                print(f"        ❌ DB error adding {ticker} to trade_history: {e}")
                
            net_trade_cash += (sell_price * shares)
        except Exception as e:
            notifier.notify_exception(f"reconcile_with_ibkr() (portfolio delete) — execution_agent.py", e)
            print(f"        ❌ DB error removing {ticker} from portfolio: {e}")

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
        buy_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

        position_data = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": avg_cost,
            "buy_date": buy_date,
            "buy_reason": "Manual IBKR order (reconciled)",
            "buy_source": "daily_triggers",   # Bug fix: always set buy_source to prevent NULL
            "stop_loss": stop_loss,
            "hwm_date": datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),   # plateau clock starts at entry
        }
        try:
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"        ✅ Added to Supabase: {shares} shares @ ${avg_cost} | SL: ${stop_loss}")
            changes += 1
            net_trade_cash -= (avg_cost * shares)
        except Exception as e:
            notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
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
                notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
                print(f"        ❌ DB error updating shares for {ticker}: {e}")

    if changes == 0:
        print("   ✅ Supabase and IBKR are in sync. No changes needed.")
    else:
        print(f"   🔄 Reconciliation complete — {changes} correction(s) applied.")

    # Cash sync moved to top of function
    pass


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
        r = fmp_session.get(url, timeout=10)
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
        notifier.notify_exception(f"is_market_bullish() — execution_agent.py", e)
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
        # Sort by final_score (quality + AI bonus) descending.
        # Falls back to quality_score, then ai_rating, then 0 if columns not yet populated.
        triggers.sort(
            key=lambda x: x.get("final_score") or x.get("quality_score") or x.get("ai_rating") or 0,
            reverse=True
        )
    except Exception as e:
        notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
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
        notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
        print(f"❌ Failed to fetch portfolio positions: {e}")
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
            notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", cool_err)
            print(f"   ⚠️ Cooling-off check failed for {ticker}: {cool_err} — allowing buy.")

        # ── AI veto: skip D-grade tickers (low-conviction AI rating < 30) ────────
        ai_grade = trigger.get("ai_grade")
        if ai_grade == "D":
            print(f"   🚫 {ticker} vetoed by AI evaluator (D-grade, conviction < 30). Skipping.")
            continue
        if ai_grade:
            print(f"   🟢 {ticker} AI grade: {ai_grade} | "
                  f"quality={trigger.get('quality_score', 'N/A')} | "
                  f"final={trigger.get('final_score', 'N/A')}")
            
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
            
        # Place Atomic Bracket Order on IBKR (Marketable Limit)
        try:
            contract = Stock(ticker, 'SMART', 'USD')
            ib.qualifyContracts(contract)
            # 1. Market Order Entry
            order = MarketOrder('BUY', shares)
            order.tif = 'DAY'   # explicit DAY prevents IBKR error 10349 (preset TIF warning)
            order.account = get_ibkr_account(ib)
            
            print(f"   Submitting Market Order for {shares} shares of {ticker}...")
            trade = ib.placeOrder(contract, order)

            print(f"   Waiting for fill on {shares} shares of {ticker}...")
            for _ in range(60):
                ib.sleep(1)
                status = trade.orderStatus.status
                filled_so_far = int(trade.orderStatus.filled)
                if status == 'Filled':
                    break
                elif status in ('Cancelled', 'Inactive'):
                    if filled_so_far == 0:
                        # Grace period: fill confirmation may still be in-flight
                        # (race condition where IBKR warning/cancel arrives before fill ack)
                        ib.sleep(2)
                        if int(trade.orderStatus.filled) > 0:
                            print(f"   ℹ️ {ticker}: fill arrived after cancel event — proceeding with position.")
                    break

            if trade.orderStatus.status != 'Filled':
                print(f"   ⚠️ {ticker} order not fully filled or was rejected. Cancelling remaining.")
                ib.cancelOrder(order)
                ib.sleep(2)

            actual_shares = int(trade.orderStatus.filled)
            if actual_shares == 0:
                reject_msgs = [entry.message for entry in trade.log if getattr(entry, 'message', '')]
                reject_msg = " | ".join(reject_msgs) if reject_msgs else "No explicit IBKR message (Order timed out, zero liquidity, or halted)"
                
                print(f"   ⚠️ {ticker} order had 0 shares filled. Reason: {reject_msg}")
                notifier.notify_buy_failure(ticker=ticker, shares=shares,
                    error=f"IBKR Log: {reject_msg}")
                continue

            fill_price = round(trade.orderStatus.avgFillPrice, 2)
            if fill_price <= 0:
                fill_price = current_price
            
            # 2. Sequential Trailing Stop
            # Now that the position is verified filled, we attach the trailing stop loss.
            # IBKR anchors it to the current market price and tracks the HWM tick-by-tick.
            oca_str = place_trailing_stop(ib, contract, actual_shares, STOP_LOSS_PCT)

            stop_loss_val = round(fill_price * (1 - STOP_LOSS_PCT), 2)
            position_data = {
                "ticker":     ticker,
                "shares":     actual_shares,
                "buy_price":  fill_price,
                "buy_reason": f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
                "buy_source": buy_source,
                "stop_loss":  stop_loss_val,
                "hwm_date":   datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
                "oca_group":  oca_str,
                # ── Entry quality snapshot (for future rotation analysis) ──────
                "entry_quality_score": trigger.get("quality_score"),
                "entry_ai_rating":     trigger.get("ai_rating"),
                "entry_ai_grade":      trigger.get("ai_grade"),
                "entry_final_score":   trigger.get("final_score"),
            }

            client.table("portfolio_positions").insert(position_data).execute()
            print(f"✅ Successfully bought {actual_shares} shares of {ticker} at ${fill_price:.2f}.")
            print(f"   Stop-Loss: ${stop_loss_val} | Trail: {STOP_LOSS_PCT*100:.0f}% (IBKR-managed)")

            # Update loop state FIRST — before notify_buy so the tracker is
            # correct even if the Telegram call raises an exception.
            active_tickers.append(ticker)
            portfolio_res = client.table("portfolio_positions").select("ticker").execute()
            holdings = portfolio_res.data or []
            slot_used = len(holdings)

            # Notify all configured Telegram recipients
            notifier.notify_buy(
                ticker=ticker, shares=actual_shares, fill_price=fill_price,
                stop_loss=stop_loss_val,
                volume_surge=float(trigger.get("volume_surge", 0)),
                pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                slot_used=slot_used, max_slots=MAX_POSITIONS
            )

        except Exception as order_err:
            notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", order_err)
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
    """Monitors open positions: updates hwm_date, self-heals trailing stops,
    applies the MA exit, and runs EOD plateau rotation."""
    print("🔍 Running Intraday Portfolio Monitoring...")
    client = get_supabase_client()

    # ── Fetch open positions ────────────────────────────────────────────────────
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        positions = portfolio_res.data or []
    except Exception as e:
        notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
        print(f"❌ Could not fetch portfolio positions: {e}")
        return

    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date()
    # Track intraday prices per-ticker in memory so hwm_date comparisons are
    # relative to the last price we polled (not the stored HWM price, which
    # IBKR now owns).
    intraday_peak: dict = {}

    for pos in positions:
        ticker     = pos["ticker"]
        shares     = int(pos["shares"])
        buy_price  = float(pos["buy_price"])
        buy_reason = pos.get("buy_reason", "Unknown")
        try:
            buy_date = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
        except Exception:
            buy_date = datetime.datetime.now(datetime.timezone.utc)

        current_price = get_live_price(ticker)
        if current_price <= 0:
            print(f"   ⚠️ Could not fetch price for {ticker} — skipping this cycle.")
            continue

        print(f"   Monitoring {ticker}: Current: ${current_price:.2f} | Entry: ${buy_price:.2f} "
              f"| IBKR Trail: {STOP_LOSS_PCT*100:.0f}%")

        # ── Update hwm_date when a new intraday high is seen ───────────────────
        # IBKR tracks the HWM price tick-by-tick for the trailing stop.
        # We only record the DATE so we can detect plateau (N days without a new high).
        # Compare to our last-polled peak (in-memory), defaulting to buy_price.
        prev_peak = intraday_peak.get(ticker, buy_price)
        if current_price > prev_peak:
            intraday_peak[ticker] = current_price
            try:
                client.table("portfolio_positions").update(
                    {"hwm_date": today_ny.isoformat()}
                ).eq("ticker", ticker).execute()
            except Exception as e:
                notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
                print(f"   ⚠️ Could not update hwm_date for {ticker}: {e}")

        # ── Self-healing: ensure trailing stop exists for this position ─────────
        # GTC trailing stops survive IBKR gateway restarts, but may be absent for
        # positions opened before this feature or after a full account reset.
        _open_sells = [
            t for t in ib.openTrades()
            if t.contract.symbol == ticker
            and t.order.action == 'SELL'
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')
        ]

        if len(_open_sells) < 1:
            print(f"   🔧 {ticker}: No trailing stop in IBKR — re-placing (self-healing).")
            try:
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _heal_contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_heal_contract)
                # Anchor from current price — IBKR tracks HWM from here onward.
                # Slightly conservative vs. the true peak but acceptable for this
                # rare self-healing edge case.
                _new_group = place_trailing_stop(ib, _heal_contract, shares, STOP_LOSS_PCT)
                client.table("portfolio_positions").update(
                    {"oca_group": _new_group}
                ).eq("ticker", ticker).execute()
            except Exception as _heal_err:
                notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", _heal_err)
                print(f"   ⚠️ Self-healing failed for {ticker}: {_heal_err}")

        # Trailing stop is fully managed by IBKR. reconcile_with_ibkr() (Case 1)
        # detects when it fires and archives the position to trade_history.

        # ── Moving Average Exit Check ──────────────────────────────────────────
        if EXIT_MA_TRIGGER_ENABLED:
            is_ma_window = True
            if EXIT_MA_EOD_ONLY:
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

    # ── EOD Plateau Rotation (3:45–4:00 PM ET) ────────────────────────────────
    # After the per-position loop: if portfolio is full and a fresh breakout trigger
    # exists, sell the most-stalled position (longest since its last HWM) to free a
    # slot. The replacement buy happens the next morning via run_market_open_buys().
    now_eod = datetime.datetime.now(tz)
    is_eod_window = (now_eod.hour == 15 and now_eod.minute >= 45)

    if is_eod_window and len(positions) >= MAX_POSITIONS:
        try:
            recent_date = (datetime.datetime.now(tz) - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            triggers_res = client.table("daily_triggers") \
                .select("ticker") \
                .gte("triggered_at", recent_date) \
                .execute()
            held_tickers = {p["ticker"] for p in positions}
            fresh_tickers = {t["ticker"] for t in (triggers_res.data or [])} - held_tickers
        except Exception:
            fresh_tickers = set()

        if fresh_tickers:
            today_eod = datetime.datetime.now(tz).date()
            plateau_candidates = []
            for p in positions:
                hwm_date_str = p.get("hwm_date")
                if not hwm_date_str:
                    continue
                hwm_d = datetime.date.fromisoformat(hwm_date_str)
                days_since_hwm = (today_eod - hwm_d).days
                if days_since_hwm >= PLATEAU_DAYS:
                    plateau_candidates.append((days_since_hwm, p))

            if plateau_candidates:
                plateau_candidates.sort(reverse=True)  # most stalled first
                days_stalled, worst = plateau_candidates[0]
                wticker     = worst["ticker"]
                wshares     = int(worst["shares"])
                wbuy_price  = float(worst["buy_price"])
                wbuy_date   = datetime.datetime.fromisoformat(worst["buy_date"].replace('Z', '+00:00'))
                wbuy_reason = worst.get("buy_reason", "Unknown")
                wprice      = get_live_price(wticker)
                replacement = next(iter(fresh_tickers))
                reason = (
                    f"Plateau Rotation — no new HWM in {days_stalled} days. "
                    f"Freeing slot for fresh breakout ({replacement})."
                )
                print(f"📉 Plateau Rotation: {wticker} ({days_stalled}d stalled) → slot freed for {replacement}")
                cancel_ticker_sell_orders(ib, wticker)
                ib.sleep(1)
                execute_sell(ib, client, wticker, wshares, wbuy_price,
                             wbuy_date, wbuy_reason, wprice, reason)


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
        order.account = get_ibkr_account(ib)
        trade = ib.placeOrder(contract, order)
        
        print(f"   Placing market sell order for {shares} shares of {ticker}...")
        
        # Wait up to 60 seconds for fill
        for _ in range(30):
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


def has_bought_today(client, today_str: str) -> bool:
    """Checks Supabase to see if any confirmed trades were placed today."""
    try:
        # Check active portfolio positions for any buys today
        res = client.table("portfolio_positions").select("buy_date").gte("buy_date", today_str).execute()
        if res.data:
            return True
        # Check trade history in case a position was bought and stopped out same day
        res = client.table("trade_history").select("buy_date").gte("buy_date", today_str).execute()
        if res.data:
            return True
        return False
    except Exception as e:
        notifier.notify_exception("has_bought_today() — execution_agent.py", e)
        # Default to True on DB error to prevent accidental spam / duplicate runs
        print(f"❌ Error checking DB for today's buys: {e}. Assuming True.")
        return True

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
        notifier.notify_exception(f"main_loop() — execution_agent.py", e)
        print(f"❌ Failed to connect to IBKR Gateway: {e}")
        print("   Ensure the ib-gateway container is running and API ports are open.")
        sys.exit(1)

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
                    ib.sleep(900)
                    continue

                is_market_open = (
                    (now.hour == 9 and now.minute >= 30)
                    or (10 <= now.hour < 16)
                )

                # 1. Daily Buy Check (No Window Restriction)
                # Ensure the buy check continues running intraday until a successful buy occurs
                # (or portfolio is full). This naturally creates an intraday failsafe.
                if is_market_open and not has_bought_today(get_supabase_client(), today_str):
                    reconcile_with_ibkr(ib)   # Sync before placing any new buys
                    run_market_open_buys(ib)
                    ib.sleep(900)
                    continue

                # 2. Intraday monitoring during market hours
                if is_market_open:
                    reconcile_with_ibkr(ib)   # Sync every 15 min — catches manual TWS trades
                    monitor_portfolio_intraday(ib)
                    ib.sleep(900)
                    continue

            # ── Smart sleep: wake exactly at 9:30 AM ET ─────────────────────────────
            # Compute seconds until next 9:30 AM ET (today or tomorrow if already past)
            next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            if now >= next_open:
                # After today's open/close — aim for tomorrow, skip weekends
                next_open += datetime.timedelta(days=1)
                while next_open.weekday() >= 5:  # skip Sat(5) / Sun(6)
                    next_open += datetime.timedelta(days=1)

            secs_to_open = int((next_open - now).total_seconds())

            if secs_to_open <= 5400:  # within 90 min of next open → sleep precisely
                sleep_secs = max(secs_to_open + 30, 60)  # +30s buffer, never < 1 min
                print(f"⏰ Market opens at 9:30 AM ET — sleeping {sleep_secs // 60}m {sleep_secs % 60}s (until {next_open.strftime('%H:%M:%S')})")
            else:
                sleep_secs = 1800  # check every 30 min during deep off-hours
                print(f"😴 Market is closed. Checking in 30 min... (Current Time: {now.strftime('%H:%M:%S')})")

            ib.sleep(sleep_secs)
            
        except KeyboardInterrupt:
            print("\nShutting down execution agent.")
            ib.disconnect()
            break
        except ConnectionError as loop_err:
            if "Socket disconnect" in str(loop_err):
                print(f"⚠️ IBKR Socket disconnected (likely daily server reset). Will attempt to reconnect...")
                # Do NOT send Telegram alert for this expected daily reset
            else:
                print(f"❌ Connection error in main execution loop: {loop_err}")
                notifier.notify_exception("main_loop() — execution_agent.py", loop_err)
            time.sleep(60)   # use time.sleep — ib.sleep() throws on a dead socket
        except Exception as loop_err:
            print(f"❌ Error in main execution loop: {loop_err}")
            notifier.notify_exception("main_loop() — execution_agent.py", loop_err)
            time.sleep(60)   # use time.sleep — ib.sleep() throws on a dead socket
            
        # Reconnection failsafe
        if not ib.isConnected():
            print("🔄 Reconnecting to IB Gateway...")
            try:
                ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
                print("✅ Reconnected to IBKR Gateway successfully!")
            except Exception as e:
                print(f"❌ Reconnection failed: {e}")
                time.sleep(60)   # wait before next reconnect attempt

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
