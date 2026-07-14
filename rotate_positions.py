#!/usr/bin/env python3
"""
rotate_positions.py — Reusable portfolio rotation tool.

Sells 1–4 weak holdings and auto-fills the freed slots with the top-scored
breakouts from daily_triggers.  Delegates all order execution to the same
battle-tested functions used by force_sell.py and force_buy.py.

Usage
─────
Interactive (prompts for sells, auto-picks buys):
    python rotate_positions.py

Direct (sell specific tickers, auto-picks buys):
    python rotate_positions.py WSFS EGP
    python rotate_positions.py WSFS EGP RSI NBIX   ← up to 4 tickers

IMPORTANT — cash account requirement:
  The execution-agent MUST be stopped before running this script, because
  both scripts connect as clientId=1 (required for cash account sells).

  Recommended way to run:
      docker compose stop execution-agent
      docker run --rm --network ai-trading-bot_trading_bridge \\
        --env-file /home/dietpi/docker/ai-trading-bot/.env \\
        -v /tmp/rotate_positions.py:/app/rotate_positions.py \\
        ghcr.io/arnabbiswas1510/ai-trading-bot-execution-agent:latest \\
        python3 /app/rotate_positions.py [TICKER1 TICKER2 ...]
      docker compose start execution-agent

Execution flow
──────────────
1. Connect to IBKR as clientId=1; subscribe to account + positions
2. Verify each sell ticker exists in IBKR (not just Supabase)
3. Cancel existing GTC trailing stops for sell tickers
4. Place marketable limit SELL orders sequentially; abort if any fails
5. Query daily_triggers, sort by final_score DESC, pick top N eligible buys
6. Price each buy via IBKR delayed ask + $0.10 (never FMP)
7. Place marketable limit BUY orders from sell proceeds
8. Place GTC trailing stops on new positions
9. Update Supabase (trade_history + portfolio_positions)
10. Send Telegram notifications
"""

import os
import sys
import datetime
from zoneinfo import ZoneInfo

# ── Load .env if running outside Docker ──────────────────────────────────────
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from ib_insync import IB
from supabase import create_client

# ── Reuse sell/buy logic from the dedicated scripts ───────────────────────────
from force_sell import _place_sell, _cancel_existing_sells, _notify
from force_buy  import _place_buy, get_available_cash

