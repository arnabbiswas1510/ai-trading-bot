"""
research/strategy_backtest.py — a strategy backtest whose EXITS are the LIVE code.

WHAT THIS IS, AND WHY IT IS DIFFERENT FROM THE OTHER HARNESSES
─────────────────────────────────────────────────────────────
This is the backtest-fidelity Phase 2 deliverable: a full-portfolio strategy
backtest that resolves every exit by calling the *actual* live exit engine —
``exit_core.evaluate_exit`` and the ``exit_rules`` primitives it orchestrates —
rather than re-implementing the exit rules in yet another place.

Every other backtester in this repo carries its OWN copy of the exit logic and
those copies drift silently from production:

  • ``backend/backtester.py``      models the RETIRED 7%-trail + EMA-21 rules;
  • ``research/atr_rank_bt.py``     hard-codes a ``SHIPPED_EXITS`` ladder;
  • ``research/exit_rule_replay.py`` mirrors the ``PROVE_IT_*`` constants inline.

Any profitability number a drifting copy produces answers a question about a
strategy the bot no longer runs. This harness removes that class of error for
the exit side: it imports ``exit_core`` and ``exit_rules`` and calls them, so the
Prove-It Stop (Phase 1 band, Phase 2 give-back floor), the dynamic trail ladder,
the power-hold widening and the partial scale-out are byte-for-byte the live
rules. Change a threshold in ``exit_rules.py`` and this backtest changes with it.

WHY IT LIVES IN research/ AND NOT IN THE WEB CONTAINER (yet)
───────────────────────────────────────────────────────────
``backend/backtester.py`` runs inside the web/dashboard image, which is built
``COPY backend/ ./backend/`` and deliberately does NOT contain the root modules
(``config.py``, ``exit_rules.py``, ``exit_core.py`` — see the NOTE ON CONTAINER
LAYOUT in ``config.py``). Importing the live exit engine there requires bringing
those files into that image, which touches the live dashboard build. That is
"Option A" and is done as a separate, deployment-touching change. This tool is
"Option B": it lives at research/ where the live modules already import cleanly,
runs from the CLI exactly like ``exit_rule_replay.py``, and carries ZERO
deployment risk. It is the trustworthy object; the web button is a convenience
that Option A repoints at the same engine afterwards.

DATA: DAILY BARS, OFFLINE, NO FMP SUBSCRIPTION
──────────────────────────────────────────────
Prices come from the committed ``benchmark_data/`` parquet via ``bardata`` — 313
names, 2023-07 .. 2026-08, no network, no rate limit, reproducible. So this needs
no FMP key at all, let alone the intraday subscription.

FIDELITY — READ THIS BEFORE TRUSTING A DOLLAR FIGURE
────────────────────────────────────────────────────
This achieves RULE parity (which exit fires, and why) but NOT exact fill-price
fidelity, because daily bars cannot resolve intraday mechanics:

  • The live loss rules do not sell — they ``arm_exit()`` a tight 0.6% IBKR trail
    with a 3.25h deadline that resolves INTRADAY. On daily bars that bounce is
    invisible, so a Prove-It arm is modelled as a sell AT the level (or the open,
    if the bar gapped through it). This is the same daily-bar limitation
    documented in ``exit_rule_replay.py``; it is why that tool uses 5-minute bars.
  • The 15-minute poll is collapsed to a once-a-day sequence: peak/HWM and arming
    see the day's HIGH, downside stops resolve against the day's LOW, and the
    trail-tightening / scale-out / hard-stop updates are applied at the CLOSE and
    take effect the NEXT day (a ~one-cycle lag versus the live intraday placement).
  • Commission and slippage are NOT modelled (roadmap item #3). Gap-through fills
    are modelled pessimistically (fill at the open when the bar opens through the
    level) but partial-fill and queue effects are not.

Therefore: TRUST this for RELATIVE questions — does a rule fire, how often, does
a change help or hurt, do entries and exits behave like production. Do NOT read
its absolute P&L as a precise +EV/−EV verdict on the tight Prove-It exits; that
needs 5-minute bars, which drop in here with NO logic change (see
``resolve_position_day`` — swap the once-a-day OHLC resolution for a per-5min-bar
loop and the exit_core calls are identical). The register work-item
``intraday-fmp-exit-fidelity`` tracks that upgrade, gated on live usage showing
it is needed.

ENTRY PARITY IS A SEPARATE STEP
───────────────────────────────
The ENTRY scan below mirrors ``backend/backtester.py`` (20-day-high breakout,
above SMA50/200, ≥1.4x volume, SPY-above-EMA21 market filter) so this tool and
the web backtester choose the SAME entries. That mechanical scan is itself a
simplification of the live screen (which leans on fundamentals + AI grading, and
needs point-in-time data — roadmap item #4). Pointing entries at
``decision_core`` is a complementary follow-up; this deliverable is scoped to
EXIT parity.

USAGE
─────
    python3 research/strategy_backtest.py                 # headline + exit histogram
    python3 research/strategy_backtest.py --json out.json # machine-readable
    python3 research/strategy_backtest.py --start 2024-01-01 --end 2026-06-30
    python3 research/strategy_backtest.py --universe pass  # only research/pass_names.txt

No secrets required — reads the committed benchmark dataset only, writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime

# research/ (for bardata) and repo root (for exit_core/exit_rules/config).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bardata  # noqa: E402
import exit_core as ec  # noqa: E402
import exit_rules as er  # noqa: E402
from config import MAX_POSITIONS  # noqa: E402

# ── Entry-side constants — mirror backend/backtester.py so entries match ──────────
MARKET_SYMBOL      = "SPY"    # dataset has SPY, not ^GSPC/QQQ; live gate is richer
EMA_WINDOW         = 21
SMA_FAST           = 50
SMA_SLOW           = 200
VOL_WINDOW         = 50
VOL_SURGE          = 1.4      # breakout volume must be >= 1.4x 50d average
HIGH_WINDOW        = 20       # break the prior 20-day high
RS_LOOKBACK        = 252      # 52-week window for the RS proxy (closeness to high)
INITIAL_CAPITAL    = 100_000.0


# ── Exit config — the live thresholds exit_core reads ────────────────────────────
def build_exit_config() -> ec.ExitConfig:
    """The live exit thresholds, as a pure snapshot for ``exit_core``.

    ``exit_rules`` already re-exports the Prove-It / power-hold / trail constants
    (it reads them from the same env vars the live agent does). The four values
    that live on ``execution_agent`` instead — the armed-exit deadline and the
    three scale-out knobs — are read here from the SAME env vars with the SAME
    defaults, so ``.env`` remains the single operational switch, exactly as
    ``config.py`` documents for the backend split. This never re-declares a
    number ``exit_rules`` already owns.
    """
    return ec.ExitConfig(
        armed_exit_deadline_hours=float(os.getenv("ARMED_EXIT_DEADLINE_HOURS", 3.25)),
        stop_loss_pct=er.STOP_LOSS_PCT,
        power_hold_trail_pct=er.POWER_HOLD_TRAIL_PCT,
        scale_out_enabled=os.getenv("SCALE_OUT_ENABLED", "true").lower() == "true",
        scale_out_trigger_pct=float(os.getenv("SCALE_OUT_TRIGGER_PCT", 0.04)),
        scale_out_fraction=float(os.getenv("SCALE_OUT_FRACTION", 0.33)),
        prove_it_p2_floor_pct=er.PROVE_IT_P2_FLOOR_PCT,
    )


# ── Indicators over bardata list-of-dict bars (lowercase keys) ────────────────────
@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    sma_fast: float | None = None
    sma_slow: float | None = None
    ema: float | None = None
    vol_sma: float | None = None
    high_prior: float | None = None   # prior HIGH_WINDOW-day high, EXCLUDING today
    rs_max: float | None = None       # trailing RS_LOOKBACK-day high (incl today)


def _prepare(bars: list[dict]) -> list[Bar]:
    """Attach the indicators the entry scan needs. shift(1) on the 20d high so
    today's break of yesterday's high is legitimate (no look-ahead)."""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    vols = [float(b["volume"]) for b in bars]
    out: list[Bar] = []
    # EMA seed = first close
    ema_prev = closes[0]
    alpha = 2.0 / (EMA_WINDOW + 1.0)
    for i, b in enumerate(bars):
        ema_prev = closes[i] * alpha + ema_prev * (1 - alpha)
        sma_fast = statistics.fmean(closes[i - SMA_FAST + 1:i + 1]) if i >= SMA_FAST - 1 else None
        sma_slow = statistics.fmean(closes[i - SMA_SLOW + 1:i + 1]) if i >= SMA_SLOW - 1 else None
        vol_sma = statistics.fmean(vols[i - VOL_WINDOW + 1:i + 1]) if i >= VOL_WINDOW - 1 else None
        high_prior = max(highs[i - HIGH_WINDOW:i]) if i >= HIGH_WINDOW else None
        rs_max = max(closes[max(0, i - RS_LOOKBACK):i + 1])
        out.append(Bar(
            date=b["date"], open=float(b["open"]), high=highs[i], low=float(b["low"]),
            close=closes[i], volume=vols[i], sma_fast=sma_fast, sma_slow=sma_slow,
            ema=ema_prev, vol_sma=vol_sma, high_prior=high_prior, rs_max=rs_max,
        ))
    return out


# ── Position state — live-shaped so exit_rules/exit_core read it unchanged ────────
def new_position(ticker: str, shares: int, buy_price: float, buy_date: str,
                 alloc: float, cfg: ec.ExitConfig) -> dict:
    """A backtest position carrying every field the live exit path reads.

    ``closed_above_entry`` is the Prove-It phase latch; ``highest_unrealized_pct``
    and ``hwm_price`` drive the trail, scale-out and Phase-2 arming;
    ``stop_loss_pct`` is the position's BASE trail fraction (the live per-position
    ATR base; the backtest has no ATR so it defaults to config STOP_LOSS_PCT and
    the profit ladder tightens off it each day); ``power_hold``/``scaled_out``
    latch once. ``days_held`` is trading days since entry (0 on the entry day,
    matching ``trading_days_between``).
    """
    pos = {
        "ticker": ticker,
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "alloc": alloc,
        "peak_price": buy_price,
        "hwm_price": buy_price,
        "highest_unrealized_pct": 0.0,
        "closed_above_entry": False,
        "scaled_out": False,
        "power_hold": False,
        "stop_loss_pct": cfg.stop_loss_pct,   # resting base trail fraction
        "days_held": 0,
        "exit_armed": False,                  # daily bars resolve arms same-day
    }
    # Place the initial resting hard stop exactly as the live OCA bracket would.
    desired = er.hard_stop_price(pos, buy_price, 0.0, power_held=False, days_held=0)
    pos["hard_stop_price"] = er.safe_hard_stop(desired, buy_price, 0.0)
    return pos


@dataclass
class DayResult:
    """What resolving one position for one day produced."""
    exit_price: float | None = None
    exit_reason: str | None = None
    scale_shares: int = 0
    scale_price: float | None = None


def _ladder_trail_pct(peak_pct: float, base_pct: float) -> float:
    """The trailing-stop fraction the LIVE profit ladder rests at for a given
    peak gain, off the position's base.

    Mirrors the profit lever in ``exit_rules._compute_dynamic_trail_pct``: walk
    the LIVE ``TRAIL_PROFIT_TIERS`` table (highest threshold first); the first
    tier the PEAK gain has crossed sets the fraction, and a ``None`` tier means
    "no change — the base applies". One-way tightening is automatic because the
    peak is monotone. The table itself is the single live source, so a change to
    ``exit_rules.TRAIL_PROFIT_TIERS`` changes this backtest.
    """
    for threshold, pct in er.TRAIL_PROFIT_TIERS:
        if peak_pct >= threshold:
            return pct if pct is not None else base_pct
    return base_pct


def resolve_position_day(pos: dict, bar: Bar, calendar_days: int,
                         cfg: ec.ExitConfig) -> DayResult:
    """Resolve one position against one DAILY bar using the live exit engine.

    The daily sequence mirrors how the live system splits work between the bot's
    poll and the broker's resting orders, and every resting LEVEL is computed by a
    live ``exit_rules`` function (never a re-derived threshold):

      A. Three resting downside LEVELS — computed off the peak/HWM as of the START
         of the day (yesterday's close), so a wide-range day cannot trip its own
         trail — are resolved against the day's LOW:
           • the Prove-It level      — ``er.prove_it_stop_level`` (Phase 1 band /
                                       Phase 2 give-back floor);
           • the profit-ladder trail — ``hwm × (1 − _ladder_trail_pct(peak))``,
                                       the ladder off the live ``TRAIL_PROFIT_TIERS``;
           • the static hard stop     — ``er.hard_stop_price`` + ``er.safe_hard_stop``.
         The HIGHEST level at/above the low fills first; a bar that OPENS through
         it fills at the open (pessimistic). Levels are computed directly rather
         than carried as a re-anchored %, so the Phase 2 floor sits exactly where
         ``exit_rules`` says (it does not drift with the HWM).
      B. If nothing stopped out, today's HIGH is folded into the peak/HWM and
         power-hold latch — for tomorrow's levels and today's close decision.
      C. The CLOSE runs ``exit_core.evaluate_exit`` to latch ``closed_above_entry``
         and take the partial scale-out — the live per-cycle verdict for survivors.

    To lift this to 5-minute fidelity, replace A/B's once-a-day OHLC resolution
    with a loop over intraday bars calling the same functions per bar — the
    decision code does not change.
    """
    buy = float(pos["buy_price"])
    days_held = int(pos["days_held"])
    base_pct = float(pos.get("stop_loss_pct") or cfg.stop_loss_pct)
    o, h, l, c = bar.open, bar.high, bar.low, bar.close

    # ── A. Resting downside LEVELS use the START-OF-DAY peak (NO look-ahead) ──────
    # The trail and Phase-2 arming that protect the position DURING the day were
    # placed off the peak seen through YESTERDAY's close. Folding today's HIGH in
    # before checking today's LOW would let a wide-range day trip its own trail —
    # the price cannot be known to have hit the high before the low within one
    # daily bar. Today's high is folded in at the END, for tomorrow.
    peak_prev = float(pos["highest_unrealized_pct"])
    power_held = bool(pos.get("power_hold")) or er.is_power_hold_active(pos, calendar_days)

    if power_held:
        proveit_level, phase = None, "power-hold"
        ladder_pct = cfg.power_hold_trail_pct
    else:
        proveit_level, phase = er.prove_it_stop_level(pos, buy, days_held, peak_prev)
        ladder_pct = _ladder_trail_pct(peak_prev, base_pct)

    trail_level = float(pos["hwm_price"]) * (1.0 - ladder_pct)
    desired_hard = er.hard_stop_price(pos, buy, peak_prev, power_held, days_held)
    # safe_hard_stop refuses to place a stop at/above the market. A resting order
    # can only fill this bar if it sat below the day's open.
    hard_level = desired_hard if desired_hard < o else 0.0

    downside: list[tuple[float, str]] = []
    if proveit_level is not None and proveit_level > 0:
        label = ("Prove-It Stop (Phase 1 band)" if phase == "phase1"
                 else "Prove-It Stop (Phase 2 give-back floor)")
        downside.append((proveit_level, label))
    if trail_level > 0:
        downside.append((trail_level, f"Trailing stop {ladder_pct * 100:.1f}% from peak"))
    if hard_level > 0:
        downside.append((hard_level, "Hard stop (static)"))

    triggered = [(lvl, rsn) for lvl, rsn in downside if l <= lvl]
    if triggered:
        lvl, rsn = max(triggered, key=lambda x: x[0])   # highest level fills first
        fill = o if o < lvl else lvl                     # gap-through → fill at open
        return DayResult(exit_price=fill, exit_reason=rsn)

    # ── B. Survived the day — NOW fold in today's HIGH (peak/HWM/power-hold) ──────
    high_unreal = (h / buy - 1.0) * 100.0
    peak = max(peak_prev, high_unreal)
    pos["highest_unrealized_pct"] = peak
    pos["hwm_price"] = max(float(pos["hwm_price"]), h)
    pos["peak_price"] = pos["hwm_price"]
    if er.is_power_hold_active(pos, calendar_days):
        pos["power_hold"] = True   # latch, mirrors maybe_arm_power_hold

    # ── C. EOD housekeeping at the CLOSE via the live decision ───────────────────
    if c > buy:
        pos["closed_above_entry"] = True
    ctx = ec.ExitContext(
        current_price=c, days_held=days_held, calendar_days=calendar_days,
        power_hold_armed=bool(pos.get("power_hold")),
    )
    decision = ec.evaluate_exit(pos, ctx, cfg)

    result = DayResult()
    if decision.action == ec.SCALE_OUT and not pos["scaled_out"] and decision.scale_shares >= 1:
        pos["scaled_out"] = True
        pos["shares"] = int(pos["shares"]) - int(decision.scale_shares)
        result.scale_shares = int(decision.scale_shares)
        result.scale_price = c

    pos["days_held"] = days_held + 1   # survived the day; advance the trading-day counter
    return result


# ── The portfolio simulation ─────────────────────────────────────────────────────
def _universe(kind: str) -> list[str]:
    if kind == "all":
        return [s for s in bardata.symbols() if s != MARKET_SYMBOL]
    path = os.path.join(_HERE, f"{kind}_names.txt")
    if not os.path.exists(path):
        raise SystemExit(f"Unknown universe '{kind}' (no {path})")
    names = [ln.strip().upper() for ln in open(path) if ln.strip() and not ln.startswith("#")]
    return [s for s in names if s != MARKET_SYMBOL]


def simulate(tickers: list[str], start: str, end: str,
             initial_capital: float = INITIAL_CAPITAL,
             max_positions: int = MAX_POSITIONS) -> dict:
    cfg = build_exit_config()

    # Warm indicators with a year of lookback, then trim to the window.
    def _load(sym: str) -> list[Bar] | None:
        raw = bardata.daily(sym)
        if not raw or len(raw) < SMA_SLOW + 5:
            return None
        prepared = _prepare(sorted(raw, key=lambda r: r["date"]))
        return [b for b in prepared if start <= b.date <= end] or None

    data: dict[str, dict[str, Bar]] = {}
    ordered: dict[str, list[Bar]] = {}
    for sym in tickers:
        prepared = _load(sym)
        if prepared:
            ordered[sym] = prepared
            data[sym] = {b.date: b for b in prepared}

    market = _load(MARKET_SYMBOL)
    if not market:
        raise SystemExit(f"Market symbol {MARKET_SYMBOL} missing from dataset.")
    market_by_date = {b.date: b for b in market}
    all_dates = sorted(market_by_date)

    cash = float(initial_capital)
    positions: dict[str, dict] = {}
    pending: list[str] = []
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for i, date in enumerate(all_dates):
        # ── A. Fill pending buys at today's OPEN (queued EOD yesterday) ──────────
        still_pending: list[str] = []
        if pending and i > 0:
            for tk in pending:
                if tk in positions or len(positions) >= max_positions:
                    continue
                bar = data.get(tk, {}).get(date)
                if bar is None or bar.open <= 0 or cash <= 0:
                    if bar is not None:
                        still_pending.append(tk)
                    continue
                remaining = max(1, max_positions - len(positions))
                held_value = sum(
                    p["shares"] * (data[t][date].close if (t in data and date in data[t]) else p["buy_price"])
                    for t, p in positions.items()
                )
                equity_now = cash + held_value
                alloc = min(cash / remaining, equity_now / max_positions)
                shares = int(alloc // bar.open)
                if shares <= 0:
                    continue
                cash -= shares * bar.open
                positions[tk] = new_position(tk, shares, bar.open, date, alloc, cfg)
        pending = still_pending

        # ── B. Resolve exits for every open position on today's bar ──────────────
        to_close: list[str] = []
        for tk, pos in positions.items():
            bar = data.get(tk, {}).get(date)
            if bar is None:
                continue
            buy_dt = datetime.strptime(pos["buy_date"], "%Y-%m-%d")
            cur_dt = datetime.strptime(date, "%Y-%m-%d")
            calendar_days = (cur_dt - buy_dt).days
            res = resolve_position_day(pos, bar, calendar_days, cfg)

            if res.scale_shares:
                cash += res.scale_shares * res.scale_price
                trades.append(_trade_record(pos, res.scale_price, date, calendar_days,
                                             "Partial scale-out (+trigger)", res.scale_shares,
                                             partial=True))
            if res.exit_price is not None:
                cash += pos["shares"] * res.exit_price
                trades.append(_trade_record(pos, res.exit_price, date, calendar_days,
                                             res.exit_reason, pos["shares"]))
                to_close.append(tk)
        for tk in to_close:
            positions.pop(tk)

        # ── C. Market filter — SPY above its EMA-21 ──────────────────────────────
        mkt = market_by_date[date]
        market_bullish = mkt.ema is None or mkt.close > mkt.ema

        # ── D. Breakout scan → queue for next-day open ───────────────────────────
        if market_bullish and (len(positions) + len(pending)) < max_positions:
            candidates: list[tuple[str, float]] = []
            for tk in ordered:
                if tk in positions or tk in pending:
                    continue
                bar = data.get(tk, {}).get(date)
                if bar is None:
                    continue
                if None in (bar.sma_fast, bar.sma_slow, bar.vol_sma, bar.high_prior):
                    continue
                is_breakout = bar.high > bar.high_prior
                is_above_ma = bar.close > bar.sma_fast and bar.close > bar.sma_slow
                is_high_vol = bar.volume > bar.vol_sma * VOL_SURGE
                if is_breakout and is_above_ma and is_high_vol:
                    dist = (bar.rs_max - bar.close) / bar.rs_max if bar.rs_max else 1.0
                    candidates.append((tk, dist))
            candidates.sort(key=lambda x: x[1])   # closest to 52w high first
            open_slots = max_positions - len(positions) - len(pending)
            for tk, _ in candidates[:open_slots]:
                if cash > 0:
                    pending.append(tk)

        # ── E. Record equity ─────────────────────────────────────────────────────
        equity = cash + sum(
            p["shares"] * (data[t][date].close if (t in data and date in data[t]) else p["buy_price"])
            for t, p in positions.items()
        )
        equity_curve.append({"date": date, "equity": round(equity, 2)})

    return _summarize(trades, equity_curve, initial_capital, all_dates, max_positions)


def _trade_record(pos: dict, sell_price: float, sell_date: str, hold_days: int,
                  reason: str, shares: int, partial: bool = False) -> dict:
    buy = float(pos["buy_price"])
    pnl = (sell_price - buy) * shares
    return {
        "ticker": pos["ticker"],
        "shares": shares,
        "buy_price": round(buy, 4),
        "buy_date": pos["buy_date"],
        "sell_price": round(sell_price, 4),
        "sell_date": sell_date,
        "profit_loss": round(pnl, 2),
        "percent_return": round((sell_price / buy - 1.0) * 100.0, 2),
        "hold_days": hold_days,
        "exit_reason": reason,
        "partial": partial,
    }


def _summarize(trades: list[dict], equity_curve: list[dict],
               initial_capital: float, all_dates: list[str], max_positions: int) -> dict:
    closed = [t for t in trades if not t["partial"]]
    n = len(closed)
    wins = [t for t in closed if t["profit_loss"] > 0]
    total_pnl = round(sum(t["profit_loss"] for t in trades), 2)
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    years = max(1e-9, (datetime.strptime(all_dates[-1], "%Y-%m-%d")
                       - datetime.strptime(all_dates[0], "%Y-%m-%d")).days / 365.25)
    cagr = ((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0 if initial_capital > 0 else 0.0

    reasons: dict[str, int] = {}
    for t in trades:
        key = _reason_bucket(t["exit_reason"])
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "summary": {
            "closed_trades": n,
            "win_rate": round(100.0 * len(wins) / n, 1) if n else 0.0,
            "avg_return_pct": round(statistics.fmean(t["percent_return"] for t in closed), 2) if n else 0.0,
            "total_pnl": total_pnl,
            "final_equity": round(final_equity, 2),
            "cagr_pct": round(cagr, 2),
            "max_positions": max_positions,
            "window": [all_dates[0], all_dates[-1]] if all_dates else [],
        },
        "exit_reason_counts": reasons,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def _reason_bucket(reason: str | None) -> str:
    r = reason or ""
    if "Phase 1" in r:
        return "prove_it_phase1"
    if "Phase 2" in r:
        return "prove_it_phase2"
    if "Trailing stop" in r:
        return "trailing_ladder"
    if "Hard stop" in r:
        return "hard_stop"
    if "scale-out" in r:
        return "scale_out"
    return "other"


# ── CLI ───────────────────────────────────────────────────────────────────────────
def _print_report(res: dict) -> None:
    s = res["summary"]
    print("=" * 68)
    print("STRATEGY BACKTEST — exits driven by the LIVE exit_core / exit_rules")
    print("Daily bars (offline benchmark_data). RULE parity, not fill-price fidelity.")
    print("=" * 68)
    print(f"  window          {s['window'][0]} .. {s['window'][1]}" if s["window"] else "  (no data)")
    print(f"  slots           {s['max_positions']}")
    print(f"  closed trades   {s['closed_trades']}")
    print(f"  win rate        {s['win_rate']}%")
    print(f"  avg return      {s['avg_return_pct']}%")
    print(f"  total P&L       ${s['total_pnl']:,.2f}")
    print(f"  final equity    ${s['final_equity']:,.2f}")
    print(f"  CAGR            {s['cagr_pct']}%")
    print("-" * 68)
    print("  exit-reason counts (proves the LIVE rules fired, not the retired ones):")
    for k, v in sorted(res["exit_reason_counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:22s} {v}")
    print("=" * 68)
    print("  NOTE: absolute P&L is approximate — daily bars cannot model the 0.6%")
    print("  arm-trail bounce or 15-minute timing. Trust RELATIVE comparisons; a")
    print("  precise +EV verdict needs 5-minute bars (register: intraday-fmp-exit-fidelity).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else "")
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2026-08-04")
    ap.add_argument("--universe", default="all",
                    help="'all', or a name matching research/<name>_names.txt (e.g. 'pass')")
    ap.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    ap.add_argument("--slots", type=int, default=MAX_POSITIONS)
    ap.add_argument("--json", default=None, help="write full result to this path")
    args = ap.parse_args(argv)

    tickers = _universe(args.universe)
    res = simulate(tickers, args.start, args.end,
                   initial_capital=args.capital, max_positions=args.slots)
    _print_report(res)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2)
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
