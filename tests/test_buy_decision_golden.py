"""
test_buy_decision_golden.py — Phase 0 characterization ("golden") tests for the
LIVE portfolio-construction path ``buying.run_market_open_buys``.

WHY THIS FILE EXISTS
────────────────────
The existing ``tests/test_buy_gates.py`` pins each buy gate in ISOLATION: given
one trigger, does a single gate place / not-place an order. That is necessary but
it does NOT pin the behaviour that actually decides what the portfolio looks like:

  • the RANKING order buys are attempted in (final_score desc),
  • MULTI-BUY SLOT DEPLETION — each successful buy consumes a slot so the book
    fills to exactly MAX_POSITIONS and the remainder are swept as SLOTS_FULL,
  • CASH ACCOUNTING across the cycle (cycle_cash_spent) and equity-capped sizing,
  • the exact SHARE COUNT the sizing formula produces, and
  • the ordered sequence of per-trigger DECISION reason codes.

These are the "decision core" behaviours the backtest-fidelity refactor (Phase 1,
extracting a shared ``decision_core``) MUST preserve byte-for-byte. This file is
the safety net that proves the refactor did not change what the live bot decides.

It is PURE TEST CODE — it adds no runtime behaviour and carries zero live risk.

WHY A CUSTOM STATEFUL DB (not conftest.make_supabase_mock)
─────────────────────────────────────────────────────────
``make_supabase_mock`` returns a FIXED ``portfolio_positions`` list — inserts do
NOT become visible on the next ``select``. Slot depletion is therefore invisible
to it, so it cannot characterize the very behaviour under test. ``_StatefulDB``
below makes ``portfolio_positions`` inserts visible to subsequent selects, which
is what drives ``len(holdings)`` up and eventually trips the capacity break.
"""

import os
import sys
import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import execution_agent as ea  # noqa: E402
import buying  # noqa: E402


# ── Stateful Supabase fake ────────────────────────────────────────────────────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Chainable query builder. Filters are waved through (the buy path applies
    its own logic); the only state that matters is that portfolio_positions
    INSERTS become visible to later SELECTS on the same table."""

    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._insert = None

    # read chain — all no-ops that return self
    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    # write chain
    def insert(self, payload):
        self._insert = payload
        return self

    def update(self, payload):
        # updates (hard_stop_price, etc.) don't change slot accounting
        return self

    def delete(self, *a, **k):
        return self

    def execute(self):
        if self._insert is not None:
            rows = self._insert if isinstance(self._insert, list) else [self._insert]
            self._db.tables.setdefault(self._table, []).extend(rows)
            return _Result(list(rows))
        return _Result(list(self._db.tables.get(self._table, [])))


class _StatefulDB:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)


# ── Filling IB fake ───────────────────────────────────────────────────────────
def _broker_position(ticker, shares, price):
    return SimpleNamespace(
        account="DU123", position=shares, averageCost=price,
        marketPrice=price, marketValue=shares * price, unrealizedPNL=0.0,
        contract=SimpleNamespace(symbol=ticker, secType="STK",
                                 conId=int.from_bytes(ticker.encode(), "big")),
    )


def _make_filling_ib(fill_price: float = 100.0):
    """An IB whose placeOrder returns a fully-Filled trade for the order's whole
    quantity at ``fill_price``. This is what lets a buy actually 'happen' so the
    position is inserted and the next slot is consumed."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.RequestTimeout = 0
    ib.managedAccounts.return_value = ["DU123"]
    ib.client.clientId = 1
    ib.positions.return_value = []
    ib.reqPositions.side_effect = lambda: list(ib.positions())
    ib.portfolio.side_effect = lambda: list(ib.positions())
    ib.openTrades.return_value = []
    ib.reqAllOpenOrders.side_effect = lambda: [
        trade for trade in ib.openTrades()
        if trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled")
    ]
    ib.sleep.return_value = None
    def qualify(*contracts):
        for contract in contracts:
            contract.conId = int.from_bytes(contract.symbol.encode(), "big")
        return list(contracts)
    ib.qualifyContracts.side_effect = qualify
    ib.cancelOrder.return_value = None

    def _place(contract, order):
        assert order.action == "BUY"
        trade = MagicMock()
        trade.contract = contract
        trade.order = order
        order.clientId = ib.client.clientId
        trade.orderStatus.status = "Filled"
        trade.orderStatus.filled = order.totalQuantity
        trade.orderStatus.remaining = 0
        trade.orderStatus.avgFillPrice = fill_price
        trade.log = []
        positions = ib.positions.return_value
        held = next((row for row in positions
                     if row.account == order.account
                     and row.contract.conId == contract.conId), None)
        if held is None:
            held = _broker_position(contract.symbol, 0, fill_price)
            assert held.contract.conId == contract.conId
            positions.append(held)
        held.position += order.totalQuantity
        held.marketValue = held.position * fill_price
        return trade

    ib.placeOrder.side_effect = _place
    return ib


