"""Tests for TeeLogger's Supabase log shipping and redaction.

This code sits in the print() path of a live-money trading agent, so the tests
below are weighted toward the ways it could do HARM rather than the ways it
could fail to work:

  * leaking a credential or account number off the host
  * raising into a caller that was only trying to print
  * recursing when Supabase is the thing that is broken
  * growing without bound during a failure storm

See decisions/2026-09-18_supabase-log-shipping.md.
"""
import datetime
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import execution_agent as ea  # noqa: E402


@pytest.fixture
def tee():
    with tempfile.TemporaryDirectory() as d:
        yield ea.TeeLogger(d)


def _emit(tee, text):
    """Mimic print(): body then newline as separate write() calls."""
    tee.write(text)
    tee.write("\n")


# ──────────────────────────────────────────────────────────────────────────────
# Redaction — nothing secret may leave the host
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,must_not_contain", [
    ("Balance synced [U12941651]: own_cash=$5000", "U12941651"),
    ("Balance synced [DU9876543]", "DU9876543"),
    ("url=https://api.telegram.org/bot8997092181:AAGXDp59Eb6oRExBJyrCmDEjk5Rmfl9gLeU/sendMessage",
     "AAGXDp59Eb6oRExBJyrCmDEjk5Rmfl9gLeU"),
    ("apikey=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
     "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
    ("Connection failed token: sk-abcdef123456789", "sk-abcdef123456789"),
    ("password='hunter2supersecret'", "hunter2supersecret"),
])
def test_secrets_are_redacted(raw, must_not_contain):
    out = ea.TeeLogger.redact(raw)
    assert must_not_contain not in out


def test_redaction_keeps_the_line_useful():
    """Redaction must not destroy the diagnostic value of the line."""
    out = ea.TeeLogger.redact("❌ Balance synced [U12941651]: own_cash=$5000 failed")
    assert "Balance synced" in out and "failed" in out
    assert "U[redacted]" in out


def test_redaction_runs_before_buffering(tee):
    """The buffer itself must never hold an unredacted secret."""
    _emit(tee, "❌ failure for account U12941651")
    assert "U12941651" not in tee.ship_buffer[0]["message"]


# ──────────────────────────────────────────────────────────────────────────────
# Capture — opt-in, and correct across write() boundaries
# ──────────────────────────────────────────────────────────────────────────────

def test_only_marked_lines_are_captured(tee):
    _emit(tee, "   Balance synced: own_cash=$5000 positions=$20000")
    _emit(tee, "✅ Successfully bought 788 shares of DHT at $23.04")
    assert len(tee.ship_buffer) == 0, "ordinary cycle output must not be shipped"

    _emit(tee, "❌ Failed to execute order for DHT")
    assert len(tee.ship_buffer) == 1


def test_marker_split_across_writes_is_still_captured(tee):
    """print() emits text and newline separately; a naive impl misses this."""
    tee.write("[TELEGRAM-")
    tee.write("FAIL] WARN: chat_id=1 HTTP 400")
    assert len(tee.ship_buffer) == 0      # no newline yet — line incomplete
    tee.write("\n")
    assert len(tee.ship_buffer) == 1
    assert "[TELEGRAM-FAIL]" in tee.ship_buffer[0]["message"]


@pytest.mark.parametrize("line,level", [
    ("[TELEGRAM-FAIL] ALARM: channel down", "ALERT_CHANNEL"),
    ("CRITICAL: Unhandled exception caught by global hook", "CRITICAL"),
    ("Traceback (most recent call last):", "CRITICAL"),
    ("❌ Failed to execute order for DHT", "ERROR"),
    ("⚠️ DHT: could not persist hard_stop_price", "WARN"),
])
def test_lines_are_classified(tee, line, level):
    _emit(tee, line)
    assert tee.ship_buffer[0]["level"] == level


def test_unterminated_line_cannot_grow_without_bound(tee):
    for _ in range(200):
        tee.write("x" * 100)          # never a newline
    assert len(tee._ship_partial) <= 8192


def test_buffer_is_bounded_and_reports_drops(tee):
    for i in range(ea.TeeLogger.SHIP_BUFFER_MAX + 50):
        _emit(tee, f"❌ error number {i}")
    assert len(tee.ship_buffer) == ea.TeeLogger.SHIP_BUFFER_MAX
    assert tee.ship_dropped == 50

    drained = tee.drain()
    assert any("50 log line(s) dropped" in r["message"] for r in drained), \
        "a silent drop would misrepresent the failure storm it was caused by"
    assert tee.ship_dropped == 0


def test_write_still_logs_to_file_when_capture_explodes(tee):
    """The durable file write must not be collateral damage from a capture bug."""
    with patch.object(ea.TeeLogger, "_classify", side_effect=RuntimeError("boom")):
        tee.write("❌ something bad\n")     # must not raise
    tee.flush()
    with open(os.path.join(tee.log_dir, f"execution_{tee._today()}.log")) as f:
        assert "something bad" in f.read()


# ──────────────────────────────────────────────────────────────────────────────
# Shipping
# ──────────────────────────────────────────────────────────────────────────────

def test_flush_ships_and_clears(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    _emit(tee, "❌ boom one")
    _emit(tee, "[TELEGRAM-FAIL] WARN: dead")

    client = MagicMock()
    assert ea.flush_logs_to_supabase(client) == 2
    rows = client.table.return_value.insert.call_args[0][0]
    assert {r["level"] for r in rows} == {"ERROR", "ALERT_CHANNEL"}
    assert len(tee.ship_buffer) == 0


def test_flush_is_a_noop_when_nothing_buffered(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    client = MagicMock()
    assert ea.flush_logs_to_supabase(client) == 0
    client.table.assert_not_called()


def test_flush_never_raises_when_supabase_is_down(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    _emit(tee, "❌ boom")
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = \
        Exception("supabase unreachable")
    assert ea.flush_logs_to_supabase(client) == 0     # must not raise


def test_shipping_failure_does_not_recurse(tee, monkeypatch):
    """THE hazard: a Supabase failure logs an error, which would be captured and
    re-shipped forever. The guard must hold for the whole call."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    _emit(tee, "❌ boom")
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = \
        Exception("supabase unreachable")

    ea.flush_logs_to_supabase(client)
    assert len(tee.ship_buffer) == 0, \
        "the shipping error was captured — this would loop forever"
    assert tee._shipping is False, "guard must be released even on failure"


def test_guard_is_released_after_success(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    _emit(tee, "❌ boom")
    ea.flush_logs_to_supabase(MagicMock())
    assert tee._shipping is False
    _emit(tee, "❌ later error")
    assert len(tee.ship_buffer) == 1, "capture must resume after a flush"


def test_flush_is_safe_without_a_tee_installed(monkeypatch):
    """Unit tests and CI run without the tee; this must be a no-op, not a crash."""
    monkeypatch.setitem(ea.__dict__, "_tee", None)
    assert ea.flush_logs_to_supabase(MagicMock()) == 0


def test_retention_runs_once_per_day(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_date", None)
    client = MagicMock()

    _emit(tee, "❌ one")
    ea.flush_logs_to_supabase(client)
    assert client.table.return_value.delete.call_count == 1

    _emit(tee, "❌ two")
    ea.flush_logs_to_supabase(client)
    assert client.table.return_value.delete.call_count == 1, \
        "retention must not re-run on every cycle"
