"""Real recorder + live hooks -> replay adapter/core -> persisted backend result."""
import copy
import datetime as dt
import importlib
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import uuid

import pytest

import buying
import execution_agent as ea
import intraday_capture as capture
from intraday_replay import build_dataset, run_comparison

DAY = "2026-09-28"
ACCOUNT = "U_SYNTHETIC"


class MemoryQuery:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.filters, self.sorts = [], []
        self.bounds = None
        self.operation, self.value, self.options = "select", None, {}

    def select(self, *args): return self
    def eq(self, key, value): self.filters.append(("eq", key, value)); return self
    def gte(self, key, value): self.filters.append(("gte", key, value)); return self
    def lte(self, key, value): self.filters.append(("lte", key, value)); return self
    def lt(self, key, value): self.filters.append(("lt", key, value)); return self
    def order(self, key, desc=False): self.sorts.append((key, desc)); return self
    def range(self, start, end): self.bounds = (start, end + 1); return self
    def limit(self, count): self.bounds = (0, count); return self
    def insert(self, value): self.operation, self.value = "insert", value; return self
    def update(self, value): self.operation, self.value = "update", value; return self
    def upsert(self, value, **options):
        self.operation, self.value, self.options = "upsert", value, options
        return self

    def execute(self):
        rows = self.store.rows.setdefault(self.name, [])
        selected = rows[:]
        for op, key, value in self.filters:
            selected = [r for r in selected if r.get(key) is not None and {
                "eq": lambda v: v == value, "gte": lambda v: v >= value,
                "lte": lambda v: v <= value, "lt": lambda v: v < value,
            }[op](r[key])]
        if self.operation == "update":
            for row in selected:
                row.update(copy.deepcopy(self.value))
        elif self.operation in ("insert", "upsert"):
            selected = self.value if isinstance(self.value, list) else [self.value]
            for row in selected:
                keys = self.options.get("on_conflict", "id").split(",")
                old = next((r for r in rows if all(k in row and r.get(k) == row[k] for k in keys)), None)
                if old is None:
                    rows.append(copy.deepcopy(row))
                elif not self.options.get("ignore_duplicates"):
                    old.update(copy.deepcopy(row))
        for key, desc in reversed(self.sorts):
            selected = sorted(selected, key=lambda r: str(r.get(key, "")), reverse=desc)
        if self.bounds:
            selected = selected[self.bounds[0]:self.bounds[1]]
        return NS(data=copy.deepcopy(selected))


class MemoryStore:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    def table(self, name):
        return MemoryQuery(self, name)


