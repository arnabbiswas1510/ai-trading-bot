"""
tests/test_oca_managed_exit.py — Smart OCA Managed Exit.

The dangerous property of this feature is that a PLACED request SUSPENDS the
automated exit ladder for that ticker. If suspension ever fires spuriously, a
position is left with no stop at all. Most of these tests exist to pin that
down.
"""
import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import execution_agent


NY = ZoneInfo("America/New_York")


def _pos(ticker="DELL", shares=46, buy_price=496.04, atr=7.6, **kw):
    p = {
        "ticker": ticker, "shares": shares, "buy_price": buy_price,
        "buy_date": "2026-08-13T16:15:18+00:00", "buy_reason": "CANSLIM",
        "entry_atr_pct": atr, "stop_loss_pct": 0.12, "hwm_price": 499.23,
    }
    p.update(kw)
    return p


# ── resolve_oca_trail_pct ────────────────────────────────────────────────────

class TestResolveTrail:
    def test_fixed_percent_is_passed_through(self):
        pct, note = execution_agent.resolve_oca_trail_pct(_pos(), "TRAIL_PCT", 2.5)
        assert pct == 0.025
        assert "fixed" in note

    def test_atr_auto_scales_to_the_stocks_own_volatility(self):
        # 7.6% ATR * 0.33 = 2.508% -> inside the [1.5%, 4.0%] clamp
        pct, note = execution_agent.resolve_oca_trail_pct(_pos(atr=7.6), "ATR_AUTO", None)
        assert abs(pct - 0.0251) < 0.0005
        assert "ATR" in note

    def test_atr_auto_clamps_low_volatility_names_up(self):
        # A 1% ATR name would imply a 0.33% trail, which sits inside ordinary
        # noise and would fire instantly, cancelling the upper leg.
        pct, _ = execution_agent.resolve_oca_trail_pct(_pos(atr=1.0), "ATR_AUTO", None)
        assert pct == execution_agent.OCA_EXIT_MIN_TRAIL_PCT

    def test_atr_auto_clamps_extreme_volatility_names_down(self):
        pct, _ = execution_agent.resolve_oca_trail_pct(_pos(atr=40.0), "ATR_AUTO", None)
        assert pct == execution_agent.OCA_EXIT_MAX_TRAIL_PCT

    def test_missing_atr_falls_back_to_the_default(self):
        pct, note = execution_agent.resolve_oca_trail_pct(_pos(entry_atr_pct=None), "ATR_AUTO", None)
        expected = (execution_agent.OCA_EXIT_DEFAULT_ATR_PCT / 100.0) * execution_agent.OCA_EXIT_ATR_FRACTION
        assert abs(pct - max(execution_agent.OCA_EXIT_MIN_TRAIL_PCT, expected)) < 1e-9
        assert "no ATR on record" in note


# ── resolve_oca_limit_price ──────────────────────────────────────────────────

class TestResolveLimit:
    def test_breakeven_returns_entry(self):
        assert execution_agent.resolve_oca_limit_price(_pos(), "BREAKEVEN", None, 468.61) == 496.04

    def test_absolute_price(self):
        assert execution_agent.resolve_oca_limit_price(_pos(), "ABS", 489.89, 468.61) == 489.89

    def test_pct_from_entry(self):
        got = execution_agent.resolve_oca_limit_price(_pos(), "PCT_FROM_ENTRY", 3, 468.61)
        assert abs(got - 496.04 * 1.03) < 1e-6

    def test_pct_from_price_uses_the_live_reference_not_entry(self):
        got = execution_agent.resolve_oca_limit_price(_pos(), "PCT_FROM_PRICE", 5, 468.61)
        assert abs(got - 468.61 * 1.05) < 1e-6

    def test_none_mode_means_trail_only(self):
        assert execution_agent.resolve_oca_limit_price(_pos(), "NONE", None, 468.61) is None


