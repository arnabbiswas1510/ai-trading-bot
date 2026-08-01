#!/usr/bin/env bash
# scripts/restart_6am.sh
# Scheduled 6:00 AM ET restart for IB Gateway & trading containers.
# Restarts containers, waits 45s for 2FA TOTP login, and runs health verification.
# Fires Telegram success/failure notification automatically.

PROJECT_DIR="/home/dietpi/docker/ai-trading-bot"
cd "$PROJECT_DIR" || exit 1

LOG_FILE="$PROJECT_DIR/logs/cron_restart.log"
mkdir -p "$PROJECT_DIR/logs"

echo "==========================================" >> "$LOG_FILE"
echo "[$(date)] Starting 6:00 AM Container Restart..." >> "$LOG_FILE"

# 1. Restart containers
docker restart ib-gateway execution-agent can-slim-trading-bot >> "$LOG_FILE" 2>&1

# 2. Wait 45 seconds for IB Gateway to perform TOTP 2FA auto-login and open API port 4000
sleep 45

# 3. Copy health check script into container and run verification
docker cp "$PROJECT_DIR/restart_and_health_check.py" execution-agent:/app/restart_and_health_check.py
docker exec execution-agent python3 /app/restart_and_health_check.py >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date)] Health check completed with exit code $EXIT_CODE." >> "$LOG_FILE"
