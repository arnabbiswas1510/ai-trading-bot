"""
Top up NBIX position on 2026-07-15 with settled proceeds from today's sells.
Buys ~143 additional shares to bring NBIX to a full ~$25k position.
"""
import os, datetime, sys
from zoneinfo import ZoneInfo

if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# DATE GUARD — only runs on 2026-07-15
ALLOWED_DATE = "2026-07-15"
tz = ZoneInfo("America/New_York")
today = datetime.datetime.now(tz).date().isoformat()
if today != ALLOWED_DATE:
    print(f"ABORTED: This script is only valid on {ALLOWED_DATE}. Today is {today}.")
    sys.exit(0)

from ib_insync import IB, Stock, Order
from supabase import create_client

SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 0.07))
IB_HOST       = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT       = int(os.getenv("IB_GATEWAY_PORT", 4000))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Connecting to IBKR...")
ib = IB()
ib.connect(IB_HOST, IB_PORT, clientId=1, timeout=15)
acct = [a for a in ib.managedAccounts() if not a.startswith("DU")][0]
print(f"Connected: {acct}")

# Get settled available funds (T+1 from yesterday's sells)
ib.reqAccountSummary()
ib.sleep(2)
cash = 0.0
for av in ib.accountValues():
    if av.tag == "AvailableFunds" and av.currency == "USD":
        cash = float(av.value)
        break
print(f"Available (settled) funds: ${cash:,.2f}")

# Target: bring NBIX to full $25k position
existing = supabase.table("portfolio_positions").select("shares,buy_price").eq("ticker", "NBIX").execute()
existing_shares = existing.data[0]["shares"] if existing.data else 0
existing_cost   = existing.data[0]["buy_price"] if existing.data else 0
print(f"Existing NBIX: {existing_shares} shares @ ${existing_cost:.2f}")

TARGET_POSITION_VALUE = 25000.0
current_value = existing_shares * existing_cost
remaining_to_deploy = min(TARGET_POSITION_VALUE - current_value, cash * 0.99)  # 1% buffer
print(f"Deploying: ${remaining_to_deploy:,.2f}")

if remaining_to_deploy < 1000:
    print("Nothing significant to deploy. Exiting.")
    ib.disconnect()
    sys.exit(0)

contract = Stock("NBIX", "SMART", "USD")
ib.qualifyContracts(contract)

# Get price
ib.reqMktData(contract, "", False, False)
ib.sleep(3)
ticker = ib.ticker(contract)
ask = ticker.ask if ticker.ask and ticker.ask == ticker.ask and ticker.ask > 0 else 0
last = ticker.last if ticker.last and ticker.last == ticker.last and ticker.last > 0 else 0
price = ask if ask > 0 else (last if last > 0 else 170.00)
ib.cancelMktData(contract)
limit_price = round(price + 0.10, 2)
shares = int(remaining_to_deploy / limit_price)
print(f"NBIX price ~${price:.2f} | Buying {shares} shares @ limit ${limit_price:.2f}")

if shares <= 0:
    print("Cannot compute shares. Exiting.")
    ib.disconnect()
    sys.exit(1)

order = Order()
order.action        = "BUY"
order.orderType     = "LMT"
order.totalQuantity = shares
order.lmtPrice      = limit_price
order.tif           = "DAY"
order.account       = acct
order.transmit      = True

trade = ib.placeOrder(contract, order)
print("Order placed. Waiting up to 90s for fill...")

for i in range(90):
    ib.sleep(1)
    if trade.orderStatus.status == "Filled":
        break
    if trade.orderStatus.status in ("Cancelled", "Inactive"):
        for e in trade.log:
            if e.message:
                print(f"  {e.message}")
        break

if trade.orderStatus.status == "Filled":
    fill_px  = round(trade.orderStatus.avgFillPrice, 2)
    filled_q = int(trade.orderStatus.filled)
    stop_px  = round(fill_px * (1 - STOP_LOSS_PCT), 2)
    print(f"FILLED: {filled_q} shares @ ${fill_px:.2f} | Stop: ${stop_px:.2f}")

    # Update Supabase — increase NBIX shares, update avg cost
    total_shares = existing_shares + filled_q
    total_cost   = (existing_shares * existing_cost + filled_q * fill_px) / total_shares
    supabase.table("portfolio_positions").update({
        "shares":    total_shares,
        "buy_price": round(total_cost, 2),
        "stop_loss": round(fill_px * (1 - STOP_LOSS_PCT), 2),
        "buy_reason": f"CANSLIM Breakout — top-up after T+1 settlement ({filled_q} shares added)",
    }).eq("ticker", "NBIX").execute()

    # Trailing stop for new shares
    from ib_insync import Order as IBOrder
    s = IBOrder()
    s.action = "SELL"; s.orderType = "TRAIL"; s.totalQuantity = filled_q
    s.trailingPercent = round(STOP_LOSS_PCT * 100, 2); s.tif = "GTC"; s.account = acct
    ib.placeOrder(contract, s)

    print(f"NBIX total position: {total_shares} shares @ avg ${total_cost:.2f}")
    print("Trailing stop placed. Supabase updated.")

    try:
        import requests
        tok  = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cids = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
        msg  = (f"🟢 *NBIX TOP-UP complete*\n"
                f"Added {filled_q} shares @ ${fill_px:.2f}\n"
                f"Total: {total_shares} shares @ avg ${total_cost:.2f}\n"
                f"Stop: ${stop_px:.2f} (7% trail)")
        for cid in cids:
            if cid.strip():
                requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": cid.strip(), "text": msg, "parse_mode": "Markdown"}, timeout=8)
    except Exception as e:
        print(f"Telegram error: {e}")
else:
    print(f"Order not filled: {trade.orderStatus.status}")

ib.disconnect()