class TestLimitCap:
    """
    PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the
    open makes the target greedier the better the gap, so without a ceiling it
    never takes the gift it was waiting for.
    """

    def test_cap_binds_when_the_gap_would_overshoot(self):
        # DELL gaps to 495; +4.54% would ask 517.50, above the 52w high of 514.
        got = execution_agent.resolve_oca_limit_price(
            _pos(), "PCT_FROM_PRICE", 4.54, 495.0, limit_cap=496.04)
        assert got == 496.04

    def test_cap_is_inert_on_a_modest_gap(self):
        got = execution_agent.resolve_oca_limit_price(
            _pos(), "PCT_FROM_PRICE", 4.54, 470.0, limit_cap=496.04)
        assert abs(got - 470.0 * 1.0454) < 1e-6

    def test_uncapped_pct_from_price_scales_with_the_gap(self):
        # The behaviour the cap exists to bound — pinned so it cannot regress.
        got = execution_agent.resolve_oca_limit_price(
            _pos(), "PCT_FROM_PRICE", 4.54, 495.0)
        assert got > 517.0

    def test_cap_applies_to_every_mode(self):
        assert execution_agent.resolve_oca_limit_price(
            _pos(), "PCT_FROM_ENTRY", 10, 468.61, limit_cap=496.04) == 496.04
        assert execution_agent.resolve_oca_limit_price(
            _pos(), "ABS", 600.0, 468.61, limit_cap=496.04) == 496.04

    def test_cap_below_market_still_yields_a_price_not_none(self):
        # A capped target under the market is fine: a SELL limit cannot fill
        # below its limit, so it fills at the better prevailing bid.
        got = execution_agent.resolve_oca_limit_price(
            _pos(), "PCT_FROM_PRICE", 4.54, 500.0, limit_cap=496.04)
        assert got == 496.04

    def test_zero_or_missing_cap_is_ignored(self):
        base = execution_agent.resolve_oca_limit_price(_pos(), "BREAKEVEN", None, 468.61)
        assert execution_agent.resolve_oca_limit_price(
            _pos(), "BREAKEVEN", None, 468.61, limit_cap=0) == base
        assert execution_agent.resolve_oca_limit_price(
            _pos(), "BREAKEVEN", None, 468.61, limit_cap=None) == base


# ── place_oca_exit ───────────────────────────────────────────────────────────

class TestPlaceOca:
    def _place(self, limit=489.89, trail=0.025):
        ib = MagicMock()
        contract = MagicMock()
        contract.symbol = "DELL"
        group, trades = execution_agent.place_oca_exit(ib, contract, 46, limit, trail, "U123")
        orders = [c.args[1] for c in ib.placeOrder.call_args_list]
        return group, orders

    def test_places_both_legs_in_one_oca_group(self):
        group, orders = self._place()
        assert len(orders) == 2
        assert {o.ocaGroup for o in orders} == {group}

    def test_both_legs_use_ocatype_1_cancel_with_block(self):
        # A cash account rejects two unblocked SELLs for the same shares, and a
        # partial fill must reduce the sibling rather than leave a naked short.
        _, orders = self._place()
        assert all(o.ocaType == 1 for o in orders)

    def test_upper_leg_is_a_limit_at_the_target(self):
        _, orders = self._place()
        lmt = [o for o in orders if o.orderType == "LMT"][0]
        assert lmt.action == "SELL"
        assert lmt.lmtPrice == 489.89
        assert lmt.totalQuantity == 46

    def test_lower_leg_is_a_trail_not_a_static_stop(self):
        # A static stop surrenders the whole bounce the upper leg is waiting for.
        _, orders = self._place()
        trail = [o for o in orders if o.orderType == "TRAIL"][0]
        assert trail.action == "SELL"
        assert trail.trailingPercent == 2.5
        assert not any(o.orderType == "STP" for o in orders)

    def test_no_limit_places_trail_only(self):
        _, orders = self._place(limit=None)
        assert len(orders) == 1
        assert orders[0].orderType == "TRAIL"


