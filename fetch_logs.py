import paramiko

def fetch_logs():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print("Connecting to DietPi...")
        client.connect('192.168.1.50', username='root', password='paro', timeout=10)
        
        print("Running docker logs...")
        stdin, stdout, stderr = client.exec_command('docker logs can-slim-trading-bot --tail 50')
        out = stdout.read().decode()
        err = stderr.read().decode()
        
        print("STDOUT:")
        print(out)
        print("STDERR:")
        print(err)
        
    except Exception as e:
        print(f"SSH Error: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    fetch_logs()
