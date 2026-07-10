import sys
import asyncio
from ib_insync import IB, Stock, MarketOrder

async def main():
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', 7497, clientId=999)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    contract = Stock('AEP', 'SMART', 'USD')
    ib.qualifyContracts(contract)
    print(f"Qualified contract: {contract}")

    order = MarketOrder('BUY', 10)
    order.account = [acc for acc in ib.managedAccounts() if acc.startswith('DU')][0]
    
    trade = ib.placeOrder(contract, order)
    print(f"Placed order. Waiting up to 10s...")
    
    for i in range(10):
        await asyncio.sleep(1)
        print(f"[{i}s] Status: {trade.orderStatus.status}")
        if trade.log:
            print(f"  Log: {trade.log[-1].message}")
        if trade.orderStatus.status == 'Filled':
            break
            
    print("Cancelling...")
    ib.cancelOrder(order)
    await asyncio.sleep(2)
    print("Done")
    ib.disconnect()

asyncio.run(main())
