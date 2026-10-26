"""Private research-only persistence; never borrows the live trading client."""
import research_diagnostics as diagnostics
from research_storage import research_client

_client = None


def get_client():
    global _client
    if _client is None:
        _client = research_client(timeout=5)
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
