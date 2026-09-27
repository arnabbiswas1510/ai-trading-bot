"""Persistent log tee + Supabase log shipping/purge, extracted from
execution_agent.py (see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: the running ``_tee`` instance, the ``_last_log_purge_at``
cursor and the AGENT_LOG_* retention constants live in the execution_agent
module (the import-time bootstrap that swaps sys.stdout stays there), so the
ship/purge functions read them via ``ea.<name>``. Tests install a fake tee with
``ea.__dict__["_tee"] = ...`` and call ``ea.flush_logs_to_supabase`` / use
``ea.TeeLogger`` directly; re-exporting these symbols keeps that live.
"""
import os
import re
import sys
import datetime
from collections import deque
from zoneinfo import ZoneInfo

import execution_agent as ea

class TeeLogger:
    """Mirrors stdout to a daily rotating log file without touching print() calls.

    Both sys.stdout AND sys.stderr are pointed at one instance (see the install
    block below), so the log file is a complete record of the session.

    It also captures lines for shipping to Supabase. That capture exists
    because the production host sits behind a home network that is unreachable
    from most corporate networks, so `docker logs` is frequently not available
    when a problem needs diagnosing. The FULL log ships by default -- see
    SHIP_ALL -- because an error line without the context that preceded it
    explains nothing. See decisions/2026-09-18_comprehensive-log-shipping.md.
    """

    KEEP_DAYS = 7

    # ── What ships ───────────────────────────────────────────────────────────
    # Everything, by default. The earlier design shipped only lines matching
    # SHIP_MARKERS, which was enough to answer "is the alert channel dead?" but
    # useless for the actual job: reconstructing what the agent was thinking
    # when it made a decision. A stack trace without the twenty lines that
    # preceded it explains nothing.
    #
    # Set AGENT_LOG_SHIP_ALL=false to fall back to markers-only (much smaller,
    # much less useful). SHIP_MARKERS survives either way as the basis of
    # level classification, which is what makes the full firehose filterable.
    SHIP_ALL = os.getenv("AGENT_LOG_SHIP_ALL", "true").strip().lower() \
        not in ("false", "0", "no")

    SHIP_MARKERS = ("[TELEGRAM-FAIL]", "CRITICAL", "Traceback", "❌", "⚠️")

    # Lines whose only content is decoration. Shipping them triples row count
    # and adds nothing — the local file keeps them for when formatting matters.
    _NOISE = re.compile(r"^[\s=\-─━_*·.]*$")

    # Bounded so a failure storm can never exhaust memory on the trading host.
    # Sized to hold several full cycles: dropping lines is worse now that the
    # buffer holds the context, not just the errors. When full the OLDEST are
    # dropped, and the drop count is shipped so a truncated view never reads as
    # a complete one.
    SHIP_BUFFER_MAX = int(os.getenv("AGENT_LOG_BUFFER_MAX", "20000"))

    # PostgREST rejects very large request bodies, so a drained buffer is
    # inserted in chunks rather than as one statement.
    SHIP_BATCH_SIZE = 500

    MAX_MESSAGE_CHARS = 4000

    # Consecutive identical lines are collapsed into one row carrying a count.
    # A retry loop or a stuck poll can emit the same line thousands of times;
    # without this, one bug can fill the retention window.
    DEDUP_MAX = 10000

    # Redaction applied before anything leaves the host. The log is written by
    # code that has no idea it may be transmitted, so this is the only place
    # that can be responsible for it. This matters far more now that every line
    # ships, not just the handful that matched a marker.
    _REDACTIONS = [
        # IBKR account numbers (U1234567 / DU1234567) — identifies the brokerage
        # account in a table that is far more widely readable than the host.
        (re.compile(r"\b(D?U)\d{6,}\b"), r"\1[redacted]"),
        # Telegram bot token (12345678:AA...) — grants full control of the bot.
        # No leading \b: the token's most likely appearance is inside a request
        # URL as ".../bot8997092181:AAG...", where the digits are preceded by a
        # letter and \b does not match. Caught by test_secrets_are_redacted.
        (re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_\-]{30,}"), "[bot-token-redacted]"),
        # JWTs (Supabase keys) — appear in request URLs inside tracebacks.
        (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
         "[jwt-redacted]"),
        # Supabase's NON-JWT key format (sb_publishable_... / sb_secret_...).
        # The JWT pattern above cannot match these, and the key=value pattern
        # below only catches them when they appear after apikey=/token=. A bare
        # occurrence — e.g. "HTTPError sb_secret_abc123 returned 401", which is
        # exactly the shape of a PostgREST auth failure — leaked in full.
        # This matters more since 2026-09-18: agent_logs is now readable with
        # the publishable key, so redaction is the PRIMARY control on this data,
        # not a second layer. Caught by test_secrets_are_redacted.
        (re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_\-]{8,}"),
         "[supabase-key-redacted]"),
        # Generic key=value secrets, e.g. inside a requests exception repr.
        (re.compile(r"(?i)\b(apikey|api_key|token|password|secret)(['\"]?\s*[=:]\s*['\"]?)"
                    r"[A-Za-z0-9._\-]{8,}"), r"\1\2[redacted]"),
    ]

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._real_stdout = sys.__stdout__
        self._log_file = None
        self._current_date: str | None = None
        # Shipping state. A deque with maxlen is the whole memory guarantee.
        self.ship_buffer: deque = deque(maxlen=self.SHIP_BUFFER_MAX)
        self.ship_dropped = 0          # lines lost to a full buffer
        self._ship_partial = ""        # accumulates a line across write() calls
        self._shipping = False         # re-entrancy guard
        # Identifies one container run. Without it, interleaved lines from a
        # restart loop are indistinguishable from one long session — which is
        # exactly the shape a crash-restart bug takes.
        self.session_id = datetime.datetime.now(
            ZoneInfo("America/New_York")).strftime("%Y%m%dT%H%M%S")
        self._seq = 0                  # restores ordering within one timestamp
        self._last_line: str | None = None
        self._last_count = 0
        self._open_today()
        self._purge_old_logs()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _open_today(self):
        today = self._today()
        if today == self._current_date:
            return
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
        path = os.path.join(self.log_dir, f"execution_{today}.log")
        self._log_file = open(path, "a", encoding="utf-8", buffering=1)
        self._current_date = today
        # Print banner so every log file is self-describing
        self._log_file.write(
            f"\n{'='*60}\n"
            f" Execution Agent — session started {datetime.datetime.now().isoformat()}\n"
            f"{'='*60}\n"
        )
        # Purge old logs on every daily rotation — guarantees cleanup even if
        # the agent runs for months without a container restart.
        self._purge_old_logs()

    def _purge_old_logs(self):
        """Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.

        Uses the date string embedded in the filename instead of mtime.
        ISO dates sort lexicographically, so a plain '<' comparison is correct.
        Purge runs at startup AND at every midnight rotation, so old logs are
        always cleaned up within 24 hours of expiry.
        """
        try:
            cutoff = (
                datetime.datetime.now() - datetime.timedelta(days=self.KEEP_DAYS)
            ).strftime("%Y-%m-%d")  # e.g. "2026-07-02" — files on this date and earlier are removed
            for fname in os.listdir(self.log_dir):
                if not (fname.startswith("execution_") and fname.endswith(".log")):
                    continue
                date_str = fname[len("execution_"):-len(".log")]  # "2026-07-02"
                if len(date_str) == 10 and date_str < cutoff:
                    os.remove(os.path.join(self.log_dir, fname))
                    self._real_stdout.write(
                        f"[TeeLogger] Purged log older than {self.KEEP_DAYS} days: {fname}\n"
                    )
        except Exception:
            pass  # never let purge errors crash the agent

    # ── file-like interface ─────────────────────────────────────────────────

    def write(self, data: str):
        self._open_today()          # auto-rotate at midnight
        self._real_stdout.write(data)
        if self._log_file:
            self._log_file.write(data)
        # Capture LAST: a fault in shipping must never cost us the log write
        # above, which is the durable record.
        if not self._shipping:
            try:
                self._capture_for_shipping(data)
            except Exception:
                pass    # logging must never raise into the caller

    # ── Supabase shipping ───────────────────────────────────────────────────

    @classmethod
    def redact(cls, line: str) -> str:
        for pattern, repl in cls._REDACTIONS:
            line = pattern.sub(repl, line)
        return line

    def _capture_for_shipping(self, data: str):
        """Buffer complete log lines. Never performs network I/O.

        print() issues the text and the newline as separate write() calls, so
        partial data is accumulated until a newline arrives. Without this a
        line split across two writes would be classified on a fragment.
        """
        self._ship_partial += data
        if "\n" not in self._ship_partial:
            # Guard against a pathological unterminated line growing forever.
            if len(self._ship_partial) > 32768:
                self._ship_partial = self._ship_partial[-8192:]
            return
        *lines, self._ship_partial = self._ship_partial.split("\n")
        for line in lines:
            self._buffer_line(line)

    def _buffer_line(self, line: str):
        line = line.rstrip()
        if not line.strip():
            return
        if self._NOISE.match(line):
            return                      # pure separator/decoration
        if not self.SHIP_ALL and not any(m in line for m in self.SHIP_MARKERS):
            return

        # Collapse consecutive repeats. One stuck retry loop would otherwise
        # fill the whole retention window with a single line.
        if line == self._last_line and self._last_count < self.DEDUP_MAX:
            self._last_count += 1
            if self.ship_buffer:
                self.ship_buffer[-1]["repeat_count"] = self._last_count
            return
        self._last_line = line
        self._last_count = 1

        self._seq += 1
        if len(self.ship_buffer) == self.SHIP_BUFFER_MAX:
            self.ship_dropped += 1
        self.ship_buffer.append({
            "logged_at": datetime.datetime.now(
                ZoneInfo("America/New_York")).isoformat(),
            "session_id":   self.session_id,
            "seq":          self._seq,
            "level":        self._classify(line),
            "message":      self.redact(line)[:self.MAX_MESSAGE_CHARS],
            "repeat_count": 1,
        })

    # Substrings that mark a line as a trade-lifecycle event. These are the
    # lines worth keeping for the full retention window even though they are
    # not errors — they are the record of what the bot actually DID.
    _TRADE_MARKERS = (
        "Successfully bought", "Successfully sold", "SELL", "BUY ",
        "order", "Order", "ORDER", "fill", "Fill", "FILL",
        "scale-out", "Scale-out", "trailing stop", "Trailing stop",
        "hard stop", "Hard stop", "OCA", "arm_exit", "ARMED",
        "Prove-It", "PROVE_IT", "sell_state", "POWER_HOLD", "EXITING",
        "🟢", "🔴", "💰", "📉", "📈",
    )

    @classmethod
    def _classify(cls, line: str) -> str:
        """Map a line to a retention/filter tier.

        Order matters: the checks run most-severe first, so a traceback line
        that also mentions an order is classified CRITICAL, not TRADE.
        """
        if "[TELEGRAM-FAIL]" in line:
            return "ALERT_CHANNEL"
        if "CRITICAL" in line or "Traceback" in line or "FATAL" in line:
            return "CRITICAL"
        if "❌" in line or "Exception" in line or "Error:" in line:
            return "ERROR"
        if "⚠️" in line or "WARNING" in line:
            return "WARN"
        if any(m in line for m in cls._TRADE_MARKERS):
            return "TRADE"
        return "INFO"

    def drain(self) -> list:
        """Atomically remove and return everything buffered."""
        items = list(self.ship_buffer)
        self.ship_buffer.clear()
        self._last_line = None          # a flush ends the dedup run
        self._last_count = 0
        if self.ship_dropped:
            self._seq += 1
            items.append({
                "logged_at": datetime.datetime.now(
                    ZoneInfo("America/New_York")).isoformat(),
                "session_id":   self.session_id,
                "seq":          self._seq,
                "level":        "WARN",
                "message":      (f"[TeeLogger] {self.ship_dropped} log line(s) dropped — "
                                 f"ship buffer full ({self.SHIP_BUFFER_MAX}). "
                                 f"The local file at {self.log_dir} is complete; "
                                 f"only the shipped copy is missing lines."),
                "repeat_count": 1,
            })
            self.ship_dropped = 0
        return items

    def flush(self):
        self._real_stdout.flush()
        if self._log_file:
            self._log_file.flush()

    # Propagate attribute lookups to real stdout for compatibility
    def __getattr__(self, name):
        return getattr(self._real_stdout, name)


