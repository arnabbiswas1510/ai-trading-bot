"""
tests/test_prove_it_stop.py

Tests for the Prove-It Stop — the single loss rule that replaced the Early Loss
Kill-switch, the Early Dollar Stop and the Thesis Stop.

It asks one question: has this position ever CLOSED above the price we paid?

  Phase 1 (unproven) anchors to ENTRY. PROVE_IT_P1_DAY0_PCT below entry on the
  entry day, PROVE_IT_P1_LATER_PCT from day 1 onward. The band WIDENS after day
  0 — holding the tight band through day 1 clipped winners for more than it
  saved on losers in the 30-trade replay.

  Phase 2 (proven) anchors to the GIVE-BACK FLOOR once the peak gain reaches
  PROVE_IT_P2_ARM_GAIN_PCT. The floor sits at PROVE_IT_P2_FLOOR_PCT of entry —
  deliberately BELOW breakeven, because an exact-breakeven floor flushes any
  position that pokes green and then retests entry.

The critical distinction these tests protect is the same one the Thesis Stop
protected: the rule must not degenerate into the old Intraday Loss Minimiser,
which cut positions that were working. The `closed_above_entry` latch is what
keeps Phase 1 confined to breakouts that never followed through, and it must
fail SAFE — a missing column counts as PROVEN, never as unproven.

See decisions/2026-09-04_prove-it-stop.md.

buy_date mapping for mock now=2026-06-17 (see test_breakout_verdict.py).
"""

import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import execution_agent

BD_DAY0 = "2026-06-17T12:00:00+00:00"
BD_DAY1 = "2026-06-16T12:00:00+00:00"
BD_DAY2 = "2026-06-15T12:00:00+00:00"
BD_DAY4 = "2026-06-11T12:00:00+00:00"
BD_DAY5 = "2026-06-10T12:00:00+00:00"
BD_DAY7 = "2026-06-08T12:00:00+00:00"


# 20 shares @ $100 = a $2,000 position. Absolute size no longer changes the
# outcome of any loss rule — the slot-derived dollar cap that used to pre-empt
# this one was retired with the Prove-It Stop (docs/retired_code.md).
def _make_pos(ticker="AAPL", buy_price=100.0, buy_date=BD_DAY4, shares=20,
              entry_atr_pct=3.0, closed_above_entry=False,
              highest_unrealized_pct=0.0, hwm_price=None,
              intraday_high_today=None, exit_armed=False):
    pos = {
        "ticker": ticker, "buy_price": buy_price, "buy_date": buy_date,
        "buy_reason": "CANSLIM breakout", "shares": shares,
        "stop_loss_pct": 0.07,
        "highest_unrealized_pct": highest_unrealized_pct,
        "hwm_price": hwm_price if hwm_price is not None else buy_price,
        "hwm_date": None, "entry_rs_score": 90, "live_rs_score": 90,
        "breakout_verdict": None, "intraday_high_today": intraday_high_today,
        "momentum_health_score": None, "rotation_recommendation": None,
        "volume_distribution_flag": False, "entry_atr_pct": entry_atr_pct,
        "exit_armed": exit_armed, "exit_armed_at": None, "exit_armed_reason": None,
    }
    if closed_above_entry is not None:
        pos["closed_above_entry"] = closed_above_entry
    return pos


def _make_ib(positions, equity=25_000.0 * 4):
    """Mock IBKR connection.

    `equity` defaults to 4 slots x $25K so that the slot-derived Early Dollar
    Stop resolves to 6% of a $25K slot = $1,500 — matching the live calibration.
    get_net_liquidation() reads the NetLiquidation tag filtered by account, so
    the mock must expose accountValues() or the rule silently disables itself.
    """
    ib = MagicMock()
    items = []
    for pos in positions:
        item = MagicMock()
        item.contract.symbol = pos["ticker"]
        item.contract.secType = "STK"
        item.position = pos["shares"]
        item.averageCost = pos["buy_price"]
        item.marketPrice = 0.0   # no live IBKR mark -> get_position_price falls back to FMP
        items.append(item)
    ib.portfolio.return_value = items
    ib.reqPositions.return_value = None
    ib.openOrders.return_value = []
    account = "DU1234567"
    ib.managedAccounts.return_value = [account]
    equity_av = MagicMock()
    equity_av.tag = "NetLiquidation"
    equity_av.currency = "USD"
    equity_av.value = str(equity)
    equity_av.account = account
    ib.accountValues.return_value = [equity_av]
    return ib


def _make_sb(positions):
    sb = MagicMock()
    pos_res = MagicMock()
    pos_res.data = positions
    sb.table.return_value.select.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return sb


