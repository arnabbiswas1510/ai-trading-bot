"""
golden_log.py — characterization recorder for execution_agent orchestrators.

WHY THIS EXISTS
---------------
The 2026-09-18 pure/impure split of execution_agent.py was safe because it was
byte-identical: the moved code was textually unchanged, so "the tests pass" plus
"the bytes match" was proof. Stage 2 — splitting the three orchestrators
(monitor_portfolio_intraday, reconcile_with_ibkr, run_market_open_buys) into
single-purpose modules — CANNOT be byte-identical: those functions are welded to
the notifier, IBKR and Supabase, so their I/O scaffolding must be reshaped, not
moved. That makes "the tests pass" materially weaker evidence, because a test
that patches a seam can silently become a no-op if a function moves out from
under it (see tests/conftest.py::patch_everywhere for the mechanism).

A golden-log (a.k.a. characterization) test replaces byte-identity with the next
best thing: it captures the EXACT ORDERED SEQUENCE of money-affecting actions the
orchestrator emits for a fixed, scripted input, and asserts that sequence is
unchanged after every refactor step. If the split reorders, drops or duplicates a
sell / arm / stop-placement / notification, the golden diff fails loudly — which
is precisely the failure mode ("a loser silently does not get sold") that opened
the 2026-09-21 investigation.

WHAT IS RECORDED, AND WHY ONLY THIS
-----------------------------------
The recorded seams are the decisions that move money or protect a position:

    execute_sell            a position is liquidated
    execute_scale_out       part of a position is booked
    arm_exit                a tight trailing exit is armed
    place_protective_stops  the OCA (trail + static hard stop) bracket is placed
    cancel_ticker_sell_orders  resting SELL orders are cancelled
    notifier.notify_*       an operator-visible alert is emitted

These are the exact seams the Stage-2 split must preserve. They are patched on
`execution_agent` itself — the call-resolution point — so the recorder stays
correct after callees move to new modules, because their CALLERS remain in
execution_agent by design.

Supabase row writes are deliberately NOT recorded here. They are high-volume,
order-unstable (HWM updates fire every cycle) and would make the golden brittle
without adding safety on the money path. If a future step needs DB-write
ordering locked, add a separate, narrower golden for it rather than widening this
one.
"""
from __future__ import annotations

import datetime
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import execution_agent


class _Event:
    """One recorded money-path action, normalized to compare across runs."""

    __slots__ = ("seq", "kind", "fields")

    def __init__(self, seq: int, kind: str, fields: dict):
        self.seq = seq
        self.kind = kind
        self.fields = fields

    def as_row(self) -> dict:
        # `seq` is intentionally excluded from equality: order is captured by the
        # LIST position, and keeping an absolute counter would make unrelated
        # insertions look like changes far down the sequence.
        return {"kind": self.kind, **self.fields}


def _reason_head(reason) -> str:
    """First clause of a sell/arm reason, before any per-run volatile detail
    (prices, fill ids, timestamps embedded later in the string)."""
    if not reason:
        return ""
    text = str(reason)
    for sep in (":", "—", " - ", "("):
        text = text.split(sep, 1)[0]
    return text.strip()


