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

# Compose env_file values are not overridden by similarly named shell variables.
# Inspect the actual consumer configuration in BOTH modes before lifecycle calls.
research_backend=$(COMPOSE_PROFILES=local-research docker compose config --format json | python3 -c '
import json, os, re, sys
from pathlib import Path
config = json.load(sys.stdin)
services = config["services"]
consumers = ("execution-agent", "intraday-observer", "shadow-worker", "calibration-worker",
             "trading-bot", "research-reporting")
values = {services[name].get("environment", {}).get("RESEARCH_STORAGE_BACKEND", "supabase")
          for name in consumers}
if len(values) != 1 or not values <= {"supabase", "local"}:
    raise SystemExit("ERROR: effective research consumer backends disagree or are invalid; no containers changed.")
backend = values.pop()
shell = os.environ.get("RESEARCH_STORAGE_BACKEND")
if shell is not None and shell != backend:
    raise SystemExit("ERROR: shell and effective Compose research backend disagree; no containers changed.")
declared = []
path = Path(".env")
for line in path.read_text().splitlines() if path.exists() else ():
    key, separator, value = line.partition("=")
    if separator and key.strip().removeprefix("export ") == "RESEARCH_STORAGE_BACKEND":
        declared.append(value.split(" #", 1)[0].strip().strip(chr(39) + chr(34)))
if declared and (len(declared) != 1 or declared[0] != backend):
    raise SystemExit("ERROR: .env and effective Compose research backend disagree; no containers changed.")
if backend == "supabase":
    print(backend)
    raise SystemExit(0)
for name in consumers:
    env = services[name].get("environment", {})
    for key in ("RESEARCH_DATABASE_KEY", "RESEARCH_DATABASE_URL"):
        value = env.get(key, "")
        if not value or value.strip() == "@bws" or any(c in value for c in "\r\n\0"):
            raise SystemExit("ERROR: local research runtime credentials are incomplete.")
    if env["RESEARCH_DATABASE_URL"].rstrip("/") != "http://research-api:3000":
        raise SystemExit("ERROR: local research endpoint must be the private research-api service.")
api = services["research-api"]["environment"]
if len(api.get("PGRST_JWT_SECRET", "")) < 32:
    raise SystemExit("ERROR: local research JWT signing secret must have at least 32 characters.")
password = services["research-db"]["environment"].get("POSTGRES_PASSWORD", "")
if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", password):
    raise SystemExit("ERROR: local PostgreSQL password must be URL-safe and at least 32 characters.")
if not re.fullmatch(r"postgres://authenticator:[A-Za-z0-9_-]{32,}@research-db:5432/research",
                    api.get("PGRST_DB_URI", "")):
    raise SystemExit("ERROR: local PostgREST requires its dedicated URL-safe authenticator password.")
env = services["research-reporting"]["environment"]
for key in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
    if not env.get(key) or env[key].strip() == "@bws":
        raise SystemExit("ERROR: local research reporter notification credentials are incomplete.")
print(backend)
'
)
export RESEARCH_STORAGE_BACKEND="$research_backend"

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
if [ "$research_backend" = local ]; then
    set -- "$@" research-reporting
fi
echo "=== Compatibility runtime label: $mode; dashboard controls new live entries ==="
if [ "$action" = deploy ]; then
    docker compose pull "$@"
    if [ "$research_backend" = local ]; then
        docker compose pull research-db research-api
    fi
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

if [ "$research_backend" = local ]; then
    docker compose up -d --no-deps --pull never --wait --wait-timeout 120 research-db
    # Only the importer can write this private schema; a loose file on the host
    # cannot authorize switching consumers to an empty or different database.
    docker compose exec -T research-db psql -X -U postgres -d research -At \
        -v ON_ERROR_STOP=1 -c \
        "SELECT json_build_object('proof', row_to_json(p), 'identity',
          json_build_array(c.system_identifier::text, d.oid::text, current_database()))
         FROM research_migration_admin.import_proof p, pg_control_system() c, pg_database d
         WHERE p.singleton AND d.datname=current_database()" | python3 -c '
import hashlib, json, re, sys
try:
    data = json.load(sys.stdin)
    proof = data["proof"]
    identity = hashlib.sha256(json.dumps(data["identity"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    valid = (proof.get("verified") is True and proof.get("target_identity") == identity
             and proof.get("source_identity") != identity
             and re.fullmatch("[a-f0-9]{64}", proof.get("manifest_sha256", "")))
except (ValueError, KeyError, TypeError):
    valid = False
if not valid:
    raise SystemExit("ERROR: verified import proof missing or belongs to another database; runtime not switched.")
'
    docker compose up -d --no-deps --pull never research-api
    # Query the restored settings through the real JWT/PostgREST route before
    # replacing any consumer. A running API container alone proves nothing.
    docker compose run --rm --no-deps --entrypoint python research-reporting -c '
import time
import httpx
from postgrest.exceptions import APIError
from research_storage import research_client
for attempt in range(6):
    try:
        client = research_client(timeout=10)
        rows = client.table("intraday_calibration_settings").select("id").eq("id", "default").execute().data
        if rows:
            break
    except (httpx.HTTPError, APIError, ValueError):
        pass
    if attempt == 5:
        raise SystemExit("ERROR: restored research settings are unavailable; runtime not switched.")
    time.sleep(5)
'
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
if [ "$research_backend" = local ]; then
    for container in research-db research-api research-reporting; do
        docker inspect "$container" --format '{{.Name}}: {{.State.Status}} (restarts: {{.RestartCount}})'
    done
fi