# ── get_oca_managed_tickers — the suspension guard ───────────────────────────

class TestManagedTickers:
    def _sb(self, rows):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = rows
        return sb

    def test_placed_rows_suspend_the_ladder(self):
        got = execution_agent.get_oca_managed_tickers(
            self._sb([{"ticker": "DELL", "status": "PLACED"}]))
        assert got == {"DELL"}

    def test_non_placed_rows_never_suspend_the_ladder(self):
        got = execution_agent.get_oca_managed_tickers(
            self._sb([{"ticker": "DELL", "status": "PENDING"},
                      {"ticker": "FRO", "status": "FILLED"}]))
        assert got == set()

    def test_rows_without_a_status_never_suspend_the_ladder(self):
        # Guards the real failure mode: a mis-scoped query returning
        # portfolio_positions rows would otherwise strip every stop.
        got = execution_agent.get_oca_managed_tickers(self._sb([_pos(), _pos("FRO")]))
        assert got == set()

    def test_a_failing_query_fails_closed(self):
        sb = MagicMock()
        sb.table.side_effect = Exception("relation exit_requests does not exist")
        assert execution_agent.get_oca_managed_tickers(sb) == set()

    def test_disabled_flag_never_suspends(self):
        with patch.object(execution_agent, "OCA_EXIT_ENABLED", False):
            assert execution_agent.get_oca_managed_tickers(
                self._sb([{"ticker": "DELL", "status": "PLACED"}])) == set()


# ── monitor_portfolio_intraday interaction ───────────────────────────────────

class TestLadderSuspension:
    def test_oca_managed_position_is_skipped_by_the_automated_ladder(self):
        pos = _pos()
        sb = MagicMock()
        sb.table.return_value.select.return_value.execute.return_value.data = [pos]
        ib = MagicMock()
        ib.openTrades.return_value = []

        with patch("execution_agent.supabase", sb), \
             patch("execution_agent.get_supabase_client", return_value=sb), \
             patch("execution_agent.get_oca_managed_tickers", return_value={"DELL"}), \
             patch("execution_agent.get_live_price", return_value=468.61), \
             patch("execution_agent.execute_sell") as sell, \
             patch("execution_agent.arm_exit") as arm, \
             patch("execution_agent.place_trailing_stop") as heal:
            execution_agent.monitor_portfolio_intraday(ib)

        # No exit rule may act, and self-healing must not re-place a stop that
        # would sit outside the OCA group.
        sell.assert_not_called()
        arm.assert_not_called()
        heal.assert_not_called()

    def test_unmanaged_position_is_still_evaluated(self):
        pos = _pos()
        sb = MagicMock()
        sb.table.return_value.select.return_value.execute.return_value.data = [pos]
        ib = MagicMock()
        ib.openTrades.return_value = []

        with patch("execution_agent.supabase", sb), \
             patch("execution_agent.get_supabase_client", return_value=sb), \
             patch("execution_agent.get_oca_managed_tickers", return_value=set()), \
             patch("execution_agent.get_live_price", return_value=468.61), \
             patch("execution_agent.execute_sell"), \
             patch("execution_agent.arm_exit") as arm, \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.place_trailing_stop"):
            execution_agent.monitor_portfolio_intraday(ib)

        # Same position, same price as the test above — the only difference is
        # that it is not OCA-managed. DELL is -$1,262 on the day, so the Early
        # Dollar Stop must arm. This is the control proving suspension is what
        # silenced the ladder in the previous test, not some unrelated skip.
        arm.assert_called_once()
        assert "Early Dollar Stop" in arm.call_args.args[5]


# ── process_exit_requests ────────────────────────────────────────────────────

