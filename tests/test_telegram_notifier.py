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


@patch('requests.post')
def test_ibkr_disconnect_alert_states_consequence(mock_post, notifier):
    """The IBKR-disconnect alert must be unmistakable: it names the outage and
    the operational consequence (stops/exits offline, positions unmonitored) so
    it can never be misread as a generic Telegram/network hiccup."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    notifier.notify_ibkr_disconnected(
        attempts=6,
        minutes=18,
        positions_unmonitored=5,
        market_open=True,
        error=TimeoutError(),
    )

    sent = mock_post.call_args.kwargs["data"]["text"]
    assert "IBKR DISCONNECTED" in sent
    assert "RISK MANAGEMENT OFFLINE" in sent
    assert "5 open position(s) are" in sent and "UNMONITORED" in sent
    assert "OPEN" in sent  # market-open urgency called out
    assert "Prove-It Stop" in sent


@patch('requests.post')
def test_ibkr_disconnect_alert_unknown_position_count(mock_post, notifier):
    """When the position count cannot be fetched (Supabase also down), the alert
    must not falsely imply an empty book — it says positions are UNMONITORED."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    notifier.notify_ibkr_disconnected(
        attempts=6, minutes=18, positions_unmonitored=None, market_open=False,
    )

    sent = mock_post.call_args.kwargs["data"]["text"]
    assert "Open positions are" in sent and "UNMONITORED" in sent
    assert "closed" in sent  # market-closed phrasing


@patch('requests.post')
def test_ibkr_disconnect_alert_is_rate_limited(mock_post, notifier):
    """A persistent outage must remind, not storm: a second call inside the
    reminder window is suppressed."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    notifier.notify_ibkr_disconnected(attempts=6, minutes=18)
    notifier.notify_ibkr_disconnected(attempts=7, minutes=23)

    assert mock_post.call_count == 1  # second call deduped within the window


@patch('requests.post')
def test_ibkr_disconnect_alert_not_deduped_by_other_exceptions(mock_post, notifier):
    """The disconnect alert uses its own cache key: an unrelated notify_exception
    must not suppress it (the 2026-09-07 failure mode)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    notifier.notify_exception("some other place", ValueError("unrelated"))
    notifier.notify_ibkr_disconnected(attempts=6, minutes=18)

    assert mock_post.call_count == 2  # both fired; disconnect not swallowed
