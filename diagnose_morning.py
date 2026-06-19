import sys, io, paramiko, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HOST, USER, PASS = "192.168.1.50", "root", "paro"
with open(r"c:\Users\arnab\OneDrive\Documents\agy\ai-trading-bot\force_buy_v4.py", "rb") as f:
    script = f.read()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(script), "/tmp/force_buy_v4.py")
sftp.close()
c.exec_command("docker cp /tmp/force_buy_v4.py execution-agent:/app/force_buy_v4.py 2>&1")[1].read()
c.close()
print("Script uploaded.")

c2 = paramiko.SSHClient()
c2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c2.connect(HOST, username=USER, password=PASS, timeout=10)
chan = c2.get_transport().open_session()
chan.settimeout(240)
chan.exec_command("docker exec execution-agent python /app/force_buy_v4.py 2>&1")
deadline = time.time() + 240
while time.time() < deadline:
    if chan.recv_ready():
        print(chan.recv(65536).decode("utf-8", "replace").rstrip())
    if chan.exit_status_ready():
        while chan.recv_ready():
            print(chan.recv(65536).decode("utf-8", "replace").rstrip())
        break
    time.sleep(0.2)
c2.close()
print("Done.")
