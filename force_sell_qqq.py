import time
import paramiko
import sys

script_content = """
import os, sys, datetime, time
from zoneinfo import ZoneInfo
from ib_insync import IB, Stock, MarketOrder
from supabase import create_client

print("=" * 62)
print("  FORCE SELL QQQ")
print("=" * 62)

ib = IB()
try:
    ib.connect("ib-gateway", 4004, clientId=99)
    print("Connected to IBKR (clientId=99)")
except Exception as e:
    print(f"IBKR connect failed: {e}")
    sys.exit(1)

ib.sleep(5)

portfolio = {p.contract.symbol: p for p in ib.portfolio()
             if p.contract.secType == "STK" and int(p.position) > 0}
print(f"IBKR portfolio: {list(portfolio.keys())}")

if "QQQ" not in portfolio:
    print("QQQ not found in IBKR portfolio.")
    ib.disconnect()
    sys.exit(0)

pos = portfolio["QQQ"]
shares = int(pos.position)
print(f"Found QQQ position: {shares} shares. Proceeding to sell...")

# Check for open sell orders and cancel them
open_trades = ib.reqAllOpenOrders()
for trade in open_trades:
    if trade.contract.symbol == "QQQ" and trade.order.action == "SELL":
        print(f"Cancelling open sell order for QQQ: {trade.order.orderId}")
        ib.cancelOrder(trade.order)

ib.sleep(2)

contract = Stock("QQQ", "SMART", "USD")
ib.qualifyContracts(contract)

order = MarketOrder("SELL", shares)
trade = ib.placeOrder(contract, order)
print(f"Order placed: SELL {shares} QQQ MKT")

for _ in range(30):
    ib.sleep(1)
    st = trade.orderStatus.status
    if st == "Filled":
        fill_price = float(trade.orderStatus.avgFillPrice)
        print(f"FILLED: {shares} sh @ ${fill_price:.2f}")
        break
    if st in ("Cancelled", "ApiCancelled", "Inactive"):
        print(f"Order {st} - skip")
        break
else:
    print(f"Not filled in 30s ({trade.orderStatus.status}) - skip")

ib.disconnect()
print("Disconnected.")
"""

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.1.50", username="root", password="paro", timeout=10)
    
    print("SSH connected. Writing script to /tmp/force_sell_qqq.py...")
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/force_sell_qqq.py", "w") as f:
        f.write(script_content)
    sftp.close()
    
    print("Copying to container...")
    ssh.exec_command("docker cp /tmp/force_sell_qqq.py execution-agent:/tmp/force_sell_qqq.py")
    time.sleep(1)
    
    print("Executing script inside execution-agent container...")
    stdin, stdout, stderr = ssh.exec_command("docker exec execution-agent python /tmp/force_sell_qqq.py")
    
    print("STDOUT:")
    print(stdout.read().decode())
    print("STDERR:")
    print(stderr.read().decode())
    
    # Optional: cleanup
    ssh.exec_command("docker exec execution-agent rm /tmp/force_sell_qqq.py")
    ssh.exec_command("rm /tmp/force_sell_qqq.py")
    
    ssh.close()
except Exception as e:
    print(f"Failed: {e}")
