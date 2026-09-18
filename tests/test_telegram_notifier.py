import pytest
from unittest.mock import patch, MagicMock
from telegram_notifier import (
    TelegramNotifier, DELIVERY_FAIL_MARKER, DELIVERY_ALARM_AFTER,
)

@pytest.fixture
def notifier():
    """Returns a configured TelegramNotifier for testing."""
    return TelegramNotifier(bot_token="test_token", chat_ids=["12345"])

@patch('requests.post')
def test_telegram_api_error_printed(mock_post, notifier, capsys):
    """A non-200 from the Telegram API must be reported, not swallowed.

    Now carries the greppable DELIVERY_FAIL_MARKER on stderr and counts as a
    failed delivery. A 400 is the dangerous case: Telegram accepted the request
    and DROPPED the message (usually malformed HTML), so without this the bot
    believes it alerted when it did not.
    """
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    mock_post.return_value = mock_response

    assert notifier._send("Test message") is False

    captured = capsys.readouterr()
    assert DELIVERY_FAIL_MARKER in captured.err
    assert "HTTP 400" in captured.err
    assert "Bad Request: chat not found" in captured.err
    assert notifier.consecutive_failures == 1

@patch('requests.post')
def test_telegram_network_error_printed(mock_post, notifier, capsys):
    """Test that a network exception (e.g. timeout) is explicitly printed and not swallowed."""
    # Mock a network exception
    mock_post.side_effect = Exception("Connection timed out")

    assert notifier._send("Test message") is False

    captured = capsys.readouterr()
    assert DELIVERY_FAIL_MARKER in captured.err
    assert "Connection timed out" in captured.err
    assert notifier.consecutive_failures == 1


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