# ── Decision capture ──────────────────────────────────────────────────────────
class _Recorder:
    """Captures the ordered (ticker, decision, reason_code) sequence the live
    path emits via trigger_audit, plus the raw position rows inserted (to assert
    share sizing)."""

    def __init__(self, db):
        self.decisions = []          # list[(ticker, decision, reason_code)]
        self._db = db

    def record_trigger_decision(self, client, trigger, decision, reason_code, **kw):
        self.decisions.append((trigger["ticker"], decision, reason_code))

    def record_decisions_bulk(self, client, triggers, decision, reason_code, **kw):
        for t in triggers:
            self.decisions.append((t["ticker"], decision, reason_code))

    # convenience views
    def bought(self):
        return [t for (t, d, r) in self.decisions if r == ea.trigger_audit.BOUGHT]

    def reason_of(self, ticker):
        return [r for (t, d, r) in self.decisions if t == ticker]

    def inserted_positions(self):
        return self._db.tables.get("portfolio_positions", [])


def _trigger(ticker, final_score, ai_grade="A", volume_surge=2.0,
             close_price=100.0):
    """A minimal BREAKOUT trigger row good enough to clear every non-target gate
    (volume, pivot, earnings, cooling-off) so the tests isolate ranking / slots /
    sizing."""
    triggered_at = datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    return {
        "ticker": ticker,
        "triggered_at": triggered_at,
        "close_price": close_price,
        "volume_surge": volume_surge,
        "pivot_distance_pct": -0.5,
        "final_score": final_score,
        "ai_grade": ai_grade,
        "trigger_type": "BREAKOUT",
    }


def _held(ticker):
    return {"ticker": ticker, "market_value": 20_000.0,
            "shares": 200, "buy_price": 100.0}


def _run(db, ib, recorder, own_cash=100_000.0, net_liq=100_000.0):
    """Drive run_market_open_buys with every non-target dependency neutralised."""
    ib.positions.return_value = [
        _broker_position(pos["ticker"], pos["shares"], pos["buy_price"])
        for pos in db.tables.get("portfolio_positions", [])
    ]
    non_degraded = SimpleNamespace(degraded=False, missing_advisory=[],
                                   missing_critical=[])
    with patch("execution_agent.supabase", db), \
         patch("execution_agent.get_supabase_client", return_value=db), \
         patch("execution_agent.schema_guard.check_schema",
               return_value=non_degraded), \
         patch("execution_agent.get_margin_loan", return_value=0.0), \
         patch("execution_agent.is_market_bullish", return_value=True), \
         patch("execution_agent.get_own_cash", return_value=own_cash), \
         patch("execution_agent.get_net_liquidation", return_value=net_liq), \
         patch("execution_agent.cooling_off.compute_cooled_map", return_value={}), \
         patch("execution_agent.fetch_ibkr_delayed_price",
               return_value=(100.0, "delayed")), \
         patch("execution_agent.get_ibkr_account", return_value="DU123"), \
         patch("execution_agent.place_protective_stops"), \
         patch("execution_agent.record_buy_commission"), \
         patch("execution_agent._get_entry_rs", return_value=None), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.trigger_audit.record_trigger_decision",
               side_effect=recorder.record_trigger_decision), \
         patch("execution_agent.trigger_audit.record_decisions_bulk",
               side_effect=recorder.record_decisions_bulk), \
         patch("buying.maybe_report_unfilled_slots"):
        buying.run_market_open_buys(ib)


