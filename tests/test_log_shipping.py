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
import io
import json
import subprocess
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
    # Supabase's non-JWT key format. These leaked in full before 2026-09-18:
    # the JWT pattern cannot match them, and the key=value pattern only fires
    # when they follow apikey=/token=. A BARE occurrence is the realistic case —
    # it is the exact shape of a PostgREST auth failure, and the agent hit one
    # every 15 minutes while agent_logs was RLS-blocked.
    ("HTTPError sb_secret_9zXyWvUt7654321abcdef returned 401",
     "sb_secret_9zXyWvUt7654321abcdef"),
    ("Authorization: Bearer sb_publishable_AbCdEf123456789xyz",
     "sb_publishable_AbCdEf123456789xyz"),
    ("client init with sb_publishable_QQQwwwEEE111222333 failed",
     "sb_publishable_QQQwwwEEE111222333"),
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
# Capture — comprehensive by default, and correct across write() boundaries
# ──────────────────────────────────────────────────────────────────────────────

def test_ordinary_lines_are_captured(tee):
    """The whole point of shipping everything: context, not just errors."""
    _emit(tee, "   Balance synced: own_cash=$5000 positions=$20000")
    _emit(tee, "✅ Successfully bought 788 shares of DHT at $23.04")
    _emit(tee, "❌ Failed to execute order for DHT")
    assert len(tee.ship_buffer) == 3
    assert [r["level"] for r in tee.ship_buffer] == ["INFO", "TRADE", "ERROR"]


def test_blank_and_decoration_lines_are_skipped(tee):
    """Separators triple the row count and carry no information."""
    for noise in ("", "   ", "=" * 60, "-" * 40, "──────────", "***"):
        _emit(tee, noise)
    assert len(tee.ship_buffer) == 0


def test_markers_only_mode_still_works(tee, monkeypatch):
    """AGENT_LOG_SHIP_ALL=false is the escape hatch if volume ever bites."""
    monkeypatch.setattr(ea.TeeLogger, "SHIP_ALL", False)
    _emit(tee, "   Balance synced: own_cash=$5000")
    assert len(tee.ship_buffer) == 0
    _emit(tee, "❌ Failed to execute order")
    assert len(tee.ship_buffer) == 1


def test_consecutive_repeats_are_collapsed(tee):
    """One stuck retry loop must not consume the whole retention window."""
    for _ in range(500):
        _emit(tee, "⚠️ Reconnection failed (attempt N)")
    assert len(tee.ship_buffer) == 1
    assert tee.ship_buffer[0]["repeat_count"] == 500


def test_dedup_only_collapses_ADJACENT_lines(tee):
    """Interleaved lines are distinct events and must stay separate rows."""
    _emit(tee, "checking DHT")
    _emit(tee, "checking TWLO")
    _emit(tee, "checking DHT")
    assert len(tee.ship_buffer) == 3


def test_sequence_numbers_are_monotonic(tee):
    """Ordering within one timestamp is otherwise unrecoverable."""
    for i in range(5):
        _emit(tee, f"line {i}")
    seqs = [r["seq"] for r in tee.ship_buffer]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5


def test_all_rows_carry_the_session_id(tee):
    """A restart loop is indistinguishable from one long session without it."""
    _emit(tee, "line one")
    _emit(tee, "line two")
    assert {r["session_id"] for r in tee.ship_buffer} == {tee.session_id}


def test_marker_split_across_writes_is_still_captured(tee):
    """print() emits text and newline separately; a naive impl misses this."""
    tee.write("[TELEGRAM-")
    tee.write("FAIL] WARN: chat_id=1 HTTP 400")
    assert len(tee.ship_buffer) == 0      # no newline yet — line incomplete
    tee.write("\n")
    assert len(tee.ship_buffer) == 1
    assert tee.ship_buffer[0]["level"] == "ALERT_CHANNEL"


