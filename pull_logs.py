"""Pull the first 600 lines of execution-agent logs (from container startup)
to find the 10:36 AM incident window."""
import paramiko, time

HOST, USER, PASS = "192.168.1.50", "root", "paro"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=10)

# Get total line count and then get the incident lines
# Container started at 07:02 UTC (3:02 AM ET). Incident at 10:36 AM ET = 14:36 UTC = 7.5h later.
# The first 600 lines should capture startup through ~10:30 AM ET.
_, o, e = c.exec_command("docker logs execution-agent 2>&1 | head -600", timeout=60)
out = (o.read() + e.read()).decode("utf-8", errors="replace")
c.close()

with open("logs_early.txt", "w", encoding="utf-8") as f:
    f.write(out)
print(f"Written {len(out.splitlines())} lines to logs_early.txt")
print(out[-3000:])  # show last 3000 chars
