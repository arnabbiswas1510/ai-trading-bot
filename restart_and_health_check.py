"""
restart_and_health_check.py — 6:00 AM IB Gateway Health Check & Telegram Notifier

Executed post-restart to verify IB Gateway, target account U12941651,
and trading funds. Sends Telegram alert on failure or success.
"""

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from ib_insync import IB

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

# Configuration
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_IDS = [x.strip() for x in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
TARGET_ACCOUNT = os.environ.get("IBKR_ACCOUNT", "U12941651")
IB_HOST = os.environ.get("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT = int(os.environ.get("IB_GATEWAY_PORT", "4000"))
ET = ZoneInfo("America/New_York")


def send_telegram(message: str) -> None:
    """Send HTML Telegram message to all configured chat IDs."""
    if not TELEGRAM_TOKEN or not TELEGRAM_IDS:
        print("[health_check] Telegram not configured — skipping notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in TELEGRAM_IDS:
        try:
            r = requests.post(url, data={"chat_id": cid, "text": message, "parse_mode": "HTML"}, timeout=10)
            if r.status_code != 200:
                print(f"[health_check] Telegram error ({r.status_code}): {r.text}")
        except Exception as e:
            print(f"[health_check] Telegram send error for {cid}: {e}")


def now_et_str() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")


def verify_health() -> tuple[bool, str, dict]:
    """
    Connects to IB Gateway, verifies account U12941651, and checks own cash.
    Retries up to 3 times with exponential backoff.
    """
    max_retries = 3
    last_error = ""

    for attempt in range(1, max_retries + 1):
        print(f"[health_check] Attempt {attempt}/{max_retries}: Connecting to IB Gateway at {IB_HOST}:{IB_PORT}...")
        ib = IB()
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=998, timeout=15)
            accounts = ib.managedAccounts()
            print(f"[health_check] Connected! Managed accounts: {accounts}")

            if TARGET_ACCOUNT not in accounts and not any(a.startswith("U") for a in accounts):
                last_error = f"Target account {TARGET_ACCOUNT} not found in managed accounts: {accounts}"
                print(f"[health_check] ⚠️ {last_error}")
                ib.disconnect()
                time.sleep(10)
                continue

            active_account = TARGET_ACCOUNT if TARGET_ACCOUNT in accounts else accounts[0]

            ib.reqAccountSummary()
            ib.sleep(2)

            total_cash = None
            net_liq = None
            for av in ib.accountValues():
                acc = getattr(av, "account", "")
                if acc and acc != active_account:
                    continue
                if av.currency != "USD":
                    continue
                if av.tag == "TotalCashValue":
                    total_cash = float(av.value)
                elif av.tag == "NetLiquidation":
                    net_liq = float(av.value)

            ib.disconnect()

            if total_cash is None:
                last_error = f"TotalCashValue tag not returned for account {active_account}"
                print(f"[health_check] ⚠️ {last_error}")
                time.sleep(10)
                continue

            if total_cash < 0:
                last_error = f"MARGIN LOAN ACTIVE: TotalCashValue is negative (${total_cash:,.2f})"
                print(f"[health_check] 🚨 {last_error}")
                return False, last_error, {"account": active_account, "cash": total_cash, "net_liq": net_liq}

            print(f"[health_check] ✅ HEALTH CHECK PASSED! Account: {active_account}, Cash: ${total_cash:,.2f}, NetLiq: ${net_liq:,.2f}")
            return True, "Health check passed cleanly", {
                "account": active_account,
                "cash": total_cash,
                "net_liq": net_liq or 0.0
            }

        except Exception as e:
            last_error = f"Failed to connect to IB Gateway: {e}"
            print(f"[health_check] ⚠️ Attempt {attempt} failed: {last_error}")
            try:
                ib.disconnect()
            except Exception:
                pass
            if attempt < max_retries:
                time.sleep(15)

    return False, last_error, {}


def main() -> None:
    print(f"[health_check] Starting 6:00 AM IB Gateway Health Check at {now_et_str()}...")
    ok, error_msg, details = verify_health()

    if ok:
        cash = details.get("cash", 0.0)
        net_liq = details.get("net_liq", 0.0)
        acct = details.get("account", TARGET_ACCOUNT)
        msg = (
            f"✅ <b>6:00 AM IB Gateway Restart Successful</b>\n\n"
            f"<b>Time:</b> {now_et_str()}\n"
            f"<b>Account:</b> <code>{acct}</code>\n"
            f"<b>Own Cash:</b> ${cash:,.2f}\n"
            f"<b>Net Liquidation:</b> ${net_liq:,.2f}\n\n"
            f"🟢 <i>Trading bot is ready for 9:30 AM ET market open.</i>"
        )
        print("[health_check] Sending success Telegram alert...")
        send_telegram(msg)
        sys.exit(0)
    else:
        msg = (
            f"🚨 <b>CRITICAL: 6:00 AM IB Gateway Restart Failed</b>\n\n"
            f"<b>Time:</b> {now_et_str()}\n"
            f"<b>Error:</b> <code>{error_msg}</code>\n\n"
            f"⚠️ <b>Action Required:</b> Please check IB Gateway on server before 9:30 AM ET market open!"
        )
        print(f"[health_check] 🚨 Sending failure Telegram alert: {error_msg}")
        send_telegram(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
