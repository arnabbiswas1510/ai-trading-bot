import pytest
from unittest.mock import patch, MagicMock
from telegram_notifier import TelegramNotifier

@pytest.fixture
def notifier():
    """Returns a configured TelegramNotifier for testing."""
    return TelegramNotifier(bot_token="test_token", chat_ids=["12345"])

@patch('requests.post')
def test_telegram_api_error_printed(mock_post, notifier, capsys):
    """Test that a non-200 HTTP response from the Telegram API is explicitly printed and not swallowed."""
    # Mock a 400 Bad Request response
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    mock_post.return_value = mock_response

    notifier._send("Test message")

    # Capture standard output
    captured = capsys.readouterr()
    
    assert "Telegram API Error (400)" in captured.out
    assert "Bad Request: chat not found" in captured.out

@patch('requests.post')
def test_telegram_network_error_printed(mock_post, notifier, capsys):
    """Test that a network exception (e.g. timeout) is explicitly printed and not swallowed."""
    # Mock a network exception
    mock_post.side_effect = Exception("Connection timed out")

    notifier._send("Test message")

    # Capture standard output
    captured = capsys.readouterr()
    
    assert "Telegram Network Error: Connection timed out" in captured.out
