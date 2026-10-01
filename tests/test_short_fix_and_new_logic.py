import pytest
from unittest.mock import MagicMock, patch
from ib_insync import IB, Stock
import execution_agent
import datetime
from tests.conftest import make_ib_mock, make_portfolio_item

# test_short_position_fix_parent_order_id and test_power_hold_race_condition_fix
# were testing place_oca_bracket() which has been eliminated in the simplified
# exit strategy refactor (HWM-Based Plateau Rotation). They are removed.
# See: migrations/20260708_add_hwm_date.sql and the implementation plan.

@patch("execution_agent.notifier.notify_error")
def test_reconcile_detects_short_positions(mock_notify):
    """Test that reconcile_with_ibkr detects short positions and sends alert."""
    ib = make_ib_mock()
    mock_pos = make_portfolio_item("ATLC", position=-247)
    ib.portfolio.return_value = [mock_pos]
    ib.positions.return_value = [mock_pos]

    with patch("execution_agent.get_supabase_client"):
        execution_agent.reconcile_with_ibkr(ib)

    mock_notify.assert_called_once()
    assert "Unexpected short position(s): ATLC -247" in mock_notify.call_args[0][0]
