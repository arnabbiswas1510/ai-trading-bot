"""
flex_query_sync.py — IBKR Flex Query Cash Flow Sync

Fetches cash deposits and withdrawals from IBKR via the Flex Query Web Service API
and upserts them into the Supabase `cash_flows` table.

PURPOSE:
  The performance report UI uses the `cash_flows` table to allow users to
  include or exclude cash deposits from portfolio return calculations.
  Without this data, a $100k deposit would appear as a $100k "gain", making
  returns meaningless. This script ensures the UI has accurate deposit data
  to compute true trading returns.

RATE LIMITING:
  IBKR's Flex Web Service is a two-step API:
    Step 1: POST to SendRequest → returns a ReferenceCode (instant)
    Step 2: GET to GetStatement with ReferenceCode → returns XML data
  IBKR requires a minimum ~2-3s between Step 1 and Step 2.
  On error 1001 (server busy), the script retries with exponential backoff.

TOKEN EXPIRY:
  The Flex Web Service token expires annually. This script checks the token
  expiry date and sends a Telegram warning 30 days before expiry.

ENVIRONMENT VARIABLES REQUIRED:
  IBKR_FLEX_TOKEN         — Flex Web Service token from IBKR portal
  IBKR_FLEX_QUERY_ID      — Query ID of the CashDepositsQuery
  IBKR_FLEX_TOKEN_EXPIRY  — Token expiry date (YYYY-MM-DD) for expiry warning
  SUPABASE_URL            — Supabase project URL
  SUPABASE_KEY            — Supabase service role key
  TELEGRAM_BOT_TOKEN      — (optional) Telegram bot token
  TELEGRAM_CHAT_IDS       — (optional) Comma-separated Telegram chat IDs
"""

import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta

import requests
from supabase import create_client, Client

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

# ── Configuration ─────────────────────────────────────────────────────────────

FLEX_TOKEN      = os.environ.get("IBKR_FLEX_TOKEN", "")
FLEX_QUERY_ID   = os.environ.get("IBKR_FLEX_QUERY_ID", "")
TOKEN_EXPIRY    = os.environ.get("IBKR_FLEX_TOKEN_EXPIRY", "")  # YYYY-MM-DD
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_IDS    = [x.strip() for x in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]

SEND_URL  = "https://ndcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
FETCH_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"

# Single-attempt — IBKR Flex is low-priority; we do not retry on busy (error 1001).
# A dedicated workflow runs at 3:30 PM EDT when server traffic is lightest.
MAX_RETRIES     = 1
INITIAL_WAIT_S  = 5    # seconds to wait between Step 1 and Step 2

# Days before expiry to send Telegram warning
EXPIRY_WARN_DAYS = 30


# ── Telegram helper ───────────────────────────────────────────────────────────

def _send_telegram(message: str) -> None:
    """Send a plain Telegram message. Never raises."""
    if not TELEGRAM_TOKEN or not TELEGRAM_IDS:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_IDS:
        try:
            requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=5)
        except Exception as e:
            print(f"[flex_query_sync] Telegram error: {e}")


# ── Token expiry check ────────────────────────────────────────────────────────

def check_token_expiry() -> None:
    """
    Sends a Telegram warning if the Flex Web Service token is expiring within
    EXPIRY_WARN_DAYS days. Called every run so the warning fires once per day
    for the entire warning window.
    """
    if not TOKEN_EXPIRY:
        print("[flex_query_sync] IBKR_FLEX_TOKEN_EXPIRY not set — skipping expiry check.")
        return

    try:
        expiry = datetime.strptime(TOKEN_EXPIRY, "%Y-%m-%d").date()
    except ValueError:
        print(f"[flex_query_sync] Could not parse IBKR_FLEX_TOKEN_EXPIRY='{TOKEN_EXPIRY}'. Use YYYY-MM-DD.")
        return

    from zoneinfo import ZoneInfo
    days_left = (expiry - datetime.now(ZoneInfo("America/New_York")).date()).days

    if days_left <= 0:
        msg = (
            "🔴 <b>IBKR Flex Token EXPIRED</b>\n\n"
            f"Your Flex Web Service token expired on <b>{expiry}</b>.\n"
            "The cash flow sync will fail until a new token is generated.\n\n"
            "👉 IBKR Portal → Performance & Reports → Flex Queries → "
            "Flex Web Service Configuration → Generate New Token\n\n"
            "Then update <code>IBKR_FLEX_TOKEN</code> and <code>IBKR_FLEX_TOKEN_EXPIRY</code> in your .env."
        )
        print(f"[flex_query_sync] ⚠️  Token EXPIRED on {expiry}!")
        _send_telegram(msg)

    elif days_left <= EXPIRY_WARN_DAYS:
        msg = (
            f"⚠️ <b>IBKR Flex Token Expiring Soon</b>\n\n"
            f"Your Flex Web Service token expires on <b>{expiry}</b> "
            f"({days_left} day{'s' if days_left != 1 else ''} remaining).\n\n"
            "👉 Renew at: IBKR Portal → Performance & Reports → Flex Queries → "
            "Flex Web Service Configuration → Generate New Token\n\n"
            "Then update <code>IBKR_FLEX_TOKEN</code> and <code>IBKR_FLEX_TOKEN_EXPIRY</code> "
            "in your server .env and GitHub Actions secrets."
        )
        print(f"[flex_query_sync] ⚠️  Token expires in {days_left} days ({expiry}).")
        _send_telegram(msg)
    else:
        print(f"[flex_query_sync] Token valid until {expiry} ({days_left} days remaining).")


