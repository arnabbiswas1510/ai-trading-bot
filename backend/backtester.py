"""
backend/backtester.py

Runs a historical simulation of the CAN SLIM breakout trading strategy.

Key design decisions (matching live execution_agent.py behaviour):
  - Entry:  Breakout detected on day T using EOD data; buy at day T+1 OPEN
            (no look-ahead bias — screener runs after close, bot buys next morning)
  - Stops:  Trailing stop from peak price (rises with winners, never drops)
  - Size:   Fixed $POSITION_SIZE block per trade, capped at available cash
  - Exit:   Trailing stop fires OR close < EMA-21 × 0.99 (no fixed profit target)
  - Market: Bullish when SPY close > SPY EMA-21 (matches live market filter)
  - Slots:  4 concurrent positions (matches live MAX_POSITIONS=4)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from fmp_client import FMPClient

# ── Constants matching execution_agent.py ─────────────────────────────────────
DEFAULT_MAX_POSITIONS = 4        # live bot uses 4 (was 5 — bug fix)
DEFAULT_POSITION_SIZE = 20_000   # fixed $ block per position
DEFAULT_STOP_LOSS_PCT = 7.0      # trailing stop % from peak price
DEFAULT_EMA_WINDOW    = 21       # EMA for market direction + exit signal
DEFAULT_EXIT_BUFFER   = 0.01     # exit when close < EMA × (1 - buffer)
MIN_VOLUME_MULTIPLIER = 1.4      # breakout volume must be ≥ 1.4× 50d avg


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

def run_backtest(
    tickers: list[str],
    start_date_str: str,
    end_date_str: str,
    initial_capital: float = 100_000.0,
    stop_loss_pct: float   = DEFAULT_STOP_LOSS_PCT,
    max_positions: int     = DEFAULT_MAX_POSITIONS,
    position_size: float   = DEFAULT_POSITION_SIZE,
    # kept for API backward-compat; ignored — live bot has no fixed profit target
    profit_target_pct: float = 25.0,
) -> dict:
    """
    Historical simulation of the CAN SLIM breakout strategy.

    Parameters
    ----------
    tickers           Tickers to scan for breakout entries
    start_date_str    Backtest window start  (YYYY-MM-DD)
    end_date_str      Backtest window end    (YYYY-MM-DD)
    initial_capital   Starting cash          (default $100,000)
    stop_loss_pct     Trailing stop %        (default 7.0)
    max_positions     Max concurrent positions (default 4)
    position_size     Fixed $ per position   (default $20,000)
    profit_target_pct Legacy — accepted but unused (live bot removed fixed targets)

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
                df[ema_col]   = _ema(df["Close"], DEFAULT_EMA_WINDOW)
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
    pending:   list[str]       = []   # tickers to buy at tomorrow's open
    trades:    list[dict]      = []
    equity_history: list[dict] = []

    stop_trail_factor = 1.0 - stop_loss_pct / 100.0

    for i, current_date in enumerate(all_dates):
        date_str = current_date.strftime("%Y-%m-%d")

        # ── A. Execute pending buys at today's OPEN (no look-ahead) ──────────
        # Buys are queued at EOD on day T and filled at day T+1 open.
        still_pending = []
        if pending and i > 0:
            for ticker in pending:
                if ticker in positions or len(positions) >= max_positions:
                    continue
                if ticker not in data or current_date not in data[ticker].index:
                    continue
                row        = data[ticker].loc[current_date]
                open_price = float(row.get("Open", row["Close"]))
                if open_price <= 0 or cash < min(position_size, 500):
                    still_pending.append(ticker)   # retry tomorrow if no cash
                    continue
                alloc  = min(position_size, cash)
                shares = int(alloc // open_price)
                if shares <= 0:
                    continue
                cost = shares * open_price
                cash -= cost
                positions[ticker] = {
                    "shares":     shares,
                    "buy_price":  open_price,
                    "peak_price": open_price,   # trailing stop tracks this
                    "buy_date":   date_str,
                }
        pending = still_pending   # keep any that couldn't fill (no cash)

        # ── B. Update trailing stops & check exits ────────────────────────────
        tickers_to_close: list[str] = []
        for ticker, pos in positions.items():
            if ticker not in data or current_date not in data[ticker].index:
                continue
            row   = data[ticker].loc[current_date]
            high  = float(row["High"])
            low   = float(row["Low"])
            close = float(row["Close"])
            ema21 = float(row.get(ema_col, float("nan")))

            # Advance peak price (trailing stop rises, never falls)
            if high > pos["peak_price"]:
                pos["peak_price"] = high

            stop_level  = pos["peak_price"] * stop_trail_factor
            exit_price  = None
            exit_reason = None

            # Priority 1 — Trailing stop
            if low <= stop_level:
                exit_price  = max(stop_level, low)   # realistic: can gap through
                exit_reason = f"Trailing Stop ({stop_loss_pct:.0f}% from peak ${pos['peak_price']:.2f})"

            # Priority 2 — EMA-21 exit with 1% buffer (EOD candle)
            elif not pd.isna(ema21) and close < ema21 * (1.0 - DEFAULT_EXIT_BUFFER):
                exit_price  = close
                exit_reason = (
                    f"EMA-{DEFAULT_EMA_WINDOW} Exit "
                    f"(close ${close:.2f} < MA ${ema21:.2f} × {1-DEFAULT_EXIT_BUFFER:.2f})"
                )

            if exit_price is not None:
                pnl       = (exit_price - pos["buy_price"]) * pos["shares"]
                pct       = (exit_price / pos["buy_price"] - 1.0) * 100.0
                buy_dt    = datetime.strptime(pos["buy_date"], "%Y-%m-%d")
                hold_days = (current_date - buy_dt).days
                trades.append({
                    "ticker":         ticker,
                    "shares":         pos["shares"],
                    "buy_price":      round(pos["buy_price"], 2),
                    "buy_date":       pos["buy_date"],
                    "sell_price":     round(exit_price, 2),
                    "sell_date":      date_str,
                    "profit_loss":    round(pnl, 2),
                    "percent_return": round(pct, 2),
                    "hold_days":      hold_days,
                    "exit_reason":    exit_reason,
                })
                cash += pos["shares"] * exit_price
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
                if cash >= position_size * 0.5:   # only queue if we can likely fill
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

    gross_profit  = sum(t["profit_loss"] for t in wins)
    gross_loss_abs = abs(sum(t["profit_loss"] for t in losses))
    profit_factor = round(gross_profit / gross_loss_abs, 2) if gross_loss_abs > 0 else 0.0

    # Expectancy per trade in $
    expectancy = round(
        (win_rate / 100.0 * avg_win_pct / 100.0 * position_size)
        + ((1.0 - win_rate / 100.0) * avg_loss_pct / 100.0 * position_size),
        2,
    )

    wl_ratio     = round(avg_win_pct / abs(avg_loss_pct), 2) if avg_loss_pct != 0 else 0.0
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
        exit_reasons[t["exit_reason"].split(" (")[0]] = \
            exit_reasons.get(t["exit_reason"].split(" (")[0], 0) + 1

    return {
        "summary": {
            # ── Return ──────────────────────────────────────────────────────
            "initial_capital":   round(initial_capital, 2),
            "final_equity":      round(final_equity, 2),
            "total_return_pct":  round(total_return_pct, 2),
            "cagr_pct":          round(cagr, 2),
            # ── Risk ─────────────────────────────────────────────────────────
            "max_drawdown_pct":  round(max_dd_pct, 2),
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