def _run(ib, sb, live_price, hour=11, minute=30):
    tz = ZoneInfo("America/New_York")
    now_mock = datetime.datetime(2026, 6, 17, hour, minute, tzinfo=tz)
    with patch("execution_agent.supabase", sb), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent._fetch_ohlcv", return_value=[]), \
         patch("execution_agent._fetch_current_rs", return_value=90), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_trailing_stop", return_value=("TS_MOCK", 0.07)), \
         patch("execution_agent.execute_sell") as mock_sell, \
         patch("execution_agent.arm_exit") as mock_arm, \
         patch("execution_agent.datetime") as mock_dt:
        mock_dt.datetime.now.side_effect = lambda *a, **kw: now_mock
        mock_dt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_dt.date.fromisoformat.side_effect = datetime.date.fromisoformat
        mock_dt.date.today.return_value = now_mock.date()
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta
        execution_agent.monitor_portfolio_intraday(ib)
    return mock_sell, mock_arm



class TestPhase1Bands:
    """Unproven positions anchor to entry, and the band widens after day 0."""

    def test_day0_band_is_tighter_than_later_days(self):
        assert (execution_agent.prove_it_p1_threshold_pct(0)
                < execution_agent.prove_it_p1_threshold_pct(1))

    def test_day0_fires_at_the_day0_band(self):
        band = execution_agent.PROVE_IT_P1_DAY0_PCT
        pos = _make_pos(buy_date=BD_DAY0, closed_above_entry=False)
        price = 100.0 * (1 - band) - 0.01
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), price)
        mock_arm.assert_called_once()
        assert "Prove-It Stop (Phase 1" in mock_arm.call_args.args[5]

    def test_day0_holds_just_inside_the_band(self):
        band = execution_agent.PROVE_IT_P1_DAY0_PCT
        pos = _make_pos(buy_date=BD_DAY0, closed_above_entry=False)
        price = 100.0 * (1 - band) + 0.05
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), price)
        mock_arm.assert_not_called()

    def test_day1_gets_the_wider_band(self):
        """The day-0 band must NOT still apply on day 1.

        This is the regression the replay paid for: CPAY closed -2.24% on day 1
        and then ran to +8.95%. A 1% band held through day 1 cuts it.
        """
        day0 = execution_agent.PROVE_IT_P1_DAY0_PCT
        pos = _make_pos(buy_date=BD_DAY1, closed_above_entry=False)
        price = 100.0 * (1 - day0) - 0.05      # through the day-0 band...
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), price)
        mock_arm.assert_not_called()           # ...but inside the day-1 band

    def test_day1_fires_at_the_wider_band(self):
        band = execution_agent.PROVE_IT_P1_LATER_PCT
        pos = _make_pos(buy_date=BD_DAY1, closed_above_entry=False)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 100.0 * (1 - band) - 0.01)
        mock_arm.assert_called_once()
        assert "Prove-It Stop (Phase 1" in mock_arm.call_args.args[5]

    def test_band_does_not_expire_with_age(self):
        """Unlike every rule it replaced, Phase 1 has no day window.

        The Thesis Stop stopped at day 5 and the Kill-switch at day 0, leaving a
        position that never confirmed to fall back on a 10%+ peak-anchored trail.
        That gap is the loss pathway this rule exists to close.
        """
        band = execution_agent.PROVE_IT_P1_LATER_PCT
        pos = _make_pos(buy_date=BD_DAY7, closed_above_entry=False)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 100.0 * (1 - band) - 0.01)
        mock_arm.assert_called_once()
        assert "Prove-It Stop (Phase 1" in mock_arm.call_args.args[5]


class TestPhase2GiveBackFloor:
    """A proven position that reached the arming gain never becomes a real loss."""

    def test_floor_sits_below_entry_not_at_it(self):
        """An exact-breakeven floor flushes a normal retest of entry."""
        assert execution_agent.PROVE_IT_P2_FLOOR_PCT < 0

    def test_fires_when_a_green_trade_gives_it_all_back(self):
        arm = execution_agent.PROVE_IT_P2_ARM_GAIN_PCT * 100.0
        floor = 100.0 * (1 + execution_agent.PROVE_IT_P2_FLOOR_PCT)
        pos = _make_pos(buy_date=BD_DAY4, closed_above_entry=True,
                        highest_unrealized_pct=arm + 1.0,
                        hwm_price=100.0 * (1 + (arm + 1.0) / 100.0))
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), floor - 0.01)
        mock_arm.assert_called_once()
        assert "Prove-It Stop (Phase 2" in mock_arm.call_args.args[5]

    def test_does_not_fire_above_the_floor(self):
        arm = execution_agent.PROVE_IT_P2_ARM_GAIN_PCT * 100.0
        floor = 100.0 * (1 + execution_agent.PROVE_IT_P2_FLOOR_PCT)
        pos = _make_pos(buy_date=BD_DAY4, closed_above_entry=True,
                        highest_unrealized_pct=arm + 1.0,
                        hwm_price=100.0 * (1 + (arm + 1.0) / 100.0))
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), floor + 0.10)
        mock_arm.assert_not_called()

    def test_unarmed_below_the_arming_gain(self):
        """Below the arming gain a floor would sit inside ordinary noise.

        A proven position whose peak never reached PROVE_IT_P2_ARM_GAIN_PCT is
        governed by the base trailing stop alone, NOT by the Phase 1 band — the
        latch has already promoted it out of Phase 1.
        """
        arm = execution_agent.PROVE_IT_P2_ARM_GAIN_PCT * 100.0
        pos = _make_pos(buy_date=BD_DAY4, closed_above_entry=True,
                        highest_unrealized_pct=arm - 0.5,
                        hwm_price=100.0 * (1 + (arm - 0.5) / 100.0))
        level, phase = execution_agent.prove_it_stop_level(
            pos, 100.0, 4, arm - 0.5)
        assert level is None and phase == "phase2-unarmed"

        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_not_called()

    def test_proven_position_is_never_judged_against_the_phase1_band(self):
        band = execution_agent.PROVE_IT_P1_LATER_PCT
        pos = _make_pos(buy_date=BD_DAY4, closed_above_entry=True,
                        highest_unrealized_pct=1.0, hwm_price=101.0)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]),
                           100.0 * (1 - band) - 0.01)
        mock_arm.assert_not_called()


