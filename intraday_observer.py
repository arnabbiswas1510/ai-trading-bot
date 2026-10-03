"""Independent observations, never live decisions or brokerage instructions.

IB's readonly=True only changes initial synchronization, not broker permissions.
This process also hides the raw connection behind a narrow capability and blocks
order submission/cancellation at both SDK layers. Dedicated broker credentials
with read-only permissions remain the stronger operational boundary.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
from pathlib import Path
import signal
import threading
import time
from types import SimpleNamespace

import research_diagnostics as diagnostics
from intraday_capture import Recorder, _fields, collector_config, now, trade_snapshot

LOG = logging.getLogger(__name__)
DEFAULT_SPOOL = "/app/logs/intraday_observer.sqlite3"
CONTRACT_FIELDS = "conId symbol localSymbol secType currency exchange"
EXECUTION_FIELDS = ("execId time acctNumber side shares price permId clientId "
                    "orderId cumQty avgPrice orderRef modelCode")
COMMISSION_FIELDS = "execId commission currency realizedPNL yield_ yieldRedemptionDate"


class ObservationError(RuntimeError):
    pass


def select_account(accounts, requested=None):
    accounts = sorted(set(accounts))
    if requested:
        if requested not in accounts:
            raise ObservationError("Requested account is not managed by this connection.")
        return requested
    if len(accounts) != 1:
        raise ObservationError("Ambiguous or empty account list; supply --account / IBKR_ACCOUNT.")
    return accounts[0]


def _deny_write(*args, **kwargs):
    raise PermissionError("Observer capability forbids brokerage order writes and binding.")


class ReadOnlyBroker:
    """Only completed reads, event pumping, and connection lifecycle are exposed."""
    __slots__ = ("__ib", "__recorder", "__account", "__timeout", "__owner",
                 "__closing", "__faults", "__seen_fills")

    def __init__(self, ib, recorder, timeout=10):
        self.__ib = ib
        self.__recorder = recorder
        self.__account = None
        self.__timeout = timeout
        self.__owner = threading.get_ident()
        self.__closing = False
        self.__faults = 0
        self.__seen_fills = {}
        for target in (ib, ib.client):
            for name in ("placeOrder", "cancelOrder", "reqGlobalCancel",
                         "reqAutoOpenOrders", "exerciseOptions", "replaceFA"):
                setattr(target, name, _deny_write)
        ib.RequestTimeout = timeout
        ib.RaiseRequestErrors = True
        ib.errorEvent += self.__error
        ib.disconnectedEvent += self.__disconnected

    def __getattr__(self, name):
        raise PermissionError(f"Broker capability does not expose {name}.")

    def __check_thread(self):
        if threading.get_ident() != self.__owner:
            raise ObservationError("Broker reads must remain on the owning main thread.")

    def __error(self, req_id, code, message, contract=None):
        # Known SDK farm notifications are recorded, but are not failed requests.
        informational = code in (2104, 2106, 2107, 2108, 2158)
        if not informational:
            self.__faults += 1
            self.__recorder.error(f"broker error {code} (request {req_id})")
            LOG.error("Observer broker error %s (request %s)", code, req_id)
        self.__recorder.emit("observer_broker_error", {
            "account": self.__account, "request_id": req_id, "code": code,
            "message": message, "informational": informational,
            "received_at": now(), "source": "IBKR_ERROR_EVENT",
        })

    def __disconnected(self):
        if self.__closing:
            return
        self.__faults += 1
        self.__recorder.error("observer broker disconnected")
        LOG.error("Observer broker disconnected")
        self.__recorder.emit("capture_gap", {
            "area": "observer_connection", "account": self.__account,
            "complete": False, "reason": "broker_disconnected", "received_at": now(),
        })

    def connected(self):
        self.__check_thread()
        return self.__ib.isConnected() is True

    def connect(self, host, port, client_id, account=None):
        self.__check_thread()
        if client_id <= 0 or client_id == 1:
            raise ObservationError("Observer client ID must be positive and distinct from execution ID 1.")
        self.__closing = False
        self.__account = None
        self.__ib.connect(host, port, clientId=client_id, readonly=True,
                          account=account or "", timeout=self.__timeout)
        self.__account = select_account(self.__ib.managedAccounts(), account)
        if not self.connected():
            raise ObservationError("Broker disconnected during observer connection.")
        self.__recorder.emit("observer_connection", {
            "account": self.__account, "client_id": client_id, "connected": True,
            "readonly_sdk_flag": True, "broker_enforced_readonly": False,
            "received_at": now(),
        })

    def disconnect(self):
        self.__check_thread()
        self.__closing = True
        self.__ib.disconnect()

    def pump(self, seconds):
        self.__check_thread()
        self.__ib.sleep(seconds)
        self.__record_fills("IBKR_FILL_CACHE_UPDATE")

    def __fill(self, fill, source):
        execution = _fields(fill.execution, EXECUTION_FIELDS)
        if not execution["execId"]:
            raise ObservationError("Execution has no execId.")
        report = _fields(fill.commissionReport, COMMISSION_FIELDS)
        available = bool(report["execId"] == execution["execId"])
        return {
            "account": execution["acctNumber"], "ticker": fill.contract.symbol,
            "contract": _fields(fill.contract, CONTRACT_FIELDS),
            "execution": execution, "commission": report if available else None,
            "commission_complete": available, "source": source,
            "origin": "unknown_observed_not_attributed_to_bot", "received_at": now(),
        }

    def __record_fills(self, source):
        if not self.__account:
            return
        for fill in self.__ib.fills():
            if fill.execution.acctNumber != self.__account:
                continue
            data = self.__fill(fill, source)
            signature = (repr(data["execution"]), repr(data["commission"]))
            key = fill.execution.execId
            if self.__seen_fills.get(key) != signature:
                self.__recorder.emit("observer_fill", data)
                self.__seen_fills[key] = signature

    def snapshot(self):
        self.__check_thread()
        if not self.connected() or not self.__account:
            raise ObservationError("Connected, explicitly scoped broker account required.")
        ib, account = self.__ib, self.__account
        started, faults = now(), self.__faults
        times = {}

        def completed(label, fn):
            begin = now()
            try:
                result = fn()
            except Exception as exc:
                raise ObservationError(f"{label} request failed ({type(exc).__name__}).") from exc
            times[label] = {"requested_at": begin, "completed_at": now()}
            if not self.connected() or self.__faults != faults:
                raise ObservationError(f"{label}: disconnected or broker error during snapshot.")
            return result

        raw_positions = completed("positions", ib.reqPositions)
        if not isinstance(raw_positions, (list, tuple)):
            raise ObservationError("Positions request did not return a completed list.")
        positions = {}
        for p in raw_positions:
            if not getattr(p, "account", None):
                raise ObservationError("Position has no account.")
            if p.account != account:
                continue
            quantity = p.position
            if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                    or not math.isfinite(quantity) or p.contract.conId <= 0):
                raise ObservationError("Invalid signed position quantity or contract.")
            # Keep every asset class, shorts and terminal zero updates.
            positions[p.contract.conId] = {
                **_fields(p, "account position avgCost"),
                "ticker": p.contract.symbol, "contract": _fields(p.contract, CONTRACT_FIELDS),
                "source": "IBKR_REQ_POSITIONS",
            }
        fresh_orders, callback_errors = [], []
        original_open_order = ib.wrapper.openOrder
        original_order_status = getattr(ib.wrapper, "orderStatus", None)
        fresh_statuses = {}

        def observe_order_status(orderId, status, filled, remaining, avgFillPrice,
                                 permId, parentId, lastFillPrice, clientId,
                                 whyHeld, mktCapPrice=0.0):
            fresh_statuses[(clientId, orderId, permId)] = {
                "status": status, "filled": filled, "remaining": remaining,
                "avgFillPrice": avgFillPrice, "lastFillPrice": lastFillPrice,
            }
            if original_order_status is not None:
                original_order_status(orderId, status, filled, remaining, avgFillPrice,
                                      permId, parentId, lastFillPrice, clientId,
                                      whyHeld, mktCapPrice)

        def observe_open_order(order_id, contract, order, state):
            # SDK 0.9.86 only refreshes selected cached Trade.order fields.
            # Copy the wire callback before the SDK merges it into that cache.
            try:
                if not getattr(order, "whatIf", False):
                    row = trade_snapshot(SimpleNamespace(
                        contract=contract, order=order, orderStatus=state))
                    row["order"]["orderId"] = order_id
                    row.update(source="IBKR_OPEN_ORDER_CALLBACK", received_at=now(),
                               status_source="openOrder_orderState_not_cached_fill_status")
                    fresh_orders.append(row)
            except Exception as exc:
                callback_errors.append(type(exc).__name__)
            finally:
                original_open_order(order_id, contract, order, state)

        ib.wrapper.openOrder = observe_open_order
        if original_order_status is not None:
            ib.wrapper.orderStatus = observe_order_status
        try:
            raw_orders = completed("open_orders_all_clients", ib.reqAllOpenOrders)
        finally:
            ib.wrapper.openOrder = original_open_order
            if original_order_status is not None:
                ib.wrapper.orderStatus = original_order_status
        if not isinstance(raw_orders, (list, tuple)):
            raise ObservationError("Open orders request did not return a completed list.")
        if callback_errors or len(fresh_orders) != len(raw_orders):
            raise ObservationError("Fresh open-order callback capture was incomplete.")
        orders = {}
        for row in fresh_orders:
            order = row["order"]
            if not order["account"]:
                raise ObservationError("Open order has no account; cannot safely scope snapshot.")
            if order["account"] == account:
                key = (order["clientId"], order["orderId"], order["permId"])
                if key in fresh_statuses:
                    row["status"] = fresh_statuses[key]
                    row["status_source"] = "fresh_orderStatus_callback_during_completed_request"
                orders[key] = row

        values, marks = {}, {}

        def account_value(row):
            if row.account == account:
                values[(row.tag, row.currency)] = _fields(row, "account tag value currency")

        def portfolio_value(row):
            if row.account == account:
                marks[row.contract.conId] = {
                    **_fields(row, "account position marketPrice marketValue averageCost unrealizedPNL realizedPNL"),
                    "ticker": row.contract.symbol, "contract": _fields(row.contract, CONTRACT_FIELDS),
                    "source": "IBKR_ACCOUNT_DOWNLOAD", "received_at": now(),
                    "provider_timestamp": None,
                }

        ib.accountValueEvent += account_value
        ib.updatePortfolioEvent += portfolio_value
        try:
            # End the previous read subscription to force a new accountDownloadEnd.
            ib.client.reqAccountUpdates(False, account)
            completed("account_download", lambda: ib.reqAccountUpdates(account))
        finally:
            ib.accountValueEvent -= account_value
            ib.updatePortfolioEvent -= portfolio_value
        if not values or not any(row["tag"] == "NetLiquidation" for row in values.values()):
            raise ObservationError("Fresh completed account download lacks NetLiquidation.")
        from ib_insync import ExecutionFilter
        fills = completed("executions", lambda: ib.reqExecutions(
            ExecutionFilter(acctCode=account)))
        if not isinstance(fills, (list, tuple)):
            raise ObservationError("Executions request did not return a completed list.")
        cached = {f.execution.execId: f for f in ib.fills()
                  if f.execution.acctNumber == account}
        scoped_fills = []
        for fill in fills:
            if not fill.execution.acctNumber:
                raise ObservationError("Execution has no account.")
            if fill.execution.acctNumber == account:
                scoped_fills.append(self.__fill(cached.get(fill.execution.execId, fill),
                                               "IBKR_REQ_EXECUTIONS"))
        self.__record_fills("IBKR_REQ_EXECUTIONS")
        rows = list(positions.values())
        return self.__recorder.emit("observer_snapshot", {
            "account": account, "started_at": started, "snapshot_at": now(),
            "connected": True, "complete": True, "atomic": False,
            "freshness": "completed_requests_with_component_receipt_times",
            "component_times": times, "positions": rows,
            "short_positions": [p for p in rows if p["position"] < 0],
            "portfolio_marks": list(marks.values()), "open_orders": list(orders.values()),
            "open_order_scope": "all_clients_visible_to_gateway_selected_account",
            "account_values": list(values.values()), "fills": scoped_fills,
            "execution_scope": "gateway_available_history_not_complete_account_history",
            "commissions_complete": all(f["commission_complete"] for f in scoped_fills),
            "decision_inputs_available": False,
        })


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--once", action="store_true", help="One broker snapshot; require cloud persistence.")
    result.add_argument("--host", default=os.getenv("IB_GATEWAY_HOST", "localhost"))
    result.add_argument("--port", type=int, default=int(os.getenv("IB_GATEWAY_PORT", "4000")))
    result.add_argument("--account", default=os.getenv("IBKR_ACCOUNT"))
    result.add_argument("--client-id", type=int, default=71)
    result.add_argument("--spool", default=DEFAULT_SPOOL)
    result.add_argument("--interval", type=float, default=300)
    result.add_argument("--request-timeout", type=float, default=10)
    result.add_argument("--persist-timeout", type=float, default=45)
    result.add_argument("--max-reconnects", type=int, default=3)
    result.add_argument("--reconnect-delay", type=float, default=5)
    return result


def run(args, *, ib_factory=None, recorder_factory=Recorder, stop_event=None):
    """Return nonzero on failed diagnostics or exhausted reconnects; retain spool."""
    from config import INTRADAY_CAPTURE_SPOOL, INTRADAY_CONFIG_ERRORS
    if (args.client_id <= 0 or args.client_id == 1 or args.max_reconnects < 0
            or any(not math.isfinite(v) or v <= 0 for v in (
                args.interval, args.request_timeout, args.persist_timeout, args.reconnect_delay))
            or Path(args.spool).resolve() == Path(INTRADAY_CAPTURE_SPOOL).resolve()
            or INTRADAY_CONFIG_ERRORS):
        LOG.error("Invalid observer settings, shared execution spool, or invalid research configuration.")
        diagnostics.emit("intraday-observer", "observer_invalid_settings",
                         context={"reason_code": "invalid_settings_or_shared_spool"})
        return 2
    if ib_factory is None:
        from ib_insync import IB
        ib_factory = IB
    stop_event = stop_event or threading.Event()
    rec = recorder_factory({
        **collector_config(), "capture_mode": "observer",
        "mode": "observer", "replay_ready": False,
        "broker_snapshot_seconds": args.interval, "client_id": args.client_id,
    }, spool=args.spool, health_id="intraday-observer", mode="observer")
    broker = None
    result, failures, next_snapshot = 0, 0, 0.0
    diagnostic_uploaded = False
    try:
        rec.start()
        rec.emit("observer_config", rec.config)
        broker = ReadOnlyBroker(ib_factory(), rec, args.request_timeout)
        while not stop_event.is_set():
            if not rec.thread.is_alive():
                raise ObservationError("Recorder worker stopped.")
            try:
                if not broker.connected():
                    broker.connect(args.host, args.port, args.client_id, args.account)
                    diagnostics.emit("intraday-observer", "broker_connected", level="INFO",
                                     context={"client_id": args.client_id})
                if time.monotonic() >= next_snapshot:
                    event = broker.snapshot()
                    if event is None:
                        raise ObservationError("Recorder rejected snapshot.")
                    diagnostics.emit("intraday-observer", "broker_snapshot_queued", level="INFO",
                                     context={"sequence": event["sequence"]})
                    next_snapshot = time.monotonic() + args.interval
                    if args.once:
                        deadline = time.monotonic() + args.persist_timeout
                        while rec.last_uploaded_sequence < event["sequence"]:
                            if (stop_event.is_set() or not rec.thread.is_alive()
                                    or time.monotonic() >= deadline or rec.dropped):
                                raise ObservationError("Snapshot cloud persistence was not confirmed; inspect retained spool.")
                            broker.pump(0.1)
                            if not broker.connected():
                                raise ObservationError("Broker disconnected during diagnostic persistence.")
                        LOG.info("Observer snapshot uploaded; no live decision coverage is claimed.")
                        diagnostic_uploaded = True
                        break
                broker.pump(0.25)
            except Exception as exc:
                diagnostics.emit("intraday-observer", "observer_capture_failed", error=exc)
                # Transport text can contain credentials; only our safe errors get text.
                reason = str(exc) if isinstance(exc, ObservationError) else type(exc).__name__
                LOG.error("Observer capture failed: %s", reason)
                rec.error(f"observer: {reason}")
                rec.emit("capture_gap", {"area": "observer", "complete": False, "reason": reason})
                broker.disconnect()
                if args.once or failures >= args.max_reconnects:
                    result = 2
                    break
                failures += 1
                rec.emit("observer_reconnect", {"attempt": failures, "limit": args.max_reconnects})
                stop_event.wait(args.reconnect_delay)
                next_snapshot = 0
    except Exception as exc:
        diagnostics.emit("intraday-observer", "observer_stopped", error=exc, level="CRITICAL")
        LOG.error("Observer stopped: %s", type(exc).__name__)
        rec.error(f"observer stopped: {type(exc).__name__}")
        result = 2
    finally:
        if broker is not None:
            try:
                broker.disconnect()
            except Exception as exc:
                diagnostics.emit("intraday-observer", "observer_disconnect_failed", error=exc)
                LOG.error("Observer disconnect failed: %s", type(exc).__name__)
                result = 2
        if args.once and not diagnostic_uploaded:
            result = 2
        rec.emit("observer_shutdown", {"status": result, "received_at": now()})
        with rec.lock:
            rec.stopping.set()
        if rec.thread is not None:
            rec.thread.join()
        if rec.dropped or not rec.queue.empty():
            diagnostics.emit("intraday-observer", "observer_shutdown_incomplete",
                             context={"dropped_events": rec.dropped, "pending_events": rec.queue.qsize()})
            LOG.error("Observer shutdown lost records; inspect durable spool and health.")
            result = 2
    return result


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    stopped = threading.Event()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, lambda *_: stopped.set())
    try:
        return run(args, stop_event=stopped)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
