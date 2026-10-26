"""Native PostgreSQL clients, with an explicit Docker fallback; no DSNs in argv."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from psycopg.conninfo import conninfo_to_dict


class MigrationError(RuntimeError):
    """An actionable, credential-free operator error."""


def client_environment(dsn):
    info = conninfo_to_dict(dsn)
    supported = {
        "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
        "dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
        "connect_timeout": "PGCONNECT_TIMEOUT",
    }
    if set(info) - supported.keys():
        raise MigrationError("DSN contains unsupported options; use explicit host, port, database, user and TLS options.")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update({supported[k]: v for k, v in info.items()})
    env["PGCONNECT_TIMEOUT"] = "15"
    env["PGOPTIONS"] = "-c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=3"
    return env


def run_client(tool, args, dsn, workspace, docker=False, stdout=None):
    """All file arguments must be relative to workspace (never host secrets)."""
    env = client_environment(dsn)
    workspace = Path(workspace).resolve()
    if docker:
        if not shutil.which("docker"):
            raise MigrationError("Docker CLI missing; install PostgreSQL client tools.")
        if (env.get("PGSSLROOTCERT") not in (None, "system")
                or any(env.get(k) for k in ("PGSSLCERT", "PGSSLKEY"))):
            raise MigrationError("Certificate-file DSNs require native PostgreSQL tools, not --docker-tools.")
        if sys.platform == "darwin" and env.get("PGHOST") in ("localhost", "127.0.0.1", "::1"):
            env["PGHOST"] = "host.docker.internal"
        command = ["docker", "run", "--rm"]
        if sys.platform != "darwin":
            command += ["--network", "host"]
        command += ["--user", f"{os.getuid()}:{os.getgid()}",
                   "--volume", f"{workspace}:/work", "--workdir", "/work"]
        for name in sorted(k for k in env if k.startswith("PG")):
            command += ["--env", name]
        command += ["postgres:17", tool, *args]
    else:
        if not shutil.which(tool):
            raise MigrationError(f"{tool} missing; install PostgreSQL 17 clients or use --docker-tools.")
        command = [tool, *args]
    result = subprocess.run(
        command, cwd=workspace, env=env, stdout=stdout or subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        # Native errors can contain the DSN, SQL values or password; never echo them.
        raise MigrationError(f"{tool} failed (exit {result.returncode}); no success marker was written. Check connectivity, permissions and PostgreSQL client/server versions.")
    return result.stdout
