#!/bin/sh
# Run from the directory containing docker-compose.yml and the operator's .env.
set -eu

if [ "$#" -ne 0 ]; then
    echo "Usage: TRADING_RUNTIME_MODE=observe|live sh scripts/deploy_runtime.sh" >&2
    exit 2
fi

mode=${TRADING_RUNTIME_MODE:-observe}
case "$mode" in
    observe)
        selected=intraday-observer
        inactive=execution-agent
        inactive_profile=live
        ;;
    live)
        selected=execution-agent
        inactive=intraday-observer
        inactive_profile=observe
        ;;
    *)
        echo "ERROR: TRADING_RUNTIME_MODE must be observe or live; no containers changed." >&2
        exit 2
        ;;
esac

# Stop first: a failed pull/start must never leave the opposite runtime active.
# This does not cancel or replace any orders already held at the broker.
echo "=== Runtime mode: $mode; stopping $inactive before deployment ==="
docker compose --profile "$inactive_profile" stop "$inactive"

echo "=== Pulling $selected and dashboard images ==="
docker compose --profile "$mode" pull "$selected" trading-bot

echo "=== Keeping the existing gateway (--no-recreate preserves its session) ==="
docker compose up -d --no-deps --no-recreate ib-gateway

# Never follow dependencies into a trading service, even if compose is changed.
docker compose --profile "$mode" up -d --no-deps "$selected" trading-bot

echo "=== Selected runtime status (inactive runtime remains stopped) ==="
docker inspect "$selected" --format '{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})'
docker inspect ib-gateway --format '{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})'
docker inspect can-slim-trading-bot --format '{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})'
