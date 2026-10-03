#!/bin/sh
# Run from the directory containing docker-compose.yml and the operator's .env.
set -eu

action=deploy
if [ "$#" -eq 1 ] && [ "$1" = "--restart" ]; then
    action=restart
elif [ "$#" -ne 0 ]; then
    echo "Usage: TRADING_RUNTIME_MODE=observe|live sh scripts/deploy_runtime.sh [--restart]" >&2
    exit 2
fi

mode=${TRADING_RUNTIME_MODE:-observe}
case "$mode" in
    observe|live) ;;
    *)
        echo "ERROR: TRADING_RUNTIME_MODE must be observe or live; no containers changed." >&2
        exit 2
        ;;
esac
export TRADING_RUNTIME_MODE="$mode"

# Check before stopping even a legacy agent. An unavailable/stale approval must
# not interrupt protective execution. Old installations without either artifact
# remain compatible; a partial installation is an error, not an inactive state.
if [ -e approved_strategy.json ] || [ -e approved_strategy.env ] || [ -e .approved_strategy_activated.json ]; then
    python3 scripts/validate_calibration_deployment.py
fi

# Only the first transition from an ungated image needs stop-before-pull.
# A failed pull must preserve an already gated agent's protective execution.
# Listing first distinguishes a missing container from a Docker daemon failure.
agent=$(docker container ls -a --filter 'name=^/execution-agent$' --format '{{.ID}}')
if [ -n "$agent" ]; then
    guarded=$(docker inspect execution-agent --format '{{index .Config.Labels "io.ai-trading-bot.live-entry-gate"}}')
    if [ "$guarded" != "1" ]; then
        echo "=== Stopping legacy ungated execution agent before transition ==="
        docker compose stop execution-agent
    fi
fi

set -- execution-agent intraday-observer shadow-worker calibration-worker trading-bot
echo "=== Compatibility runtime label: $mode; dashboard controls new live entries ==="
if [ "$action" = deploy ]; then
    docker compose pull "$@"
    # A newer image can change effective defaults even when the host .env did
    # not change. Check its isolated configuration before replacing services.
    if [ -e approved_strategy.json ] || [ -e approved_strategy.env ] || [ -e .approved_strategy_activated.json ]; then
        python3 scripts/validate_calibration_deployment.py
    fi
fi

# Refuse rollback to an image that predates the persistent entry-permission gate.
# Restart uses installed images only; it cannot accidentally launch a legacy agent.
agent_image=ghcr.io/arnabbiswas1510/ai-trading-bot-execution-agent:latest
guarded=$(docker image inspect "$agent_image" --format '{{index .Config.Labels "io.ai-trading-bot.live-entry-gate"}}')
if [ "$guarded" != "1" ]; then
    echo "ERROR: execution image lacks the live-entry gate; no services started." >&2
    exit 1
fi

echo "=== Keeping the existing gateway (--no-recreate preserves its session) ==="
docker compose up -d --no-deps --no-recreate ib-gateway

if [ "$action" = restart ]; then
    docker compose restart ib-gateway
    docker compose up -d --no-deps --pull never --force-recreate "$@"
else
    docker compose up -d --no-deps --pull never "$@"
fi

echo "=== Protection, independent research, gateway and dashboard status ==="
for container in execution-agent intraday-observer shadow-worker calibration-worker ib-gateway can-slim-trading-bot; do
    docker inspect "$container" --format '{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})'
done