def _ship_diag(msg: str) -> None:
    """Report a log-shipping problem without going through TeeLogger.

    Goes to the real STDERR for two reasons: the caller holds the _shipping
    guard (so a normal print() would reach the log file but silently not be
    buffered, reading as "no error occurred"), and other tooling imports this
    module and parses its stdout -- a diagnostic must never corrupt that.
    """
    try:
        stream = sys.__stderr__
        if stream is not None:
            stream.write(msg + "\n")
            stream.flush()
    except Exception:
        pass


def _purge_agent_logs(client) -> None:
    """Enforce retention. Runs at most hourly; caller holds the shipping guard.

    Three sweeps, because no single one bounds the table on its own:
      1. Short window for routine INFO/TRADE chatter (the bulk of the volume).
      2. Long window for WARN and above (what a post-mortem needs).
      3. A hard row ceiling, which is the only thing that bounds a burst
         occurring between two age-based sweeps.
    """
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    if ea._last_log_purge_at and (now - ea._last_log_purge_at) < ea._LOG_PURGE_INTERVAL:
        return

    short_cutoff = (now - datetime.timedelta(days=ea.AGENT_LOG_INFO_RETENTION_DAYS)).isoformat()
    client.table("agent_logs").delete() \
        .in_("level", list(ea._SHORT_RETENTION_LEVELS)).lt("logged_at", short_cutoff).execute()

    long_cutoff = (now - datetime.timedelta(days=ea.AGENT_LOG_RETENTION_DAYS)).isoformat()
    client.table("agent_logs").delete().lt("logged_at", long_cutoff).execute()

    # Row ceiling: find the id at the cutoff position and delete everything
    # older. One SELECT plus one DELETE, regardless of how far over we are.
    resp = client.table("agent_logs").select("id") \
        .order("id", desc=True).limit(1).offset(ea.AGENT_LOG_MAX_ROWS).execute()
    if getattr(resp, "data", None):
        client.table("agent_logs").delete().lt("id", resp.data[0]["id"]).execute()

    ea._last_log_purge_at = now


