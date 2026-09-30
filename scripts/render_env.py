#!/usr/bin/env python3
"""Resolve @bws sentinels in .env.template against Bitwarden secrets.

Called by scripts/render_env.sh. Kept as a standalone, import-safe module so its
logic can be unit-tested without a live Bitwarden connection
(tests/test_render_env.py).

Contract
--------
* Input:  the secrets JSON (as produced by ``bws secret list <PROJECT_ID> -o
          json`` — the listing is scoped to a single Bitwarden project by
          render_env.sh) via the ``BWS_SECRETS_JSON`` environment variable; the
          template path and the sentinel string as argv[1] and argv[2].
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


def resolve_project_id(projects_json_text: str, name: str) -> str:
    """Map a Bitwarden project name to its id, or raise LookupError.

    The bootstrap token file is shared with other applications on this host, so
    it carries only the machine-account token -- never a project id, which is
    per-application. The project is therefore identified by name here.
    """
    projects = json.loads(projects_json_text)
    matches = [p for p in projects if p.get("name") == name]
    if not matches:
        visible = ", ".join(sorted(str(p.get("name", "?")) for p in projects))
        raise LookupError(
            f"no Bitwarden project named {name!r}; the machine account can see: "
            f"{visible or '<none>'}"
        )
    if len(matches) > 1:
        raise LookupError(
            f"{len(matches)} Bitwarden projects are named {name!r}; set "
            f"BWS_PROJECT_ID explicitly to disambiguate"
        )
    return str(matches[0]["id"])


def build_secret_map(secrets: list[dict], project_id: str | None = None) -> dict[str, str]:
    """Index secrets by key, scoped to a project and refusing ambiguous keys.

    render_env.sh already scopes the `bws secret list` call, so this is defence
    in depth. A machine account with access to several projects can surface the
    same key more than once; silently letting the last one win would quietly
    render another application's credential into this .env, so conflicting
    duplicates are a hard error rather than a coin flip.
    """
    mapping: dict[str, str] = {}
    conflicts: set[str] = set()
    for secret in secrets:
        if project_id is not None and secret.get("projectId") != project_id:
            continue
        key = secret["key"]
        value = secret.get("value", "")
        if key in mapping and mapping[key] != value:
            conflicts.add(key)
        mapping[key] = value
    if conflicts:
        raise ValueError(
            "duplicate Bitwarden secrets with conflicting values for: "
            + ",".join(sorted(conflicts))
        )
    return mapping


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


def _resolve_project_mode(name: str) -> int:
    try:
        projects_json = os.environ["BWS_PROJECTS_JSON"]
    except KeyError:
        sys.stderr.write("missing BWS_PROJECTS_JSON\n")
        return 2
    try:
        sys.stdout.write(resolve_project_id(projects_json, name) + "\n")
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"bad BWS_PROJECTS_JSON: {exc}\n")
        return 2
    except LookupError as exc:
        sys.stderr.write(f"{exc}\n")
        return 4
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--resolve-project":
        return _resolve_project_mode(argv[2])
    if len(argv) < 2:
        sys.stderr.write(
            "usage: render_env.py <template> [sentinel]\n"
            "       render_env.py --resolve-project <project-name>\n"
        )
        return 2
    template_path = argv[1]
    sentinel = argv[2] if len(argv) > 2 else "@bws"
    try:
        secrets_list = json.loads(os.environ["BWS_SECRETS_JSON"])
    except (KeyError, json.JSONDecodeError) as e:
        sys.stderr.write(f"bad or missing BWS_SECRETS_JSON: {e}\n")
        return 2
    try:
        secrets = build_secret_map(secrets_list, os.environ.get("BWS_PROJECT_ID") or None)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 5
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