# ── Config ────────────────────────────────────────────────────────────────────
IB_HOST      = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
IB_PORT      = int(os.getenv("IB_GATEWAY_PORT", 4000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
MAX_POSITIONS         = int(os.getenv("MAX_POSITIONS", 4))
STOP_LOSS_PCT         = float(os.getenv("STOP_LOSS_PCT", 0.07))
COOLING_OFF_DAYS      = int(os.getenv("COOLING_OFF_DAYS", 3))
TRIGGER_LOOKBACK_DAYS = int(os.getenv("TRIGGER_LOOKBACK_DAYS", 3))
MAX_PIVOT_EXTENSION   = float(os.getenv("MAX_PIVOT_EXTENSION", 0.05))
CLIENT_ID             = 1   # Required for cash account sells (must stop agent first)


# ── Portfolio display ─────────────────────────────────────────────────────────

def _show_portfolio(holdings: list):
    print("\nCurrent portfolio:")
    print(f"  {'#':2}  {'Ticker':6}  {'Shares':>6}  {'Buy @ $':>9}  {'Score':>6}  Buy reason")
    print("  " + "─" * 65)
    for i, h in enumerate(holdings, 1):
        score = h.get("entry_final_score") or h.get("entry_quality_score") or "—"
        print(f"  {i:2}.  {h['ticker']:6}  {h['shares']:>6}  {h['buy_price']:>9.2f}  {str(score):>6}  {h.get('buy_reason','')[:38]}")
    print()


# ── Interactive sell selection ────────────────────────────────────────────────

def _pick_sells_interactively(holdings: list) -> list[dict]:
    """Prompt the user to pick 1–4 tickers to sell. Returns list of position dicts."""
    _show_portfolio(holdings)
    ticker_map = {h["ticker"].upper(): h for h in holdings}

    while True:
        raw = input(
            "Enter tickers to sell (space-separated, 1–4), e.g.  WSFS EGP\n"
            "Or enter position numbers, e.g.  1 3\n"
            "> "
        ).strip().upper()

        if not raw or raw in ("Q", "QUIT", "EXIT"):
            print("Aborted.")
            sys.exit(0)

        tokens = raw.split()
        chosen = []

        # Accept numbers (position in the list) or ticker strings
        valid = True
        for token in tokens:
            if token.isdigit():
                idx = int(token) - 1
                if 0 <= idx < len(holdings):
                    chosen.append(holdings[idx])
                else:
                    print(f"  ✗ '{token}' is out of range (1–{len(holdings)}).")
                    valid = False
            elif token in ticker_map:
                chosen.append(ticker_map[token])
            else:
                print(f"  ✗ '{token}' is not in your portfolio. Holdings: {', '.join(sorted(ticker_map))}")
                valid = False

        if not valid:
            continue
        if not chosen:
            print("  ✗ No valid tickers entered.")
            continue
        if len(chosen) > 4:
            print("  ✗ Maximum 4 tickers per rotation.")
            continue

        # Deduplicate
        seen = set()
        unique = []
        for p in chosen:
            if p["ticker"] not in seen:
                unique.append(p)
                seen.add(p["ticker"])

        # Confirm
        summary = ", ".join(f"{p['ticker']} ({p['shares']} shares)" for p in unique)
        conf = input(f"\n  Sell: {summary}\n  Confirm? [y/N] ").strip().lower()
        if conf == "y":
            return unique
        print("  Re-enter tickers.\n")


# ── Auto-select buys from daily_triggers ─────────────────────────────────────

def _select_buy_triggers(client, n: int, exclude_tickers: set) -> list[dict]:
    """
    Return the top-N eligible triggers from daily_triggers, sorted by final_score DESC.
    Excludes tickers in exclude_tickers (current holdings + things being sold).
    """
    tz    = ZoneInfo("America/New_York")
    today = datetime.datetime.now(tz).date()
    lookback_date = (today - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).isoformat()
    cooloff_date  = (today - datetime.timedelta(days=COOLING_OFF_DAYS)).isoformat()

    triggers = client.table("daily_triggers").select("*") \
                     .gte("triggered_at", lookback_date) \
                     .execute().data or []
    triggers.sort(
        key=lambda x: x.get("final_score") or x.get("quality_score") or 0,
        reverse=True,
    )

    # Filter out what's already held / cooling off
    recent_sells = client.table("trade_history").select("ticker,sell_date") \
                         .gte("sell_date", cooloff_date).execute().data or []
    cooled = {r["ticker"] for r in recent_sells} - exclude_tickers  # sells being rotated are OK to re-buy

    eligible = [
        t for t in triggers
        if t["ticker"] not in exclude_tickers and t["ticker"] not in cooled
    ]
    return eligible[:n]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── Parse CLI args ────────────────────────────────────────────────────────
    cli_sells = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]

    print("=" * 62)
    print("  ROTATE POSITIONS — Sell weak holdings, buy top breakouts")
    print("=" * 62)
    print(f"  Price source : IBKR delayed quotes (never FMP)")
    print(f"  Order type   : Marketable limit (sells: bid×0.995, buys: ask+$0.10)")
    print(f"  Client ID    : {CLIENT_ID}  ← execution-agent must be STOPPED first")
    print()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("✗ SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)

    client   = create_client(SUPABASE_URL, SUPABASE_KEY)
    holdings = client.table("portfolio_positions").select("*").execute().data or []

    if not holdings:
        print("Portfolio is empty — nothing to rotate.")
        sys.exit(0)

    # ── Resolve sell positions ────────────────────────────────────────────────
    ticker_map = {h["ticker"].upper(): h for h in holdings}

    if cli_sells:
        invalid = [t for t in cli_sells if t not in ticker_map]
        if invalid:
            print(f"✗ Not in portfolio: {', '.join(invalid)}")
            print(f"  Holdings: {', '.join(sorted(ticker_map))}")
            sys.exit(1)
        sell_positions = [ticker_map[t] for t in cli_sells]
    else:
        sell_positions = _pick_sells_interactively(holdings)

    sell_tickers = {p["ticker"].upper() for p in sell_positions}
    n_sells = len(sell_positions)

    # ── Find buy candidates ───────────────────────────────────────────────────
    held_tickers = {h["ticker"].upper() for h in holdings}
    # Tickers after the rotation: held minus sells
    post_rotation_held = held_tickers - sell_tickers

    buy_triggers = _select_buy_triggers(client, n_sells, post_rotation_held)

    if not buy_triggers:
        print(f"⚠️  No eligible breakout triggers found in the last {TRIGGER_LOOKBACK_DAYS} days.")
        print("   Sells will proceed, but buys will be skipped.")
        buy_triggers = []

    # ── Summary before confirmation ───────────────────────────────────────────
    print("─" * 62)
    print(f"PLAN — {n_sells} sell(s), {len(buy_triggers)} auto-selected buy(s)")
    print()
    print("  SELLS:")
    for p in sell_positions:
        print(f"    {p['ticker']:6}  {p['shares']} shares @ ${p['buy_price']:.2f}")
    print()
    print("  BUYS (from daily_triggers, ranked by final_score):")
    if buy_triggers:
        for t in buy_triggers:
            score = t.get("final_score") or t.get("quality_score") or "?"
            print(f"    {t['ticker']:6}  score={score}  vol_surge={t.get('volume_surge','?')}x  "
                  f"pivot=${float(t.get('close_price',0)):.2f}")
    else:
        print("    (none available — proceeds will remain as cash)")
    print()

    confirm = input("Proceed with this rotation? [yes/N] ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    # ── Connect to IBKR ───────────────────────────────────────────────────────
    print(f"\nConnecting to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={CLIENT_ID})...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
    except Exception as e:
        print(f"✗ Could not connect to IB Gateway: {e}")
        print("  Hint: docker compose stop execution-agent (then retry)")
        sys.exit(1)

    # Subscribe — critical: IBKR must see our long positions before we sell
    acct = next((a for a in ib.managedAccounts() if not a.startswith("DU")),
                ib.managedAccounts()[0] if ib.managedAccounts() else "")
    print(f"✅ Connected. Account: {acct}")

    ib.reqAccountSummary()
    ib.reqPositions()
    ib.sleep(3)

    ibkr_positions = {p.contract.symbol: int(p.position) for p in ib.positions()}
    print(f"\nIBKR confirms {len(ibkr_positions)} position(s): {', '.join(sorted(ibkr_positions))}")

    # Validate all sell tickers exist in IBKR
    for p in sell_positions:
        t = p["ticker"]
        if t not in ibkr_positions:
            print(f"✗ ABORT — {t} not found in IBKR positions (Supabase may be out of sync).")
            print(f"  IBKR sees: {list(ibkr_positions.keys())}")
            ib.disconnect()
            sys.exit(1)
        ibkr_shares = ibkr_positions[t]
        if ibkr_shares != int(p["shares"]):
            print(f"⚠️  {t} share mismatch: Supabase={p['shares']}, IBKR={ibkr_shares}. Using IBKR.")
            p["shares"] = ibkr_shares

    # ── PHASE 1: SELL ─────────────────────────────────────────────────────────
    print("\n" + "─" * 62)
    print("PHASE 1 — Selling")
    print("─" * 62)

    tz = ZoneInfo("America/New_York")
    sell_proceeds = 0.0
    sell_results  = []

    for pos in sell_positions:
        result = _place_sell(ib, client, pos, acct)
        if result is None:
            print(f"\n✗ SELL for {pos['ticker']} FAILED — aborting rotation to avoid unbalanced state.")
            print("  Remaining sells have NOT been executed.")
            ib.disconnect()
            sys.exit(1)
        sell_proceeds += result["proceeds"]
        sell_results.append(result)
        _notify(
            f"🔴 *SOLD {result['ticker']}* (rotation)\n"
            f"{result['shares']} shares @ ${result['fill_price']:.2f}\n"
            f"P&L: ${result['pnl']:+,.2f} ({result['pct']:+.2f}%)"
        )

    print(f"\nTotal sell proceeds: ${sell_proceeds:,.2f}")

    # ── PHASE 2: BUY ─────────────────────────────────────────────────────────
    if not buy_triggers:
        print("\nNo buy triggers — proceeds remain as cash.")
        ib.disconnect()
    else:
        print("\n" + "─" * 62)
        print("PHASE 2 — Buying")
        print("─" * 62)

        position_size = sell_proceeds / len(buy_triggers)
        print(f"Position size per buy: ${position_size:,.2f}")

        held_set  = post_rotation_held.copy()
        buy_results = []

        for trigger in buy_triggers:
            print(f"\n[{trigger['ticker']}]")
            result = _place_buy(
                ib=ib, client=client, trigger=trigger,
                position_size=position_size, held_set=held_set,
                acct=acct, tz=tz, interactive=False,  # non-interactive in rotation
            )
            if result:
                buy_results.append(result)
            else:
                _notify(
                    f"⚠️ *BUY {trigger['ticker']} FAILED* (rotation)\n"
                    f"${position_size:,.2f} uninvested. Check IBKR TWS."
                )

        ib.disconnect()

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "=" * 62)
        print("ROTATION COMPLETE — SUMMARY")
        print("=" * 62)

        print("\nSOLD:")
        for r in sell_results:
            print(f"  {r['ticker']:6}  {r['shares']} shares @ ${r['fill_price']:.2f}  "
                  f"P&L: ${r['pnl']:+,.2f} ({r['pct']:+.2f}%)")

        print("\nBOUGHT:")
        for r in buy_results:
            print(f"  {r['ticker']:6}  {r['shares']} shares @ ${r['fill_price']:.2f}  "
                  f"Trail stop: ${r['stop']:.2f}")

        total_deployed = sum(r["shares"] * r["fill_price"] for r in buy_results)
        cash_remainder = sell_proceeds - total_deployed
        print(f"\nSell proceeds:   ${sell_proceeds:,.2f}")
        print(f"Deployed:        ${total_deployed:,.2f}")
        print(f"Cash remainder:  ${cash_remainder:,.2f}")
        print("\n✅ Supabase updated. Trailing stops active in IBKR.")

        _notify(
            f"🔄 *Rotation complete*\n"
            f"Sold: {', '.join(r['ticker'] for r in sell_results)}\n"
            f"Bought: {', '.join(r['ticker'] for r in buy_results)}\n"
            f"Proceeds: ${sell_proceeds:,.2f} | Deployed: ${total_deployed:,.2f}"
        )


if __name__ == "__main__":
    main()
