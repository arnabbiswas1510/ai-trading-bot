import os
import requests
from telegram_notifier import TelegramNotifier

with open("c:/Users/arnab/OneDrive/Documents/agy/ai-trading-bot/.env") as f:
    for line in f:
        if line.strip() and not line.strip().startswith("#"):
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                os.environ[parts[0].strip()] = parts[1].strip()

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")

class DebugNotifier(TelegramNotifier):
    def _send(self, message: str) -> None:
        if not self._is_configured():
            return
        for chat_id in self.chat_ids:
            res = requests.post(
                self._url,
                data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5
            )
            print(f"[{chat_id}] Status: {res.status_code}")
            if res.status_code != 200:
                print(f"Error: {res.text}")

notifier = DebugNotifier(bot_token=bot_token, chat_ids=chat_ids)
print("Testing notify_buy...")
notifier.notify_buy(
    ticker="AAPL", shares=10, fill_price=150.0, stop_loss=140.0, profit_target=180.0,
    volume_surge=1.5, pivot_dist_pct=2.0, slot_used=1, max_slots=4
)
print("Testing notify_sell...")
notifier.notify_sell(
    ticker="AAPL", shares=10, buy_price=150.0, buy_date="2026-06-01T10:00:00Z",
    fill_price=180.0, reason="CANSLIM Breakout [daily_triggers]: Vol Surge 2.27x"
)
print("Testing notify_manual_close...")
notifier.notify_manual_close(
    ticker="AAPL", shares=10, buy_price=150.0, sell_price=180.0,
    sell_price_source="live price fallback", buy_date="2026-06-01T10:00:00Z"
)
print("Done!")
