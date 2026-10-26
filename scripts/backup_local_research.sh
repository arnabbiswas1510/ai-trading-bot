#!/usr/bin/env bash
# Run on the Linux production host. This never clears either database.
set -euo pipefail
umask 077

if [[ $# -ne 2 ]]; then
    echo "Usage: bash scripts/backup_local_research.sh PRIVATE_ENV_FILE SECOND_DEVICE_DIRECTORY" >&2
    exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${RESEARCH_MIGRATION_PYTHON:-$HOME/.venvs/research-migration/bin/python}"
CONFIG="$1"
SECOND="$2"

"$PY" - "$CONFIG" "$SECOND" <<'PY'
import os
import stat
import sys
from pathlib import Path

config, second = map(Path, sys.argv[1:])
info = config.stat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
    raise SystemExit("Private environment must be an operator-owned regular file with no group/other access.")
if not second.is_dir():
    raise SystemExit("Second-device backup directory must already exist.")
PY

# Only an operator-owned private file is sourced; never use a downloaded config.
set -a
source "$CONFIG"
set +a
cd "$ROOT"

if [[ -z "${RESEARCH_POSTGRES_PASSWORD:-}" ]]; then
    echo "RESEARCH_POSTGRES_PASSWORD is required in the private environment file." >&2
    exit 2
fi
DB_IP="$(docker inspect research-db --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
if [[ ! "$DB_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Expected one private IPv4 address for research-db." >&2
    exit 1
fi
export LOCAL_RESEARCH_PG_DSN="host=$DB_IP port=5432 dbname=research user=postgres password=$RESEARCH_POSTGRES_PASSWORD sslmode=disable"
ARCHIVES="${RESEARCH_ARCHIVE_DIR:-$ROOT/research-archives}"
mkdir -p "$ARCHIVES"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT="$ARCHIVES/$STAMP"
if [[ -e "$SNAPSHOT" || -e "$SECOND/$STAMP" ]]; then
    echo "Snapshot path already exists; refusing overwrite." >&2
    exit 1
fi
"$PY" scripts/research_migration.py --docker-tools export --database local --output "$SNAPSHOT"
"$PY" scripts/research_migration.py check-archive --archive "$SNAPSHOT"
"$PY" scripts/research_migration.py archive --archive "$SNAPSHOT" --destination "$SECOND/$STAMP"
