"""
telegram_notifier.py — CANSLIM Trading Bot Telegram Notification Module

Fires rich-text Telegram messages to a configured list of users on 3 event types:
  1. Trade events: BUY, SELL, POWER HOLD, MANUAL CLOSE
  2. Order failures
  3. Critical exceptions (rate-limited to prevent alert storms)

Configuration (via environment variables):
  TELEGRAM_BOT_TOKEN  — Bot API token from @BotFather
  TELEGRAM_CHAT_IDS   — Comma-separated list of recipient chat IDs

If either variable is empty/missing, all notify_* calls are silent no-ops.
Notification failures NEVER raise exceptions or affect trading logic.

Setup Instructions:
  1. Open Telegram → search @BotFather → send /newbot
  2. Choose a name and username → copy the API token → set TELEGRAM_BOT_TOKEN
  3. Each recipient must start a chat with your bot first (search by username)
  4. Visit https://api.telegram.org/bot{TOKEN}/getUpdates after each user messages the bot
  5. Find "chat": {"id": 123456789} → add all IDs to TELEGRAM_CHAT_IDS (comma-separated)
"""

import hashlib
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
ET = ZoneInfo("America/New_York")

# Suppress identical exception alerts within this window (prevents storm if same
# error fires every 15-min monitoring cycle)
EXCEPTION_COOLDOWN_SECONDS = 300   # 5 minutes


