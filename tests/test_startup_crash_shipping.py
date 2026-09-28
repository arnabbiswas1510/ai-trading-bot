"""Tests for the fatal-safe entrypoint and the execution_agent_ref proxy.

Covers the two failure modes exposed by the 2026-09-27 crash-loop:

1. execution_agent must import and run as __main__ without a circular-import
   ImportError (the bug: sibling modules did `import execution_agent as ea`,
   which re-entered execution_agent while it was still loading as __main__).

2. A startup crash — including an import-time one, before TeeLogger/Supabase
   shipping exists — must be shipped to agent_logs by agent_entrypoint, so it is
   diagnosable without SSHing into the host.

See decisions/2026-09-27_startup-crash-shipping.md.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

import agent_entrypoint  # noqa: E402
import execution_agent   # noqa: E402
from execution_agent_ref import ea  # noqa: E402


# ── 1. import safety ─────────────────────────────────────────────────────────

def test_execution_agent_runs_as_main_without_circular_import():
    """`python execution_agent.py --help` must exit 0.

    This is the exact condition that crash-looped the container: run directly,
    execution_agent is __main__, and any sibling's top-level
    `import execution_agent` re-enters a half-loaded module. --help exercises
    every top-level import, then argparse exits 0.
    """
    result = subprocess.run(
        [sys.executable, "execution_agent.py", "--help"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"execution_agent.py --help failed (rc={result.returncode}).\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "ImportError" not in result.stderr


def test_ea_proxy_resolves_to_the_execution_agent_module():
    # The proxy must forward attribute access to the real, single module object.
    assert ea.MAX_POSITIONS == execution_agent.MAX_POSITIONS
    # And writes must land on that same module (the retention cursor is set this
    # way by the log shipper).
    sentinel = object()
    ea._test_proxy_write = sentinel
    try:
        assert execution_agent._test_proxy_write is sentinel
    finally:
        del execution_agent._test_proxy_write


# ── 2. startup-crash shipping ────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, sink): self.sink = sink
    def insert(self, row): self.sink.append(row); return self
    def execute(self): return None


class _FakeClient:
    def __init__(self, sink): self.sink = sink
    def table(self, name): assert name == "agent_logs"; return _FakeTable(self.sink)


def _install_fake_supabase(monkeypatch, sink):
    fake = types.ModuleType("supabase")
    fake.create_client = lambda url, key: _FakeClient(sink)
    monkeypatch.setitem(sys.modules, "supabase", fake)


def test_startup_crash_is_shipped_to_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "service-role-key")
    sink = []
    _install_fake_supabase(monkeypatch, sink)

    boom = ImportError("cannot import name 'TeeLogger'")
    monkeypatch.setattr(execution_agent, "main", lambda: (_ for _ in ()).throw(boom))

    with pytest.raises(SystemExit) as exc:
        agent_entrypoint.main()
    assert exc.value.code == 1

    assert len(sink) == 1, "exactly one crash row must be shipped"
    row = sink[0]
    assert row["level"] == "CRITICAL"
    assert row["message"].startswith("[STARTUP-CRASH]")
    assert "TeeLogger" in row["message"]          # the real traceback is included
    assert {"logged_at", "level", "message"} <= row.keys()  # NOT NULL columns present


def test_clean_sys_exit_is_not_shipped(monkeypatch):
    """A deliberate sys.exit (e.g. missing FMP_API_KEY) is not a crash to ship."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "service-role-key")
    sink = []
    _install_fake_supabase(monkeypatch, sink)

    monkeypatch.setattr(execution_agent, "main", lambda: sys.exit(3))
    with pytest.raises(SystemExit) as exc:
        agent_entrypoint.main()
    assert exc.value.code == 3
    assert sink == [], "clean exits must not produce a crash row"


def test_ship_is_silent_when_supabase_creds_absent(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    monkeypatch.setattr(execution_agent, "main",
                        lambda: (_ for _ in ()).throw(RuntimeError("early boom")))
    with pytest.raises(SystemExit):
        agent_entrypoint.main()
    err = capsys.readouterr().err
    assert "cannot ship crash to Supabase" in err  # reported, never raised