class TestFailsSafeWithoutMigration:
    """A missing `closed_above_entry` column must read as PROVEN, never unproven."""

    def test_missing_column_with_a_peak_above_entry_is_proven(self):
        pos = _make_pos(closed_above_entry=None, highest_unrealized_pct=4.0,
                        hwm_price=104.0)
        assert execution_agent.prove_it_is_proven(pos, 4.0) is True

    def test_missing_column_with_an_intraday_high_above_entry_is_proven(self):
        pos = _make_pos(closed_above_entry=None, intraday_high_today=101.0)
        assert execution_agent.prove_it_is_proven(pos, 0.0) is True

    def test_missing_column_that_never_traded_above_entry_is_unproven(self):
        pos = _make_pos(closed_above_entry=None, hwm_price=99.0)
        assert execution_agent.prove_it_is_proven(pos, 0.0) is False

    def test_unreadable_buy_price_is_proven(self):
        """Never apply a tight band to a position we cannot price."""
        pos = _make_pos(closed_above_entry=None)
        pos["buy_price"] = None
        assert execution_agent.prove_it_is_proven(pos, 0.0) is True


class TestStopLevelArithmetic:
    def test_phase1_level_is_below_entry(self):
        level, phase = execution_agent.prove_it_stop_level(
            {"closed_above_entry": False}, 100.0, 3, 0.0)
        assert phase == "phase1"
        assert level == 100.0 * (1 - execution_agent.PROVE_IT_P1_LATER_PCT)

    def test_phase2_level_is_the_floor(self):
        arm = execution_agent.PROVE_IT_P2_ARM_GAIN_PCT * 100.0
        level, phase = execution_agent.prove_it_stop_level(
            {"closed_above_entry": True}, 100.0, 3, arm)
        assert phase == "phase2"
        assert level == 100.0 * (1 + execution_agent.PROVE_IT_P2_FLOOR_PCT)

    def test_disabled_returns_no_level(self):
        import unittest.mock as m
        with m.patch.object(execution_agent, "PROVE_IT_ENABLED", False):
            level, phase = execution_agent.prove_it_stop_level(
                {"closed_above_entry": False}, 100.0, 3, 0.0)
        assert level is None and phase == "disabled"


class TestBackstopTrailPct:
    """
    The resting IBKR order is solved against the CURRENT price, because IBKR
    resets the trailingPercent anchor on every cancel-and-replace — which is
    exactly what the tightening block does.
    """

    def test_phase2_pins_the_order_on_the_floor(self):
        pct = execution_agent.prove_it_trail_pct(95.0, 100.0, "phase2")
        assert pct == 0.05

    def test_phase1_order_rests_wider_than_the_bot_side_exit(self):
        """It is a gap backstop, so it must never front-run the armed exit."""
        level = 97.0
        pct = execution_agent.prove_it_trail_pct(level, 100.0, "phase1")
        assert pct > 1 - (level / 100.0)
        implied_stop = 100.0 * (1 - pct)
        assert implied_stop < level

    def test_no_level_yields_no_percentage(self):
        assert execution_agent.prove_it_trail_pct(None, 100.0, "phase2") is None

    def test_level_at_or_above_price_yields_no_percentage(self):
        """Already through the level — the bot-side exit acts, not a new order."""
        assert execution_agent.prove_it_trail_pct(101.0, 100.0, "phase2") is None


class TestPowerHoldSuppression:
    def test_power_hold_suppresses_the_prove_it_stop(self):
        """A confirmed leader is exempt; the 30% disaster stop is its backstop."""
        band = execution_agent.PROVE_IT_P1_LATER_PCT
        pos = _make_pos(buy_date=BD_DAY7, closed_above_entry=False)
        pos["power_hold"] = True
        pos["power_hold_expiry"] = "2026-08-01"
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]),
                           100.0 * (1 - band) - 1.0)
        for c in mock_arm.call_args_list:
            assert "Prove-It" not in c.args[5]


class TestAlreadyArmedIsNotRearmed:
    def test_armed_position_is_left_alone(self):
        band = execution_agent.PROVE_IT_P1_LATER_PCT
        pos = _make_pos(buy_date=BD_DAY4, closed_above_entry=False, exit_armed=True)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]),
                           100.0 * (1 - band) - 1.0)
        mock_arm.assert_not_called()
