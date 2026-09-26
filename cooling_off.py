"""Reason-aware cooling-off (re-entry block) — single source for all buy paths.

The cooling-off gate does two SEPARABLE jobs, and they get different treatment:

  (A) DATA INTEGRITY — same-session churn guard (UNCONDITIONAL).
      Block ANY re-buy of a name sold *today*, profit or loss. Selling and
      re-buying the same name inside one session blends the IBKR averageCost
      basis, which then poisons every downstream stop/P&L/size calc. This is
      what happened to NTRA on 2026-08-31 (sold 61 sh at 10:26, re-bought 61 sh
      at 10:32). Non-negotiable.

  (B) STRATEGY — the multi-day calendar block (LOSS EXITS ONLY).
      A name sold because it was FALLING (a stop-out / loss exit) is a failed
      setup; re-buying it within COOLING_OFF_DAYS catches a knife. Measured on
      25 real re-entries: prior-loss re-entries returned -$1,750 (30% win) vs
      prior-profit re-entries +$61 (53% win). A name sold at a PROFIT is a
      proven leader that may re-base and re-trigger cleanly — blocking it for
      days only idles capital (the 2026-09-25 zero-trade day). So a profit exit
      skips the calendar block; the buy-quality gates (extension, breakout
      quality, RS) decide whether the fresh trigger is clean.

Two data sources, because trade_history alone is not sufficient: it is written
by the bot's own sell path, so a resting IBKR stop firing between cycles (or a
failed write) leaves no row and the gate goes blind. ibkr_fills is written by
the real-time fill hook the instant IBKR reports a fill.

See decisions/2026-09-26_reason-aware-cooling-off.md and
decisions/2026-09-10_lot-basis-and-broker-aware-cooling-off.md.
"""
import datetime


def compute_cooled_map(client, today, cooling_off_days):
    """Return {ticker: reason} for every ticker currently blocked from re-entry.

    `today` is a datetime.date (America/New_York). Only tickers present in the
    returned dict are blocked; a profit sale older than today is intentionally
    ABSENT, so it is eligible for re-entry.
    """
    cutoff      = (today - datetime.timedelta(days=cooling_off_days)).isoformat()
    today_start = today.isoformat()          # NY calendar day, for job (A)
    cooled = {}

    # --- trade_history: most-recent sale per ticker within the window ---------
    try:
        rows = (client.table("trade_history")
                .select("ticker,sell_date,net_profit_loss,profit_loss,sell_reason")
                .gte("sell_date", cutoff)
                .order("sell_date", desc=True)
                .execute().data) or []
    except Exception:
        rows = []

    ledger_seen = set()
    for r in rows:
        t = r.get("ticker")
        if not t or t in ledger_seen:
            continue
        ledger_seen.add(t)                    # rows are desc → first = most recent
        sell_date = str(r.get("sell_date") or "")
        pnl = r.get("net_profit_loss")
        if pnl is None:
            pnl = r.get("profit_loss")
        sold_today = sell_date >= today_start
        # None P&L is treated as a loss — conservative, never a free re-entry.
        was_loss = (pnl is None) or (float(pnl) <= 0)

        if sold_today:
            cooled[t] = f"(A) sold today — same-session churn guard ({sell_date})"
        elif was_loss:
            cooled[t] = (f"(B) loss sale within {cooling_off_days}d "
                         f"(pnl={pnl}; {r.get('sell_reason')})")
        # else: PROFIT sale older than today → NOT cooled; gates decide.

    # --- ibkr_fills belt-and-suspenders --------------------------------------
    try:
        fills = (client.table("ibkr_fills")
                 .select("ticker,fill_time")
                 .eq("side", "SLD")
                 .gte("fill_time", cutoff)
                 .order("fill_time", desc=True)
                 .execute().data) or []
    except Exception:
        fills = []

    fill_seen = set()
    for f in fills:
        t = f.get("ticker")
        if not t or t in fill_seen:
            continue
        fill_seen.add(t)
        if t in cooled:
            continue                          # already decided by the ledger
        ft = str(f.get("fill_time") or "")
        if ft >= today_start:
            cooled[t] = f"(A) sold today — same-session churn guard (ibkr_fills SLD @ {ft})"
        elif t not in ledger_seen:
            # SLD in window with NO ledger row: reason unknown (likely a resting
            # stop that fired = a loss). Block conservatively — a missing ledger
            # row must never open a re-entry the loss path would have closed.
            cooled[t] = f"(B) SLD in window, no ledger row (reason unknown) @ {ft}"
        # else: a profit ledger sale older than today whose SLD is the same
        # (older-than-today) fill → allowed.

    return cooled
