"""Run a durable decision-only portfolio; never connect to IB or submit orders."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import logging
import os
import signal
import threading
import time

import research_diagnostics as diagnostics

from market_calendar import session_bounds
from research_configuration import effective_config
from shadow_inputs import InputGap, InputProducer, PublicMarketData, ReadOnlySources, NY, timestamp
from shadow_store import ShadowStore, StoreError, fingerprint, legacy_gap_reason, RECOVERABLE_REASONS

LOG = logging.getLogger(__name__)
UTC = dt.timezone.utc
DEFAULT_SPOOL = "/app/shadow/shadow.sqlite3"


def semantic_config_fingerprint(config):
    """Build labels remain provenance, not strategy settings."""
    return fingerprint({key: value for key, value in config.items() if key != "git_commit"})


def transport_failure_type(error):
    """Recognize typed network failures, including explicitly chained wrappers."""
    import httpx
    import ssl
    from postgrest.exceptions import APIError
    from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout, SSLError

    seen, chain = set(), []
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(error)
        # An unrelated exception merely raised while handling another exception
        # is not proof of a transport failure; follow explicit causes only.
        error = error.__cause__
    if any(isinstance(item, (ssl.SSLError, SSLError)) for item in chain):
        return None
    for error in chain:
        if isinstance(error, APIError):
            return ("SupabaseTransportError" if str(error.code) in {
                "429", "502", "503", "504", "520", "522", "523", "524",
            } else None)
        if isinstance(error, (RequestsConnectionError, Timeout, httpx.TimeoutException,
                              httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError, TimeoutError)):
            return type(error).__name__
    return None


def session_ticks(day):
    bounds = session_bounds(day)
    if bounds is None:
        return []
    opening, closing = bounds
    ticks, current = [], opening
    while current < closing:
        ticks.append(current)
        current += dt.timedelta(minutes=5)
    # Capture just before close, leaving time for HTTP acquisition without assigning
    # after-hours quotes to a regular-session event.
    ticks.append(closing - dt.timedelta(minutes=1))
    return ticks


def next_tick(run, now):
    """Never backfill an elapsed slot with a quote fetched now."""
    if not run["state"].get("last_frame_id"):
        bounds = session_bounds(now.astimezone(NY).date())
        if bounds and bounds[0] <= now < bounds[1]:
            opening = bounds[0]
            minutes = int((now - opening).total_seconds() // 300) * 5
            floor = opening + dt.timedelta(minutes=minutes)
            if now >= bounds[1] - dt.timedelta(minutes=1):
                return bounds[1] - dt.timedelta(minutes=1)
            # A newly observed seed has no earlier missing shadow cycle. Start
            # immediately rather than pretending its older five-minute floor was due.
            return floor if (now - floor).total_seconds() <= 120 else now
        return None
    # Frame keys are the scheduled timestamp, not the later receipt timestamp.
    last = timestamp(run["state"]["last_frame_id"])
    day = last.astimezone(NY).date()
    for _ in range(370):
        for tick in session_ticks(day):
            if tick > last:
                return tick
        day += dt.timedelta(days=1)
    raise InputGap("Could not resolve next NYSE session; calendar unavailable.")


class Worker:
    def __init__(self, store, producer, cloud, *, engine=None, clock=lambda: dt.datetime.now(UTC)):
        if engine is None:
            import shadow_engine as engine
        self.store, self.producer, self.cloud, self.engine, self.clock = store, producer, cloud, engine, clock
        self._next_diagnostic = 0
        self._retry = None
        self._preopen = None

    def _queue_recovery(self, run):
        failure = self.store.recovery_failure(run)
        if not failure:
            return None
        self._validate_recovery(run)
        request = self.store.queue_recovery()
        diagnostics.emit("shadow-worker", "shadow_recovery_queued", level="WARNING", context={
            "old_run_id": run["id"], "new_run_id": None, "reason_code": request["reason_code"]})
        return request

    def _validate_continuation(self, run):
        if semantic_config_fingerprint(run["config"]) != semantic_config_fingerprint(self.producer.config):
            raise ValueError("Effective strategy configuration changed; explicit new run is required.")
        return self.engine.restore(run["state"])

    def _validate_recovery(self, run):
        if semantic_config_fingerprint(run["config"]) != semantic_config_fingerprint(self.producer.config):
            raise ValueError("Effective strategy configuration changed; explicit new run is required.")
        # Empty preflight failures contain no simulated trajectory to resume.
        # Their checksums are checked independently; only a fresh current-
        # engine seed/frame can create the replacement.
        if not self.store.empty_run(run):
            self.engine.restore(run["state"])

    def _acquire(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (InputGap, ValueError, StoreError):
            raise
        except Exception as exc:
            transport = transport_failure_type(exc)
            if transport is None:
                raise
            gap = InputGap(f"Source transport temporarily unavailable ({transport}).")
            gap.reason_code = "source_unavailable"
            raise gap from None

    def tick(self, *, new_run=False):
        now = self.clock()
        run, request, due, pending = None, None, None, None
        starting, phase = True, "store"
        try:
            run = self.store.active()
            starting = run is None or new_run
            request = self.store.new_run_request(run)
            if run and run["status"] != "running" and not new_run and not request:
                request = self._queue_recovery(run)
                if not request:
                    self.store.health("blocked", "Run is blocked; correct input sources and explicitly use --new-run.")
                    diagnostics.emit("shadow-worker", "shadow_run_blocked", context={
                        "run_status": run["status"], "reason_code": "explicit_new_run_required"})
                    return False
            automatic = request and request.get("mode") == "automatic"
            if automatic:
                # Recovery never silently opts an old experiment into a changed strategy.
                self._validate_recovery(run)
            new_run = bool(request) or new_run
            starting = run is None or new_run
            if starting:
                bounds = session_bounds(now.astimezone(NY).date())
                near_open = bounds and 0 < (bounds[0] - now).total_seconds() <= 120
                if not bounds or (not near_open and not bounds[0] <= now < bounds[1]):
                    self._preopen = None
                    message = "Waiting for a regular NYSE session to capture actual seed."
                    if automatic:
                        message = f"Recovery for {request['run_id']} queued ({request['reason_code']}). {message}"
                    self.store.health("waiting", message)
                    return False
                key = (run["id"] if run else None, fingerprint(self.producer.config))
                phase = "seed"
                cached = self._preopen
                self._preopen = None
                if (not near_open and cached and cached["key"] == key
                        and 0 <= (now - timestamp(cached["seed"]["timestamp"])).total_seconds() <= 120):
                    seed = cached["seed"]
                else:
                    seed = self._acquire(self.producer.seed)
                if self.clock() >= bounds[1]:
                    raise InputGap("Startup seed acquisition crossed the session boundary; fresh inputs are required.")
                if automatic:
                    seed = copy.deepcopy(seed)
                    seed["source_evidence"]["recovery"] = {
                        "mode": "automatic", "previous_run_id": request["run_id"],
                        "requested_at": request["requested_at"], "reason_code": request["reason_code"],
                    }
                phase = "engine"
                state = self.engine.initialize(seed, self.producer.config)
                initial_state = self.engine.checkpoint(state)
                if self.clock() < bounds[0]:
                    self._preopen = {"key": key, "seed": seed}
                    self.store.health("waiting", "Coherent pre-open seed held in memory; awaiting actual opening frame."
                                      + (f" Recovery queued for {request['run_id']}." if automatic else ""))
                    return False
                # A seed alone is not a usable experiment. Keep the attempt in
                # memory until a complete first frame passes engine validation.
                run = {"config": self.producer.config, "state": initial_state}
                if near_open:
                    now = self.clock()
            phase = "engine"
            state = self._validate_continuation(run)
            phase = "store"
            pending = None if starting else self.store.pending(run["id"])
            if pending:
                # Replay durable prices even if the process is now past this slot.
                frame = pending
            else:
                due = next_tick(run, now)
                if due is None or now < due:
                    self.store.health("waiting")
                    return False
                if (now - due).total_seconds() > 120:
                    phase = "acquisition"
                    raise InputGap("Missed scheduled observation; cannot backfill using current quotes.")
                opening, closing = session_bounds(due.astimezone(NY).date())
                if now >= closing:
                    phase = "acquisition"
                    raise InputGap("Cycle acquisition crossed the regular-session boundary; cannot use after-hours quotes.")
                minutes = int((due - opening).total_seconds() // 60)
                first = not state.get("last_frame_id")
                decision = first or minutes % 15 == 0
                final = due == closing - dt.timedelta(minutes=1)
                eod = due == closing - dt.timedelta(minutes=15) or (
                    first and due >= closing - dt.timedelta(minutes=15))
                phase = "acquisition"
                frame = self._acquire(self.producer.frame,
                    state, due.isoformat(), due, eod=eod,
                    decision_cycle=decision and (first or not final), end_mark=final)
                if (self.clock() - due).total_seconds() > 120:
                    raise InputGap("Cycle acquisition missed its 120-second scheduled observation window.")
                if not starting and self.clock() >= closing:
                    raise InputGap("Cycle acquisition crossed the regular-session boundary; cannot use after-hours quotes.")
                if starting and (self.clock() - timestamp(seed["timestamp"])).total_seconds() > 120:
                    raise InputGap("Startup seed exceeded its 120-second freshness window; fresh inputs are required.")
                phase = "store"
                if not starting:
                    self.store.stage(run["id"], frame)
            if starting and not opening <= self.clock() < closing:
                raise InputGap("Startup frame acquisition crossed the session boundary; fresh inputs are required.")
            phase = "engine"
            state, output = self.engine.advance(state, frame["engine_frame"])
            phase = "store"
            if starting:
                if not opening <= self.clock() < closing:
                    raise InputGap("Startup acquisition crossed the session boundary; fresh inputs are required.")
                self.store.create_run(seed, self.producer.config, initial_state,
                                      self.engine.engine_fingerprint(), explicit=new_run)
                starting = False
                run = self.store.active()
                diagnostics.emit("shadow-worker", "shadow_run_created", level="INFO")
                self.store.stage(run["id"], frame)
            self.store.commit_cycle(run["id"], frame, self.engine.checkpoint(state), output)
            if hasattr(self.producer, "committed"):
                self.producer.committed(frame)
            self._retry = None
            self.store.health("running", cycle_at=frame["occurred_at"])
            if automatic:
                diagnostics.emit("shadow-worker", "shadow_run_recovered", level="INFO", context={
                    "old_run_id": request["run_id"], "new_run_id": run["id"],
                    "reason_code": request["reason_code"]})
            diagnostics.emit("shadow-worker", "shadow_cycle_committed", level="INFO",
                             context={"cycle_at": frame["occurred_at"], "run_status": "running"})
            return True
        except (InputGap, ValueError, StoreError) as exc:
            if starting and isinstance(exc, InputGap) and phase in {"seed", "acquisition", "store"}:
                if (phase == "acquisition" and timestamp(seed["timestamp"]) < bounds[0]
                        and 0 <= (self.clock() - timestamp(seed["timestamp"])).total_seconds() <= 120
                        and self.clock() < bounds[1]):
                    # A failed quote attempt does not invalidate an independently
                    # observed, still-fresh opening portfolio.
                    self._preopen = {"key": key, "seed": seed}
                message = f"Startup inputs unavailable; will retry with a fresh seed: {exc}"[:1000]
                self.store.health("waiting", message)
                diagnostics.emit("shadow-worker", "shadow_startup_waiting", error=exc,
                                 context={"reason_code": "startup_input_readiness"})
                LOG.warning("%s", message)
                return False
            reason_code = ((getattr(exc, "reason_code", None) or legacy_gap_reason(str(exc)))
                           if isinstance(exc, InputGap) and phase == "acquisition" else None)
            recoverable = reason_code in RECOVERABLE_REASONS and not starting and pending is None
            if (recoverable and due and (self.clock() - due).total_seconds() < 120
                    and self.clock() < session_bounds(due.astimezone(NY).date())[1]):
                self._retry = {"run_id": run["id"], "scheduled_at": due.isoformat(), "last_error": str(exc)}
                self.store.health("waiting", f"Retrying scheduled observation {due.isoformat()} "
                                  f"within its 120-second window: {exc}"[:1000])
                diagnostics.emit("shadow-worker", "shadow_input_retry", error=exc, context={
                    "run_id": run["id"], "scheduled_at": due.isoformat(), "reason_code": reason_code})
                return False
            diagnostics.emit("shadow-worker", "shadow_input_blocked", error=exc,
                             context={"reason_code": "input_or_engine_validation"})
            if request and request.get("mode") == "automatic" and not recoverable:
                self.store.hold_recovery(str(exc)[:1000])
            # Messages in these classes contain controlled validation text, not URLs/keys.
            if starting:
                self.store.health("blocked", str(exc)[:1000])
            else:
                evidence = {"failure_class": "acquisition" if recoverable else phase,
                            "reason_code": reason_code if recoverable else "manual_action_required"}
                if due:
                    evidence["scheduled_at"] = due.isoformat()
                if self._retry and run and self._retry["run_id"] == run["id"]:
                    evidence["last_attempt"] = self._retry
                try:
                    self.store.block(str(exc)[:1000], self.clock().isoformat(), evidence)
                    if recoverable:
                        self._queue_recovery(self.store.active())
                except (StoreError, ValueError) as blocked_exc:
                    self.store.health("blocked", str(blocked_exc)[:1000])
            LOG.error("Shadow input/engine blocked: %s", exc)
            return False
        except Exception as exc:
            diagnostics.emit("shadow-worker", "shadow_acquisition_failed", error=exc)
            # Never persist HTTP exceptions containing secret-bearing URLs.
            detail = ("no new run was created; inspect diagnostics."
                      if starting else "explicit new run required.")
            message = f"Shadow acquisition failed ({type(exc).__name__}); {detail}"
            if request and request.get("mode") == "automatic":
                self.store.hold_recovery(message)
            if starting:
                self.store.health("blocked", message)
            else:
                try:
                    self.store.block(message, now.isoformat(), {
                        "failure_class": phase, "reason_code": "manual_action_required"})
                except (StoreError, ValueError):
                    self.store.health("blocked", message)
            LOG.error("%s", message)
            return False

    def flush(self):
        try:
            self.store.upload(self.cloud)
            if time.monotonic() >= self._next_diagnostic:
                self._next_diagnostic = time.monotonic() + 60
                active = self.store.active()
                diagnostics.emit("shadow-worker", "shadow_upload_progress", level="INFO", context={
                    "run_status": active["status"] if active else "waiting",
                    "sequence": active["sequence"] if active else 0,
                    "pending_events": self.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],
                })
            return True
        except Exception as exc:
            diagnostics.emit("shadow-worker", "shadow_upload_failed", error=exc)
            self.store.health("upload_error", f"Cloud upload failed ({type(exc).__name__}); durable outbox retained.")
            LOG.error("Shadow upload failed (%s); retained for retry.", type(exc).__name__)
            return False


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--once", action="store_true")
    result.add_argument("--poll", type=float, default=30)
    result.add_argument("--spool", default=DEFAULT_SPOOL)
    result.add_argument("--account", default=os.getenv("IBKR_ACCOUNT"))
    recovery = result.add_mutually_exclusive_group()
    recovery.add_argument("--new-run", action="store_true",
                          help="Explicitly supersede prior hypothetical run with fresh actual account evidence.")
    recovery.add_argument("--queue-new-run", action="store_true",
                          help="Queue one replacement of the blocked run in the local spool, then exit.")
    return result


def run(args, *, producer=None, cloud=None, stop=None, engine=None):
    if getattr(args, "queue_new_run", False):
        store = ShadowStore(args.spool)
        try:
            request = store.queue_new_run()
            print(f"Queued one replacement for blocked run {request['run_id']}; "
                  "the worker will wait for valid regular-session inputs.")
            return 0
        finally:
            store.close()
    if args.poll <= 0 or not args.account:
        raise ValueError("A positive --poll and explicit --account / IBKR_ACCOUNT are required.")
    if producer is None or cloud is None:
        import requests
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("INTRADAY_SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("Server-side Supabase configuration is required.")
        cloud = create_client(url, key)
        source_key = os.getenv("SHADOW_SOURCE_SUPABASE_KEY") or key
        sources = ReadOnlySources(create_client(url, source_key))
        producer = InputProducer(sources, PublicMarketData(requests.Session(), os.getenv("FMP_API_KEY")),
                                 args.account, effective_config())
    stop = stop or threading.Event()
    store = ShadowStore(args.spool)
    worker = Worker(store, producer, cloud, engine=engine)
    try:
        new_run = args.new_run
        while not stop.is_set():
            before = store.active()
            worker.tick(new_run=new_run)
            after = store.active()
            # --new-run remains pending while closed; consume only after a fresh run exists.
            if after and (not before or after["id"] != before["id"]):
                new_run = False
            uploaded = worker.flush()
            if args.once:
                return 0 if uploaded and after and after["status"] == "running" and not new_run else 1
            stop.wait(args.poll)
    finally:
        store.close()


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        return run(parser().parse_args(), stop=stop)
    except Exception as exc:
        diagnostics.emit("shadow-worker", "shadow_startup_failed", error=exc, level="CRITICAL")
        LOG.error("Shadow startup failed (%s); inspect configuration and local spool permissions.",
                  type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
