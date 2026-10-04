"""Load only watchdog credentials from Bitwarden, without secret files or caches."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from scripts.render_env import resolve_project_id


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "SUPABASE_URL", "SUPABASE_KEY", "INTRADAY_SUPABASE_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
)


class BootstrapError(RuntimeError):
    """Fixed, safe operational messages only; never include transport output."""


def mask(value):
    if isinstance(value, str) and value:
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::add-mask::" + escaped, flush=True)


def valid_secret(value):
    return (isinstance(value, str) and bool(value.strip()) and value.strip() != "@bws"
            and not any(char in value for char in ("\r", "\n", "\0")))


def load_credentials(env, runner=subprocess.run):
    token = env.get("BWS_ACCESS_TOKEN", "")
    mask(token)
    if not valid_secret(token):
        raise BootstrapError(
            "Configure GitHub Actions repository secret BWS_ACCESS_TOKEN with a Bitwarden "
            "machine-account token granting read access to project ai-trading-bot."
        )
    bws_env = dict(env)
    # A caller-supplied server override bypasses the no-cache profile in bws.
    for key in ("BWS_SERVER_URL", "BWS_PROFILE", "BWS_CONFIG_FILE"):
        bws_env.pop(key, None)

    def request(arguments, operation):
        try:
            result = runner(
                ["bws", "--config-file", str(ROOT / "scripts/bws_ci.toml"),
                 "--profile", "watchdog", *arguments, "-o", "json"],
                env=bws_env, capture_output=True, text=True, timeout=60, check=False,
            )
            if result.returncode:
                raise BootstrapError(
                    f"Bitwarden {operation} failed; check Actions BWS_ACCESS_TOKEN, "
                    "project read permission and Bitwarden connectivity."
                )
            return json.loads(result.stdout)
        except BootstrapError:
            raise
        except Exception:
            raise BootstrapError(f"Bitwarden {operation} failed; CLI output withheld.") from None

    projects = request(["project", "list"], "project lookup")
    try:
        project_id = resolve_project_id(json.dumps(projects), "ai-trading-bot")
        uuid.UUID(project_id)
    except Exception:
        raise BootstrapError(
            "Bitwarden project ai-trading-bot is missing, ambiguous or invalid; "
            "check Actions BWS_ACCESS_TOKEN project read permission."
        ) from None
    rows = request(["secret", "list", project_id], "secret lookup")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BootstrapError("Bitwarden secret lookup returned an invalid response.")
    values = {}
    for key in REQUIRED:
        matches = [row.get("value") for row in rows
                   if row.get("projectId") == project_id and row.get("key") == key]
        for value in matches:
            mask(value)
        if len(matches) != 1 or not valid_secret(matches[0]):
            raise BootstrapError(
                f"Bitwarden project ai-trading-bot requires exactly one nonempty, "
                f"single-line, non-placeholder secret named {key}."
            )
        values[key] = matches[0]
    return values


def main(argv=None, env=None, runner=subprocess.run):
    env = dict(os.environ if env is None else env)
    try:
        credentials = load_credentials(env, runner)
        child_env = {key: value for key, value in env.items() if not key.startswith("BWS_")}
        child_env.update(credentials)
        return runner(
            [sys.executable, str(ROOT / "research_entrypoint.py"), "research-reporting",
             *(sys.argv[1:] if argv is None else argv)],
            cwd=ROOT, env=child_env, check=False,
        ).returncode
    except BootstrapError as exc:
        print("::error::Intraday research Bitwarden bootstrap failed: " + str(exc),
              file=sys.stderr)
    except Exception:
        print("::error::Intraday research Bitwarden bootstrap or reporting launch failed; "
              "details withheld to protect credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
