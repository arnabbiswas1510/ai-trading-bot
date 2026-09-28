#!/usr/bin/env python3
"""Resolve @bws sentinels in .env.template against Bitwarden secrets.

Called by scripts/render_env.sh. Kept as a standalone, import-safe module so its
logic can be unit-tested without a live Bitwarden connection
(tests/test_render_env.py).

Contract
--------
* Input:  the secrets JSON (as produced by ``bws secret list -o json``) via the
          ``BWS_SECRETS_JSON`` environment variable; the template path and the
          sentinel string as argv[1] and argv[2].
* Output: the fully-rendered .env written to stdout.
* Every template line whose value is exactly the sentinel (default ``@bws``) is
  replaced with the matching secret's value, looked up by the env-var name.
* Non-secret lines — blanks, ``#`` comments, and literal ``KEY=value`` config —
  pass through verbatim, including any inline comment.
* FAIL-CLOSED: if any sentinel key is absent from the vault or resolves to an
  empty value, nothing is written to stdout, the offending keys are reported on
  stderr, and the process exits 3. render_env.sh treats any non-zero exit as a
  hard abort and leaves the existing .env untouched.

See decisions/2026-09-27_bitwarden-secret-resolution.md.
"""
from __future__ import annotations

import json
import os
import sys


def render(template_text: str, secrets: dict[str, str], sentinel: str = "@bws") -> str:
    """Return the rendered .env text, or raise KeyError listing unmet sentinels."""
    out: list[str] = []
    missing: list[str] = []
    for raw in template_text.splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        key, _, value = raw.partition("=")
        key_name = key.strip()
        if value.strip() == sentinel:
            resolved = secrets.get(key_name, "")
            if resolved == "":
                missing.append(key_name)
                out.append(f"{key_name}=")  # placeholder; render() will raise
            else:
                out.append(f"{key_name}={resolved}")
        else:
            out.append(raw)
    if missing:
        raise KeyError(",".join(missing))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: render_env.py <template> [sentinel]\n")
        return 2
    template_path = argv[1]
    sentinel = argv[2] if len(argv) > 2 else "@bws"
    try:
        secrets_list = json.loads(os.environ["BWS_SECRETS_JSON"])
    except (KeyError, json.JSONDecodeError) as e:
        sys.stderr.write(f"bad or missing BWS_SECRETS_JSON: {e}\n")
        return 2
    secrets = {s["key"]: s.get("value", "") for s in secrets_list}
    with open(template_path, encoding="utf-8") as f:
        template_text = f.read()
    try:
        sys.stdout.write(render(template_text, secrets, sentinel))
    except KeyError as e:
        sys.stderr.write(f"MISSING:{e.args[0]}\n")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