def _queue_sb(requests, positions):
    """Supabase mock that keeps exit_requests and portfolio_positions apart."""
    sb = MagicMock()
    tables = {}

    def _table(name):
        if name in tables:
            return tables[name]
        t = MagicMock()
        if name == "exit_requests":
            t.select.return_value.in_.return_value.execute.return_value.data = requests
            t.select.return_value.eq.return_value.execute.return_value.data = [
                r for r in requests if r.get("status") == "PLACED"]
            t.update.return_value.eq.return_value.execute.return_value = MagicMock()
        elif name == "portfolio_positions":
            t.select.return_value.execute.return_value.data = positions
        tables[name] = t
        return t

    sb.table.side_effect = _table
    sb._tables = tables
    return sb


def _run_queue(requests, positions, price, hour=11, minute=30):
    sb = _queue_sb(requests, positions)
    ib = MagicMock()
    now = datetime.datetime(2026, 8, 18, hour, minute, tzinfo=NY)
    with patch("execution_agent.get_supabase_client", return_value=sb), \
         patch("execution_agent.get_live_price", return_value=price), \
         patch("execution_agent.get_ibkr_account", return_value="U1"), \
         patch("execution_agent.cancel_ticker_sell_orders") as cancel, \
         patch("execution_agent.place_oca_exit", return_value=("OCA_X", [])) as place, \
         patch("execution_agent.execute_sell", return_value=True) as sell, \
         patch("execution_agent.notifier"), \
         patch("execution_agent.datetime") as mdt:
        mdt.datetime.now.side_effect = lambda *a, **kw: now
        mdt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mdt.timedelta = datetime.timedelta
        mdt.timezone = datetime.timezone
        execution_agent.process_exit_requests(ib)
    return sb, place, sell, cancel


def _pending(**kw):
    r = {"id": 1, "ticker": "DELL", "status": "PENDING", "limit_mode": "ABS",
         "limit_value": 489.89, "stop_mode": "TRAIL_PCT", "stop_value": 2.5,
         "hard_floor_pct": 5.0, "expires_after_days": 3}
    r.update(kw)
    return r


class TestQueuePending:
    def test_places_the_oca_and_marks_placed(self):
        sb, place, _, _ = _run_queue([_pending()], [_pos()], 468.61)
        place.assert_called_once()
        _, _, shares, limit, trail, _ = place.call_args.args
        assert shares == 46 and limit == 489.89 and abs(trail - 0.025) < 1e-9
        payload = sb._tables["exit_requests"].update.call_args.args[0]
        assert payload["status"] == "PLACED"
        assert payload["placed_limit_price"] == 489.89

    def test_cancels_the_existing_gtc_trail_first(self):
        # Left in place it is a third SELL outside the OCA group, so filling it
        # would not cancel the OCA legs.
        _, _, _, cancel = _run_queue([_pending()], [_pos()], 468.61)
        cancel.assert_called_once()

    def test_defers_placement_until_the_tape_settles(self):
        _, place, _, _ = _run_queue([_pending()], [_pos()], 468.61, hour=9, minute=31)
        place.assert_not_called()

    def test_places_once_the_settle_window_passes(self):
        _, place, _, _ = _run_queue([_pending()], [_pos()], 468.61, hour=9, minute=50)
        place.assert_called_once()

    def test_keeps_an_upper_leg_that_is_already_marketable(self):
        # A SELL limit never fills below its limit price, so a marketable one
        # fills at the better prevailing bid. Dropping it would decline the very
        # price the request asked for and leave the position on the trail alone.
        _, place, _, _ = _run_queue([_pending(limit_value=450.0)], [_pos()], 468.61)
        assert place.call_args.args[3] == 450.0

    def test_resolves_a_missing_position_instead_of_placing(self):
        sb, place, _, _ = _run_queue([_pending()], [], 468.61)
        place.assert_not_called()
        assert sb._tables["exit_requests"].update.call_args.args[0]["status"] == "CANCELLED"


