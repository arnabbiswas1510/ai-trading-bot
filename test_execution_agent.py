import unittest
from unittest.mock import MagicMock, patch
import datetime
import os
import sys

# Add directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import execution_agent

class TestExecutionAgentRules(unittest.TestCase):
    def setUp(self):
        # Reset global supabase client mock
        execution_agent.supabase = MagicMock()
        self.mock_client = execution_agent.supabase
        
        # Mock IB connection
        self.mock_ib = MagicMock()

    @patch("execution_agent.get_live_price")
    @patch("execution_agent.execute_sell")
    def test_stop_loss_trigger(self, mock_execute_sell, mock_live_price):
        """Verifies that a stock whose price falls below the 7% stop-loss is sold."""
        # Entry price: $100. Stop-loss: $93. Live price: $92.5
        mock_live_price.return_value = 92.50
        
        # Set up mock portfolio position
        self.mock_client.table().select().execute.return_value.data = [{
            "ticker": "AAPL",
            "shares": 100,
            "buy_price": 100.0,
            "buy_date": "2026-06-01T09:30:00+00:00",
            "stop_loss": 93.00,
            "profit_target": 125.00,
            "is_power_hold": False,
            "power_hold_expiry": None
        }]
        
        # Mock IB returns AAPL position in sync check
        mock_pos = MagicMock()
        mock_pos.contract.symbol = "AAPL"
        self.mock_ib.positions.return_value = [mock_pos]
        
        execution_agent.monitor_portfolio_intraday(self.mock_ib)
        
        # Check that execute_sell was called with the correct parameters
        mock_execute_sell.assert_called_once()
        args, kwargs = mock_execute_sell.call_args
        self.assertEqual(args[2], "AAPL")      # ticker
        self.assertEqual(args[3], 100)          # shares
        self.assertEqual(args[7], 92.50)        # current price
        self.assertEqual(args[8], "7% Stop Loss") # reason

    @patch("execution_agent.get_live_price")
    @patch("execution_agent.execute_sell")
    def test_profit_target_trigger(self, mock_execute_sell, mock_live_price):
        """Verifies that a stock whose price rises above the 25% target is sold when not in power hold."""
        # Entry price: $100. Target: $125. Live price: $126
        mock_live_price.return_value = 126.00
        
        # Set up mock portfolio position
        self.mock_client.table().select().execute.return_value.data = [{
            "ticker": "NVDA",
            "shares": 150,
            "buy_price": 100.0,
            "buy_date": "2026-06-01T09:30:00+00:00",
            "stop_loss": 93.00,
            "profit_target": 125.00,
            "is_power_hold": False,
            "power_hold_expiry": None
        }]
        
        # Mock IB returns NVDA position in sync check
        mock_pos = MagicMock()
        mock_pos.contract.symbol = "NVDA"
        self.mock_ib.positions.return_value = [mock_pos]
        
        execution_agent.monitor_portfolio_intraday(self.mock_ib)
        
        # Check that execute_sell was called with the correct parameters
        mock_execute_sell.assert_called_once()
        args, kwargs = mock_execute_sell.call_args
        self.assertEqual(args[2], "NVDA")
        self.assertEqual(args[3], 150)
        self.assertEqual(args[7], 126.00)
        self.assertEqual(args[8], "25% Profit Target")

    @patch("execution_agent.get_live_price")
    @patch("execution_agent.execute_sell")
    def test_power_hold_activation(self, mock_execute_sell, mock_live_price):
        """Verifies that a stock that gains 20% within 21 days triggers a power hold."""
        # Entry price: $100. Target: $125. Power hold price: $120+. Live price: $121
        mock_live_price.return_value = 121.00
        
        # Set up mock portfolio position (10 days old, not yet in power hold)
        today = datetime.datetime.now(datetime.timezone.utc)
        buy_date = (today - datetime.timedelta(days=10)).isoformat()
        
        self.mock_client.table().select().execute.return_value.data = [{
            "ticker": "AMZN",
            "shares": 200,
            "buy_price": 100.0,
            "buy_date": buy_date,
            "stop_loss": 93.00,
            "profit_target": 125.00,
            "is_power_hold": False,
            "power_hold_expiry": None
        }]
        
        # Mock IB returns AMZN position in sync check
        mock_pos = MagicMock()
        mock_pos.contract.symbol = "AMZN"
        self.mock_ib.positions.return_value = [mock_pos]
        
        execution_agent.monitor_portfolio_intraday(self.mock_ib)
        
        # Should NOT sell (since target is $125, but it hit $121 which is the 20% power hold trigger)
        mock_execute_sell.assert_not_called()
        
        # Verify that Supabase was updated to activate the power hold
        self.mock_client.table().update.assert_called_once()
        update_data = self.mock_client.table().update.call_args[0][0]
        self.assertTrue(update_data["is_power_hold"])
        self.assertIsNotNone(update_data["power_hold_expiry"])

    @patch("execution_agent.get_live_price")
    @patch("execution_agent.execute_sell")
    def test_power_hold_exemption(self, mock_execute_sell, mock_live_price):
        """Verifies that a stock in power hold is exempt from selling even if it exceeds the 25% target."""
        # Entry price: $100. Target: $125. Live price: $130
        mock_live_price.return_value = 130.00
        
        # Set up mock portfolio position (already in power hold)
        today = datetime.datetime.now(datetime.timezone.utc)
        buy_date = (today - datetime.timedelta(days=12)).isoformat()
        expiry_date = (today + datetime.timedelta(days=30)).date().isoformat()
        
        self.mock_client.table().select().execute.return_value.data = [{
            "ticker": "MSFT",
            "shares": 180,
            "buy_price": 100.0,
            "buy_date": buy_date,
            "stop_loss": 93.00,
            "profit_target": 125.00,
            "is_power_hold": True,
            "power_hold_expiry": expiry_date
        }]
        
        mock_pos = MagicMock()
        mock_pos.contract.symbol = "MSFT"
        self.mock_ib.positions.return_value = [mock_pos]
        
        execution_agent.monitor_portfolio_intraday(self.mock_ib)
        
        # Should NOT sell because is_power_hold is True
        mock_execute_sell.assert_not_called()

if __name__ == "__main__":
    unittest.main()