@pytest.mark.parametrize("line,level", [
    ("[TELEGRAM-FAIL] ALARM: channel down", "ALERT_CHANNEL"),
    ("CRITICAL: Unhandled exception caught by global hook", "CRITICAL"),
    ("Traceback (most recent call last):", "CRITICAL"),
    ("❌ Failed to execute order for DHT", "ERROR"),
    ("⚠️ DHT: could not persist hard_stop_price", "WARN"),
    ("✅ Successfully bought 788 shares of DHT", "TRADE"),
    ("   DHT: placing OCA bracket", "TRADE"),
    ("   DHT sell_state UNPROVEN → PROVEN", "TRADE"),
    ("   Balance synced: own_cash=$5000", "INFO"),
    ("😴 Market is closed. Checking in 30 min...", "INFO"),
])
def test_lines_are_classified(tee, line, level):
    _emit(tee, line)
    assert tee.ship_buffer[0]["level"] == level


def test_severity_wins_over_trade_classification(tee):
    """A traceback mentioning an order is CRITICAL, not TRADE — it must not be
    purged on the short INFO/TRADE retention window."""
    _emit(tee, "Traceback (most recent call last): during order placement")
    assert tee.ship_buffer[0]["level"] == "CRITICAL"


def test_unterminated_line_cannot_grow_without_bound(tee):
    for _ in range(2000):
        tee.write("x" * 100)          # never a newline
    assert len(tee._ship_partial) <= 32768


def test_buffer_is_bounded_and_reports_drops(tee):
    over = 50
    for i in range(ea.TeeLogger.SHIP_BUFFER_MAX + over):
        _emit(tee, f"❌ error number {i}")     # distinct — defeats dedup
    assert len(tee.ship_buffer) == ea.TeeLogger.SHIP_BUFFER_MAX
    assert tee.ship_dropped == over

    drained = tee.drain()
    assert any(f"{over} log line(s) dropped" in r["message"] for r in drained), \
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


def test_large_backlog_is_inserted_in_batches(tee, monkeypatch):
    """One 20,000-row insert would exceed PostgREST's body limit and lose the
    whole drain — precisely the backlog most worth keeping."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    n = ea.TeeLogger.SHIP_BATCH_SIZE * 3 + 7
    for i in range(n):
        _emit(tee, f"line {i}")

    client = MagicMock()
    assert ea.flush_logs_to_supabase(client) == n
    sizes = [len(c[0][0]) for c in client.table.return_value.insert.call_args_list]
    assert len(sizes) == 4 and max(sizes) <= ea.TeeLogger.SHIP_BATCH_SIZE
    assert sum(sizes) == n


def test_partial_batch_failure_reports_what_was_written(tee, monkeypatch):
    """Silently returning 0 after writing 500 rows would misreport the state."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    for i in range(ea.TeeLogger.SHIP_BATCH_SIZE * 2):
        _emit(tee, f"line {i}")

    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = [
        MagicMock(), Exception("body too large"),
    ]
    assert ea.flush_logs_to_supabase(client) == ea.TeeLogger.SHIP_BATCH_SIZE


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


def test_shipping_diagnostics_go_to_stderr_not_stdout(monkeypatch):
    """Other tooling imports this module and parses its stdout as JSON. A
    shipping warning on stdout corrupts that -- it broke
    tests/test_max_positions_config.py when this was first written."""
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", out)
    monkeypatch.setattr(sys, "__stderr__", err)

    ea._ship_diag("   ⚠️ could not ship log lines to Supabase")

    assert "could not ship" in err.getvalue()
    assert out.getvalue() == "", f"diagnostic leaked to stdout: {out.getvalue()!r}"


