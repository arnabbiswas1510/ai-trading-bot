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
import html
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
ET = ZoneInfo("America/New_York")

# Suppress identical exception alerts within this window (prevents storm if same
# error fires every monitoring cycle — e.g. gateway connection refused during restart)
EXCEPTION_COOLDOWN_SECONDS = 3600  # 1 hour — reminder frequency, not silence


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
            for attempt in range(2):          # 1 retry on timeout
                try:
                    r = requests.post(
                        self._url,
                        data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                        timeout=(5, 15),      # (connect_timeout, read_timeout) in seconds
                    )
                    if r.status_code != 200:
                        print(f"Telegram API Error ({r.status_code}): {r.text}")
                    break                     # success — no retry needed
                except requests.exceptions.Timeout:
                    if attempt == 0:
                        print(f"Telegram timeout for chat_id={chat_id}, retrying...")
                    else:
                        print(f"Telegram timeout for chat_id={chat_id} after retry — giving up.")
                except Exception as e:
                    print(f"Telegram Network Error: {e}")
                    break                     # non-timeout errors don't benefit from retry

    # ──────────────────────────────────────────────────────────────────────────
    # Trade Event Notifications
    # ──────────────────────────────────────────────────────────────────────────

    def notify_breakouts_detected(self, breakouts: list[dict]) -> None:
        """Fires from technical_screener.py when breakouts are pushed to the database.
        Silent when no breakouts found — only notifies when there are actual signals.
        """
        if not breakouts:
            return  # No alert on quiet days — don't noise the channel

        msg = f"🚀 <b>CANSLIM Breakouts Detected!</b>\n\nFound {len(breakouts)} breakout(s) today:\n\n"
        for t in breakouts:
            msg += f"• <b>{t['ticker']}</b> — ${t['close_price']} (Vol: {t['volume_surge']}x)\n"

        msg += f"\n<i>AI evaluation running... scores will follow shortly.</i>\n\n🕒 {self._now_et()}"
        self._send(msg)

    def notify_ai_evaluation_complete(self, triggers: list[dict]) -> None:
        """
        Fires from ai_evaluator.py after all 5-component scores are computed.
        Shows final score, grade, component breakdown, and AI rationale for each trigger.
        Triggers are expected to be sorted by final_score descending before calling.
        """
        if not triggers:
            return

        # Sort by final_score descending so the best pick is shown first
        sorted_triggers = sorted(triggers, key=lambda x: x.get("final_score", 0), reverse=True)

        msg = f"🧠 <b>AI Evaluation Complete</b> — {len(triggers)} trigger(s) scored\n\n"

        for t in sorted_triggers:
            ticker     = t.get("ticker", "?")
            price      = t.get("close_price", 0)
            final      = t.get("final_score", "—")
            grade      = t.get("ai_grade", "?")
            tech       = t.get("technical_score") or t.get("quality_score", "—")
            liq        = t.get("liquidity_score", "—")
            ai_s       = t.get("ai_rating", "—")
            sent       = t.get("sentiment_score", "—")
            rs         = t.get("rs_score", "—")
            atr_pct    = t.get("atr_pct", 0) or 0
            est_days   = t.get("est_days_to_target", 999) or 999
            rationale  = t.get("score_rationale", "").strip()

            grade_emoji = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}.get(grade, "⚪")
            swing_emoji = (
                "🚀" if 0 < est_days <= 15 else
                "✅" if est_days <= 30 else
                "⚠️" if est_days <= 60 else
                "❌"
            )

            msg += (
                f"{grade_emoji} <b>{ticker}</b>  ${price}  →  "
                f"<b>Score: {final}</b> ({grade})\n"
                f"  Tech:{tech} | Liq:{liq} | AI:{ai_s} | Sent:{sent} | RS:{rs}\n"
                f"  {swing_emoji} ATR: {atr_pct}%/day  →  Est. {est_days} days to +25%\n"
            )
            if rationale:
                msg += f"  📝 <b>Rating Reason:</b> <i>{rationale}</i>\n"
            msg += "\n"


        msg += f"🕒 {self._now_et()}"
        self._send(msg)

    def notify_buy(
        self,
        ticker: str,
        shares: int,
        fill_price: float,
        stop_loss: float,
        volume_surge: float,
        pivot_dist_pct: float,
        slot_used: int,
        max_slots: int,
        trail_pct: float = None,
        stop_method: str = None,
        profit_target: float = None,  # kept for backwards-compat; no longer used
    ) -> None:
        """Fires after a successful IBKR market buy order is filled and recorded."""
        position_size = shares * fill_price
        stop_pct = ((stop_loss / fill_price) - 1.0) * 100.0
        dist_str = f"{pivot_dist_pct:+.1f}%" if pivot_dist_pct != 0 else "At high"

        # Trail stop line — show ATR-derived % and method label if available
        if trail_pct is not None:
            trail_pct_display = f"{trail_pct * 100:.1f}%"
            method_label = stop_method or "dynamic"
            trail_line = (
                f"  Trail Stop:    <code>${stop_loss:,.2f}</code>  "
                f"({trail_pct_display} — {method_label})\n"
            )
        else:
            trail_line = (
                f"  Trail Stop:    <code>${stop_loss:,.2f}</code>  "
                f"({stop_pct:.1f}%)\n"
            )

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
            f"🛡️ <b>Risk Management</b>\n"
            f"{trail_line}"
            f"  Exit Rule:     EMA-21 end-of-day | plateau rotation\n"
            f"\n"
            f"🗂️ Portfolio: {slot_used} / {max_slots} slots used\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_buy_failure(self, ticker: str, shares: int, error: object) -> None:
        """Fires when a buy order placement on IBKR fails."""
        # html.escape prevents Telegram 400 errors when IBKR error messages
        # contain raw HTML fragments (e.g. <br> in Error 201 cash-check messages).
        error_str = html.escape(str(error)[:300])
        msg = (
            f"❌ <b>BUY ORDER FAILED</b> — ${ticker}\n"
            f"\n"
            f"  Attempted:  <code>{shares:,} shares</code>\n"
            f"  Error:      <code>{error_str}</code>\n"
            f"\n"
            f"⚠️ <i>Position was NOT opened. Check IBKR logs.</i>\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_buy_loop_halted(self, ticker: str, reason: str) -> None:
        """
        Fires when the buy loop is stopped after a failed order attempt.

        Distinct from notify_buy_failure: this message signals that ALL remaining
        breakout candidates for this cycle were skipped — the loop is dead — and
        manual intervention is required to preserve the intended buying order.
        """
        reason_str = html.escape(str(reason)[:300])
        msg = (
            f"🛑 <b>BUY LOOP HALTED — MANUAL INTERVENTION REQUIRED</b>\n"
            f"\n"
            f"  Failed ticker: <code>${ticker}</code>\n"
            f"  Reason:        <code>{reason_str}</code>\n"
            f"\n"
            f"⚠️ <i>No further buy orders will be placed this cycle.\n"
            f"   Remaining breakout candidates were skipped to preserve\n"
            f"   portfolio construction priority order.</i>\n"
            f"\n"
            f"👉 <b>Action needed</b>: Review and manually place any missed orders.\n"
            f"🕒 {self._now_et()}"
        )
        self._send(msg)

    def notify_breakout_verdict_fail(
        self,
        ticker: str,
        buy_price: float,
        current_price: float,
        price_pass: bool,
        vol_pass: bool,
    ) -> None:
        """Sent at EOD of Day 3 when a position fails the breakout verdict.
        Activates the Intraday Loss Minimiser from Day 4 onwards.
        """
        if not self._is_configured():
            return
        try:
            ret_pct = ((current_price / buy_price) - 1.0) * 100.0
            price_icon = "✅" if price_pass else "❌"
            vol_icon   = "✅" if vol_pass   else "❌"
            msg = (
                f"❌ <b>BREAKOUT VERDICT FAILED — ${ticker}</b>\n\n"
                f"{price_icon} Price check:  {ret_pct:+.2f}% "
                f"{'(passed)' if price_pass else '(needed +1.0%)'}\n"
                f"{vol_icon} Volume check: "
                f"{'above 75% avg (passed)' if vol_pass else 'below 75% avg'}\n\n"
                f"⚡ <b>Intraday Loss Minimiser now active.</b>\n"
                f"   Bot will sell on next 0.5% pullback from intraday high.\n"
                f"🕒 {self._now_et()}"
            )
            self._send(msg)
        except Exception:
            pass

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

        import html  # already imported at top-level; local import kept for clarity
        error_str = html.escape(str(error)[:300])  # Truncate and escape HTML to prevent Telegram 400 errors
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
