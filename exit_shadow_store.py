"""Private research-only persistence; never borrows the live trading client."""
import os

import research_diagnostics as diagnostics

_client = None


def get_client():
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("INTRADAY_SUPABASE_KEY", "")
        if any(not value.strip() or value.strip() == "@bws"
               or any(char in value for char in ("\r", "\n", "\0"))
               for value in (url, key)):
            raise ValueError("Exit-shadow private credentials are not configured.")
        from supabase import ClientOptions, create_client

        _client = create_client(
            url, key, options=ClientOptions(postgrest_client_timeout=5),
        )
    return _client


def report_failure(error):
    """Use the independent diagnostic sink and a redacted console fallback."""
    try:
        diagnostics.emit(
            "execution-agent", "exit_shadow_write_failed", error=error,
            context={"table": "exit_shadow_log", "operation": "insert"},
        )
    except Exception:
        pass
    try:
        detail = diagnostics.exception_details(error)
        print("   ⚠️ Exit-shadow observation not saved: "
              f"{detail.get('category', 'unknown')} {detail.get('code', '')}. "
              "Check private credentials, table migration and research diagnostics.")
    except Exception:
        pass


def write_observation(row):
    """Return success without allowing research persistence to interrupt exits."""
    try:
        get_client().table("exit_shadow_log").insert(row).execute()
    except Exception as exc:
        report_failure(exc)
        return False
    return True