def test_importing_the_module_registers_no_shutdown_hook():
    """The shutdown hooks attempt a Supabase round trip at exit. Registering
    them at import made every `python -c "import execution_agent; print(json)"`
    caller emit a warning into its own stdout -- it broke
    tests/test_max_positions_config.py, which parses exactly that.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import execution_agent; print('{\"ok\": true}')"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "SUPABASE_URL": "https://unreachable.invalid",
             "SUPABASE_KEY": "x" * 40, "LOG_DIR": tempfile.mkdtemp()},
    )
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}, \
        f"import-time hook corrupted stdout: {proc.stdout!r}"


def test_flush_quietly_survives_a_dead_supabase_client(tee, monkeypatch):
    """Its call sites are exception handlers and shutdown — getting the client
    is itself likely to fail there."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setattr(ea, "get_supabase_client",
                        MagicMock(side_effect=Exception("no creds")))
    _emit(tee, "❌ boom")
    assert ea.flush_logs_quietly() == 0      # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# Retention — what stops the table jamming Supabase
# ──────────────────────────────────────────────────────────────────────────────

def test_retention_purges_info_on_the_short_window(tee, monkeypatch):
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_at", None)
    client = MagicMock()
    _emit(tee, "some info line")
    ea.flush_logs_to_supabase(client)

    # The level-scoped sweep must target exactly INFO and TRADE: anything more
    # would expire errors early, anything less lets chatter accrue for 14 days.
    levels = client.table.return_value.delete.return_value.in_.call_args[0][1]
    assert set(levels) == {"INFO", "TRADE"}


def test_retention_enforces_a_hard_row_ceiling(tee, monkeypatch):
    """Age alone cannot bound a burst occurring between two sweeps."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_at", None)
    client = MagicMock()
    sel = client.table.return_value.select.return_value.order.return_value \
        .limit.return_value.offset.return_value
    sel.execute.return_value = MagicMock(data=[{"id": 4242}])

    _emit(tee, "line")
    ea.flush_logs_to_supabase(client)

    client.table.return_value.select.return_value.order.return_value \
        .limit.return_value.offset.assert_called_with(ea.AGENT_LOG_MAX_ROWS)
    client.table.return_value.delete.return_value.lt.assert_any_call("id", 4242)


def test_row_ceiling_is_a_noop_when_under_the_cap(tee, monkeypatch):
    """An empty result means fewer rows than the cap — deleting on it would
    wipe the table."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_at", None)
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value \
        .limit.return_value.offset.return_value.execute.return_value = \
        MagicMock(data=[])

    _emit(tee, "line")
    ea.flush_logs_to_supabase(client)

    id_deletes = [c for c in client.table.return_value.delete.return_value
                  .lt.call_args_list if c[0][0] == "id"]
    assert not id_deletes


def test_retention_is_rate_limited(tee, monkeypatch):
    """Purging on every 15-minute flush is three wasted queries per cycle."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_at", None)
    client = MagicMock()

    _emit(tee, "one")
    ea.flush_logs_to_supabase(client)
    first = client.table.return_value.delete.call_count
    assert first > 0

    _emit(tee, "two")
    ea.flush_logs_to_supabase(client)
    assert client.table.return_value.delete.call_count == first, \
        "retention must not re-run on the very next cycle"


def test_retention_failure_is_reported_as_a_purge_failure(tee, monkeypatch):
    """The insert already succeeded. Reporting this as "could not ship" would
    send whoever reads it hunting a delivery bug that does not exist."""
    monkeypatch.setitem(ea.__dict__, "_tee", tee)
    monkeypatch.setitem(ea.__dict__, "_last_log_purge_at", None)
    client = MagicMock()
    client.table.return_value.delete.side_effect = Exception("purge failed")

    sink = io.StringIO()
    tee._real_stdout = sink
    monkeypatch.setattr(ea, "_ship_diag", lambda m: sink.write(m + "\n"))

    _emit(tee, "❌ boom")
    assert ea.flush_logs_to_supabase(client) == 1
    assert tee._shipping is False

    out = sink.getvalue()
    assert "retention sweep failed" in out, out
    assert "could not ship" not in out, out