class TelegramNotifier:

    def __init__(self, bot_token: str, chat_ids: list[str]):
        self.bot_token = bot_token.strip() if bot_token else ""
        self.chat_ids = [cid.strip() for cid in chat_ids if cid.strip()]
        self._exception_cache: dict[str, float] = {}
        self._url = TELEGRAM_API_URL.format(token=self.bot_token)

    def _is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_ids)

    def _now_et(self) -> str:
        return datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")

    def _send(self, message: str) -> None:
        """Send message to all configured chat IDs. Never raises."""
        if not self._is_configured():
            return
        for chat_id in self.chat_ids:
            try:
                requests.post(
                    self._url,
                    data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                    timeout=5
                )
            except Exception:
                pass  # Notification failures must never affect trading

    # ──────────────────────────────────────────────────────────────────────────
    # Trade Event Notifications
    # ──────────────────────────────────────────────────────────────────────────

    def notify_buy(
        self,
        ticker: str,
        shares: int,
        fill_price: float,
        stop_loss: float,
        profit_target: float,
        volume_surge: float,
        pivot_dist_pct: float,
        slot_used: int,
        max_slots: int,
    ) -> None:
        """Fires after a successful IBKR market buy order is filled and recorded."""
        position_size = shares * fill_price
        stop_pct = ((stop_loss / fill_price) - 1.0) * 100.0
        target_pct = ((profit_target / fill_price) - 1.0) * 100.0
        dist_str = f"{pivot_dist_pct:+.1f}%" if pivot_dist_pct != 0 else "At high"

        msg = (
            f"🟢 <b>BUY EXECUTED</b> — ${ticker}\n"
            f"\n"
            f"📋 <b>Order Details</b>\n"
            f"  Shares:        <code>{shares:,}</code>\n"
            f"  Fill Price:    <code>${fill_price:,.2f}</code>\n"
            f"  Position Size: <code>${position_size:,.2f}</code>\n"
            f"\n"
            f"📊 <b>CANSLIM Signal</b>\n"
            f"  Vol Surge:     <code>{volume_surge:.2f}x</code> avg volume\n"
            f"  52w High Dist: <code>{dist_str}</code>\n"
            f"\n"
            f"🛡️ <b>Risk Levels</b>\n"
            f"  Stop-Loss:     <code>${stop_loss:,.2f}</code>  ({stop_pct:.1f}%)\n"
            f"  Profit Target: <code>${profit_target:,.2f}</code>  ({target_pct:+.1f}%)\n"
            f"\n"
            f"🗂️ Portfolio: {slot_used} / {max_slots} slots used\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_buy_failure(self, ticker: str, shares: int, error: Exception) -> None:
        """Fires when a buy order placement on IBKR fails."""
        msg = (
            f"❌ <b>BUY ORDER FAILED</b> — ${ticker}\n"
            f"\n"
            f"  Attempted:  <code>{shares:,} shares</code>\n"
            f"  Error:      <code>{type(error).__name__}: {str(error)[:200]}</code>\n"
            f"\n"
            f"⚠️ <i>Position was NOT opened. Check IBKR logs.</i>\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_sell(
        self,
        ticker: str,
        shares: int,
        buy_price: float,
        buy_date: str,
        fill_price: float,
        reason: str,
    ) -> None:
        """Fires after a successful IBKR market sell order is filled and logged."""
        profit_loss = (fill_price - buy_price) * shares
        percent_return = ((fill_price / buy_price) - 1.0) * 100.0
        pnl_emoji = "💰" if profit_loss >= 0 else "🔻"
        result_sign = "+" if profit_loss >= 0 else ""

        # Days held
        try:
            bought_dt = datetime.fromisoformat(buy_date.replace("Z", "+00:00"))
            days_held = (datetime.now(ET).replace(tzinfo=None) - bought_dt.replace(tzinfo=None)).days
            bought_str = bought_dt.strftime("%Y-%m-%d")
        except Exception:
            days_held = "?"
            bought_str = buy_date[:10] if len(buy_date) >= 10 else buy_date

        msg = (
            f"🔴 <b>SELL EXECUTED</b> — ${ticker}\n"
            f"   Reason: <b>{reason}</b>\n"
            f"\n"
            f"{pnl_emoji} <b>Trade Result</b>\n"
            f"  Shares:  <code>{shares:,}</code>\n"
            f"  Entry:   <code>${buy_price:,.2f}</code>\n"
            f"  Exit:    <code>${fill_price:,.2f}</code>\n"
            f"  P&amp;L:     <code>{result_sign}${abs(profit_loss):,.2f}  ({result_sign}{percent_return:.1f}%)</code>\n"
            f"\n"
            f"📅 <b>Hold Period</b>\n"
            f"  Bought:  {bought_str}\n"
            f"  Sold:    {datetime.now(ET).strftime('%Y-%m-%d')}\n"
            f"  Days:    {days_held}\n"
            f"\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_manual_close(
        self,
        ticker: str,
        shares: int,
        buy_price: float,
        sell_price: float,
        sell_price_source: str,
        buy_date: str,
    ) -> None:
        """Fires when reconcile_with_ibkr() detects a position closed manually in TWS."""
        profit_loss = (sell_price - buy_price) * shares
        percent_return = ((sell_price / buy_price) - 1.0) * 100.0
        pnl_emoji = "💰" if profit_loss >= 0 else "🔻"
        result_sign = "+" if profit_loss >= 0 else ""

        try:
            bought_dt = datetime.fromisoformat(buy_date.replace("Z", "+00:00"))
            days_held = (datetime.now(ET).replace(tzinfo=None) - bought_dt.replace(tzinfo=None)).days
            bought_str = bought_dt.strftime("%Y-%m-%d")
        except Exception:
            days_held = "?"
            bought_str = buy_date[:10] if len(buy_date) >= 10 else buy_date

        msg = (
            f"🔄 <b>MANUAL CLOSE DETECTED</b> — ${ticker}\n"
            f"   Closed in IBKR TWS (reconciled)\n"
            f"\n"
            f"{pnl_emoji} <b>Trade Result</b>\n"
            f"  Shares:   <code>{shares:,}</code>\n"
            f"  Entry:    <code>${buy_price:,.2f}</code>\n"
            f"  Exit:     <code>${sell_price:,.2f}</code>  <i>({sell_price_source})</i>\n"
            f"  P&amp;L:      <code>{result_sign}${abs(profit_loss):,.2f}  ({result_sign}{percent_return:.1f}%)</code>\n"
            f"\n"
            f"📅 Bought {bought_str} · {days_held} days held\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_power_hold(
        self,
        ticker: str,
        gain_pct: float,
        days_held: int,
        expiry_date: str,
        stop_loss: float,
    ) -> None:
        """Fires when the Power Hold Rule is activated (20%+ gain in ≤21 days)."""
        msg = (
            f"🔥 <b>POWER HOLD ACTIVATED</b> — ${ticker}\n"
            f"\n"
            f"Stock surged <b>+{gain_pct:.1f}%</b> in just <b>{days_held} day{'s' if days_held != 1 else ''}!</b>\n"
            f"\n"
            f"⏳ Profit target suspended for 8 weeks.\n"
            f"   Normal exits resume: <b>{expiry_date}</b>\n"
            f"   Stop-Loss still active at: <code>${stop_loss:,.2f}</code>\n"
            f"\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Exception Notifications (rate-limited)
    # ──────────────────────────────────────────────────────────────────────────

    def notify_exception(self, context: str, error: Exception) -> None:
        """
        Rate-limited exception alert. Suppresses duplicate (same context + error type)
        notifications within EXCEPTION_COOLDOWN_SECONDS to prevent alert storms.

        Args:
            context: Human-readable location string, e.g. "main_loop() — execution_agent.py"
            error:   The caught exception
        """
        error_key = hashlib.md5(
            f"{context}:{type(error).__name__}".encode()
        ).hexdigest()
        now = time.time()

        if now - self._exception_cache.get(error_key, 0) < EXCEPTION_COOLDOWN_SECONDS:
            return  # Suppress duplicate within cooldown window

        self._exception_cache[error_key] = now

        error_str = str(error)[:300]  # Truncate very long error messages
        msg = (
            f"⚠️ <b>TRADING BOT EXCEPTION</b>\n"
            f"\n"
            f"📍 Location:  <code>{context}</code>\n"
            f"❌ Error:     <code>{type(error).__name__}: {error_str}</code>\n"
            f"\n"
            f"🔧 <i>The bot will attempt to continue. Check logs for details.</i>\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)
