import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path so we can import execution_agent
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the module to mock its dependencies
import execution_agent
from tests.conftest import make_ib_mock

class MockPosition:
    def __init__(self, symbol, avg_cost, position_size):
        self.contract = MagicMock()
        self.contract.symbol = symbol
        self.contract.secType = "STK"
        self.contract.conId = int.from_bytes(symbol.encode(), "big")
        self.account = "DU123"
        self.averageCost = avg_cost
        self.position = position_size
        self.marketPrice = avg_cost
        self.marketValue = avg_cost * position_size
        self.unrealizedPNL = 0.0


def _polling_ib(fill_after=None):
    ib = make_ib_mock()
    ib.managedAccounts.return_value = ["DU123"]
    buy_trade = None
    polls = []

    def place(contract, order):
        nonlocal buy_trade
        trade = MagicMock()
        trade.contract = contract
        trade.order = order
        order.clientId = ib.client.clientId
        trade.orderStatus.status = "Submitted"
        trade.orderStatus.filled = 0
        trade.orderStatus.remaining = order.totalQuantity
        trade.orderStatus.avgFillPrice = 0.0
        trade.log = []
        trade.fills = []
        if order.action == "BUY":
            buy_trade = trade
        ib.openTrades.return_value.append(trade)
        return trade

    def sleep(seconds):
        if buy_trade is None or buy_trade.orderStatus.status != "Submitted":
            return
        polls.append(seconds)
        if len(polls) == fill_after:
            qty = buy_trade.order.totalQuantity
            buy_trade.orderStatus.status = "Filled"
            buy_trade.orderStatus.filled = qty
            buy_trade.orderStatus.remaining = 0
            buy_trade.orderStatus.avgFillPrice = 101.0
            buy_trade.fills = [
                SimpleNamespace(
                    execution=SimpleNamespace(shares=qty, price=101.0, acctNumber="DU123"),
                    commissionReport=SimpleNamespace(commission=1.0))
            ]
            item = MockPosition(buy_trade.contract.symbol, 101.0, qty)
            assert item.contract.conId == buy_trade.contract.conId
            ib.positions.return_value = [item]
            ib.portfolio.return_value = [item]

    def cancel(order):
        assert order is buy_trade.order
        buy_trade.orderStatus.status = "Cancelled"

    ib.placeOrder.side_effect = place
    ib.sleep.side_effect = sleep
    ib.cancelOrder.side_effect = cancel
    return ib, polls

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
    mock_ib, polls = _polling_ib(fill_after=3)
    mock_get_live_price.return_value = 100.0
    
    triggers = [{"ticker": "AAPL", "close_price": 99.0, "volume_surge": 2.0, "final_score": 75}]
    mock_get_supabase_client.return_value = FakeSupabaseClient(triggers, [])
    
    with patch('execution_agent.get_own_cash', return_value=100000.0), \
         patch('execution_agent.get_margin_loan', return_value=0.0), \
         patch('execution_agent.is_market_bullish', return_value=True), \
         patch('execution_agent._get_entry_rs', return_value=None), \
         patch('execution_agent.fetch_ibkr_delayed_price', return_value=(0.0, '')):
        execution_agent.run_market_open_buys(mock_ib)
        assert polls == [1, 1, 1]
        # Three polling sleeps plus the real protective bracket's settle sleep.
        assert mock_ib.sleep.call_count == 4
        buy = mock_ib.placeOrder.call_args_list[0].args[1]
        assert mock_ib.positions()[0].position == buy.totalQuantity
        assert mock_ib.placeOrder.call_count == 3
        mock_ib.cancelOrder.assert_not_called()

@patch('builtins.print')
@patch('execution_agent.notifier')
@patch('execution_agent.get_supabase_client')
@patch('execution_agent.get_live_price')
def test_smart_polling_timeout(mock_get_live_price, mock_get_supabase_client, mock_notifier, mock_print):
    mock_ib, polls = _polling_ib()
    mock_get_live_price.return_value = 100.0
    
    triggers = [{"ticker": "AAPL", "close_price": 99.0, "volume_surge": 2.0, "final_score": 75}]
    mock_get_supabase_client.return_value = FakeSupabaseClient(triggers, [])
    
    with patch('execution_agent.get_own_cash', return_value=100000.0), \
         patch('execution_agent.get_margin_loan', return_value=0.0), \
         patch('execution_agent.is_market_bullish', return_value=True), \
         patch('execution_agent.fetch_ibkr_delayed_price', return_value=(0.0, '')):
        execution_agent.run_market_open_buys(mock_ib)
        # 60 calls in loop + 1 call (sleep 2) in cancel block
        assert mock_ib.sleep.call_count == 61
        assert mock_ib.cancelOrder.call_count == 1
        assert polls == [1] * 60
        assert mock_ib.positions() == []
        mock_ib.placeOrder.assert_called_once()
        assert mock_ib.openTrades()[0].orderStatus.status == "Cancelled"
