from ib_insync import IB
import execution_agent
import os
import sys

# Ensure stdout uses UTF-8 to prevent console encode crashes under Windows
sys.stdout.reconfigure(encoding='utf-8')

# Load env variables from .env if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

ib = IB()
try:
    print("Connecting to IB Gateway inside docker bridge network...")
    ib.connect("ib-gateway", 4004, clientId=88)
    print("✅ Connected to IBKR Gateway successfully!")
    
    print("🚀 Triggering run_market_open_buys() routine...")
    execution_agent.run_market_open_buys(ib)
    
    ib.disconnect()
    print("✅ Completed manual trigger execution successfully!")
except Exception as e:
    print(f"❌ Error executing buy triggers: {e}")
