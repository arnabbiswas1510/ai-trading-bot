"""
HMDS warmup before MarketOrder:
1. Call reqHistoricalData() to force HMDS to wake up
2. Wait for response (confirms HMDS is active)
3. Place MarketOrder normally - should fill now
"""
import os, sys, datetime, time
from ib_insync import IB, Stock, MarketOrder
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

print("=" * 62)
print("  FORCE BUY - HMDS warmup then MarketOrder (clientId=10)")
print("=" * 62)

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

STOP_LOSS_PCT     = float(os.environ.get("STOP_LOSS_PCT",    "7"))  / 100
PROFIT_TARGET_PCT = float(os.environ.get("PROFIT_TARGET_PCT","20")) / 100
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS",      "4"))
POSITION_SIZE_PCT = float(os.environ.get("POSITION_SIZE_PCT","0.20"))
MAX_PIVOT_EXT     = float(os.environ.get("MAX_PIVOT_EXTENSION","0.05"))

ib = IB()

def on_error(reqId, errorCode, errorString, contract, *args):
    if errorCode not in (2104, 2106, 2107, 2108, 2158, 2119):  # suppress info msgs
        print(f"  [ERR {errorCode}] {errorString[:100]}")

ib.errorEvent += on_error
ib.connect("ib-gateway", 4004, clientId=10)
print("Connected (clientId=10)")
ib.sleep(3)

# ── STEP 1: Warm up HMDS ────────────────────────────────────────────────
print("\n>> Step 1: Warming up HMDS via reqHistoricalData...")
warmup_contract = Stock("SPY", "SMART", "USD")
ib.qualifyContracts(warmup_contract)
bars = ib.reqHistoricalData(
    warmup_contract,
    endDateTime="",
    durationStr="1 D",
    barSizeSetting="1 min",
    whatToShow="TRADES",
    useRTH=True,
    formatDate=1
)
if bars:
    print(f"   HMDS active! SPY last bar: {bars[-1].date} close={bars[-1].close}")
else:
    print("   HMDS warmup returned no bars - will try anyway")
ib.sleep(2)

# ── STEP 2: Account / portfolio state ───────────────────────────────────
portfolio = {p.contract.symbol: p for p in ib.portfolio()
             if p.contract.secType == "STK" and int(p.position) > 0}
print(f"\nIBKR portfolio: {list(portfolio.keys())}")

acct    = {v.tag: v.value for v in ib.accountValues() if v.currency == "USD"}
net_liq = float(acct.get("NetLiquidation", 0))
cash    = float(acct.get("AvailableFunds", acct.get("TotalCashValue", 0)))
print(f"NetLiq: ${net_liq:,.2f}  Available: ${cash:,.2f}")

held        = {p["ticker"] for p in (db.table("portfolio_positions").select("ticker").execute().data or [])}
stock_count = sum(1 for p in (db.table("portfolio_positions").select("buy_source").execute().data or [])
                  if p.get("buy_source") != "etf_parking")
slots_free  = MAX_POSITIONS - stock_count
print(f"Supabase: {held}  Slots free: {slots_free}")

if slots_free <= 0:
    print("No slots.")
    ib.disconnect()
    sys.exit(0)

mtrig = db.table("momentum_triggers").select("*").execute().data or []
if mtrig:
    latest_date = sorted(set(m["triggered_at"] for m in mtrig), reverse=True)[0]
    mtrig = [m for m in mtrig if m["triggered_at"] == latest_date]
    print(f"Momentum triggers ({latest_date}): {[m['ticker'] for m in mtrig]}")

# ── STEP 3: Place MarketOrders now that HMDS is warm ────────────────────
bought = 0
for trig in mtrig:
    ticker = trig["ticker"]
    pivot  = float(trig["close_price"])

    if ticker in held:
        print(f"\n{ticker}: already held - skip")
        continue
    if slots_free - bought <= 0:
        break

    # Warm up HMDS specifically for this ticker too
    print(f"\n{'='*50}")
    print(f"{ticker}: pivot=${pivot}")
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)
    bars2 = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="1 D",
        barSizeSetting="1 day", whatToShow="TRADES",
        useRTH=True, formatDate=1
    )
    live_price = float(bars2[-1].close) if bars2 else pivot
    print(f"  HMDS close: ${live_price:.2f}")

    ext_pct = (live_price - pivot) / pivot if pivot > 0 else 0
    if ext_pct > MAX_PIVOT_EXT:
        print(f"  {ext_pct*100:.1f}% above pivot — extended. Skip.")
        continue

    position_cash = min(net_liq * POSITION_SIZE_PCT, cash * 0.95)
    shares = max(1, int(position_cash / live_price))
    cost   = shares * live_price
    print(f"  Sizing: {shares} sh @ ~${live_price:.2f}  cost=${cost:,.2f}")

    if cost > cash:
        print(f"  Insufficient cash - skip")
        continue

    # Place MarketOrder (HMDS is now warm, fill simulation should work)
    order = MarketOrder("BUY", shares)
    trade = ib.placeOrder(contract, order)
    print(f"  Order placed: BUY {shares} {ticker} MKT")

    # Wait 20s and verify via portfolio (same pattern as force_buy.py)
    ib.sleep(8)
    pos_check = {p.contract.symbol: p for p in ib.portfolio()
                 if p.contract.secType == "STK" and int(p.position) > 0}
    if ticker not in pos_check:
        ib.sleep(5)
        pos_check = {p.contract.symbol: p for p in ib.portfolio()
                     if p.contract.secType == "STK" and int(p.position) > 0}

    if ticker not in pos_check:
        print(f"  {ticker} not in portfolio after 13s. Status={trade.orderStatus.status}. Skip.")
        continue

    fill_price    = round(pos_check[ticker].averageCost, 2)
    actual_shares = int(pos_check[ticker].position)
    stop_loss     = round(fill_price * (1 - STOP_LOSS_PCT), 2)
    profit_target = round(fill_price * (1 + PROFIT_TARGET_PCT), 2)
    print(f"  FILLED: {actual_shares} sh @ ${fill_price:.2f}")

    pos_data = {
        "ticker":          ticker,
        "shares":          actual_shares,
        "buy_price":       fill_price,
        "buy_date":        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "buy_reason":      f"Momentum force buy (pivot=${pivot})",
        "buy_source":      "momentum_triggers",
        "stop_loss":       stop_loss,
        "profit_target":   profit_target,
        "is_power_hold":   False,
        "high_water_mark": fill_price,
    }
    try:
        db.table("portfolio_positions").insert(pos_data).execute()
        print(f"  Supabase: SL=${stop_loss}  PT=${profit_target}  -> OK")
        held.add(ticker)
        cash -= cost
        bought += 1
    except Exception as ex:
        print(f"  Supabase error: {ex}")

print(f"\n{'='*62}")
print(f"DONE. Bought: {bought}")
pos_final = db.table("portfolio_positions").select("ticker,shares,buy_price,buy_source").execute().data or []
print("Final portfolio_positions:")
for p in pos_final:
    print(f"  {p['ticker']}  {p['shares']}sh @ ${p['buy_price']}  [{p.get('buy_source','')}]")
ib.disconnect()