# ── Golden characterization ───────────────────────────────────────────────────
class TestBuyDecisionGolden:

    def test_full_cycle_ranking_slots_veto_and_bulk_sweep(self):
        """GOLDEN: 3 held + 5 triggers → exactly the two top-scored eligible names
        are bought (in score order), the D-grade name is vetoed even though it
        outranks the buys' capacity, and every remaining trigger from the point
        of capacity is swept as one SLOTS_FULL bulk.

        This pins ranking + multi-buy slot depletion + veto ordering + bulk sweep
        in a single scenario — the whole decision-core contract."""
        db = _StatefulDB(
            daily_triggers=[
                # deliberately UNSORTED on input
                _trigger("LOWSCR", 55, ai_grade="C"),
                _trigger("WIN_A", 95),
                _trigger("VETO_D", 88, ai_grade="D"),
                _trigger("WIN_B", 90),
                _trigger("WIN_C", 85),
            ],
            portfolio_positions=[_held("HELD1"), _held("HELD2"), _held("HELD3")],
        )
        ib = _make_filling_ib()
        rec = _Recorder(db)
        _run(db, ib, rec)

        # 1) exactly the two highest-scored eligible names bought, IN score order
        assert rec.bought() == ["WIN_A", "WIN_B"]

        # 2) book filled to MAX_POSITIONS (3 held + 2 bought)
        assert len(db.tables["portfolio_positions"]) == ea.MAX_POSITIONS == 5

        # 3) D-grade vetoed even though it is processed before the sweep
        assert rec.reason_of("VETO_D") == [ea.trigger_audit.AI_VETO]

        # 4) remaining eligible name(s) after capacity are swept as SLOTS_FULL.
        #    WIN_C is the first trigger reached with the book full → bulk sweep
        #    covers WIN_C..end (LOWSCR sorts last, after WIN_C).
        assert rec.reason_of("WIN_C") == [ea.trigger_audit.SLOTS_FULL]
        assert ea.trigger_audit.SLOTS_FULL in rec.reason_of("LOWSCR")

        # 5) the overall decision order follows the score ranking
        order = [t for (t, d, r) in rec.decisions]
        assert order[:3] == ["WIN_A", "WIN_B", "VETO_D"]

    def test_share_sizing_is_equity_capped_and_exact(self):
        """Pins the exact share count the sizing formula produces so a refactor
        cannot silently change position size.

        3 held → 2 free slots, $100k cash, $100k equity:
          equity cap = 100_000 / MAX_POSITIONS(5) = 20_000
          uncapped   = 100_000 / 2 = 50_000  → CAPPED to 20_000
          shares     = int((20_000 - PRICE_SAFETY_RESERVE) / 100.0)
        """
        expected = int((20_000.0 - ea.PRICE_SAFETY_RESERVE) / 100.0)
        db = _StatefulDB(
            daily_triggers=[_trigger("WIN_A", 95), _trigger("WIN_B", 90)],
            portfolio_positions=[_held("H1"), _held("H2"), _held("H3")],
        )
        ib = _make_filling_ib(fill_price=100.0)
        rec = _Recorder(db)
        _run(db, ib, rec, own_cash=100_000.0, net_liq=100_000.0)

        inserted = rec.inserted_positions()
        bought = [p for p in inserted if p["ticker"] in ("WIN_A", "WIN_B")]
        assert len(bought) == 2
        for p in bought:
            assert p["shares"] == expected, (
                f"{p['ticker']} sized {p['shares']}, expected {expected}")

    # ── Teeth: the tests must FAIL if the decision logic is perturbed ──────────

    def test_teeth_ranking_determines_which_names_are_bought(self):
        """If the two top scores move to different tickers, the bought set MUST
        follow the scores — proving the test observes ranking, not input order."""
        db = _StatefulDB(
            daily_triggers=[
                _trigger("ALPHA", 60),
                _trigger("BRAVO", 99),   # now top
                _trigger("CHARLIE", 98),  # now second
                _trigger("DELTA", 61),
            ],
            portfolio_positions=[_held("H1"), _held("H2"), _held("H3")],
        )
        ib = _make_filling_ib()
        rec = _Recorder(db)
        _run(db, ib, rec)
        assert rec.bought() == ["BRAVO", "CHARLIE"]

    def test_teeth_single_free_slot_depletes_after_one_buy(self):
        """4 held → 1 free slot. Only the single highest-scored name is bought;
        the next eligible name is SLOTS_FULL. Proves the slot counter depletes
        after a buy rather than allowing a second."""
        db = _StatefulDB(
            daily_triggers=[_trigger("TOP", 95), _trigger("NEXT", 90)],
            portfolio_positions=[_held("H1"), _held("H2"), _held("H3"),
                                 _held("H4")],
        )
        ib = _make_filling_ib()
        rec = _Recorder(db)
        _run(db, ib, rec)
        assert rec.bought() == ["TOP"]
        assert ea.trigger_audit.SLOTS_FULL in rec.reason_of("NEXT")
        assert len(db.tables["portfolio_positions"]) == ea.MAX_POSITIONS

    def test_teeth_sizing_tracks_available_cash(self):
        """Halving equity halves the equity cap and therefore the share count —
        proving the sizing formula is actually exercised, not a fixed constant."""
        # 3 held → 2 free. net_liq 50k → cap 10k → shares int((10k-reserve)/100)
        expected = int((10_000.0 - ea.PRICE_SAFETY_RESERVE) / 100.0)
        db = _StatefulDB(
            daily_triggers=[_trigger("WIN_A", 95)],
            portfolio_positions=[_held("H1"), _held("H2"), _held("H3")],
        )
        ib = _make_filling_ib(fill_price=100.0)
        rec = _Recorder(db)
        _run(db, ib, rec, own_cash=100_000.0, net_liq=50_000.0)
        inserted = [p for p in rec.inserted_positions() if p["ticker"] == "WIN_A"]
        assert len(inserted) == 1
        assert inserted[0]["shares"] == expected
