import paramiko, time

HOST = "192.168.1.50"
USER = "root"
PASS = "paro"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=10)

# Run the reconcile directly inside the execution-agent container
# using the SAME logic as the fixed reconcile_with_ibkr (ib.portfolio())
reconcile = '''
import os, datetime
from supabase import create_client
from ib_insync import IB

STOP_LOSS_PCT    = float(os.environ.get("STOP_LOSS_PCT", 0.07))
PROFIT_TARGET_PCT = float(os.environ.get("PROFIT_TARGET_PCT", 0.25))

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
ib = IB()
ib.connect("ib-gateway", 4004, clientId=99)

# Use portfolio() — same fix as in the updated reconcile_with_ibkr()
ib_raw = ib.portfolio()
ib_map = {
    p.contract.symbol: p
    for p in ib_raw
    if p.contract.secType == "STK" and int(p.position) > 0
}
print(f"IBKR portfolio (STK only): {list(ib_map.keys())}")

supabase_rows  = db.table("portfolio_positions").select("*").execute().data or []
supabase_map   = {r["ticker"]: r for r in supabase_rows}
print(f"Supabase positions: {list(supabase_map.keys())}")

changes = 0

# Case 1: In Supabase NOT in IBKR -> delete
for ticker in set(supabase_map) - set(ib_map):
    print(f"  Case 1: {ticker} in Supabase but not IBKR -> removing")
    db.table("portfolio_positions").delete().eq("ticker", ticker).execute()
    changes += 1

# Case 2: In IBKR NOT in Supabase -> insert
for ticker in set(ib_map) - set(supabase_map):
    pos = ib_map[ticker]
    avg_cost = round(float(pos.averageCost), 2)
    shares   = int(pos.position)
    stop     = round(avg_cost * (1 - STOP_LOSS_PCT), 2)
    target   = round(avg_cost * (1 + PROFIT_TARGET_PCT), 2)
    print(f"  Case 2: {ticker} in IBKR but not Supabase -> inserting")
    db.table("portfolio_positions").insert({
        "ticker":          ticker,
        "shares":          shares,
        "buy_price":       avg_cost,
        "high_water_mark": avg_cost,
        "stop_loss":       stop,
        "profit_target":   target,
        "buy_reason":      "Manual IBKR order (reconciled)",
        "buy_source":      "daily_triggers",
        "is_power_hold":   False,
    }).execute()
    print(f"    -> Inserted: {shares} shares @ ${avg_cost} | stop=${stop} | target=${target}")
    changes += 1

# Case 3: In both, share count mismatch -> update
for ticker in set(ib_map) & set(supabase_map):
    ib_shares = int(ib_map[ticker].position)
    db_shares = int(supabase_map[ticker]["shares"])
    if ib_shares != db_shares:
        print(f"  Case 3: {ticker} share mismatch IBKR={ib_shares} vs DB={db_shares} -> updating")
        db.table("portfolio_positions").update({"shares": ib_shares}).eq("ticker", ticker).execute()
        changes += 1

print(f"\\nChanges applied: {changes}")
print("\\nFinal Supabase state:")
for r in db.table("portfolio_positions").select("*").execute().data:
    print(f"  {r['ticker']}  shares={r['shares']}  buy=${r['buy_price']}  stop=${r['stop_loss']}  target=${r['profit_target']}")

ib.disconnect()
'''

sftp = client.open_sftp()
sftp.putfo(__import__('io').BytesIO(reconcile.encode()), "/tmp/reconcile_fix.py")
sftp.close()

chan = client.get_transport().open_session()
chan.settimeout(30)
chan.exec_command("docker cp /tmp/reconcile_fix.py execution-agent:/app/reconcile_fix.py && "
                  "docker exec execution-agent python /app/reconcile_fix.py 2>&1")
out = b""
deadline = time.time() + 30
while time.time() < deadline:
    if chan.recv_ready():
        out += chan.recv(65536)
    if chan.exit_status_ready():
        while chan.recv_ready():
            out += chan.recv(65536)
        break
    time.sleep(0.3)
print(out.decode("utf-8", errors="replace").encode("ascii", errors="replace").decode("ascii"))
client.close()
