"""Check jts.ini for order preset config, then retry force buy after gateway restart."""
import sys, io, paramiko, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HOST, USER, PASS = "192.168.1.50", "root", "paro"

def ssh(cmd, timeout=20):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    return (o.read() + e.read()).decode("utf-8", errors="replace")

# Read jts.ini for order preset
print("=== jts.ini contents (order/preset related lines) ===")
ini = ssh("docker exec ib-gateway cat /home/ibgateway/Jts/jts.ini 2>&1 | grep -iE 'preset|tif|order|cancel|day' | head -30")
print(ini if ini.strip() else "(no matching lines)")

# Also read ibc config
print("=== ibc config.ini ===")
ibc = ssh("docker exec ib-gateway cat /home/ibgateway/ibc/config.ini 2>&1 | head -50")
print(ibc)

print("\n=== Full jts.ini ===")
full_ini = ssh("docker exec ib-gateway cat /home/ibgateway/Jts/jts.ini 2>&1 | head -80")
print(full_ini)
