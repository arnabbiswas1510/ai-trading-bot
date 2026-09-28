#!/usr/bin/env python3
"""Fatal-safe entrypoint for the execution agent.

Docker runs THIS module (not ``execution_agent.py`` directly). Its only job is to
import and start the agent inside a try/except so that ANY exception raised while
the agent is starting — including import-time errors that occur before TeeLogger
and Supabase log shipping are initialised — is written to the ``agent_logs``
table before the process exits.

Why this exists
---------------
On 2026-09-27 a circular import made ``execution_agent`` raise ``ImportError`` at
module load, crash-looping the container. Because the failure happened during
import — before the TeeLogger/Supabase pipeline was installed — nothing reached
``agent_logs``. The traceback existed ONLY in ``docker logs``, and the prod host
sits on a home network, so diagnosing it required an SSH session. That defeats
the entire purpose of Supabase log shipping, which is to make the agent
diagnosable without logging into the host.

This wrapper closes that gap: a startup crash now lands in Supabase as a single
``CRITICAL`` ``[STARTUP-CRASH]`` row, visible from anywhere the database is.

Design constraints
------------------
* It depends only on stdlib + ``supabase`` (already a hard dependency that
  imports cleanly on its own), never on anything in ``execution_agent`` or
  ``agent_logging`` — precisely the code that may have failed to import.
* It reads ``SUPABASE_URL`` / ``SUPABASE_KEY`` straight from the environment.
* It re-raises after shipping so the container still exits non-zero and Docker's
  restart policy is unchanged; the crash is recorded, not swallowed.

See decisions/2026-09-27_startup-crash-shipping.md.
"""
from __future__ import annotations

import datetime
import os
import sys
import traceback
from zoneinfo import ZoneInfo

_MAX_CRASH_CHARS = 8000  # keep well under any row limit; a traceback is short


def _ship_startup_crash(text: str) -> None:
    """Best-effort single-row insert of a startup traceback into agent_logs.

    Never raises: a failure to report the crash must not mask the crash itself.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        sys.stderr.write(
            "[STARTUP-CRASH] SUPABASE_URL/SUPABASE_KEY unset; cannot ship crash to Supabase.\n"
        )
        return
    try:
        from supabase import create_client

        client = create_client(url, key)
        now = datetime.datetime.now(ZoneInfo("America/New_York")).isoformat()
        message = ("[STARTUP-CRASH] execution-agent failed to start:\n" + text)[:_MAX_CRASH_CHARS]
        client.table("agent_logs").insert(
            {
                "logged_at": now,
                "level": "CRITICAL",
                "message": message,
                "session_id": f"startup-{now}",
                "seq": 0,
                "repeat_count": 1,
            }
        ).execute()
        sys.stderr.write("[STARTUP-CRASH] traceback shipped to Supabase agent_logs.\n")
    except Exception as ship_err:  # noqa: BLE001 — reporting must never raise
        sys.stderr.write(f"[STARTUP-CRASH] failed to ship crash to Supabase: {ship_err}\n")


def main() -> None:
    try:
        import execution_agent

        execution_agent.main()
    except SystemExit:
        # An explicit sys.exit() (e.g. missing FMP_API_KEY) is a clean, intended
        # exit with its own message — not a crash to ship.
        raise
    except BaseException:  # noqa: BLE001 — we want EVERYTHING, incl. import errors
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        _ship_startup_crash(tb)
        sys.exit(1)


if __name__ == "__main__":
    main()
