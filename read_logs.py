import paramiko, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.1.50", username="root", password="paro", timeout=10)

# Get agent logs - look for what triggered MS sell
_, out, err = c.exec_command(
    "docker logs execution-agent --since 2h 2>&1 | tail -300", timeout=30
)
logs = out.read().decode('utf-8', 'replace')
lines = logs.splitlines()

# Filter relevant lines
keywords = ['MS', 'Trailing', 'Trail Stop', 'Current:', 'Entry:', 'SELL NOT',
            'Error.*sell', 'Closed Position', 'stop_loss', 'execute_sell',
            'Running Intraday', 'Monitoring', 'Intraday']
for i, line in enumerate(lines):
    if any(k.lower() in line.lower() for k in ['ms', 'trailing', 'trail stop',
            'sell not', 'closed position', 'intraday', 'monitoring ms', 'current:']):
        print(f"[{i}] {line}")

c.close()