class TestNextMorningReanchor:
    """
    End-to-end: a request queued tonight must price off tomorrow's settled
    price, not tonight's. This is the whole point of storing intent.
    """

    def _req(self, **kw):
        r = _pending(limit_mode="PCT_FROM_PRICE", limit_value=4.54,
                     limit_cap=496.04, stop_mode="TRAIL_PCT", stop_value=2.5)
        r.update(kw)
        return r

    def test_gap_up_is_capped_at_breakeven(self):
        _, place, _, _ = _run_queue([self._req()], [_pos()], 495.00)
        assert place.call_args.args[3] == 496.04

    def test_gap_down_re_anchors_the_target_downward(self):
        # 455 * 1.0454 = 475.66 — the plan follows the stock rather than
        # stranding the target at a level the open made unreachable.
        _, place, _, _ = _run_queue([self._req()], [_pos()], 455.00)
        assert abs(place.call_args.args[3] - 455.0 * 1.0454) < 0.01

    def test_flat_open_is_close_to_the_hand_computed_target(self):
        _, place, _, _ = _run_queue([self._req()], [_pos()], 468.61)
        assert abs(place.call_args.args[3] - 489.89) < 0.05

    def test_trail_percent_is_unaffected_by_the_gap(self):
        for price in (455.0, 468.61, 495.0):
            _, place, _, _ = _run_queue([self._req()], [_pos()], price)
            assert abs(place.call_args.args[4] - 0.025) < 1e-9

    def test_placed_price_records_the_actual_open_not_the_request_time_price(self):
        sb, _, _, _ = _run_queue([self._req()], [_pos()], 495.00)
        payload = sb._tables["exit_requests"].update.call_args.args[0]
        assert payload["placed_price"] == 495.00
        assert payload["placed_limit_price"] == 496.04


def _placed(**kw):
    r = {"id": 1, "ticker": "DELL", "status": "PLACED", "limit_mode": "ABS",
         "limit_value": 489.89, "stop_mode": "TRAIL_PCT", "stop_value": 2.5,
         "hard_floor_pct": 5.0, "expires_after_days": 3,
         "placed_at": "2026-08-18T09:45:00-04:00", "placed_price": 468.61,
         "placed_limit_price": 489.89, "placed_trail_pct": 2.5}
    r.update(kw)
    return r


class TestQueueBackstops:
    def test_holds_while_inside_the_floor_and_expiry(self):
        _, _, sell, _ = _run_queue([_placed()], [_pos()], 468.61)
        sell.assert_not_called()

    def test_hard_floor_breach_forces_a_market_exit(self):
        # 5% below the 468.61 placement price = 445.18
        _, _, sell, _ = _run_queue([_placed()], [_pos()], 444.00)
        sell.assert_called_once()
        assert "hard floor" in sell.call_args.args[8]

    def test_expiry_forces_a_market_exit(self):
        # An OCA can otherwise sit unfilled indefinitely while it bleeds.
        _, _, sell, _ = _run_queue(
            [_placed(placed_at="2026-08-11T09:45:00-04:00")], [_pos()], 468.61)
        sell.assert_called_once()
        assert "expired" in sell.call_args.args[8]

    def test_a_filled_leg_closes_the_request(self):
        sb, _, sell, _ = _run_queue([_placed()], [], 468.61)
        sell.assert_not_called()
        assert sb._tables["exit_requests"].update.call_args.args[0]["status"] == "FILLED"

    def test_request_is_left_open_when_the_backstop_sell_is_not_confirmed(self):
        sb = _queue_sb([_placed()], [_pos()])
        now = datetime.datetime(2026, 8, 18, 11, 30, tzinfo=NY)
        with patch("execution_agent.get_supabase_client", return_value=sb), \
             patch("execution_agent.get_live_price", return_value=444.00), \
             patch("execution_agent.get_ibkr_account", return_value="U1"), \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.execute_sell", return_value=False), \
             patch("execution_agent.notifier"), \
             patch("execution_agent.datetime") as mdt:
            mdt.datetime.now.side_effect = lambda *a, **kw: now
            mdt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mdt.timedelta, mdt.timezone = datetime.timedelta, datetime.timezone
            execution_agent.process_exit_requests(MagicMock())
        # Never mark FILLED on an unconfirmed sell — that would orphan the position.
        upd = sb._tables["exit_requests"].update
        assert not any(c.args[0].get("status") == "FILLED" for c in upd.call_args_list)


