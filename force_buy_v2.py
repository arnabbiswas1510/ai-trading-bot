import os, sys, datetime, time
from zoneinfo import ZoneInfo
from ib_insync import IB, Stock, MarketOrder
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

print("=" * 62)
print("  FORCE BUY v2 - Historical Data Pricing (clientId=81)")
print("=" * 62)

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

STOP_LOSS_PCT     = float(os.environ.get("STOP_LOSS_PCT", "7"))    / 100
PROFIT_TARGET_PCT = float(os.environ.get("PROFIT_TARGET_PCT","20")) / 100
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", "4"))
POSITION_SIZE_PCT = float(os.environ.get("POSITION_SIZE_PCT","0.20"))
PIVOT_EXT_MAX     = 0.05

ib = IB()
try:
    ib.connect("ib-gateway", 4004, clientId=81)
    print("Connected to IBKR (clientId=81)")
except Exception as e:
    print(f"IBKR connect failed: {e}")
    sys.exit(1)
ib.sleep(5)

portfolio = {p.contract.symbol: p for p in ib.portfolio()
             if p.contract.secType == "STK" and int(p.position) > 0}
print(f"IBKR portfolio: {list(portfolio.keys())}")

acct = {v.tag: v.value for v in ib.accountValues() if v.currency == "USD"}
net_liq = float(acct.get("NetLiquidation", 0))
cash    = float(acct.get("AvailableFunds", acct.get("TotalCashValue", 0)))
print(f"NetLiq: ${net_liq:,.2f}  Available: ${cash:,.2f}")

held = {p["ticker"] for p in (db.table("portfolio_positions").select("ticker").execute().data or [])}
slots_free = MAX_POSITIONS - len(held)
print(f"Supabase positions: {held}  Slots free: {slots_free}")

if slots_free <= 0:
    print("No free slots. Exiting.")
    ib.disconnect()
    sys.exit(0)

mtrig = db.table("momentum_triggers").select("ticker,close_price,triggered_at").execute().data or []
if mtrig:
    latest_date = sorted(set(m["triggered_at"] for m in mtrig), reverse=True)[0]
    mtrig = [m for m in mtrig if m["triggered_at"] == latest_date]
    print(f"Momentum triggers ({latest_date}): {[m['ticker'] for m in mtrig]}")
else:
    print("No momentum triggers found.")
    ib.disconnect()
    sys.exit(0)

def get_price(ib, contract):
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1
        )
        if bars:
            price = float(bars[-1].close)
            print(f"    Historical close: ${price:.2f} ({bars[-1].date})")
            return price
    except Exception as e:
        print(f"    reqHistoricalData failed: {e}")
    return 0.0

bought = 0
for trig in mtrig:
    ticker  = trig["ticker"]
    pivot   = float(trig["close_price"])
    max_buy = round(pivot * (1 + PIVOT_EXT_MAX), 2)

    if ticker in held:
        print(f"\n{ticker}: already held - skip")
        continue
    if slots_free - bought <= 0:
        print(f"\n{ticker}: no slots left")
        break

    print(f"\n{'='*50}")
    print(f"{ticker}: pivot=${pivot:.2f}  max_buy=${max_buy:.2f}")

    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    price = get_price(ib, contract)
    if price <= 0:
        print(f"  No price for {ticker} - skip")
        continue

    if price > max_buy:
        print(f"  ${price:.2f} > max_buy ${max_buy:.2f} - skip (outside zone)")
        continue

    position_cash = min(net_liq * POSITION_SIZE_PCT, cash * 0.95)
    shares = max(1, int(position_cash / price))
    cost   = shares * price
    print(f"  Sizing: {shares} sh @ ${price:.2f}  cost=${cost:,.2f}")

    if cost > cash:
        print(f"  Insufficient cash - skip")
        continue

    order = MarketOrder("BUY", shares)
    trade = ib.placeOrder(contract, order)
    print(f"  Order placed: BUY {shares} {ticker} MKT")

    fill_price = 0.0
    for _ in range(30):
        ib.sleep(1)
        st = trade.orderStatus.status
        if st == "Filled":
            fill_price = float(trade.orderStatus.avgFillPrice or price)
            print(f"  FILLED: {shares} sh @ ${fill_price:.2f}")
            break
        if st in ("Cancelled", "ApiCancelled", "Inactive"):
            print(f"  Order {st} - skip")
            break
    else:
        print(f"  Not filled in 30s ({trade.orderStatus.status}) - skip")
        continue

    if fill_price <= 0:
        continue

    ib.sleep(2)
    fresh = {p.contract.symbol: p for p in ib.portfolio()
             if p.contract.secType == "STK" and int(p.position) > 0}
    if ticker not in fresh:
        print(f"  WARNING: {ticker} not in portfolio after fill - skip")
        continue

    stop_loss     = round(fill_price * (1 - STOP_LOSS_PCT), 2)
    profit_target = round(fill_price * (1 + PROFIT_TARGET_PCT), 2)
    buy_date      = datetime.datetime.now(datetime.timezone.utc).isoformat()

    pos_data = {
        "ticker":          ticker,
        "shares":          int(fresh[ticker].position),
        "buy_price":       fill_price,
        "buy_date":        buy_date,
        "buy_reason":      f"Momentum force buy (pivot={pivot})",
        "buy_source":      "momentum_triggers",
        "stop_loss":       stop_loss,
        "profit_target":   profit_target,
        "is_power_hold":   False,
        "high_water_mark": fill_price,
    }
    try:
        db.table("portfolio_positions").insert(pos_data).execute()
        print(f"  Supabase: SL={stop_loss}  PT={profit_target}")
        held.add(ticker)
        cash -= cost
        bought += 1
    except Exception as ex:
        print(f"  Supabase insert error: {ex}")

print(f"\n{'='*62}")
print(f"Force buy done. Bought: {bought} position(s)")

pos_final = db.table("portfolio_positions").select("ticker,shares,buy_price,buy_source").execute().data or []
print("\nFinal portfolio_positions:")
for p in pos_final:
    print(f"  {p['ticker']}  {p['shares']}sh @ ${p['buy_price']}  [{p.get('buy_source','')}]")

ib.disconnect()
print("Disconnected.")