@pytest.fixture(params=[False, True], ids=["complete-universe", "unrelated-delisted-symbol"])
def recorded_day(monkeypatch, request):
    root = Path(__file__).parent / f".capture-integration-{uuid.uuid4().hex}"
    clock = {"now": dt.datetime.fromisoformat(DAY + "T09:30:00-04:00")}

    class LiveClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(capture, "now", lambda: clock["now"].isoformat())
    monkeypatch.setattr(ea, "datetime", NS(datetime=LiveClock, date=dt.date,
                                         timedelta=dt.timedelta, timezone=dt.timezone))
    position = dict(
        ticker="HELD", shares=10, buy_price=100.0, buy_date="2026-09-25T13:30:00+00:00",
        stop_loss_pct=ea.STOP_LOSS_PCT, highest_unrealized_pct=1.0, hwm_price=101.0,
        hwm_date="2026-09-25", closed_above_entry=True, scaled_out=False,
        scaled_out_at=None, power_hold=False, exit_armed=False, exit_armed_at=None,
        exit_armed_reason=None, buy_commission=0.35, hard_stop_price=95.0,
        entry_rs_score=90, entry_final_score=80, entry_atr_pct=3.0,
        buy_reason="Synthetic existing holding", breakout_verdict=None,
    )
    signal = json.loads((Path(__file__).parent / "fixtures/live_rule_replay_example.json")
                        .read_text())["events"][0]["triggers"][0]
    veto = dict(signal, ticker="VETO", ai_grade="D", final_score=90,
                adjusted_score=90, observed_at=clock["now"].isoformat(),
                triggered_at=clock["now"].isoformat())
    cooled = dict(veto, ticker="COOLED", ai_grade="A", final_score=95, adjusted_score=95)
    loss = dict(ticker="COOLED", sell_date="2026-09-25T15:00:00-04:00",
                net_profit_loss=-75.0, profit_loss=-74.65, sell_reason="Prior real loss")
    prior_fill = dict(ticker="COOLED", side="SLD", fill_time=loss["sell_date"],
                      shares=5, price=95, exec_id="prior-loss-fill")
    store = MemoryStore({"portfolio_positions": [position], "daily_triggers": [veto, cooled],
                         "trade_history": [loss], "ibkr_fills": [prior_fill]})
    contract = NS(symbol="HELD", conId=1, secType="STK", currency="USD", exchange="SMART")
    held = NS(account=ACCOUNT, contract=contract, position=10, marketPrice=101.0,
              marketValue=1010.0, averageCost=100.0, avgCost=100.0,
              unrealizedPNL=10.0, realizedPNL=0.0)
    orders = []
    for order_id, kind in enumerate(("TRAIL", "STP"), 1):
        order = NS(account=ACCOUNT, action="SELL", orderType=kind, totalQuantity=10,
                   orderId=order_id, clientId=1, permId=1000 + order_id, parentId=0,
                   ocaGroup="existing-protection", ocaType=1,
                   trailingPercent=ea.STOP_LOSS_PCT * 100,
                   trailStopPrice=101 * (1 - ea.STOP_LOSS_PCT), auxPrice=95.0,
                   lmtPrice=0.0, tif="GTC", transmit=True, orderRef="")
        orders.append(NS(contract=contract, order=order,
                         orderStatus=NS(status="Submitted", filled=0, remaining=10,
                                        avgFillPrice=0.0, lastFillPrice=0.0)))
    ib = MagicMock()
    ib.portfolio.return_value = [held]
    ib.positions.return_value = [held]
    ib.openTrades.return_value = orders
    ib.isConnected.return_value = True
    ib.accountValues.return_value = [NS(account=ACCOUNT, tag="NetLiquidation",
                                         currency="USD", value="10000")]
    monkeypatch.setattr(ea, "get_ibkr_account", lambda ib: ACCOUNT)
    monkeypatch.setattr(ea, "get_supabase_client", lambda: store)
    monkeypatch.setattr(ea, "notifier", MagicMock())
    monkeypatch.setattr(buying, "assert_schema_ok", lambda client: True)
    monkeypatch.setattr(buying, "maybe_report_unfilled_slots", lambda *a, **k: None)
    monkeypatch.setattr(ea, "get_own_cash", lambda ib: 8990.0)
    monkeypatch.setattr(ea, "get_margin_loan", lambda ib: 0.0)
    monkeypatch.setattr(ea, "get_net_liquidation", lambda ib: 10000.0)
    monkeypatch.setattr(ea, "is_market_bullish", lambda: True)
    monkeypatch.setattr(ea, "build_ibkr_price_map", lambda ib: {"HELD": 101.0})
    monkeypatch.setattr(ea, "get_position_price", lambda *a: (101.0, "IBKR"))
    monkeypatch.setattr(ea, "get_oca_managed_tickers", lambda client: set())
    monkeypatch.setattr(ea, "_get_market_regime", lambda: "neutral")
    monkeypatch.setattr(ea, "_fetch_current_rs", lambda ticker: 90)
    monkeypatch.setattr(ea, "_fetch_ohlcv", lambda *a, **k: [])
    monkeypatch.setattr(ea, "fetch_held_position_sentiment", lambda ticker: 50)
    monkeypatch.setattr(ea, "maybe_notify_sell_state", lambda *a, **k: None)
    rec = capture.Recorder(capture.effective_config(ea), spool=root / "events.sqlite3")
    monkeypatch.setattr(capture, "_recorder", rec)
    rec.emit("effective_config", rec.config)
    http = MagicMock()

    def quotes(*args, **kwargs):
        elapsed = (clock["now"] - dt.datetime.fromisoformat(DAY + "T09:30:00-04:00")).total_seconds()
        values = {"HELD": 101.0, "VETO": 100.0 if elapsed == 0 else 101.5, "COOLED": 100.0}
        return NS(raise_for_status=lambda: None, json=lambda: [
            {"symbol": ticker, "price": values[ticker], "timestamp": clock["now"].timestamp()}
            for ticker in kwargs["params"]["symbols"].split(",") if ticker in values])
    http.get.side_effect = quotes
    db = None
    try:
        db = rec.open_spool()
        rec.symbols = {t: {"ticker": t, "first_seen_at": capture.now(), "last_seen_at": capture.now()}
                       for t in ("HELD", "VETO", "COOLED") + (("DELISTED",) if request.param else ())}
        for minute in range(0, 391, 5):
            clock["now"] = dt.datetime.fromisoformat(DAY + "T09:30:00-04:00") + dt.timedelta(minutes=minute)
            rec.sample(http)
            rec.next_error_log = 0
            rec.log_errors()
            if minute % 15 == 0 and minute < 390:
                ea.run_market_open_buys(ib)
            if minute == 0:
                # Run the actual durable worker job path after the live cycle.
                rec.journal(db)
                rec.process_snapshot_jobs(db, store)
            if minute % 15 == 0 and minute < 390:
                ea.monitor_portfolio_intraday(ib)
        ib.placeOrder.assert_not_called()
        while not rec.queue.empty():
            rec.journal(db)
        while db.execute("select count(*) from pending").fetchone()[0]:
            rec.flush(db, store)
        yield store
    finally:
        if db is not None:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_real_recorder_live_hooks_to_saved_backend_result(monkeypatch, recorded_day):
    records = recorded_day.rows["intraday_capture_events"]
    assert records and all(r["run_id"] == records[0]["run_id"] for r in records)
    snapshot = next(r["payload"] for r in records if r["kind"] == "portfolio_snapshot"
                    and r["payload"].get("coherence") == "main_thread_adjacent_observations")
    assert snapshot["source_run_id"] == records[0]["run_id"]
    assert snapshot["seed_event_id"] in {r["id"] for r in records}
    assert snapshot["portfolio_positions"][0]["ticker"] == "HELD"
    assert snapshot["trade_history"][0]["ticker"] == "COOLED"
    assert snapshot["ibkr_fills"][0]["exec_id"] == "prior-loss-fill"
    decisions = {r["payload"]["ticker"]: r["payload"]["reason_code"]
                 for r in records if r["kind"] == "trigger_decision"}
    assert decisions == {"COOLED": "COOLING_OFF", "VETO": "AI_VETO"}
    dataset = build_dataset(records, DAY, DAY)
    if any(r["kind"] == "capture_gap" for r in records):
        warnings = dataset["capture_evidence"]["coverage_warnings"]
        assert any(w.get("missing_irrelevant_symbols") == ["DELISTED"] for w in warnings)
        assert any(w.get("related_capture_gap_id") for w in warnings)
    assert dataset["initial_cash"] == 8990.0
    assert dataset["initial_positions"][0]["shares"] == 10
    assert dataset["initial_positions"][0]["broker_anchor"] == pytest.approx(101.0)
    result = run_comparison(records, DAY, DAY)
    assert result["baseline"]["initial_equity"] == result["variant"]["initial_equity"] == 10000
    assert not result["baseline"]["fills"]
    assert [f["ticker"] for f in result["variant"]["fills"] if f["side"] == "BUY"] == ["VETO"]
    assert result["net_final_equity_difference"] > 0
    assert any("COOL" in d["reason"] for d in result["variant"]["decisions"])
    assert any(e["type"] == "eod_latch" for e in dataset["events"])
    assert result["recommendation"] == "research_only_manual_approval"

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    service = importlib.import_module("intraday_service")
    monkeypatch.setattr(service, "get_client", lambda: recorded_day)
    monkeypatch.setattr(service, "_pending_terminal", {})
    monkeypatch.setattr(service, "_job_lock", threading.Lock())

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(service.threading, "Thread", ImmediateThread)
    submitted = service.submit(DAY, DAY)
    saved = service.get_run(submitted["id"])
    assert saved["status"] == "completed", saved.get("error")
    assert saved["result"] == result
    assert saved["summary"]["difference"] == result["net_final_equity_difference"]
    assert saved["request"]["initial_state"] == "recorded_actual"
    json.dumps(saved, allow_nan=False)
