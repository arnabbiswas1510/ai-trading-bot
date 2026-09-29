"""
trade_costs.py — the commission + slippage model the backtesters apply to fills.

WHY THIS MODULE EXISTS
----------------------
Neither backtester modelled trading costs, so every profitability figure was an
UPPER BOUND: it banked the full quote-to-quote move and paid no commission and no
slippage. When the measured per-trade edge is a fraction of a percent (the exit
review's 52-trade sample averages −0.16%/trade), an unmodelled ~0.65¢/share
commission and a few basis points of slippage can be the entire difference
between +EV and −EV. Backtest-fidelity roadmap item #3.

DOCTRINE (matches backend/commissions.py)
-----------------------------------------
`backend/commissions.py` keeps `trade_history.profit_loss` GROSS on purpose, so
that every exit threshold in `decisions/` — all measured gross — stays comparable
with `research/exit_rule_replay.py`, which also models no fees. This module keeps
that doctrine: the backtesters leave per-trade P&L GROSS (quote-to-quote) for
comparability, and apply costs to CASH instead, so the equity curve, CAGR,
expectancy and final equity are NET. The cost totals are reported separately so a
result is never presented as costless.

THE NUMBERS ARE ASSUMPTIONS, NOT MEASUREMENTS
---------------------------------------------
- Commission defaults to IBKR's tiered 0.35¢/share with a $0.35 per-order
  minimum. Roadmap item #3 quotes ~0.65¢/share as a conservative all-in figure;
  set `BACKTEST_COMMISSION_PER_SHARE=0.0065` to use it.
- Slippage defaults to 5 bps (0.05%) applied ADVERSELY to every fill (buys fill
  higher, sells fill lower). This is a placeholder assumption, NOT a measured
  fill-quality number — real slippage on a market-open breakout entry is not
  known without live fill data. Tune via `BACKTEST_SLIPPAGE_BPS`.

Both are env-overridable so a run can sweep cost sensitivity without a code
change. Import-safe: env reads only, no side effects, no third-party imports.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# IBKR tiered US-equity default: $0.0035/share, $0.35 per-order minimum.
DEFAULT_COMMISSION_PER_SHARE = _env_float("BACKTEST_COMMISSION_PER_SHARE", 0.0035)
DEFAULT_COMMISSION_MIN       = _env_float("BACKTEST_COMMISSION_MIN", 0.35)
# Adverse slippage applied to each fill, in basis points (1 bp = 0.01%).
DEFAULT_SLIPPAGE_BPS         = _env_float("BACKTEST_SLIPPAGE_BPS", 5.0)


@dataclass(frozen=True)
class CostModel:
    """A commission + slippage snapshot. Defaults come from the env at import;
    build one per backtest run so a run can override without touching globals."""
    commission_per_share: float = DEFAULT_COMMISSION_PER_SHARE
    commission_min: float = DEFAULT_COMMISSION_MIN
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS

    @property
    def slippage_frac(self) -> float:
        return self.slippage_bps / 10_000.0

    def commission(self, shares: int, price: float) -> float:
        """Per-execution commission: max(per-share × shares, per-order minimum).
        Zero shares → zero (no execution). Never negative."""
        shares = abs(int(shares))
        if shares <= 0:
            return 0.0
        return round(max(shares * self.commission_per_share, self.commission_min), 4)

    def buy_fill(self, quote: float) -> float:
        """A BUY crosses the spread upward — you pay a little more than the quote."""
        return float(quote) * (1.0 + self.slippage_frac)

    def sell_fill(self, quote: float) -> float:
        """A SELL crosses the spread downward — you receive a little less."""
        return float(quote) * (1.0 - self.slippage_frac)


def build_cost_model() -> CostModel:
    """The cost model for a backtest run, from the env defaults above."""
    return CostModel()