class TestQueueMarketMode:
    """--now: a force sell routed through the queue, so the agent stays up."""

    def _market(self, **kw):
        r = _pending(stop_mode="MARKET", stop_value=None,
                     limit_mode="NONE", limit_value=None)
        r.update(kw)
        return r

    def test_sells_at_market_without_placing_an_oca(self):
        sb, place, sell, _ = _run_queue([self._market()], [_pos()], 468.61)
        place.assert_not_called()
        sell.assert_called_once()
        assert sell.call_args.args[2] == "DELL"
        assert sell.call_args.args[3] == 46

    def test_cancels_the_existing_stop_before_selling(self):
        _, _, _, cancel = _run_queue([self._market()], [_pos()], 468.61)
        cancel.assert_called_once()

    def test_marks_the_request_filled_with_a_market_outcome(self):
        sb, _, _, _ = _run_queue([self._market()], [_pos()], 468.61)
        payload = sb._tables["exit_requests"].update.call_args.args[0]
        assert payload["status"] == "FILLED"
        assert payload["outcome"] == "MARKET"

    def test_does_not_wait_for_the_settle_window(self):
        # "Get me out" is urgent by definition; deferring it to 09:45 would
        # silently convert a force sell into a 15-minute wait.
        _, _, sell, _ = _run_queue([self._market()], [_pos()], 468.61, hour=9, minute=31)
        sell.assert_called_once()

    def test_stays_pending_when_the_sell_is_not_confirmed(self):
        sb = _queue_sb([self._market()], [_pos()])
        now = datetime.datetime(2026, 8, 18, 11, 30, tzinfo=NY)
        with patch("execution_agent.get_supabase_client", return_value=sb), \
             patch("execution_agent.get_live_price", return_value=468.61), \
             patch("execution_agent.get_ibkr_account", return_value="U1"), \
             patch("execution_agent.cancel_ticker_sell_orders"), \
             patch("execution_agent.execute_sell", return_value=False), \
             patch("execution_agent.notifier"), \
             patch("execution_agent.datetime") as mdt:
            mdt.datetime.now.side_effect = lambda *a, **kw: now
            mdt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
            mdt.timedelta, mdt.timezone = datetime.timedelta, datetime.timezone
            execution_agent.process_exit_requests(MagicMock())
        upd = sb._tables["exit_requests"].update
        assert not any(c.args[0].get("status") == "FILLED" for c in upd.call_args_list)

    def test_market_requests_never_suspend_the_automated_ladder(self):
        # A PENDING market request must not strip a position's stops while it
        # waits for the next cycle.
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            self._market()]
        assert execution_agent.get_oca_managed_tickers(sb) == set()


class TestQueueResilience:
    def test_missing_table_does_not_break_the_cycle(self):
        sb = MagicMock()
        sb.table.side_effect = Exception('relation "exit_requests" does not exist (42P01)')
        with patch("execution_agent.get_supabase_client", return_value=sb), \
             patch("execution_agent.notifier") as n:
            execution_agent.process_exit_requests(MagicMock())
        n.notify_exception.assert_not_called()

    def test_disabled_flag_short_circuits(self):
        sb = MagicMock()
        with patch.object(execution_agent, "OCA_EXIT_ENABLED", False), \
             patch("execution_agent.get_supabase_client", return_value=sb):
            execution_agent.process_exit_requests(MagicMock())
        sb.table.assert_not_called()


# ── ATR_AUTO upper leg (the default for a bare insert) ───────────────────────

