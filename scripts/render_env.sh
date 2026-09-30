#!/usr/bin/env bash
# scripts/render_env.sh
#
# Materialise the host .env from Bitwarden Secrets Manager + .env.template.
#
# WHY THIS EXISTS
#   .env.template is committed and public. It must never contain a real secret
#   again (a leaked IBKR Flex token + Telegram bot token in this very file cost us
#   an incident on 2026-09-27). So every secret line in the template is the
#   sentinel `@bws`, and this script resolves those sentinels from Bitwarden at
#   deploy time, writing the real .env locally (gitignored, chmod 600). Non-secret
#   config lines pass through verbatim.
#
# FAIL-CLOSED CONTRACT
#   On ANY error — bws missing, token missing, project id missing, Bitwarden
#   unreachable, or a single @bws key not present in the vault — this script
#   leaves the existing .env completely untouched and exits non-zero, so the
#   caller (deploy / restart) aborts BEFORE starting containers. It never writes
#   a partial .env and never falls back to stale values silently.
#
# PROJECT SCOPING
#   The machine account may see more than one Bitwarden Secrets Manager project.
#   Secrets are matched downstream by env-var NAME, so an unscoped listing could
#   pull a same-named key from the wrong project. A project id is therefore
#   MANDATORY and is passed to `bws secret list <PROJECT_ID>` so only this
#   project's secrets are returned. It is resolved from BWS_PROJECT_NAME (this
#   repository's own configuration), NOT from the bootstrap token file, which is
#   shared with other applications on this host and so cannot carry a value that
#   is specific to any one of them.
#
# See decisions/2026-09-27_bitwarden-secret-resolution.md.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/pom/docker/ai-trading-bot}"
TEMPLATE="${ENV_TEMPLATE:-$PROJECT_DIR/.env.template}"
OUT="${ENV_OUT:-$PROJECT_DIR/.env}"
# Bootstrap token file search order. The machine-account token is shared with
# other applications on this host (the coach reads the same file), so it lives
# in a neutral location rather than under any one application's directory. An
# app-specific file is still honoured first, so a single application can be
# given a different token without disturbing the others.
#
# The shared file holds ONLY BWS_ACCESS_TOKEN. The project id is per-application
# and is resolved by name below -- putting it in a shared file is what would
# let one application render another's secrets.
BOOTSTRAP_CANDIDATES=(
    "$HOME/.config/ai-trading-bot/bws.env"
    "$HOME/.config/bws/bws.env"
)
BWS_PROJECT_NAME="${BWS_PROJECT_NAME:-ai-trading-bot}"
SENTINEL='@bws'

