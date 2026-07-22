import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import sys
import os

# Add parent directory to path so we can import execution_agent
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the module to mock its dependencies
import execution_agent

class MockPosition:
    def __init__(self, symbol, avg_cost, position_size):
        self.contract = MagicMock()
        self.contract.symbol = symbol
        self.averageCost = avg_cost
        self.position = position_size

class FakeQuery:
    def __init__(self, data):
        self._data = data
    def execute(self):
        res = MagicMock()
        res.data = self._data
        return res
    def gte(self, col, val):
        return self
    def eq(self, col, val):
        return self

class FakeTable:
    def __init__(self, data):
        self._data = data
    def select(self, *args, **kwargs):
        return FakeQuery(self._data)
    def insert(self, *args, **kwargs):
        return FakeQuery([])
    def update(self, *args, **kwargs):
        return FakeQuery([])

class FakeSupabaseClient:
    def __init__(self, triggers, positions):
        self.triggers = triggers
        self.positions = positions
    def table(self, name):
        if name == 'daily_triggers':
            return FakeTable(self.triggers)
        elif name == 'portfolio_positions':
            return FakeTable(self.positions)
        return FakeTable([])

@patch('builtins.print')
@patch('execution_agent.notifier')
@patch('execution_agent.get_supabase_client')
@patch('execution_agent.get_live_price')
def test_smart_polling_fast_fill(mock_get_live_price, mock_get_supabase_client, mock_notifier, mock_print):
    mock_ib = MagicMock()
    mock_get_live_price.return_value = 100.0
    mock_ib.client.getReqId.return_value = 1
    mock_ib.managedAccounts.return_value = ['DU123']
    
    mock_ib.portfolio.side_effect = [
        [], [], [MockPosition("AAPL", 101.0, 10)]
    ]
    
    # Mock trade orderStatus
    mock_trade = MagicMock()
    # On the 3rd iteration, the status becomes 'Filled'
    type(mock_trade.orderStatus).status = PropertyMock(side_effect=['Submitted', 'Submitted', 'Filled', 'Filled', 'Filled'])
    mock_ib.placeOrder.return_value = mock_trade
    
    triggers = [{"ticker": "AAPL", "close_price": 99.0, "volume_surge": 2.0}]
    mock_get_supabase_client.return_value = FakeSupabaseClient(triggers, [])
    
    with patch('execution_agent.get_own_cash', return_value=100000.0), \
         patch('execution_agent.get_margin_loan', return_value=0.0), \
         patch('execution_agent.fetch_ibkr_delayed_price', return_value=(0.0, '')):
        execution_agent.run_market_open_buys(mock_ib)
        assert mock_ib.sleep.call_count == 3

@patch('builtins.print')
@patch('execution_agent.notifier')
@patch('execution_agent.get_supabase_client')
@patch('execution_agent.get_live_price')
def test_smart_polling_timeout(mock_get_live_price, mock_get_supabase_client, mock_notifier, mock_print):
    mock_ib = MagicMock()
    mock_get_live_price.return_value = 100.0
    mock_ib.client.getReqId.return_value = 1
    mock_ib.managedAccounts.return_value = ['DU123']
    
    mock_ib.portfolio.return_value = []
    
    mock_trade = MagicMock()
    type(mock_trade.orderStatus).status = PropertyMock(return_value='Submitted')
    mock_ib.placeOrder.return_value = mock_trade
    
    triggers = [{"ticker": "AAPL", "close_price": 99.0, "volume_surge": 2.0}]
    mock_get_supabase_client.return_value = FakeSupabaseClient(triggers, [])
    
    with patch('execution_agent.get_own_cash', return_value=100000.0), \
         patch('execution_agent.get_margin_loan', return_value=0.0), \
         patch('execution_agent.fetch_ibkr_delayed_price', return_value=(0.0, '')):
        execution_agent.run_market_open_buys(mock_ib)
        # 60 calls in loop + 1 call (sleep 2) in cancel block
        assert mock_ib.sleep.call_count == 61
        assert mock_ib.cancelOrder.call_count == 1
