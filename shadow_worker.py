"""Run a durable decision-only portfolio; never connect to IB or submit orders."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import signal
import threading

from market_calendar import session_bounds
from research_configuration import effective_config
from shadow_inputs import InputGap, InputProducer, PublicMarketData, ReadOnlySources, NY, timestamp
from shadow_store import ShadowStore, StoreError, fingerprint

LOG = logging.getLogger(__name__)
UTC = dt.timezone.utc
DEFAULT_SPOOL = "/app/shadow/shadow.sqlite3"


def semantic_config_fingerprint(config):
    """Build labels remain provenance, not strategy settings."""
    return fingerprint({key: value for key, value in config.items() if key != "git_commit"})


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

    def tick(self, *, new_run=False):
        now = self.clock()
        run = self.store.active()
        if run and run["status"] != "running" and not new_run:
            self.store.health("blocked", "Run is blocked; correct input sources and explicitly use --new-run.")
            return False
        try:
            if run is None or new_run:
                # Avoid acquiring an overnight seed and falsely claiming its next-day
                # account state was observed at the opening decision.
                bounds = session_bounds(now.astimezone(NY).date())
                if not bounds or not bounds[0] <= now < bounds[1]:
                    self.store.health("waiting", "Waiting for a regular NYSE session to capture actual seed.")
                    return False
                seed = self.producer.seed()
                state = self.engine.initialize(seed, self.producer.config)
                self.store.create_run(seed, self.producer.config, self.engine.checkpoint(state),
                                      self.engine.engine_fingerprint(), explicit=new_run)
                run = self.store.active()
            if semantic_config_fingerprint(run["config"]) != semantic_config_fingerprint(self.producer.config):
                raise InputGap("Effective strategy configuration changed; explicit new run is required.")
            state = self.engine.restore(run["state"])
            pending = self.store.pending(run["id"])
            if pending:
                # Replay durable prices even if the process is now past this slot.
                frame = pending
            else:
                due = next_tick(run, now)
                if due is None or now < due:
                    self.store.health("waiting")
                    return False
                if (now - due).total_seconds() > 120:
                    raise InputGap("Missed scheduled observation; cannot backfill using current quotes.")
                opening, closing = session_bounds(due.astimezone(NY).date())
                minutes = int((due - opening).total_seconds() // 60)
                first = not state.get("last_frame_id")
                decision = first or minutes % 15 == 0
                final = due == closing - dt.timedelta(minutes=1)
                eod = due == closing - dt.timedelta(minutes=15) or (
                    first and due >= closing - dt.timedelta(minutes=15))
                frame = self.producer.frame(
                    state, due.isoformat(), due, eod=eod,
                    decision_cycle=decision and (first or not final), end_mark=final)
                self.store.stage(run["id"], frame)
            state, output = self.engine.advance(state, frame["engine_frame"])
            self.store.commit_cycle(run["id"], frame, self.engine.checkpoint(state), output)
            if hasattr(self.producer, "committed"):
                self.producer.committed(frame)
            self.store.health("running", cycle_at=frame["occurred_at"])
            return True
        except (InputGap, ValueError, StoreError) as exc:
            # Messages in these classes contain controlled validation text, not URLs/keys.
            self.store.block(str(exc)[:1000], now.isoformat())
            LOG.error("Shadow input/engine blocked: %s", exc)
            return False
        except Exception as exc:
            # Never persist HTTP exceptions containing secret-bearing URLs.
            message = f"Shadow acquisition failed ({type(exc).__name__}); explicit new run required."
            self.store.block(message, now.isoformat())
            LOG.error("%s", message)
            return False

    def flush(self):
        try:
            self.store.upload(self.cloud)
            return True
        except Exception as exc:
            self.store.health("upload_error", f"Cloud upload failed ({type(exc).__name__}); durable outbox retained.")
            LOG.error("Shadow upload failed (%s); retained for retry.", type(exc).__name__)
            return False


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--once", action="store_true")
    result.add_argument("--poll", type=float, default=30)
    result.add_argument("--spool", default=DEFAULT_SPOOL)
    result.add_argument("--account", default=os.getenv("IBKR_ACCOUNT"))
    result.add_argument("--new-run", action="store_true",
                        help="Explicitly supersede prior hypothetical run with fresh actual account evidence.")
    return result


def run(args, *, producer=None, cloud=None, stop=None, engine=None):
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
                return 0 if uploaded and after and after["status"] == "running" else 1
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
        LOG.error("Shadow startup failed (%s); inspect configuration and local spool permissions.",
                  type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
