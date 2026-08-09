"""
tests/test_thesis_stop.py

Tests for the Thesis Stop — an ATR-normalised failure-to-advance exit.

Fires when a breakout has NEVER closed above entry and is now more than
THESIS_STOP_ATR_MULT x ATR below it, between day THESIS_STOP_START_DAY and
THESIS_STOP_LAST_DAY. It arms a tight trailing exit rather than market-selling,
because the trigger price is usually a local trough.

The critical distinction these tests protect: the Thesis Stop must NOT
degenerate into the old Intraday Loss Minimiser. That rule required the
intraday high to be AT OR ABOVE entry, so it cut positions that were working
and roughly halved expectancy. The `closed_above_entry` latch is what keeps the
Thesis Stop confined to breakouts that never followed through.

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


def _make_pos(ticker="AAPL", buy_price=100.0, buy_date=BD_DAY4, shares=100,
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


def _make_ib(positions):
    ib = MagicMock()
    items = []
    for pos in positions:
        item = MagicMock()
        item.contract.symbol = pos["ticker"]
        item.contract.secType = "STK"
        item.position = pos["shares"]
        item.averageCost = pos["buy_price"]
        items.append(item)
    ib.portfolio.return_value = items
    ib.reqPositions.return_value = None
    ib.openOrders.return_value = []
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


class TestThesisStopFires:

    def test_fires_when_never_closed_above_entry_and_below_1x_atr(self):
        """Day 4, ATR 3%/day, price -5% -> beyond -3% threshold -> arm exit."""
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0)
        mock_sell, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_called_once()
        mock_sell.assert_not_called()
        assert "Thesis Stop" in mock_arm.call_args.args[5]

    def test_arms_rather_than_market_sells(self):
        """The trigger price is usually a local trough.

        Arming a tight trail beat an immediate market sell in BOTH backtest
        universes (a market sell scored -7.6 dCAGR, P(better) 21%, in broad).
        """
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0)
        mock_sell, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_sell.assert_not_called()
        mock_arm.assert_called_once()

    def test_fires_on_first_eligible_day(self):
        pos = _make_pos(buy_date=BD_DAY2, entry_atr_pct=3.0)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_called_once()


class TestThesisStopVolatilityNormalisation:
    """A fixed percentage is meaningless across names: DXCM moves 4%/day, so a
    2% stop is half a normal session and would fire on noise."""

    def test_high_atr_name_survives_a_move_that_cuts_a_low_atr_name(self):
        # -3.5% with a 4.04%/day ATR (DXCM-like) is inside one day's range.
        high_atr = _make_pos(ticker="DXCM", entry_atr_pct=4.04, buy_date=BD_DAY4)
        _, arm_high = _run(_make_ib([high_atr]), _make_sb([high_atr]), 96.5)
        arm_high.assert_not_called()

        # The identical -3.5% move in a 1.5%/day name is 2.3x ATR — thesis dead.
        low_atr = _make_pos(ticker="KO", entry_atr_pct=1.5, buy_date=BD_DAY4)
        _, arm_low = _run(_make_ib([low_atr]), _make_sb([low_atr]), 96.5)
        arm_low.assert_called_once()

    def test_missing_atr_uses_fallback(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=None)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_called_once()
        assert f"{execution_agent.THESIS_STOP_ATR_FALLBACK:.2f}%/day" in mock_arm.call_args.args[5]


class TestThesisStopDoesNotCutWorkingPositions:
    """The failure mode that killed the Intraday Loss Minimiser."""

    def test_suppressed_once_closed_above_entry(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0, closed_above_entry=True)
        mock_sell, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_not_called()
        mock_sell.assert_not_called()

    def test_not_fired_on_shallow_loss(self):
        """-1% with a 3%/day ATR is noise, not thesis failure."""
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 99.0)
        mock_arm.assert_not_called()


class TestThesisStopWindow:

    def test_not_fired_before_start_day(self):
        """Day 1 belongs to the Early Loss Kill-switch, not the thesis stop."""
        pos = _make_pos(buy_date=BD_DAY1, entry_atr_pct=3.0)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        if mock_arm.called:
            assert "Thesis Stop" not in mock_arm.call_args.args[5]

    def test_not_fired_after_last_day(self):
        """Day 7+ is handled by EMA-21 / stale / rank-and-replace instead."""
        pos = _make_pos(buy_date=BD_DAY7, entry_atr_pct=3.0)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        if mock_arm.called:
            assert "Thesis Stop" not in mock_arm.call_args.args[5]

    def test_not_rearmed_when_already_armed(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0, exit_armed=True)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        for c in mock_arm.call_args_list:
            assert "Thesis Stop" not in c.args[5]


class TestThesisStopFailsSafeWithoutMigration:
    """If add_closed_above_entry.sql has not been run the column is absent, so
    the latch reads None. That must NOT be treated as 'never followed through'."""

    def test_missing_column_falls_back_to_peak_gain(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0,
                        closed_above_entry=None, highest_unrealized_pct=4.0)
        pos.pop("closed_above_entry", None)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_not_called()

    def test_missing_column_falls_back_to_hwm_price(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0,
                        closed_above_entry=None, hwm_price=103.0)
        pos.pop("closed_above_entry", None)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_not_called()

    def test_missing_column_falls_back_to_intraday_high(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0,
                        closed_above_entry=None, intraday_high_today=101.0)
        pos.pop("closed_above_entry", None)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_not_called()

    def test_missing_column_still_fires_when_never_above_entry(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0, closed_above_entry=None)
        pos.pop("closed_above_entry", None)
        _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        mock_arm.assert_called_once()
        assert "Thesis Stop" in mock_arm.call_args.args[5]


class TestThesisStopDisableSwitch:

    def test_can_be_disabled(self):
        pos = _make_pos(buy_date=BD_DAY4, entry_atr_pct=3.0)
        with patch.object(execution_agent, "THESIS_STOP_ENABLED", False):
            _, mock_arm = _run(_make_ib([pos]), _make_sb([pos]), 95.0)
        for c in mock_arm.call_args_list:
            assert "Thesis Stop" not in c.args[5]
