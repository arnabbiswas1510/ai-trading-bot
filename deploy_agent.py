import paramiko, io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HOST, USER, PASS = "192.168.1.50", "root", "paro"

with open(r"c:\Users\arnab\OneDrive\Documents\agy\ai-trading-bot\execution_agent.py", "rb") as f:
    content = f.read()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(content), "/tmp/execution_agent.py")
sftp.close()
_, out, err = c.exec_command("docker cp /tmp/execution_agent.py execution-agent:/app/execution_agent.py && docker restart execution-agent && echo DEPLOYED_OK")
print(out.read().decode())
print(err.read().decode())
c.close()
print("Done.")