class TestAtrAutoUpperLeg:
    """
    The upper leg must stay reachable however far underwater the position is.

    BREAKEVEN anchors to the ENTRY, so the bounce it demands grows with the loss
    already taken — on DELL (entry 496.04, price 468.61) it needed +5.85%, which
    is why that OCA could only ever resolve via the trail or the expiry.
    ATR_AUTO anchors to the CURRENT price, so entry drops out entirely.
    See decisions/2026-08-19_atr-anchored-upper-leg.md.
    """

    def test_targets_a_fraction_of_atr_above_the_current_price(self):
        # 7.6% ATR * 0.50 = 3.8%, inside the [0.75%, 5.0%] clamp
        got = execution_agent.resolve_oca_limit_price(_pos(atr=7.6), "ATR_AUTO", None, 468.61)
        assert abs(got - 468.61 * 1.038) < 0.01

    def test_ignores_entry_price_entirely(self):
        """The whole point: a deep loser gets the same target as a winner."""
        a = execution_agent.resolve_oca_limit_price(_pos(buy_price=496.04, atr=4.0), "ATR_AUTO", None, 100.0)
        b = execution_agent.resolve_oca_limit_price(_pos(buy_price=10.00, atr=4.0), "ATR_AUTO", None, 100.0)
        assert a == b == 102.0

    def test_is_always_reachable_on_a_deep_loser(self):
        """On DELL, breakeven needed +5.85%; ATR_AUTO needs 3.8%."""
        price = 468.61
        atr_target = execution_agent.resolve_oca_limit_price(_pos(atr=7.6), "ATR_AUTO", None, price)
        be_target  = execution_agent.resolve_oca_limit_price(_pos(), "BREAKEVEN", None, price)
        assert atr_target < be_target

    def test_quiet_stock_is_clamped_up_off_the_spread(self):
        # 0.5% ATR * 0.50 = 0.25% -> clamped up to the 0.75% floor
        got = execution_agent.resolve_oca_limit_price(_pos(atr=0.5), "ATR_AUTO", None, 100.0)
        assert abs(got - 100.75) < 1e-6

    def test_wild_stock_is_clamped_down_to_something_reachable(self):
        # 20% ATR * 0.50 = 10% -> clamped down to the 5% ceiling
        got = execution_agent.resolve_oca_limit_price(_pos(atr=20.0), "ATR_AUTO", None, 100.0)
        assert abs(got - 105.0) < 1e-6

    def test_missing_atr_falls_back_to_the_default(self):
        # default ATR 3.0% * 0.50 = 1.5%
        got = execution_agent.resolve_oca_limit_price(_pos(atr=None), "ATR_AUTO", None, 100.0)
        assert abs(got - 101.5) < 1e-6

    def test_is_the_default_when_no_mode_is_stored(self):
        """A bare `INSERT INTO exit_requests (ticker) VALUES (...)` path."""
        got = execution_agent.resolve_oca_limit_price(_pos(atr=4.0), None, None, 100.0)
        assert abs(got - 102.0) < 1e-6

    def test_upper_leg_sits_further_out_than_the_trail(self):
        """Optimistic leg must be looser than the protective one, at any ATR."""
        for atr in (1.0, 2.0, 4.0, 7.6, 12.0):
            pos = _pos(atr=atr)
            trail, _ = execution_agent.resolve_oca_trail_pct(pos, "ATR_AUTO", None)
            upper = execution_agent.resolve_oca_limit_price(pos, "ATR_AUTO", None, 100.0)
            assert (upper / 100.0 - 1) > 0, f"upper must be above the market at ATR {atr}"
            assert 100.0 * (1 - trail) < upper, f"legs crossed at ATR {atr}"

    def test_limit_cap_still_applies(self):
        got = execution_agent.resolve_oca_limit_price(_pos(atr=7.6), "ATR_AUTO", None, 468.61,
                                                      limit_cap=475.0)
        assert got == 475.0

    def test_no_reference_price_yields_no_leg(self):
        assert execution_agent.resolve_oca_limit_price(_pos(), "ATR_AUTO", None, 0) is None
