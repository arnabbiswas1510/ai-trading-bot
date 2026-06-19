"""
Order test - fixed script, no delayedBid attribute.
Tests MarketOrder + explicit DAY, LimitOrder, and IOC.
"""
import os, sys, time
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

errors_seen = []
def on_error(reqId, errorCode, errorString, contract, *args):
    msg = f"ERR reqId={reqId} code={errorCode}: {errorString}"
    errors_seen.append(msg)
    print(f"  [ERR] {msg}")

print("=== Order Tests ===")
ib = IB()
ib.errorEvent += on_error

ib.connect("ib-gateway", 4004, clientId=7)
print("Connected.")
ib.sleep(3)

# Enable delayed market data
ib.reqMarketDataType(4)
ib.sleep(1)

acct = {v.tag: v.value for v in ib.accountValues() if v.currency == "USD"}
print(f"Net=${float(acct.get('NetLiquidation',0)):,.2f}  Cash=${float(acct.get('AvailableFunds',0)):,.2f}")
print(f"TradingTypeSuspended={acct.get('TradingTypeSuspended','(not found)')!r}")

contract = Stock("INTC", "SMART", "USD")
ib.qualifyContracts(contract)
print(f"\nContract qualified: {contract}")

def try_order(label, order):
    errors_seen.clear()
    trade = ib.placeOrder(contract, order)
    ib.sleep(10)
    print(f"\n--- {label} ---")
    print(f"  Status: {trade.orderStatus.status}  Filled: {trade.orderStatus.filled}")
    for log in trade.log:
        print(f"  Log: {log.status!r} | err={log.errorCode} | {log.message!r}")
    print(f"  Errors: {errors_seen or '(none)'}")
    # Cancel if open
    if trade.orderStatus.status not in ("Filled","Cancelled","ApiCancelled","Inactive"):
        ib.cancelOrder(trade.order)
        ib.sleep(2)
    return trade.orderStatus.status == "Filled"

# Test 1: MarketOrder + explicit DAY
o1 = MarketOrder("BUY", 10)
o1.tif = "DAY"
if try_order("MKT tif=DAY", o1):
    print("  SUCCESS")
else:
    # Test 2: LimitOrder $140 + DAY (above ask)
    o2 = LimitOrder("BUY", 10, 140.0)
    o2.tif = "DAY"
    if try_order("LMT $140 tif=DAY", o2):
        print("  SUCCESS")
    else:
        # Test 3: GTC order
        o3 = LimitOrder("BUY", 10, 140.0)
        o3.tif = "GTC"
        if try_order("LMT $140 tif=GTC", o3):
            print("  SUCCESS")
        else:
            # Test 4: IOC order
            o4 = LimitOrder("BUY", 10, 140.0)
            o4.tif = "IOC"
            try_order("LMT $140 tif=IOC", o4)

pos = {p.contract.symbol: p for p in ib.portfolio()
       if p.contract.secType == "STK" and int(p.position) > 0}
print(f"\nPortfolio: {list(pos.keys())}")
ib.disconnect()
print("Done.")
