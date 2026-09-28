"""Guard: no real secret may live in a tracked file.

This test is the standing enforcement behind the 2026-09-27 secret-leak incident,
in which a live IBKR Flex token and Telegram bot token sat in the public
`.env.template` for ~80 days. Two checks:

1. Every secret line in `.env.template` is the `@bws` sentinel (resolved from
   Bitwarden at deploy time), never a literal value.
2. The specific credential strings that leaked never reappear in any tracked
   file. The banned strings are assembled from fragments so THIS file does not
   itself contain them contiguously (which would make the test find itself).

See decisions/2026-09-27_bitwarden-secret-resolution.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Secret vars that must be resolved from Bitwarden — their .env.template value
# must be exactly the sentinel, never a literal.
SENTINEL = "@bws"
BWS_SECRET_KEYS = {
    "IBKR_ACCOUNT", "IBKR_LIVE_USER", "IBKR_LIVE_PASS", "IBKR_TOTP_SECRET",
    "IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID", "IBKR_FLEX_EXEC_QUERY_ID",
    "SUPABASE_URL", "SUPABASE_KEY", "FMP_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
}

# Assembled from fragments on purpose — see module docstring.
_LEAKED_TELEGRAM_SECRET = "AAGXDp59Eb6" + "oRExBJyrCmDEjk5Rmfl9gLeU"
_LEAKED_FLEX_TOKEN = "744951508262" + "970976219401"
BANNED_LITERALS = (_LEAKED_TELEGRAM_SECRET, _LEAKED_FLEX_TOKEN)

# Generated / vendored trees are not authored secrets; skip them.
SKIP_PREFIXES = ("graphify-out/", "frontend/dist/", "node_modules/")
SKIP_SUFFIXES = (".patch",)


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for rel in out:
        if rel.startswith(SKIP_PREFIXES) or rel.endswith(SKIP_SUFFIXES):
            continue
        files.append(REPO / rel)
    return files


def _parse_env_template() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO / ".env.template").read_text().splitlines():
        s = line.lstrip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


@pytest.mark.parametrize("key", sorted(BWS_SECRET_KEYS))
def test_env_template_secret_is_a_sentinel(key):
    values = _parse_env_template()
    assert key in values, f"{key} missing from .env.template"
    assert values[key] == SENTINEL, (
        f".env.template must not carry a literal for {key}; expected the "
        f"'{SENTINEL}' sentinel but found '{values[key]}'. Store the value in the "
        f"Bitwarden ai-trading-bot project instead."
    )


def test_no_leaked_credential_literal_in_any_tracked_file():
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — not an authored secret
        for banned in BANNED_LITERALS:
            if banned in text:
                offenders.append(f"{path.relative_to(REPO)} contains a leaked credential")
    assert not offenders, "Leaked credential(s) present in tracked files:\n" + "\n".join(offenders)