def flush_logs_to_supabase(client) -> int:
    """Ship buffered log lines to Supabase. Returns rows written.

    Called at the end of every cycle -- market-open and off-hours alike -- and
    again on shutdown. Exists because the production host is on a home network
    that is unreachable from most corporate networks, so `docker logs` is
    often unavailable exactly when a diagnosis is needed, which is how the
    2026-09-18 alert-channel outage stayed unexplained.

    Ships the FULL log by default, not just error lines: a stack trace without
    the lines that preceded it explains nothing. Volume is controlled by
    consecutive-line dedup, tiered retention and a hard row ceiling rather than
    by discarding context at capture time.

    Three properties this must never violate:
      * It must never raise. Log shipping is strictly less important than
        trading, and an exception here would propagate into the monitor cycle.
      * It must never recurse. Any print() from inside this function re-enters
        TeeLogger.write(), so the _shipping guard is held for the duration;
        without it a Supabase failure would log an error, buffer it, and
        re-ship it forever.
      * It must never block on a large backlog. The buffer is bounded by
        SHIP_BUFFER_MAX and inserted in SHIP_BATCH_SIZE chunks, so the work is
        bounded even after a long outage.
    """
    tee = getattr(ea, "_tee", None)
    if tee is None or not isinstance(tee, TeeLogger):
        return 0                      # log tee not installed (CI/unit tests)

    rows = tee.drain()
    if not rows:
        return 0

    tee._shipping = True
    written = 0
    try:
        # Batch: one 20,000-row insert would exceed PostgREST's body limit and
        # lose the whole drain, which is precisely the backlog we most want.
        for i in range(0, len(rows), TeeLogger.SHIP_BATCH_SIZE):
            batch = rows[i:i + TeeLogger.SHIP_BATCH_SIZE]
            client.table("agent_logs").insert(batch).execute()
            written += len(batch)
        # Retention is reported separately: a purge failure is a capacity
        # problem, and reporting it as a shipping failure would send whoever
        # reads this hunting a delivery bug that does not exist.
        try:
            ea._purge_agent_logs(client)
        except Exception as purge_err:
            ea._ship_diag(f"   ⚠️ agent_logs retention sweep failed (rows WERE shipped): "
                       f"{purge_err}")
        return written
    except Exception as e:
        # Report on the real STDERR, bypassing TeeLogger: we hold the _shipping
        # guard, so a normal print() would reach the file but silently not be
        # buffered, which reads as "no error occurred". stderr rather than
        # stdout because other tooling imports this module and parses its
        # stdout -- a diagnostic must never corrupt a caller's output.
        ea._ship_diag(f"   ⚠️ could not ship log lines to Supabase "
                   f"({written}/{len(rows)} written): {e}")
        return written
    finally:
        tee._shipping = False


def flush_logs_quietly() -> int:
    """flush_logs_to_supabase() that also swallows client-construction errors.

    Every call site is a diagnostic afterthought placed on a path that matters
    (a sleep, an exception handler, shutdown), so nothing here may propagate --
    including get_supabase_client() itself failing, which is likely in exactly
    the situation the logs are most wanted.
    """
    try:
        return ea.flush_logs_to_supabase(ea.get_supabase_client())
    except Exception:
        return 0