class MonitorRecorder:
    """Installs recording spies over the money-path seams and collects a
    normalized, ordered event list. Non-recording behaviour is a benign default
    so the orchestrator loop runs to completion."""

    def __init__(self):
        self.events: list[_Event] = []
        self._seq = 0

    def _log(self, kind: str, **fields):
        self.events.append(_Event(self._seq, kind, fields))
        self._seq += 1

    # ── seam spies ────────────────────────────────────────────────────────────
    def _spy_execute_sell(self, ib, client, ticker, shares, buy_price,
                          buy_date, buy_reason, current_price, reason,
                          pos_row=None, market_regime="neutral"):
        self._log("execute_sell", ticker=ticker, shares=int(shares),
                  reason=_reason_head(reason))
        return True

    def _spy_execute_scale_out(self, ib, client, pos, ticker, *a, **kw):
        self._log("execute_scale_out", ticker=ticker,
                  shares=int(pos.get("shares") or 0))
        return True

    def _spy_arm_exit(self, ib, client, ticker, shares, current_price, reason,
                     now_ny, *a, **kw):
        self._log("arm_exit", ticker=ticker, shares=int(shares),
                  reason=_reason_head(reason))
        return True

    def _spy_place_protective_stops(self, ib, contract, shares, trail_pct,
                                    hard_price, account, *a, **kw):
        self._log("place_protective_stops",
                  ticker=getattr(contract, "symbol", str(contract)),
                  shares=int(shares),
                  trail_pct=round(float(trail_pct), 4),
                  hard_price=round(float(hard_price), 2))
        return (f"OCA_{getattr(contract, 'symbol', 'X')}", round(float(trail_pct), 4))

    def _spy_cancel(self, ib, ticker, *a, **kw):
        self._log("cancel_sells", ticker=ticker)
        return 0

    def _make_notifier(self):
        rec = self

        class _RecordingNotifier:
            def __getattr__(self, name):
                def _method(*args, **kwargs):
                    ticker = None
                    if args and isinstance(args[0], str) and args[0].isupper() \
                            and len(args[0]) <= 6:
                        ticker = args[0]
                    ticker = kwargs.get("ticker", ticker)
                    rec._log("notify", method=name, ticker=ticker)
                    return None
                return _method

        return _RecordingNotifier()

    def rows(self) -> list[dict]:
        return [e.as_row() for e in self.events]


@contextmanager
def record_monitor(positions, live_price_map, *, now=None,
                   ohlcv=None, current_rs=90, extra_patches=None):
    """Drive monitor_portfolio_intraday over a scripted book and yield a
    MonitorRecorder holding the ordered money-path event list.

    positions       list of portfolio_positions rows (see conftest.make_position)
    live_price_map   {ticker: price} used for get_live_price and IBKR marks
    now              fixed America/New_York datetime (default 2026-06-17 11:30)
    """
    tz = ZoneInfo("America/New_York")
    now = now or datetime.datetime(2026, 6, 17, 11, 30, tzinfo=tz)
    rec = MonitorRecorder()

    # Supabase read stub: return the scripted positions for the open-positions
    # select, empty for the trigger/history selects, and accept every write.
    sb = MagicMock()
    pos_res = MagicMock(data=positions)
    sb.table.return_value.select.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()

    ib = MagicMock()
    ib.portfolio.return_value = []          # marks come from get_live_price stub
    ib.positions.return_value = []
    ib.managedAccounts.return_value = ["DU1234567"]
    ib.qualifyContracts.return_value = None
    ib.sleep.return_value = None
    ib.reqTickers.return_value = []
    ib.accountValues.return_value = []

    def _price(ticker, *a, **kw):
        return float(live_price_map.get(ticker, 0.0))

    patches = [
        patch("execution_agent.supabase", sb),
        patch("execution_agent.notifier", rec._make_notifier()),
        patch("execution_agent.get_live_price", side_effect=_price),
        patch("execution_agent._fetch_ohlcv", return_value=ohlcv or []),
        patch("execution_agent._fetch_current_rs", return_value=current_rs),
        patch("execution_agent.execute_sell", side_effect=rec._spy_execute_sell),
        patch("execution_agent.execute_scale_out", side_effect=rec._spy_execute_scale_out),
        patch("execution_agent.arm_exit", side_effect=rec._spy_arm_exit),
        patch("execution_agent.place_protective_stops", side_effect=rec._spy_place_protective_stops),
        patch("execution_agent.cancel_ticker_sell_orders", side_effect=rec._spy_cancel),
    ]
    for p in (extra_patches or []):
        patches.append(p)

    dt_patch = patch("execution_agent.datetime")
    patches.append(dt_patch)

    started = [p.start() for p in patches]
    try:
        mock_dt = started[-1]
        mock_dt.datetime.now.side_effect = lambda *a, **kw: now
        mock_dt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_dt.date.fromisoformat.side_effect = datetime.date.fromisoformat
        mock_dt.date.today.return_value = now.date()
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta
        execution_agent.monitor_portfolio_intraday(ib)
        yield rec
    finally:
        for p in reversed(patches):
            p.stop()
