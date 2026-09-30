"""
backend/backtester.py

Runs a historical simulation of the CAN SLIM breakout trading strategy.

Daily simulation assumptions (NOT equivalent to live execution):
  - Entry:  Breakout detected on day T using EOD data; buy at day T+1 OPEN
            (no look-ahead bias — screener runs after close, bot buys next morning)
  - Exit:   The LIVE exit engine — Prove-It Stop (Phase 1 band / Phase 2 give-back
            floor), the dynamic profit-ladder trail, power-hold widening and the
            partial scale-out — resolved once per daily bar by
            daily_exit_sim.resolve_position_day (shared with
            research/strategy_backtest.py; it calls exit_core/exit_rules directly).
            There is NO fixed trailing-stop % or EMA-21 exit here any more; those
            retired rules were removed in Option A (see docs/retired_code.md and
            decisions/2026-09-29_backtester-option-a-live-exits.md).
  - Size:   min(available_cash / remaining_slots, equity / MAX_POSITIONS)  (equal-weight cap — matches live bot)
  - Market: A coarse index EMA-21 entry filter, not the live SPY SMA-200 gate.
  - Slots:  MAX_POSITIONS concurrent positions, read from the same env var
            the live bot uses (default 5) so a backtest cannot silently
            simulate a different portfolio shape than production runs

  FIDELITY: shared primitives do not establish execution parity. Daily ordering,
  armed exits and broker trail anchors can change even relative profit rankings.
  See docs/backtesting.md and research/live_rule_replay.py for recorded inputs.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from fmp_client import FMPClient

# The daily-bar EXIT engine is the LIVE code, shared with research/strategy_backtest.py
# via the root module daily_exit_sim (which drives exit_core/exit_rules). Option A
# (decisions/2026-09-29_backtester-option-a-live-exits.md) brought config.py,
# exit_rules.py, exit_core.py and daily_exit_sim.py into THIS image (see the Dockerfile
# COPY line + tests/test_web_image_completeness.py). Shared thresholds replace the
# retired 7%-trail + EMA-21 rules; daily execution is still approximate.
from daily_exit_sim import build_exit_config, new_position, resolve_position_day, DayBar
from trade_costs import CostModel, build_cost_model

# ── Constants matching execution_agent.py ─────────────────────────────────────
# Mirrors config.MAX_POSITIONS. backend/ ships as its own image; the env var is
# read directly so .env stays the single operational switch. Keep the default in
# sync with config.py: a backtest run against a different slot count than live
# silently answers a question nobody asked.
DEFAULT_MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 5))
DEFAULT_EMA_WINDOW    = 21       # EMA-21 for the SPY market-direction ENTRY filter
MIN_VOLUME_MULTIPLIER = 1.4      # breakout volume must be ≥ 1.4× 50d avg
# No fixed position size — sizing: min(cash / remaining_slots, equity / MAX_POSITIONS)
# Exits are the LIVE Prove-It Stop + dynamic trail ladder + scale-out via
# daily_exit_sim.resolve_position_day — there is no fixed trailing-stop % or EMA exit.


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, window: int) -> pd.Series:
    """Exponential moving average (matches pandas ewm default, adjust=False)."""
    return series.ewm(span=window, adjust=False).mean()


def _cagr(start_val: float, end_val: float, n_years: float) -> float:
    if start_val <= 0 or n_years <= 0:
        return 0.0
    return ((end_val / start_val) ** (1.0 / n_years) - 1.0) * 100.0


def _max_consecutive_losses(trades: list[dict]) -> int:
    best = current = 0
    for t in trades:
        if t["profit_loss"] <= 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _max_underwater_days(equity_series: pd.Series) -> int:
    rolling_max = equity_series.cummax()
    underwater   = (equity_series < rolling_max)
    best = current = 0
    for val in underwater:
        if val:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


# ── Main simulation ───────────────────────────────────────────────────────────

def _make_trade(ticker: str, pos: dict, shares: int, sell_price: float,
                sell_date: str, hold_days: int, exit_reason: str,
                costs: CostModel | None = None, partial: bool = False) -> dict:
    """One closed (or partially closed) trade record, in the shape the API/UI
    and the summary metrics expect. ``shares`` is the quantity SOLD in this event
    (the whole position on a full exit, the scaled fraction on a partial).

    ``profit_loss`` stays GROSS (quote-to-quote) so it remains comparable with the
    exit-review harness and the decisions/ thresholds, all of which are gross.
    ``commission``/``slippage_cost``/``net_profit_loss`` expose the cost drag; the
    equity curve is charged the same costs so final_equity/CAGR are NET."""
    buy_price = float(pos["buy_price"])
    pnl = (sell_price - buy_price) * shares
    pct = (sell_price / buy_price - 1.0) * 100.0
    commission = 0.0
    slippage_cost = 0.0
    if costs is not None:
        sell_fill = costs.sell_fill(sell_price)
        commission = costs.commission(shares, sell_fill)
        slippage_cost = (sell_price - sell_fill) * shares
        if not partial:   # a full exit also carries the whole position's buy costs
            commission += float(pos.get("buy_commission", 0.0))
            slippage_cost += float(pos.get("buy_slippage", 0.0))
    net = pnl - commission - slippage_cost
    return {
        "ticker":         ticker,
        "shares":         shares,
        "buy_price":      round(buy_price, 2),
        "buy_date":       pos["buy_date"],
        "sell_price":     round(sell_price, 2),
        "sell_date":      sell_date,
        "profit_loss":    round(pnl, 2),
        "commission":     round(commission, 2),
        "slippage_cost":  round(slippage_cost, 2),
        "net_profit_loss": round(net, 2),
        "percent_return": round(pct, 2),
        "hold_days":      hold_days,
        "exit_reason":    exit_reason,
        "alloc":          round(pos.get("alloc", 0), 2),
    }


def run_backtest(
    tickers: list[str],
    start_date_str: str,
    end_date_str: str,
    initial_capital: float = 100_000.0,
    # Legacy — accepted for API/UI backward-compat but IGNORED for exits. The live
    # exit engine uses config STOP_LOSS_PCT as the trail BASE (via daily_exit_sim),
    # not a caller-supplied fixed trailing stop.
    stop_loss_pct: float   = 7.0,
    max_positions: int     = DEFAULT_MAX_POSITIONS,
    # kept for API backward-compat; ignored — live bot has no fixed profit target
    profit_target_pct: float = 25.0,
    costs: CostModel | None = None,
) -> dict:
    """
    Historical simulation of the CAN SLIM breakout strategy.

    Position sizing matches live execution_agent.py:
        remaining_slots = max(1, MAX_POSITIONS - len(open_positions))
        position_size   = min(available_cash / remaining_slots,
                              equity / MAX_POSITIONS)   # equal-weight ceiling
    There is NO fixed dollar block — sizing is proportional to remaining cash,
    capped at one equal-weight share of total equity (see
    decisions/2026-09-21_equity-capped-position-size.md).

    Parameters
    ----------
    tickers           Tickers to scan for breakout entries
    start_date_str    Backtest window start  (YYYY-MM-DD)
    end_date_str      Backtest window end    (YYYY-MM-DD)
    initial_capital   Starting cash          (default $100,000)
    stop_loss_pct     Legacy — accepted but IGNORED for exits (the live exit
                      engine uses config STOP_LOSS_PCT as the trail base)
    max_positions     Max concurrent positions (default 5)
    profit_target_pct Legacy — accepted but unused (live bot has no fixed target)

    Returns
    -------
    dict with keys: summary, trades, equity_curve
    """
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt   = datetime.strptime(end_date_str,   "%Y-%m-%d")

    fmp = FMPClient()
    if not fmp.is_configured():
        raise ValueError("FMP API Key is not configured. Go to settings to set it.")

    # ── Download S&P 500 (market direction + benchmark) ───────────────────────
    sp500_df = fmp.get_historical_prices("^GSPC", start_date_str, end_date_str)
    if sp500_df.empty:
        raise ValueError("Could not download index data (^GSPC) from FMP.")
    ema_col = f"EMA{DEFAULT_EMA_WINDOW}"
    sp500_df[ema_col] = _ema(sp500_df["Close"], DEFAULT_EMA_WINDOW)

    # ── Download and prepare ticker data ─────────────────────────────────────
    data: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            # Extra 365-day lookback so SMAs/EMAs are warm from the start date
            dl_start = (start_dt - timedelta(days=365)).strftime("%Y-%m-%d")
            df = fmp.get_historical_prices(t, dl_start, end_date_str)
            if not df.empty:
                df["SMA50"]   = df["Close"].rolling(50).mean()
                df["SMA200"]  = df["Close"].rolling(200).mean()
                df["VolSMA50"] = df["Volume"].rolling(50).mean()
                # shift(1) so today's high can legitimately break yesterday's 20d high
                df["High20"]  = df["High"].rolling(20).max().shift(1)
                # Trim to backtest window AFTER indicator calculation
                df = df.loc[start_date_str:end_date_str]
                data[t] = df
        except Exception as e:
            print(f"Backtest: error fetching {t}: {e}")

    all_dates = sorted(sp500_df.index.tolist())

    # ── Portfolio state ───────────────────────────────────────────────────────
    cash: float = initial_capital
    positions: dict[str, dict] = {}   # ticker → position record
    last_marks: dict[str, float] = {}
    pending:   list[str]       = []   # tickers to buy at tomorrow's open
    trades:    list[dict]      = []
    equity_history: list[dict] = []

    # The live exit thresholds, snapshot once for the shared exit engine.
    exit_cfg = build_exit_config()
    # Commission + slippage model (roadmap item #3). Applied to CASH only, never to
    # the per-trade GROSS P&L and never to the exit math — see trade_costs.py.
    costs = costs if costs is not None else build_cost_model()
    total_commission = 0.0   # $ paid in commissions across all fills
    total_slippage   = 0.0   # $ lost to adverse slippage across all fills

    for i, current_date in enumerate(all_dates):
        date_str = current_date.strftime("%Y-%m-%d")

        # ── A. Execute pending buys at today's OPEN (no look-ahead) ──────────
        # Buys queued at EOD on day T; filled at day T+1 open.
        # Sizing mirrors live bot: cash / remaining_slots, recomputed per buy.
        still_pending = []
        if pending and i > 0:
            for ticker in pending:
                if ticker in positions or len(positions) >= max_positions:
                    continue
                if ticker not in data or current_date not in data[ticker].index:
                    continue
                row        = data[ticker].loc[current_date]
                open_price = float(row.get("Open", float("nan")))
                if not np.isfinite(open_price) or open_price <= 0 or cash <= 0:
                    still_pending.append(ticker)
                    continue
                # Proportional: spread cash equally across unfilled slots, but
                # never exceed one equal-weight share of total equity — mirrors
                # the live per-position ceiling added 2026-09-21
                # (decisions/2026-09-21_equity-capped-position-size.md).
                remaining_slots = max(1, max_positions - len(positions))
                held_value = 0.0
                for _t, _p in positions.items():
                    mark = float("nan")
                    if _t in data and current_date in data[_t].index:
                        mark = float(data[_t].loc[current_date].get("Open", float("nan")))
                    if not np.isfinite(mark) or mark <= 0:
                        mark = last_marks.get(_t, _p["buy_price"])
                    held_value += _p["shares"] * mark
                equity_now = cash + held_value
                alloc = min(cash / remaining_slots, equity_now / max_positions)
                # Costs charged to CASH only; the position's buy_price stays the
                # unslipped OPEN quote so the exit math is identical with or without
                # costs. Size on the slipped fill so a buy never overspends alloc.
                buy_fill = costs.buy_fill(open_price)
                shares = int(alloc // buy_fill)
                if shares <= 0:
                    continue
                commission = costs.commission(shares, buy_fill)
                cash -= shares * buy_fill + commission
                total_commission += commission
                total_slippage += (buy_fill - open_price) * shares
                # Live-shaped position carrying every field the exit engine reads.
                positions[ticker] = new_position(
                    ticker, shares, open_price, date_str, alloc, exit_cfg)
                positions[ticker]["buy_commission"] = commission
                positions[ticker]["buy_slippage"] = (buy_fill - open_price) * shares
        pending = still_pending   # keep any that couldn't fill

        # ── B. Resolve exits via the LIVE engine (shared daily_exit_sim) ──────
        # resolve_position_day computes the Prove-It level, the dynamic trail
        # ladder and the static hard stop from live exit_rules functions each day,
        # folds today's high into the peak AFTER the low is resolved (no
        # look-ahead), and takes the partial scale-out at the close — byte-for-byte
        # the production exit decisions.
        tickers_to_close: list[str] = []
        for ticker, pos in positions.items():
            if ticker not in data or current_date not in data[ticker].index:
                continue
            row = data[ticker].loc[current_date]
            bar = DayBar(
                open=float(row.get("Open", row["Close"])),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
            )
            buy_dt        = datetime.strptime(pos["buy_date"], "%Y-%m-%d")
            calendar_days = (current_date - buy_dt).days
            res           = resolve_position_day(pos, bar, calendar_days, exit_cfg)

            # Partial scale-out: bank the sold fraction, keep the position open.
            if res.scale_shares and res.scale_price is not None:
                trades.append(_make_trade(
                    ticker, pos, res.scale_shares, res.scale_price,
                    date_str, calendar_days, "Partial scale-out (+trigger)",
                    costs=costs, partial=True))
                sell_fill = costs.sell_fill(res.scale_price)
                commission = costs.commission(res.scale_shares, sell_fill)
                cash += res.scale_shares * sell_fill - commission
                total_commission += commission
                total_slippage += (res.scale_price - sell_fill) * res.scale_shares

            # Full exit: close the remaining position.
            if res.exit_price is not None:
                trades.append(_make_trade(
                    ticker, pos, pos["shares"], res.exit_price,
                    date_str, calendar_days, res.exit_reason, costs=costs))
                sell_fill = costs.sell_fill(res.exit_price)
                commission = costs.commission(pos["shares"], sell_fill)
                cash += pos["shares"] * sell_fill - commission
                total_commission += commission
                total_slippage += (res.exit_price - sell_fill) * pos["shares"]
                tickers_to_close.append(ticker)

        for t in tickers_to_close:
            positions.pop(t)

        # ── C. Market direction filter — EMA-21 on SPY ───────────────────────
        market_bullish = True
        if current_date in sp500_df.index:
            sp_row    = sp500_df.loc[current_date]
            sp_close  = float(sp_row["Close"])
            sp_ema    = float(sp_row.get(ema_col, float("nan")))
            if not pd.isna(sp_ema):
                market_bullish = sp_close > sp_ema

        # ── D. Scan for breakout setups → queue for next-day open ─────────────
        if market_bullish and len(positions) + len(pending) < max_positions:
            candidates: list[tuple[str, float]] = []
            for ticker in tickers:
                if ticker in positions or ticker in pending:
                    continue
                if ticker not in data or current_date not in data[ticker].index:
                    continue
                df  = data[ticker]
                idx = df.index.get_loc(current_date)
                if idx < 1:
                    continue
                row      = df.iloc[idx]
                close    = float(row["Close"])
                high     = float(row["High"])
                vol      = float(row["Volume"])
                sma50    = float(row["SMA50"])
                sma200   = float(row["SMA200"])
                vol_sma  = float(row["VolSMA50"])
                high20   = float(row["High20"])

                if any(pd.isna(v) for v in [sma50, sma200, vol_sma, high20]):
                    continue

                is_breakout    = high > high20                          # breaks 20d high
                is_above_ma    = close > sma50 and close > sma200       # above both MAs
                is_high_volume = vol > vol_sma * MIN_VOLUME_MULTIPLIER   # strong volume

                if is_breakout and is_above_ma and is_high_volume:
                    # Relative strength proxy: closeness to 52w high
                    max_52w = df["Close"].iloc[max(0, idx - 252): idx + 1].max()
                    dist    = (max_52w - close) / max_52w if max_52w > 0 else 1.0
                    candidates.append((ticker, dist))

            # Best RS first (closest to 52w high)
            candidates.sort(key=lambda x: x[1])
            open_slots = max_positions - len(positions) - len(pending)
            for ticker, _ in candidates[:open_slots]:
                if cash > 0:   # queue as long as any cash remains
                    pending.append(ticker)

        # ── E. Record daily equity value ─────────────────────────────────────
        current_equity = cash
        for ticker, pos in positions.items():
            if ticker in data and current_date in data[ticker].index:
                current_equity += pos["shares"] * float(data[ticker].loc[current_date]["Close"])
            else:
                current_equity += pos["shares"] * pos["buy_price"]

        equity_history.append({
            "date":   date_str,
            "equity": round(current_equity, 2),
            "cash":   round(cash, 2),
        })
        for ticker, frame in data.items():
            if current_date in frame.index:
                mark = float(frame.loc[current_date]["Close"])
                if np.isfinite(mark) and mark > 0:
                    last_marks[ticker] = mark

    # ── Compute summary metrics ───────────────────────────────────────────────
    final_equity     = equity_history[-1]["equity"] if equity_history else initial_capital
    total_return_pct = (final_equity / initial_capital - 1.0) * 100.0

    n_days  = max((end_dt - start_dt).days, 1)
    n_years = n_days / 365.25
    cagr    = _cagr(initial_capital, final_equity, n_years)

    # Daily return series
    eq_series     = pd.Series([h["equity"] for h in equity_history])
    daily_rets    = eq_series.pct_change().dropna()
    daily_std     = float(daily_rets.std()) if len(daily_rets) > 1 else 0.0
    daily_mean    = float(daily_rets.mean()) if len(daily_rets) > 1 else 0.0

    # Sharpe (annualised, risk-free ≈ 0)
    sharpe = round(daily_mean / daily_std * (252 ** 0.5), 2) if daily_std > 0 else 0.0

    # Sortino (downside deviation)
    downside_rets = daily_rets[daily_rets < 0]
    down_std      = float(downside_rets.std()) if len(downside_rets) > 1 else 0.0
    sortino       = round(daily_mean / down_std * (252 ** 0.5), 2) if down_std > 0 else 0.0

    # Max Drawdown
    rolling_max   = eq_series.cummax()
    dd_series     = (eq_series - rolling_max) / rolling_max
    max_dd_pct    = float(dd_series.min() * 100.0)

    # Calmar ratio
    calmar = round(cagr / abs(max_dd_pct), 2) if max_dd_pct != 0 else 0.0

    # Underwater period
    underwater_days = _max_underwater_days(eq_series)

    # Trade-level stats
    wins   = [t for t in trades if t["profit_loss"] > 0]
    losses = [t for t in trades if t["profit_loss"] <= 0]
    n_total = len(trades)
    win_rate = len(wins) / n_total * 100.0 if n_total else 0.0

    avg_win_pct  = float(np.mean([t["percent_return"] for t in wins]))   if wins   else 0.0
    avg_loss_pct = float(np.mean([t["percent_return"] for t in losses]))  if losses else 0.0

    gross_profit   = sum(t["profit_loss"] for t in wins)
    gross_loss_abs = abs(sum(t["profit_loss"] for t in losses))
    profit_factor  = round(gross_profit / gross_loss_abs, 2) if gross_loss_abs > 0 else 0.0

    # Expectancy per trade in $ — uses actual per-trade allocation stored at buy time
    if trades:
        avg_alloc  = float(np.mean([t.get("alloc", initial_capital / max_positions) for t in trades]))
    else:
        avg_alloc  = initial_capital / max_positions
    expectancy = round(
        (win_rate / 100.0 * avg_win_pct / 100.0 * avg_alloc)
        + ((1.0 - win_rate / 100.0) * avg_loss_pct / 100.0 * avg_alloc),
        2,
    )

    wl_ratio      = round(avg_win_pct / abs(avg_loss_pct), 2) if avg_loss_pct != 0 else 0.0
    avg_hold_days = round(float(np.mean([t["hold_days"] for t in trades])), 1) if trades else 0.0
    max_consec_losses = _max_consecutive_losses(trades)

    # Benchmark
    sp_start      = float(sp500_df["Close"].iloc[0])
    sp_end        = float(sp500_df["Close"].iloc[-1])
    sp_return_pct = (sp_end / sp_start - 1.0) * 100.0
    sp_cagr       = _cagr(sp_start, sp_end, n_years)
    alpha         = round(cagr - sp_cagr, 2)

    # Exit reason breakdown
    exit_reasons: dict[str, int] = {}
    for t in trades:
        key = t["exit_reason"].split(" (")[0]
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    return {
        "summary": {
            # ── Return ──────────────────────────────────────────────────────
            "initial_capital":   round(initial_capital, 2),
            "final_equity":      round(final_equity, 2),
            "total_return_pct":  round(total_return_pct, 2),
            "cagr_pct":          round(cagr, 2),
            # ── Trading costs (roadmap item #3) ──────────────────────────────
            # P&L above/below is GROSS; final_equity/CAGR are NET (cash charged).
            "total_commission":     round(total_commission, 2),
            "total_slippage":       round(total_slippage, 2),
            "total_trading_costs":  round(total_commission + total_slippage, 2),
            "gross_pnl":            round(sum(t["profit_loss"] for t in trades), 2),
            "net_pnl":              round(sum(t.get("net_profit_loss", t["profit_loss"]) for t in trades), 2),
            # ── Risk ─────────────────────────────────────────────────────────
            "max_drawdown":      round(max_dd_pct, 2),
            "underwater_days":   underwater_days,
            "sharpe_ratio":      sharpe,
            "sortino_ratio":     sortino,
            "calmar_ratio":      calmar,
            # ── Trade quality ────────────────────────────────────────────────
            "total_trades":      n_total,
            "winning_trades":    len(wins),
            "losing_trades":     len(losses),
            "win_rate":          round(win_rate, 2),
            "avg_win_pct":       round(avg_win_pct, 2),
            "avg_loss_pct":      round(avg_loss_pct, 2),
            "wl_ratio":          wl_ratio,
            "profit_factor":     profit_factor,
            "expectancy_usd":    expectancy,
            "avg_hold_days":     avg_hold_days,
            "max_consec_losses": max_consec_losses,
            "exit_reasons":      exit_reasons,
            # ── Benchmark ───────────────────────────────────────────────────
            "sp500_return_pct":  round(sp_return_pct, 2),
            "sp500_cagr_pct":    round(sp_cagr, 2),
            "alpha_pct":         alpha,
        },
        "trades":       trades,
        "equity_curve": equity_history,
    }
