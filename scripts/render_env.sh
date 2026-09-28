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
#   On ANY error — bws missing, token missing, Bitwarden unreachable, or a single
#   @bws key not present in the vault — this script leaves the existing .env
#   completely untouched and exits non-zero, so the caller (deploy / restart)
#   aborts BEFORE starting containers. It never writes a partial .env and never
#   falls back to stale values silently.
#
# See decisions/2026-09-27_bitwarden-secret-resolution.md.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/pom/docker/ai-trading-bot}"
TEMPLATE="${ENV_TEMPLATE:-$PROJECT_DIR/.env.template}"
OUT="${ENV_OUT:-$PROJECT_DIR/.env}"
BOOTSTRAP="${BWS_ENV_FILE:-$HOME/.config/ai-trading-bot/bws.env}"
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
[ -f "$BOOTSTRAP" ] || fail "bootstrap token file not found: $BOOTSTRAP"

# ── Load the single bootstrap secret (BWS_ACCESS_TOKEN) ──────────────────────
set -a
# shellcheck disable=SC1090
. "$BOOTSTRAP"
set +a
[ -n "${BWS_ACCESS_TOKEN:-}" ] || fail "BWS_ACCESS_TOKEN not set by $BOOTSTRAP"

# ── Fetch every readable secret once, as JSON ────────────────────────────────
SECRETS_JSON="$("$BWS_BIN" secret list -o json)" || fail "bws secret list failed (token/connectivity?)"

# ── Render into a temp file in the SAME directory (atomic mv) ─────────────────
TMP="$(mktemp "${OUT}.XXXXXX")" || fail "mktemp failed next to $OUT"
trap 'rm -f "$TMP"' EXIT

# The whole substitution is done in python3 so secret values (JWTs, base32 TOTP
# seeds, tokens with punctuation) never pass through shell word-splitting.
RENDER_PY="${RENDER_PY:-$PROJECT_DIR/scripts/render_env.py}"
[ -f "$RENDER_PY" ] || fail "resolver not found: $RENDER_PY"
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
