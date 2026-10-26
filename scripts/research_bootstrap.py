#!/usr/bin/env python3
"""Generate restricted local secrets, then explicitly initialize LOCAL PostgreSQL."""

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import time

import psycopg
from psycopg import sql

if __package__:
    from .research_pg_tools import MigrationError
else:
    from research_pg_tools import MigrationError


def service_jwt(secret, now=None):
    now = int(time.time()) if now is None else now
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(json.dumps({
        "role": "service_role", "iss": "research-local",
        "iat": now, "exp": now + 365 * 86400,
    }, separators=(",", ":")).encode())
    message = header + b"." + payload
    return (message + b"." + encode(hmac.new(secret.encode(), message, hashlib.sha256).digest())).decode()


def generate_secrets(path):
    path = Path(path)
    if not path.parent.is_dir():
        raise MigrationError("Create the private secrets directory first.")
    values = {
        "RESEARCH_POSTGRES_PASSWORD": secrets.token_hex(32),
        "RESEARCH_API_PASSWORD": secrets.token_hex(32),
        "RESEARCH_JWT_SECRET": secrets.token_hex(32),
    }
    values["RESEARCH_DATABASE_KEY"] = service_jwt(values["RESEARCH_JWT_SECRET"])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")
        output.flush()
        os.fsync(output.fileno())
    return path


def bootstrap_roles(connection, password):
    if len(password) < 32:
        raise MigrationError("RESEARCH_API_PASSWORD must have at least 32 characters.")
    connection.execute("SELECT pg_advisory_xact_lock(721986043)")
    # Never silently convert existing privileged roles into public API roles.
    for name in ("anon", "authenticated", "service_role", "authenticator"):
        row = connection.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname=%s",
            (name,),
        ).fetchone()
        if row and any(row):
            raise MigrationError("Existing API role has administrative privileges; refusing bootstrap.")
        if not row:
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(name)))
    unexpected_member = connection.execute("""
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles granted ON granted.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member
        WHERE (granted.rolname='service_role' AND member.rolname<>'authenticator')
           OR (member.rolname='authenticator'
               AND granted.rolname NOT IN ('anon','authenticated','service_role'))
        LIMIT 1
    """).fetchone()
    if unexpected_member:
        raise MigrationError("Unexpected API role membership; only authenticator may assume service_role.")
    for name in ("anon", "authenticated"):
        connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN NOINHERIT NOBYPASSRLS").format(sql.Identifier(name)))
    connection.execute("ALTER ROLE service_role NOLOGIN NOINHERIT BYPASSRLS")
    connection.execute(sql.SQL("ALTER ROLE authenticator LOGIN NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Literal(password)))
    connection.execute("GRANT anon, authenticated, service_role TO authenticator")
    connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    connection.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    secret_parser = commands.add_parser("secrets", help="Create a NEW 0600 env file; never overwrites.")
    secret_parser.add_argument("--output", type=Path, required=True)
    token_parser = commands.add_parser("token", help="Renew only RESEARCH_DATABASE_KEY using the existing RESEARCH_JWT_SECRET.")
    token_parser.add_argument("--output", type=Path, required=True)
    role_parser = commands.add_parser("roles", help="Initialize local-only PostgREST roles after PostgreSQL starts.")
    role_parser.add_argument("--confirm", required=True, help="Must be BOOTSTRAP LOCAL RESEARCH ONLY")
    args = parser.parse_args(argv)
    try:
        if args.command == "secrets":
            generate_secrets(args.output)
            print("Created private env file. Do not commit it. Service JWT expires after 365 days.")
        elif args.command == "token":
            secret = os.environ.get("RESEARCH_JWT_SECRET", "")
            if len(secret) < 32:
                raise MigrationError("Existing RESEARCH_JWT_SECRET must contain at least 32 characters.")
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(f"RESEARCH_DATABASE_KEY={service_jwt(secret)}\n")
                output.flush()
                os.fsync(output.fileno())
            print("Created private service JWT renewal file. Apply only its key to consumers; signing secret and database passwords are unchanged.")
        else:
            if args.confirm != "BOOTSTRAP LOCAL RESEARCH ONLY":
                raise MigrationError("Confirmation phrase does not match.")
            dsn = os.environ.get("LOCAL_RESEARCH_PG_DSN")
            password = os.environ.get("RESEARCH_API_PASSWORD", "")
            if not dsn:
                raise MigrationError("LOCAL_RESEARCH_PG_DSN is required.")
            with psycopg.connect(dsn, connect_timeout=15) as connection:
                live = connection.execute(
                    "SELECT to_regclass('public.portfolio_positions'), to_regclass('public.trade_history')"
                ).fetchone()
                if any(live):
                    raise MigrationError("Live trading tables found; this is not an isolated research database.")
                bootstrap_roles(connection, password)
            print("Local PostgREST roles initialized. No research data imported.")
    except MigrationError as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except (psycopg.Error, OSError, ValueError):
        print("STOP: operation failed; verify environment, privileges and output path. Credentials suppressed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