@patch('requests.post')
def test_sell_state_change_escapes_ticker_html(mock_post, notifier):
    """Ticker text must be escaped before sending HTML parse_mode Telegram."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    notifier.notify_sell_state_change(
        "PSX <= trigger",
        "Unproven",
        "Exiting",
        "EXITING",
        unrealized_pct=-1.2,
        peak_pct=0.0,
        days_held=0,
    )

    sent = mock_post.call_args.kwargs["data"]["text"]
    assert "PSX &lt;= trigger" in sent
    assert "PSX <= trigger" not in sent


# ══════════════════════════════════════════════════════════════════════════════
# Delivery health — the 2026-09-18 silent-outage regression suite
#
# The bug these pin was not a crash. Every alert "worked": no exception, no
# traceback, trading unaffected. Six trade events were simply never announced,
# and nothing anywhere said so. Each test below asserts that a specific failure
# mode now leaves evidence.
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def unconfigured():
    return TelegramNotifier(bot_token="", chat_ids=[])


def test_unconfigured_send_is_no_longer_silent(unconfigured, capsys):
    """THE regression. A missing token used to `return` with zero output, so a
    deployment that lost its env vars was indistinguishable from a quiet day."""
    assert unconfigured._send("anything") is False
    err = capsys.readouterr().err
    assert DELIVERY_FAIL_MARKER in err
    assert "TELEGRAM_BOT_TOKEN" in err
    assert unconfigured.consecutive_failures == 1


def test_unconfigured_names_the_missing_variable(capsys):
    """Token present but no recipients is a different fix from no token."""
    n = TelegramNotifier(bot_token="tok", chat_ids=[])
    n._send("x")
    err = capsys.readouterr().err
    assert "TELEGRAM_CHAT_IDS" in err and "TELEGRAM_BOT_TOKEN" not in err


@patch('requests.post')
def test_successful_send_resets_the_failure_counter(mock_post, notifier, capsys):
    mock_post.side_effect = Exception("boom")
    notifier._send("fail")
    assert notifier.consecutive_failures == 1

    ok = MagicMock(); ok.status_code = 200
    mock_post.side_effect = None
    mock_post.return_value = ok
    assert notifier._send("recover") is True
    assert notifier.consecutive_failures == 0
    assert notifier.last_success_at is not None
    assert "RECOVERED" in capsys.readouterr().err


@patch('requests.post')
def test_alarm_escalates_after_repeated_failures(mock_post, notifier, capsys):
    """One blip is a warning; a run of them is an outage and must say so."""
    mock_post.side_effect = Exception("down")
    for _ in range(DELIVERY_ALARM_AFTER):
        notifier._send("x")
    err = capsys.readouterr().err
    assert "ALARM" in err
    assert "NOT being" in err and "announced" in err


@patch('requests.post')
def test_partial_delivery_counts_as_failure(mock_post, capsys):
    """Two recipients, one broken: that is a fault, not a rounding error."""
    n = TelegramNotifier(bot_token="t", chat_ids=["good", "bad"])
    good = MagicMock(); good.status_code = 200
    bad = MagicMock(); bad.status_code = 403; bad.text = "bot was blocked by the user"

    mock_post.side_effect = lambda url, **kw: good if kw["data"]["chat_id"] == "good" else bad
    assert n._send("x") is False
    assert n.consecutive_failures == 1
    assert "bot was blocked" in capsys.readouterr().err


@patch('requests.post')
def test_timeout_retries_once_then_records_failure(mock_post, notifier):
    import requests as _rq
    mock_post.side_effect = _rq.exceptions.Timeout()
    assert notifier._send("x") is False
    assert mock_post.call_count == 2           # original + 1 retry
    assert "timed out after retry" in notifier.last_error


# ── verify_delivery: the startup self-test ───────────────────────────────────

def test_verify_delivery_reports_missing_token():
    ok, detail = TelegramNotifier("", []).verify_delivery()
    assert ok is False and "TELEGRAM_BOT_TOKEN" in detail


@patch('requests.get')
def test_verify_delivery_distinguishes_revoked_token(mock_get, notifier):
    """401 vs unreachable need completely different fixes. Guessing between
    them is what made the 2026-09-18 outage slow to diagnose."""
    r = MagicMock(); r.status_code = 401
    mock_get.return_value = r
    ok, detail = notifier.verify_delivery()
    assert ok is False
    assert "revoked" in detail and "401" in detail


@patch('requests.get')
def test_verify_delivery_distinguishes_network_failure(mock_get, notifier):
    mock_get.side_effect = Exception("Name or service not known")
    ok, detail = notifier.verify_delivery()
    assert ok is False
    assert "cannot reach api.telegram.org" in detail


@patch('requests.get')
def test_verify_delivery_succeeds_and_names_the_bot(mock_get, notifier):
    r = MagicMock(); r.status_code = 200
    r.json.return_value = {"ok": True, "result": {"username": "canslim_bot"}}
    mock_get.return_value = r
    ok, detail = notifier.verify_delivery()
    assert ok is True
    assert "@canslim_bot" in detail and "1 recipient" in detail


@patch('requests.get')
def test_verify_delivery_never_raises(mock_get, notifier):
    """A health check that can crash the agent is worse than no health check."""
    mock_get.side_effect = KeyboardInterrupt  # not caught by `except Exception`
    with pytest.raises(KeyboardInterrupt):
        notifier.verify_delivery()
    mock_get.side_effect = ValueError("anything else")
    assert notifier.verify_delivery()[0] is False


def test_health_snapshot_performs_no_network_io(notifier):
    with patch('requests.post') as p, patch('requests.get') as g:
        h = notifier.health()
        assert p.call_count == 0 and g.call_count == 0
    assert h["configured"] is True
    assert h["consecutive_failures"] == 0
    assert h["last_success_at"] is None


@patch('requests.post')
def test_notify_buy_reports_a_dropped_alert(mock_post, notifier, capsys):
    """End-to-end on the exact call that went missing: a real notify_* method
    whose delivery fails must leave a marker, not return quietly."""
    r = MagicMock(); r.status_code = 400; r.text = "Bad Request"
    mock_post.return_value = r
    notifier.notify_buy(ticker="DHT", shares=788, fill_price=23.04, stop_loss=21.4,
                        volume_surge=1.66, pivot_dist_pct=-0.5, slot_used=5,
                        max_slots=5, trail_pct=0.07, stop_method="atr")
    assert DELIVERY_FAIL_MARKER in capsys.readouterr().err
    assert notifier.consecutive_failures == 1
