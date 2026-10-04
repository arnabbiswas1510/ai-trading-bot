"""Run the backup with the same in-memory Bitwarden bootstrap as the watchdog."""
import os
import subprocess
import sys

from scripts.run_intraday_reporting_bws import BootstrapError, ROOT, load_credentials


def main(argv=None, env=None, runner=subprocess.run):
    env = dict(os.environ if env is None else env)
    try:
        credentials = load_credentials(env, runner)
        child_env = {key: value for key, value in env.items() if not key.startswith("BWS_")}
        child_env.update(credentials)
        return runner(
            [sys.executable, str(ROOT / "supabase_backup.py"),
             *(sys.argv[1:] if argv is None else argv)],
            cwd=ROOT, env=child_env, check=False,
        ).returncode
    except BootstrapError as exc:
        print("::error::Supabase backup Bitwarden bootstrap failed: " + str(exc),
              file=sys.stderr)
    except Exception:
        print("::error::Supabase backup bootstrap or launch failed; "
              "details withheld to protect credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