log()  { printf '[render_env] %s\n' "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

# ── Locate the bws binary ────────────────────────────────────────────────────
BWS_BIN="${BWS_BIN:-}"
if [ -z "$BWS_BIN" ]; then
    BWS_BIN="$(command -v bws || true)"
    [ -z "$BWS_BIN" ] && [ -x "$HOME/bin/bws" ] && BWS_BIN="$HOME/bin/bws"
fi
[ -n "$BWS_BIN" ] && [ -x "$BWS_BIN" ] || fail "bws binary not found (set BWS_BIN or install to ~/bin/bws)"

[ -f "$TEMPLATE" ]  || fail "template not found: $TEMPLATE"
BOOTSTRAP="${BWS_ENV_FILE:-}"
if [ -z "$BOOTSTRAP" ]; then
    for candidate in "${BOOTSTRAP_CANDIDATES[@]}"; do
        [ -f "$candidate" ] && { BOOTSTRAP="$candidate"; break; }
    done
fi
[ -n "$BOOTSTRAP" ] && [ -f "$BOOTSTRAP" ] \
    || fail "bootstrap token file not found; looked for: ${BOOTSTRAP_CANDIDATES[*]} (or set BWS_ENV_FILE)"
log "using bootstrap token file $BOOTSTRAP"

# An explicit BWS_PROJECT_ID in the environment takes precedence over the value
# in the bootstrap file; capture it before sourcing so the source cannot clobber
# a deliberate override.
BWS_PROJECT_ID_OVERRIDE="${BWS_PROJECT_ID:-}"

# ── Load the bootstrap token (BWS_ACCESS_TOKEN) ───────────────────────────────
set -a
# shellcheck disable=SC1090
. "$BOOTSTRAP"
set +a
[ -n "${BWS_ACCESS_TOKEN:-}" ] || fail "BWS_ACCESS_TOKEN not set by $BOOTSTRAP"

# The bootstrap file may be shared with other applications on this host. A
# BWS_PROJECT_ID found there would belong to whichever app wrote it, and
# honouring it would render that app's secrets into this .env. Only an explicit
# environment override counts; a value from the file is discarded.
if [ -n "${BWS_PROJECT_ID:-}" ] && [ "${BWS_PROJECT_ID:-}" != "$BWS_PROJECT_ID_OVERRIDE" ]; then
    log "ignoring BWS_PROJECT_ID from $BOOTSTRAP (it is not app-specific); resolving '$BWS_PROJECT_NAME' by name instead"
fi
BWS_PROJECT_ID="$BWS_PROJECT_ID_OVERRIDE"

RENDER_PY="${RENDER_PY:-$PROJECT_DIR/scripts/render_env.py}"
[ -f "$RENDER_PY" ] || fail "resolver not found: $RENDER_PY"

if [ -z "${BWS_PROJECT_ID:-}" ]; then
    PROJECTS_JSON="$("$BWS_BIN" project list -o json)" \
        || fail "bws project list failed (token/connectivity?)"
    BWS_PROJECT_ID="$(BWS_PROJECTS_JSON="$PROJECTS_JSON" \
        python3 "$RENDER_PY" --resolve-project "$BWS_PROJECT_NAME")" \
        || fail "could not resolve Bitwarden project '$BWS_PROJECT_NAME'"
    log "using Bitwarden project '$BWS_PROJECT_NAME' ($BWS_PROJECT_ID)"
else
    log "using Bitwarden project id $BWS_PROJECT_ID (supplied via BWS_PROJECT_ID)"
fi
export BWS_PROJECT_ID
# Project scoping is MANDATORY and fail-closed: the machine account may have read
# access to more than one Bitwarden Secrets Manager project, and secrets are
# matched by env-var NAME downstream (render_env.py). An unscoped `secret list`
# would merge every accessible project, so a same-named key in another project
# could silently resolve the wrong value into .env. Requiring the project id
# guarantees `secret list` returns ONLY this project's secrets.
[ -n "${BWS_PROJECT_ID:-}" ] || fail "could not determine the Bitwarden project id — required so 'bws secret list' is scoped to this project's secrets only"

# ── Fetch every readable secret in THIS PROJECT once, as JSON ─────────────────
SECRETS_JSON="$("$BWS_BIN" secret list "$BWS_PROJECT_ID" -o json)" || fail "bws secret list failed (token/connectivity/project id?)"

# ── Render into a temp file in the SAME directory (atomic mv) ─────────────────
TMP="$(mktemp "${OUT}.XXXXXX")" || fail "mktemp failed next to $OUT"
trap 'rm -f "$TMP"' EXIT

# The whole substitution is done in python3 so secret values (JWTs, base32 TOTP
# seeds, tokens with punctuation) never pass through shell word-splitting.
set +e
BWS_SECRETS_JSON="$SECRETS_JSON" python3 "$RENDER_PY" "$TEMPLATE" "$SENTINEL" > "$TMP" 2> "${TMP}.err"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
    log "$(cat "${TMP}.err" 2>/dev/null)"
    rm -f "${TMP}.err"
    fail "template resolution failed (rc=$rc); .env left unchanged"
fi
rm -f "${TMP}.err"

chmod 600 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
log "wrote $OUT ($(grep -c '=' "$OUT" || true) key lines)"