# ── IBKR Flex Query API ───────────────────────────────────────────────────────

def _request_reference_code() -> str:
    """
    Step 1: Submit the query to IBKR and get a ReferenceCode.
    Returns the ReferenceCode string on success, raises on failure.
    Retries on error 1001 (server busy) with exponential backoff.
    """
    params = {"t": FLEX_TOKEN, "q": FLEX_QUERY_ID, "v": "3"}

    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            wait = INITIAL_WAIT_S + (attempt - 2) * RETRY_BACKOFF_S
            print(f"[flex_query_sync] Retrying Step 1 in {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)

        resp = requests.get(SEND_URL, params=params, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        status = root.findtext("Status", "")

        if status == "Success":
            ref = root.findtext("ReferenceCode", "")
            if not ref:
                raise RuntimeError("IBKR returned Success but no ReferenceCode in response.")
            return ref

        error_code = root.findtext("ErrorCode", "")
        error_msg  = root.findtext("ErrorMessage", "")

        if error_code == "1001":
            print(f"[flex_query_sync] IBKR Step 1 returned error 1001 (server busy). Retrying...")
            continue

        raise RuntimeError(f"IBKR SendRequest failed [{error_code}]: {error_msg}")

    raise RuntimeError(f"IBKR SendRequest returned error 1001 (server busy). Try again later.")


def _fetch_statement(ref_code: str) -> str:
    """
    Step 2: Retrieve the generated report using the ReferenceCode.
    Returns the raw XML string on success.
    Retries on error 1001 (server busy) with exponential backoff.
    """
    params = {"t": FLEX_TOKEN, "q": ref_code, "v": "3"}

    for attempt in range(1, MAX_RETRIES + 1):
        wait = INITIAL_WAIT_S + (attempt - 1) * RETRY_BACKOFF_S
        print(f"[flex_query_sync] Waiting {wait}s before fetching statement (attempt {attempt}/{MAX_RETRIES})...")
        time.sleep(wait)

        resp = requests.get(FETCH_URL, params=params, timeout=30)
        resp.raise_for_status()

        # Check for error 1001 (server busy / report not ready yet)
        if "<ErrorCode>1001</ErrorCode>" in resp.text:
            print(f"[flex_query_sync] IBKR returned error 1001 (server busy). Retrying...")
            continue

        # Any other error
        if "<Status>Fail</Status>" in resp.text:
            root = ET.fromstring(resp.text)
            error_code = root.findtext("ErrorCode", "")
            error_msg  = root.findtext("ErrorMessage", "")
            raise RuntimeError(f"IBKR GetStatement failed [{error_code}]: {error_msg}")

        return resp.text

    raise RuntimeError(f"IBKR GetStatement returned error 1001 (server busy). Try again later.")


def fetch_cash_transactions() -> list[dict]:
    """
    Full two-step Flex Query flow. Returns a list of parsed cash transaction dicts.
    Each dict maps directly to a row in the Supabase `cash_flows` table.
    """
    print(f"[flex_query_sync] Step 1: Requesting Flex Query {FLEX_QUERY_ID}...")
    ref_code = _request_reference_code()
    print(f"[flex_query_sync] Got ReferenceCode: {ref_code}")

    print(f"[flex_query_sync] Step 2: Fetching statement...")
    xml_text = _fetch_statement(ref_code)

    return _parse_cash_transactions(xml_text)


def _parse_cash_transactions(xml_text: str) -> list[dict]:
    """
    Parse the IBKR Flex XML and return a list of dicts ready for Supabase upsert.

    IBKR XML format:
      <CashTransaction accountId="U12941651" currency="USD"
        description="ADJUSTMENT: DEPOSIT ADVANCE"
        dateTime="20260701;151817" amount="698.66"
        type="Deposits/Withdrawals" tradeID="" transactionID="41109247745" />

    Mapped to cash_flows columns:
      transaction_id, date, date_time, amount, type, description, currency, account_id
    """
    root = ET.fromstring(xml_text)
    transactions = []

    for ct in root.iter("CashTransaction"):
        transaction_id = ct.get("transactionID", "").strip()
        if not transaction_id:
            # Skip rows without a unique ID (e.g., summary rows)
            continue

        raw_dt = ct.get("dateTime", "")          # e.g. "20260701;151817"
        date_str, time_str = (raw_dt.split(";") + ["000000"])[:2]

        try:
            parsed_date = datetime.strptime(date_str, "%Y%m%d").date().isoformat()
            parsed_dt   = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            parsed_date = date_str
            parsed_dt   = raw_dt

        try:
            amount = float(ct.get("amount", "0"))
        except ValueError:
            amount = 0.0

        transactions.append({
            "transaction_id": transaction_id,
            "date":           parsed_date,
            "date_time":      parsed_dt,
            "amount":         amount,
            "type":           ct.get("type", ""),
            "description":    ct.get("description", ""),
            "currency":       ct.get("currency", "USD"),
            "account_id":     ct.get("accountId", ""),
        })

    return transactions


# ── Supabase upsert ───────────────────────────────────────────────────────────

def upsert_cash_flows(client: Client, transactions: list[dict]) -> int:
    """
    Upsert transactions into the `cash_flows` table.
    Uses `transaction_id` as the conflict key — safe to run daily (idempotent).
    Returns number of records upserted.
    """
    if not transactions:
        print("[flex_query_sync] No transactions to upsert.")
        return 0

    # Supabase upsert in batches of 100
    total = 0
    for i in range(0, len(transactions), 100):
        batch = transactions[i:i + 100]
        client.table("cash_flows").upsert(batch, on_conflict="transaction_id").execute()
        total += len(batch)

    return total


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("[flex_query_sync] Starting IBKR Flex Query cash flow sync...")

    # Validate required config
    missing = [v for v, k in [
        ("IBKR_FLEX_TOKEN", FLEX_TOKEN),
        ("IBKR_FLEX_QUERY_ID", FLEX_QUERY_ID),
        ("SUPABASE_URL", SUPABASE_URL),
        ("SUPABASE_KEY", SUPABASE_KEY),
    ] if not k]
    if missing:
        print(f"[flex_query_sync] ❌ Missing required env vars: {', '.join(missing)}")
        sys.exit(1)

    # 1. Check token expiry — send Telegram warning if expiring soon
    check_token_expiry()

    # 2. Fetch cash transactions from IBKR
    try:
        transactions = fetch_cash_transactions()
        print(f"[flex_query_sync] Retrieved {len(transactions)} cash transaction(s) from IBKR.")
    except Exception as e:
        print(f"[flex_query_sync] ❌ Failed to fetch from IBKR: {e}")
        _send_telegram(
            f"❌ <b>Flex Query Sync Failed</b>\n\n"
            f"Could not retrieve cash flow data from IBKR.\n"
            f"Error: <code>{e}</code>\n\n"
            f"Performance report deposit data may be stale."
        )
        sys.exit(1)

    if not transactions:
        print("[flex_query_sync] ✅ No deposits/withdrawals in the query period. Nothing to sync.")
        return

    # Log what was found
    for t in transactions:
        direction = "DEPOSIT" if t["amount"] >= 0 else "WITHDRAWAL"
        print(f"  {direction}: ${abs(t['amount']):.2f} on {t['date']} — {t['description']}")

    # 3. Upsert into Supabase
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        count = upsert_cash_flows(supabase, transactions)
        print(f"[flex_query_sync] ✅ Upserted {count} record(s) into cash_flows table.")
    except Exception as e:
        print(f"[flex_query_sync] ❌ Supabase upsert failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
