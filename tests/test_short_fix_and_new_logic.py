import pytest
from unittest.mock import MagicMock, patch
from ib_insync import IB, Stock
import execution_agent
import datetime

def test_short_position_fix_parent_order_id():
    """Test that place_oca_bracket attaches parentId to the TrailingStop and Limit sell orders."""
    ib = MagicMock(spec=IB)
    contract = Stock('AAPL', 'SMART', 'USD')
    
    execution_agent.place_oca_bracket(
        ib, contract, shares=100, buy_price=150.0,
        profit_target_pct=0.25, stop_loss_pct=0.07,
        submit_limit_order=True
    )
    
    assert ib.placeOrder.call_count == 2
    
    stop = ib.placeOrder.call_args_list[0][0][1]
    limit = ib.placeOrder.call_args_list[1][0][1]
    
    assert stop.orderType == 'TRAIL'
    
    assert getattr(limit, 'orderType', '') == 'LMT'

def test_power_hold_race_condition_fix():
    """Test that power hold correctly defers the limit order."""
    ib = MagicMock(spec=IB)
    contract = Stock('AAPL', 'SMART', 'USD')
    
    # 1. Initial buy - only trailing stop placed
    execution_agent.place_oca_bracket(
        ib, contract, shares=100, buy_price=150.0,
        profit_target_pct=0.25, stop_loss_pct=0.07,
        submit_limit_order=False
    )
    
    assert ib.placeOrder.call_count == 1
    stop = ib.placeOrder.call_args_list[0][0][1]
    assert stop.orderType == 'TRAIL'
    
    # 2. 22nd day - limit order placed along with reset trailing stop
    ib.placeOrder.reset_mock()
    execution_agent.place_oca_bracket(
        ib, contract, shares=100, buy_price=150.0,
        profit_target_pct=0.25, stop_loss_pct=0.07,
        submit_limit_order=True
    )
    assert ib.placeOrder.call_count == 2
    stop2 = ib.placeOrder.call_args_list[0][0][1]
    limit2 = ib.placeOrder.call_args_list[1][0][1]
    assert stop2.orderType == 'TRAIL'
    assert getattr(limit2, 'orderType', '') == 'LMT'

@patch("execution_agent.notifier.notify_error")
def test_reconcile_detects_short_positions(mock_notify):
    """Test that reconcile_with_ibkr detects short positions and sends alert."""
    ib = MagicMock()
    
    # Mock portfolio with a short position
    mock_pos = MagicMock()
    mock_pos.contract.secType = "STK"
    mock_pos.contract.symbol = "ATLC"
    mock_pos.position = -247
    
    ib.portfolio.return_value = [mock_pos]
    
    with patch("execution_agent.get_supabase_client"):
        execution_agent.reconcile_with_ibkr(ib)
        
    mock_notify.assert_called_once()
    assert "SHORT POSITION DETECTED: ATLC has -247 shares" in mock_notify.call_args[0][0]
