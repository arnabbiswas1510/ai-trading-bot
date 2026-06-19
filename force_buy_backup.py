"""
Backup plan: stop agent, run force buy using docker run --rm with clientId=1,
restart agent. This uses clientId=1 (proven to work) without docker exec issues.
"""
import sys, io, paramiko, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HOST, USER, PASS = "192.168.1.50", "root", "paro"

def ssh(cmd, timeout=30):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    return (o.read() + e.read()).decode("utf-8", errors="replace")

# Get execution-agent image and network
print("=== Getting agent metadata ===")
image = ssh("docker inspect execution-agent --format '{{.Config.Image}}' 2>&1").strip()
network = ssh("docker inspect execution-agent --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>&1").strip()
print(f"Image:   {image}")
print(f"Network: {network}")

# Get all env vars as -e flags for docker run
env_raw = ssh("docker inspect execution-agent --format '{{range .Config.Env}}{{.}}\n{{end}}' 2>&1")
env_flags = " ".join(f'-e "{line.strip()}"' for line in env_raw.strip().splitlines() if "=" in line and line.strip())

print(f"\n=== Stop agent, run with clientId=1, restart ===")
print(ssh("docker stop execution-agent 2>&1"))
time.sleep(3)

# Upload the force_buy_v4 script to /tmp on the server
with open(r"c:\Users\arnab\OneDrive\Documents\agy\ai-trading-bot\force_buy_v4.py", "rb") as f:
    script = f.read()

c2 = paramiko.SSHClient()
c2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c2.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = c2.open_sftp()
sftp.putfo(io.BytesIO(script), "/tmp/force_buy_final.py")
sftp.close()
c2.close()

# Run via docker run --rm with clientId=1 (replace 10 in the script)
# Modify script to use clientId=1
script_c1 = script.replace(b"clientId=10", b"clientId=1")
c3 = paramiko.SSHClient()
c3.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c3.connect(HOST, username=USER, password=PASS, timeout=10)
sftp3 = c3.open_sftp()
sftp3.putfo(io.BytesIO(script_c1), "/tmp/force_buy_c1.py")
sftp3.close()

# Run via docker run
run_cmd = (
    f"docker run --rm "
    f"--network {network} "
    f"{env_flags} "
    f"-v /tmp/force_buy_c1.py:/app/force_buy_c1.py "
    f"{image} "
    f"python /app/force_buy_c1.py 2>&1"
)

print(f"\nRunning: {run_cmd[:200]}...")
chan = c3.get_transport().open_session()
chan.settimeout(180)
chan.exec_command(run_cmd)
out = b""
deadline = time.time() + 180
while time.time() < deadline:
    if chan.recv_ready():
        chunk = chan.recv(65536)
        out += chunk
        print("[BUY] " + chunk.decode("utf-8", "replace").rstrip())
    if chan.exit_status_ready():
        while chan.recv_ready():
            chunk = chan.recv(65536)
            out += chunk
            print("[BUY] " + chunk.decode("utf-8", "replace").rstrip())
        break
    time.sleep(0.3)
c3.close()

print("\n=== Restart agent ===")
print(ssh("docker start execution-agent 2>&1"))
time.sleep(8)
print(ssh("docker logs execution-agent --tail 10 2>&1 | cat"))
