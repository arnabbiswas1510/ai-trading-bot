"""Explicit research-only endpoint selection; live trading keeps its own client."""

import os
from urllib.parse import urlsplit


def backend():
    value = os.getenv("RESEARCH_STORAGE_BACKEND", "supabase").strip().lower()
    if value not in {"supabase", "local"}:
        raise ValueError("RESEARCH_STORAGE_BACKEND must be supabase or local.")
    return value


def _required(name):
    value = os.getenv(name, "").strip()
    if not value or value == "@bws" or any(c in value for c in "\r\n\0"):
        raise ValueError(f"{name} must be configured for research storage.")
    return value


def research_url():
    local = backend() == "local"
    value = _required("RESEARCH_DATABASE_URL" if local else "SUPABASE_URL").rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Research URL must be an HTTP(S) origin without credentials or a path.")
    if local:
        source = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if parsed.hostname.endswith(".supabase.co") or value == source:
            raise ValueError("Local research storage must not point to Supabase.")
    return value


def research_key():
    return _required(
        "RESEARCH_DATABASE_KEY" if backend() == "local" else "INTRADAY_SUPABASE_KEY"
    )


def research_headers():
    key = research_key()
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def research_rest_url():
    """Supabase prefixes PostgREST; the self-hosted API serves it at its root."""
    url = research_url()
    return url if backend() == "local" else f"{url}/rest/v1"


def research_client(*, timeout=30):
    if backend() == "local":
        from postgrest import SyncPostgrestClient

        return SyncPostgrestClient(
            research_rest_url(), headers=research_headers(), timeout=timeout,
        )
    from supabase import ClientOptions, create_client

    return create_client(
        research_url(), research_key(),
        options=ClientOptions(postgrest_client_timeout=timeout),
    )
