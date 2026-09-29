"""Replay the bot's OWN closed trades against alternative exit-rule parameters.

This harness is different in kind from the others in `research/`. Those backtest
the strategy over a synthetic universe of screener-generated entries. This one
takes the trades the bot *actually placed*, replays each one on 5-minute bars,
and reports what a different set of exit parameters would have done — expressed
as a dollar delta against the exit that really happened.

That framing is the point. It cannot tell you whether the strategy is good; it
can only tell you whether the exit parameters are leaving money on the table on
the entries the strategy is already choosing. For periodic parameter review that
is exactly the question, and it has no simulation-universe bias: every entry is
real, every comparison baseline is a real fill.

WHY 5-MINUTE BARS
-----------------
The agent evaluates rules on a 15-minute cycle and its loss rules do not sell —
they call `arm_exit()`, which places a tight (0.6%) IBKR trailing stop with a
3.25-hour deadline. On daily bars that mechanism cannot be modelled at all: the
armed trail resolves intraday, usually on the same day it was placed. Replaying
on daily bars silently converts every armed exit into a next-day market sell,
which is both wrong and pessimistic.

So this harness:
  - evaluates rules ONLY on 15-minute boundaries (:00, :15, :30, :45)
  - on a trigger, arms a trail at that bar's CLOSE (never its high — see below)
  - resolves the trail against every subsequent 5-minute bar
  - forces a market sell at the first 15-minute boundary past the deadline

ANCHOR CORRECTNESS (read before changing)
-----------------------------------------
`arm_exit()` places the stop at the moment the trigger fires, so the trail can
only ever ratchet from the price at THAT moment. Seeding the peak with the
trigger bar's HIGH back-dates the stop to a price that printed before it existed
and books exits better than were reachable. This is the same look-ahead bug
documented in decisions/2026-08-17_armed-exit-backtest-lookahead.md; do not
reintroduce it here. The trail is seeded with the trigger bar's close.

Gap handling is deliberately pessimistic: if the first bar after arming OPENS
through the trail level, the fill is the open, not the level.

LIMITATIONS
-----------
  - Fills are modelled at the trail level when a bar's low reaches it. Real fills
    are marginally worse.
  - Commission and slippage are not modelled. They are near-identical across the
    configurations being compared, so they cancel in the delta.
  - Every trade comes from one market regime. A parameter that wins here has not
    been shown to generalise.
  - Sample size is small. Check `n` in the output before believing anything: a
    result driven by one or two trades is noise. The report prints per-trade
    deltas precisely so this is visible rather than hidden in an average.

USAGE
-----
    set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
    python3 research/exit_rule_replay.py                  # headline comparison
    python3 research/exit_rule_replay.py --grid           # full parameter sweep
    python3 research/exit_rule_replay.py --json out.json  # machine-readable

Requires SUPABASE_URL, SUPABASE_KEY and FMP_API_KEY in the environment. Reads
Supabase and FMP only — it never writes anything anywhere.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any

import requests

# ── Live agent defaults these experiments are measured against ────────────────
# Mirrors of execution_agent.py. If a default changes there, change it here and
# say so, otherwise "current config" in the report is a lie.
ARMED_EXIT_TRAIL_PCT = 0.006
ARMED_EXIT_DEADLINE_HOURS = 3.25
CHECK_MINUTES = (0, 15, 30, 45)

# Mirror of config.MAX_POSITIONS. The slot-opportunity-cost model (--slotcost)
# needs the concurrent-position cap to know when a longer hold BLOCKS a later
# entry. If the live cap changes, change it here and say so, or the slot model
# silently prices contention against the wrong ceiling.
MAX_POSITIONS = 5

FMP_5MIN = "https://financialmodelingprep.com/stable/historical-chart/5min"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"Missing {name}. Load credentials first:\n"
            "  set -a && . ~/.config/ai-trading-bot/secrets.env && set +a"
        )
    return val


@dataclass
class ExitConfig:
    """One candidate parameterisation of the early-exit rules."""

    label: str
    # Percentage kill-switch
    pct: float | None = None          # e.g. 1.0 for -1%
    pct_last_day: int = 0
    # Flat dollar stop
    dollar: float | None = None
    dollar_last_day: int = 5
    # ATR-normalised thesis stop
    atr_mult: float | None = None
    atr_start_day: int = 2
    atr_last_day: int = 5
    # Mechanism
    mode: str = "armed"               # "armed" (live behaviour) or "market"
    trail: float = ARMED_EXIT_TRAIL_PCT
    deadline_h: float = ARMED_EXIT_DEADLINE_HOURS

    # ── Prove-It Stop ────────────────────────────────────────────────────────
    # When `proveit` is True the trade is replayed by simulate_proveit() instead
    # of simulate(), and the three entry-anchored rules above are ignored: the
    # whole point of the design is that Phase 1 REPLACES them.
    proveit: bool = False
    # Phase 1 (position has never closed above entry) — entry-anchored tiers,
    # ordered, as ((last_day, pct), ...). The first tier whose last_day >= the
    # current day index wins, so ((1, 1.0), (99, 1.5)) means "1% on days 0-1,
    # then 1.5% from day 2 onwards".
    p1_tiers: tuple[tuple[int, float], ...] | None = None
    # True  = fire the instant price TOUCHES the level (models a resting IBKR
    #         stop; wick-sensitive).
    # False = fire only if a 15-minute CLOSE is at/below the level (models the
    #         agent's monitoring cycle; ignores wicks).
    p1_touch: bool = False
    # Days on which Phase 1 is enforced BROKER-SIDE: a resting IBKR stop sitting
    # exactly on the level, filling the instant price touches it, with no
    # arm_exit() bounce-capture. `None` disables it entirely (the shipped
    # behaviour, where every Phase 1 day is bot-polled and then armed).
    # Set to 0 to model "day 0 hard at the broker, day 1+ unchanged".
    p1_hard_max_day: int | None = None
    # ── The resting Phase 1 broker leg ───────────────────────────────────────
    # Live, Phase 1 is NOT only a bot-polled band. place_protective_stops() also
    # rests an IBKR order at PROVE_IT_BACKSTOP_SLACK_PCT below the band, so a
    # disconnect still has a floor. `p1_broker_leg` models that second order.
    #
    # `p1_ratchet` is what makes it faithful. The resting leg is submitted as
    # orderType='TRAIL' (execution_agent.place_protective_stops), and an IBKR
    # TRAIL order's anchor RATCHETS UP with the high water mark. exit_rules.py
    # documents the Phase 1 level as a FIXED floor anchored to entry; the order
    # actually placed does not behave that way. With `p1_ratchet=True` the leg
    # climbs as peak * (1 - trail_pct) and can rise ABOVE entry -- converting a
    # loss cap into a profit-taker. With False it stays pinned where the design
    # says it sits. The gap between the two is the cost of the defect.
    #
    # Both default OFF so every pre-existing config replays unchanged.
    p1_broker_leg: bool = False
    p1_ratchet: bool = False
    p1_backstop_slack: float = 0.01
    # Phase 2 (position HAS closed above entry) — peak-anchored give-back.
    p2_enabled: bool = True
    # Minimum peak gain % before the breakeven floor arms. Below this a floor
    # would sit inside spread/tick noise and shake the position out; see INCY.
    p2_arm_gain: float = 2.0
    # Where the floor sits, as % above entry. 0.0 == breakeven.
    p2_floor_pct: float = 0.0
    # Above this gain the existing tight ladder rung takes over instead.
    p2_ladder_gain: float = 5.0
    p2_ladder_trail: float = 0.015
    # PROTECTION CLIFF FIX (candidate). Shipped behaviour leaves a position that
    # has closed above entry but not yet reached `p2_arm_gain` with NO Prove-It
    # level at all -- only the wide base trail. So a close a single cent above
    # entry REMOVES the Phase 1 entry-anchored band and strictly WORSENS
    # protection. NTRA (8/26-8/31) is the proof: it closed $0.27 above entry on
    # 8/27, peaked at +1.40%, never armed, and its floor fell from 328.28 to the
    # ~308 base trail; it exited at -5.2%.
    # When True, the unarmed window keeps the Phase 1 band as its floor, so
    # protection can never get worse as a result of good news.
    p2_unarmed_keeps_p1: bool = False

    # ── Always-on base/disaster trailing stop ────────────────────────────────
    # Models the single GTC TrailingStopOrder that ALWAYS rests at the broker
    # underneath every Prove-It phase (execution_agent.place_trailing_stop). It
    # trails `base_trail` below the running HWM. Being broker-side it survives a
    # bot disconnect — though the static hard stop (execution_agent.hard_stop_price,
    # not modelled here) is the fixed-price disconnect floor. Live the base trail
    # is the ATR base (10-12%); this knob exists to measure the NORMAL-OPERATION
    # cost of tightening it — how often a tighter base trail would fire BEFORE the
    # Prove-It floor and clip a winner. The disconnect-protection benefit is a
    # reliability property this price-path replay cannot score; see the analysis notes.
    base_trail: float | None = None

    # ── End-of-day give-back variant ─────────────────────────────────────────
    # When True the ladder rung stops being a resting intraday stop and becomes
    # a once-a-day test on the CLOSE, evaluated on the final bar of the session.
    # This is a different rule, not a tighter setting of the same one: a resting
    # trail is triggered by the low of any 5-minute bar, so it is wick-sensitive
    # and cannot be tightened far without firing on noise. A close-based test
    # ignores wicks entirely, which is what makes a much tighter band arguable.
    #
    # The trade-off is real and must not be glossed: between two closes there is
    # no ladder protection at all. `p2_eod_backstop` optionally keeps a wider
    # resting stop underneath as a crash guard, which is the honest comparison —
    # "0.5% at the close" and "0.5% at the close PLUS a 3% intraday backstop"
    # are different risk profiles.
    p2_eod: bool = False
    p2_eod_trail: float = 0.005
    # "intraday" anchors give-back to the highest HIGH seen (what the live
    # high_water_mark column stores); "close" anchors to the highest CLOSE. The
    # second is the internally consistent choice for a close-based rule — an
    # intraday anchor measures a close against a price that only ever existed
    # inside a wick, which reintroduces the wick sensitivity being removed.
    p2_eod_anchor: str = "intraday"
    p2_eod_backstop: float | None = None

    # ── Partial scale-out (profit-taking on strength) ────────────────────────
    # A structurally different lever from every stop-LEVEL knob above. Instead of
    # tightening the trail on the whole position (which trades winner-upside for
    # loss-avoidance ~1:1 and loses on this book), it books a FRACTION of the
    # position at a profit target and lets the remainder ride the unchanged rule.
    # This breaks the symmetry: the give-back on a fader is capped on `scale_frac`
    # of the shares, while the runner keeps full upside on `1 - scale_frac`.
    #
    # `scale_frac` = fraction sold at the target (None disables). `scale_trigger`
    # = the gain % at which the limit sell rests. `scale_be_remainder` moves the
    # remainder's Phase-2 floor to breakeven AFTER the scale fills (a "free
    # trade"): only 1-frac shares are then exposed to the tighter floor, so the
    # winner-clip cost is scaled down with the share count.
    scale_frac: float | None = None
    scale_trigger: float = 4.0
    scale_be_remainder: bool = False

    # ── Drawdown-conditional ladder ("clean leader" trail) ───────────────────
    # The audit of 2026-09-18 found that widening the +5% ladder rung for EVERY
    # position does not pay: it buys upside on the few that keep running and
    # gives it straight back on the many that stall. The rung is one knob serving
    # two populations.
    #
    # This splits them using information available AT THE TIME, with no
    # lookahead: how far the position has already fallen below entry. On the
    # seven matched trades the separation was clean -- FRO (+24.7%), LPG (+20.8%)
    # and PSX (+13.2%) never fell more than 3.1% below entry, while every trade
    # that stalled had already been 8-13% underwater.
    #
    # When `clean_dd_pct` is set, a position that has reached the ladder rung
    # having NEVER traded more than `clean_dd_pct` below entry is treated as a
    # clean leader and trails at `clean_ladder_trail` instead of the tight
    # `p2_ladder_trail`. Anything that has been deeper underwater keeps the tight
    # rung. `None` disables it, so every pre-existing config replays unchanged.
    #
    # NOTE the confound this must be checked against: Phase 1 already exits at
    # -1%/-3% from entry, so among positions that SURVIVE to the rung, a shallow
    # drawdown may be near-automatic and this would collapse into a plain
    # widening. The `--clean` report prints the qualifying rate so that cannot be
    # glossed over.
    clean_dd_pct: float | None = None
    clean_ladder_trail: float = 0.03

    # ── Power hold (the "let a proven leader run" rule) ───────────────────────
    # The only rule in the book whose PURPOSE is to hold longer, and the only
    # one the harness has never modelled. Live (execution_agent ~L4217-4335):
    # once a position gains `power_hold_gain` % within `power_hold_trigger_days`
    # calendar days of entry, it latches for `power_hold_duration_days`; while
    # latched the whole Prove-It stack is SUSPENDED (prove_it_level = None), the
    # hard stop drops back to the disaster floor, and the trail widens to
    # `power_hold_trail`. It is also the one case allowed to LOOSEN a stop —
    # without that the +5% ladder rung, already clamped to 1.5%, would strangle
    # the leader the rule exists to protect.
    #
    # `None` disables it, which is every pre-existing config: power hold has
    # never fired in live trading and no replay before this one could see it.
    # Measuring it requires the run-on window, because the rule can only pay off
    # in bars AFTER the exit the tight ladder actually took.
    power_hold_gain: float | None = None
    power_hold_trail: float = 0.30
    power_hold_trigger_days: int = 21
    power_hold_duration_days: int = 56

    def describe(self) -> str:
        bits = []
        if self.proveit:
            if self.p1_tiers:
                spans = []
                prev = 0
                for last_day, pct in self.p1_tiers:
                    span = (f"d{prev}" if last_day >= 90 or last_day == prev
                            else f"d{prev}-{last_day}")
                    span = f"d{prev}+" if last_day >= 90 else span
                    spans.append(f"{span}:{pct}%")
                    prev = last_day + 1
                bits.append("P1[" + " ".join(spans) + "]")
                bits.append("touch" if self.p1_touch else "close")
                if self.p1_hard_max_day is not None:
                    bits.append(f"hard<=d{self.p1_hard_max_day}")
            if self.p2_enabled:
                if self.p2_eod:
                    back = ("none" if self.p2_eod_backstop is None
                            else f"{self.p2_eod_backstop * 100:.1f}%")
                    bits.append(f"P2[arm>={self.p2_arm_gain}% floor=+{self.p2_floor_pct}% "
                                f"EOD>={self.p2_ladder_gain}%@{self.p2_eod_trail * 100:.2f}% "
                                f"anchor={self.p2_eod_anchor} backstop={back}]")
                else:
                    bits.append(f"P2[arm>={self.p2_arm_gain}% floor=+{self.p2_floor_pct}% "
                                f"ladder>={self.p2_ladder_gain}%@{self.p2_ladder_trail*100:.1f}%]")
            bits.append(self.mode)
            return ", ".join(bits)
        if self.pct is not None:
            span = "day 0" if self.pct_last_day == 0 else f"days 0-{self.pct_last_day}"
            bits.append(f"{self.pct}% {span}")
        if self.dollar is not None:
            bits.append(f"${self.dollar:.0f} days 0-{self.dollar_last_day}")
        if self.atr_mult is not None:
            bits.append(f"{self.atr_mult}xATR days {self.atr_start_day}-{self.atr_last_day}")
        bits.append(self.mode)
        return ", ".join(bits)

    def p1_pct_for_day(self, day: int) -> float | None:
        if not self.p1_tiers:
            return None
        for last_day, pct in self.p1_tiers:
            if day <= last_day:
                return pct
        return None


@dataclass
class Trade:
    ticker: str
    buy_price: float
    buy_ts: dt.datetime
    sell_price: float
    sell_ts: dt.datetime
    shares: int
    profit_loss: float
    sell_reason: str | None
    bars: list[dict] = field(default_factory=list)
    trade_days: list[str] = field(default_factory=list)
    entry_atr_pct: float | None = None
    # Number of bars in `bars` that fall at or before the REAL exit. With the
    # run-on window enabled `bars` continues past the realised sell, so this is
    # the boundary between "replayed history" and "counterfactual future".
    # 0 means no run-on bars were fetched (every other mode).
    runon_from: int = 0

    @property
    def is_loser(self) -> bool:
        return self.profit_loss < 0


# ── Data loading ──────────────────────────────────────────────────────────────

def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_trades(verify_tls: bool = True) -> list[Trade]:
    """Every closed trade from Supabase `trade_history`."""
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_KEY")
    resp = requests.get(
        f"{url}/rest/v1/trade_history",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={
            "select": "ticker,buy_price,buy_date,sell_price,sell_date,shares,"
                      "profit_loss,sell_reason",
            "order": "sell_date.asc",
        },
        timeout=30,
        verify=verify_tls,
    )
    resp.raise_for_status()
    out = []
    for row in resp.json():
        if not row.get("sell_date") or not row.get("buy_date"):
            continue
        out.append(Trade(
            ticker=row["ticker"],
            buy_price=float(row["buy_price"]),
            buy_ts=_parse_ts(row["buy_date"]),
            sell_price=float(row["sell_price"]),
            sell_ts=_parse_ts(row["sell_date"]),
            shares=int(row["shares"]),
            profit_loss=float(row.get("profit_loss") or 0.0),
            sell_reason=row.get("sell_reason"),
        ))
    return out


def _fmp_get(url: str, params: dict) -> requests.Response:
    """GET with backoff on FMP rate limits.

    The run-on window roughly doubles the number of chunk requests per trade, so
    a sweep that used to sit under the rate limit now trips it. A 429 halfway
    through hydration discards every trade fetched so far, which is an expensive
    way to lose an hour — retry instead of dying.
    """
    delay = 2.0
    for attempt in range(6):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == 5:
                resp.raise_for_status()
            print(f"  . FMP {resp.status_code}, retrying in {delay:.0f}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")


def fetch_5min(symbol: str, start: dt.datetime, end: dt.datetime, api_key: str) -> list[dict]:
    """5-minute bars covering [start, end]. FMP caps the span, so chunk it."""
    collected: dict[str, dict] = {}
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        chunk_end = min(cursor + dt.timedelta(days=5), last)
        resp = _fmp_get(FMP_5MIN, {
            "symbol": symbol,
            "from": cursor.isoformat(),
            "to": chunk_end.isoformat(),
            "apikey": api_key,
        })
        payload = resp.json()
        if isinstance(payload, list):
            for row in payload:
                collected[row["date"]] = row
        cursor = chunk_end + dt.timedelta(days=1)

    bars = []
    for stamp in sorted(collected):
        row = collected[stamp]
        ts = dt.datetime.fromisoformat(row["date"].replace(" ", "T") + "+00:00")
        if start <= ts <= end:
            bars.append({
                "ts": ts,
                "date": ts.date().isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    bars.sort(key=lambda b: b["ts"])
    return bars


def fetch_entry_atr_pct(symbol: str, buy_date: dt.date, api_key: str,
                        window: int = 14) -> float | None:
    """Wilder ATR% on the daily bars strictly BEFORE entry (no look-ahead)."""
    # Through _fmp_get, not a bare requests.get: a 50-trade run issues two FMP
    # calls per trade and reliably trips the rate limiter partway through. An
    # unprotected call here aborts the whole sweep after ~40 trades of work.
    resp = _fmp_get(
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        {
            "symbol": symbol,
            "from": (buy_date - dt.timedelta(days=120)).isoformat(),
            "to": buy_date.isoformat(),
            "apikey": api_key,
        },
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return None
    rows = sorted((r for r in rows if r["date"] < buy_date.isoformat()),
                  key=lambda r: r["date"])

    atr = None
    prev_close = None
    trs: list[float] = []
    result = None
    for i, row in enumerate(rows):
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        tr = (high - low) if prev_close is None else max(
            high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        if i == window - 1:
            atr = sum(trs[:window]) / window
        elif i >= window:
            atr = (atr * (window - 1) + tr) / window
        if atr is not None and close:
            result = atr / close * 100.0
        prev_close = close
    return result


def hydrate(trades: list[Trade], api_key: str, quiet: bool = False,
            runon_days: int = 0) -> list[Trade]:
    """Attach price history. This is the slow part — one FMP call per chunk.

    `runon_days` extends the fetch window past the REAL exit by that many
    calendar days. Without it the replay is truncated at the realised sell, and
    any configuration whose rule would have fired LATER than the live rule is
    silently scored at the live exit price — a delta of exactly zero. That is
    fine when measuring tighter rules (they always fire first) but it makes the
    loosening direction unmeasurable by construction: "hold longer" can only pay
    off in the bars that truncation removes. See runon_configs().
    """
    hydrated = []
    for i, trade in enumerate(trades, 1):
        if not quiet:
            print(f"  [{i}/{len(trades)}] {trade.ticker}", file=sys.stderr)
        # Widen to whole-day boundaries. Supabase stores buy_date/sell_date at
        # midnight, so a same-day round trip yields a zero-width window and the
        # trade is silently dropped -- which excluded exactly the fastest,
        # largest same-day losers (OII, FROG) that Phase 1 is meant to catch.
        window_start = trade.buy_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = trade.sell_ts.replace(hour=23, minute=59, second=59, microsecond=0)
        if runon_days:
            window_end += dt.timedelta(days=runon_days)
        trade.bars = fetch_5min(trade.ticker, window_start, window_end, api_key)
        if not trade.bars:
            continue
        days: list[str] = []
        for bar in trade.bars:
            if not days or days[-1] != bar["date"]:
                days.append(bar["date"])
        trade.trade_days = days
        _correct_split(trade)
        # ONLY meaningful when a run-on window was requested. The default
        # whole-day window already extends past an intraday sell to 23:59, so
        # setting this unconditionally would make score() mark every unfired
        # rule out at the session close instead of crediting the realised exit
        # — silently changing every other sweep in the file.
        if runon_days:
            trade.runon_from = sum(1 for b in trade.bars if b["ts"] <= trade.sell_ts)
        trade.entry_atr_pct = fetch_entry_atr_pct(
            trade.ticker, trade.buy_ts.date(), api_key)
        hydrated.append(trade)
    return hydrated


# Common corporate-action ratios. FMP's intraday history is split-ADJUSTED,
# but trade_history stores the unadjusted price actually paid, so any trade
# spanning a split replays against prices on a different scale. APH (2-for-1)
# produced a fictitious -$11,650 loss before this guard existed.
_SPLIT_RATIOS = (1/10, 1/7, 1/5, 1/4, 1/3, 1/2, 2/3, 3/2, 2, 3, 4, 5, 7, 10)


def _correct_split(trade: Trade) -> None:
    """Rescale split-adjusted bars back onto the price actually paid."""
    if not trade.bars:
        return
    ref = min(trade.bars, key=lambda b: abs(b["ts"] - trade.buy_ts))["close"]
    if ref <= 0:
        return
    raw = trade.buy_price / ref
    if 0.8 <= raw <= 1.25:
        return
    ratio = min(_SPLIT_RATIOS, key=lambda r: abs(r - raw))
    if abs(ratio - raw) / raw > 0.15:
        print(f"  ! {trade.ticker}: price scale {raw:.3f} matches no known "
              f"split ratio -- excluding from replay", file=sys.stderr)
        trade.bars = []
        return
    print(f"  ~ {trade.ticker}: rescaling bars by {ratio:g} "
          f"(split-adjusted history)", file=sys.stderr)
    for bar in trade.bars:
        for field_name in ("open", "high", "low", "close"):
            bar[field_name] *= ratio


# ── The replay itself ─────────────────────────────────────────────────────────

def simulate_scaleout(trade: Trade, cfg: ExitConfig) -> dict | None:
    """Replay one trade with a partial scale-out plus a rule-driven remainder.

    Returns a single BLENDED exit price so score()'s existing
    (price - actual_sell) * shares delta works unchanged:

        blended = frac * scale_fill + (1 - frac) * remainder_exit

    The scale-out is a resting LIMIT sell at entry*(1+scale_trigger%). It fills
    the first bar whose HIGH reaches the target (at the target, or at the open if
    the bar gapped above it). If the peak never reaches the target, no scale-out
    happens and the whole position follows the base rule — blended == remainder.

    The remainder rides the SAME Prove-It config, except that when
    scale_be_remainder is set AND the scale actually filled, the remainder's
    Phase-2 floor is lifted to breakeven (p2_floor_pct = 0). Only 1-frac shares
    are then exposed to that tighter floor.

    LIMITATION (must be read with any result): the bars only extend to the trade's
    ACTUAL sell date. For positions the live rules cut early, the remainder cannot
    "run" past that date, so this UNDERSTATES scale-out's upside on exactly the
    trades where letting a runner run matters most. Treat the net as a floor on
    the strategy's value, not an estimate of it. Fills are slippage-free, which
    flatters the scale leg slightly in the other direction.
    """
    frac = cfg.scale_frac or 0.0
    target = trade.buy_price * (1 + cfg.scale_trigger / 100.0)

    scale_fill: float | None = None
    for bar in trade.bars:
        if bar["ts"] < trade.buy_ts:
            continue
        if bar["high"] >= target:
            scale_fill = bar["open"] if bar["open"] > target else target
            break

    # Remainder rule. If the scale filled and we want a "free trade", lift the
    # remainder floor to breakeven for the remainder simulation only.
    rem_cfg = cfg
    if scale_fill is not None and cfg.scale_be_remainder:
        rem_cfg = replace(cfg, p2_floor_pct=0.0)
    rem = simulate_proveit(trade, rem_cfg)
    # RUN-ON: `rem is None` means the remainder's rule never fired. Booking the
    # REAL sell price here reintroduces exactly the truncation bias the run-on
    # window exists to remove -- it hands the remainder the live exit for free,
    # so a wider trail on the remainder can never show its upside. Mark it out
    # at the last available close instead, the same way score() does for the
    # non-scale path.
    if rem is None and trade.runon_from and trade.runon_from < len(trade.bars):
        rem = {"price": trade.bars[-1]["close"], "reason": "runon_open",
               "ts": trade.bars[-1]["ts"]}
    remainder_exit = rem["price"] if rem is not None else trade.sell_price
    reason = (rem["reason"] if rem is not None else "held")
    # The position is fully closed only when the REMAINDER leaves, so that is
    # when its portfolio slot frees. The scale leg is a partial and does not free
    # the slot. Fall back to the real sell timestamp when the remainder never
    # fired (no run-on window).
    exit_ts = rem.get("ts") if rem is not None else trade.sell_ts

    if scale_fill is None or frac <= 0.0:
        return {"price": remainder_exit, "reason": reason, "ts": exit_ts}

    blended = frac * scale_fill + (1 - frac) * remainder_exit
    return {"price": round(blended, 4),
            "reason": f"scale{int(frac*100)}@{cfg.scale_trigger:.0f}%+{reason}",
            "ts": exit_ts}


def simulate(trade: Trade, cfg: ExitConfig) -> dict | None:
    """Replay one trade. Returns the modelled exit, or None if nothing fired."""
    entry = trade.buy_price
    day_index = {d: i for i, d in enumerate(trade.trade_days)}
    armed: dict | None = None
    closed_above_entry = False
    day_bars: list[dict] = []
    current_date: str | None = None

    for bar in trade.bars:
        # ── Resolve an armed exit before considering new triggers ────────────
        if armed:
            level = armed["peak"] * (1 - cfg.trail)
            if armed["first"] and bar["open"] <= level:
                return {"price": bar["open"], "reason": f"{armed['reason']}_gap", "ts": bar["ts"]}
            if bar["low"] <= level:
                return {"price": level, "reason": f"{armed['reason']}_trail", "ts": bar["ts"]}
            armed["peak"] = max(armed["peak"], bar["high"])
            armed["first"] = False
            if bar["ts"].minute in CHECK_MINUTES:
                held_h = (bar["ts"] - armed["at"]).total_seconds() / 3600.0
                if held_h >= cfg.deadline_h:
                    return {"price": bar["close"], "reason": f"{armed['reason']}_deadline", "ts": bar["ts"]}

        # ── Track the daily-close follow-through latch the thesis stop uses ──
        if current_date != bar["date"]:
            if day_bars and day_bars[-1]["close"] > entry:
                closed_above_entry = True
            day_bars = []
            current_date = bar["date"]
        day_bars.append(bar)

        if bar["ts"].minute not in CHECK_MINUTES or bar["ts"] < trade.buy_ts:
            continue

        day = day_index[bar["date"]]
        close = bar["close"]
        triggered = None

        # Evaluation order mirrors monitor_portfolio_intraday().
        if (cfg.pct is not None and day <= cfg.pct_last_day
                and close <= entry * (1 - cfg.pct / 100.0)):
            triggered = "pct"
        elif (cfg.dollar is not None and day <= cfg.dollar_last_day
                and trade.shares * (close - entry) <= -cfg.dollar):
            triggered = "dollar"
        elif (cfg.atr_mult is not None
                and cfg.atr_start_day <= day <= cfg.atr_last_day
                and not closed_above_entry and trade.entry_atr_pct):
            threshold = cfg.atr_mult * trade.entry_atr_pct
            if (close / entry - 1) * 100.0 <= -threshold:
                triggered = "thesis"

        if not triggered:
            continue
        if cfg.mode == "market":
            return {"price": close, "reason": f"{triggered}_market", "ts": bar["ts"]}
        armed = {"at": bar["ts"], "peak": close, "first": True, "reason": triggered}

    return None


def simulate_proveit(trade: Trade, cfg: ExitConfig) -> dict | None:
    """Replay one trade under the two-phase Prove-It Stop.

    The governing question is asked once per bar: has this position ever CLOSED
    above entry?

      No  -> PHASE 1. Entry-anchored tiered stop. Fires via arm_exit() (a 0.6%
             trail with a deadline) exactly like the live kill-switch, because
             the replay already showed arming beats a market sell by ~$600.
      Yes -> PHASE 2. Peak-anchored give-back. Below `p2_ladder_gain` the stop
             is a flat floor at/above entry so a green trade cannot become a
             loss; above it, the existing tight ladder rung takes over.

    Phase 2 is modelled as a RESTING stop (filled at the level, or at the open
    when a bar gaps through it) because that is how it is intended to live at
    IBKR. Phase 1 respects `p1_touch`: touch = resting stop, close = the agent's
    15-minute cycle.

    The Phase 2 stop is one-way. It never moves down, mirroring the live
    `_compute_dynamic_trail_pct()` contract.
    """
    entry = trade.buy_price
    day_index = {d: i for i, d in enumerate(trade.trade_days)}
    armed: dict | None = None
    proven = False
    power_held = False
    peak = entry
    stop_price: float | None = None
    p1_broker_stop: float | None = None
    day_bars: list[dict] = []
    current_date: str | None = None
    # Final bar of each session, so the EOD variant can be evaluated without
    # looking ahead: the agent at the close knows the whole session, and nothing
    # after it.
    last_bar_ts: dict[str, Any] = {}
    for _b in trade.bars:
        last_bar_ts[_b["date"]] = _b["ts"]
    peak_close = entry
    # Deepest excursion BELOW entry seen so far, as a positive %. Updated only
    # from bars at/after entry, and read before the current bar is folded in, so
    # the decision never uses information from the future.
    max_dd_pct = 0.0

    for bar in trade.bars:
        # ── Resolve an armed Phase 1 exit before anything else ───────────────
        if armed:
            level = armed["peak"] * (1 - cfg.trail)
            if armed["first"] and bar["open"] <= level:
                return {"price": bar["open"], "reason": f"{armed['reason']}_gap", "ts": bar["ts"]}
            if bar["low"] <= level:
                return {"price": level, "reason": f"{armed['reason']}_trail", "ts": bar["ts"]}
            armed["peak"] = max(armed["peak"], bar["high"])
            armed["first"] = False
            if bar["ts"].minute in CHECK_MINUTES:
                held_h = (bar["ts"] - armed["at"]).total_seconds() / 3600.0
                if held_h >= cfg.deadline_h:
                    return {"price": bar["close"], "reason": f"{armed['reason']}_deadline", "ts": bar["ts"]}
            continue

        # ── Daily rollover: a position becomes "proven" on a close above entry
        if current_date != bar["date"]:
            if day_bars and day_bars[-1]["close"] > entry:
                proven = True
            if day_bars:
                peak_close = max(peak_close, day_bars[-1]["close"])
            day_bars = []
            current_date = bar["date"]
        day_bars.append(bar)

        if bar["ts"] < trade.buy_ts:
            continue
        day = day_index[bar["date"]]

        # HWM from bars BEFORE this one (this bar's high is folded in at the loop
        # tail), so testing this bar's low/open against a peak-anchored level is
        # not look-ahead.
        base_level = peak * (1 - cfg.base_trail) if cfg.base_trail is not None else None

        if proven and cfg.p2_enabled:
            # Level is derived from the peak BEFORE this bar, then this bar's
            # low is tested against it. Raising the stop with the same bar's
            # high and then filling on its low would be look-ahead.
            peak_gain = (peak / entry - 1) * 100.0
            candidate: float | None = None
            floor_level = entry * (1 + cfg.p2_floor_pct / 100.0)

            # ── Power hold ───────────────────────────────────────────────────
            # Latch on the peak as it stood BEFORE this bar, same no-look-ahead
            # rule as every level below. Once latched the Prove-It stack is
            # suspended and the wide trail REPLACES stop_price outright rather
            # than being max()'d into it — modelling the live `power_held or
            # _unproven` branch that permits this single loosening.
            if cfg.power_hold_gain is not None:
                cal_days = (bar["ts"].date() - trade.buy_ts.date()).days
                if (not power_held
                        and peak_gain >= cfg.power_hold_gain
                        and cal_days <= cfg.power_hold_trigger_days):
                    power_held = True
                if power_held and cal_days > cfg.power_hold_duration_days:
                    power_held = False
                    # Expiry hands the position back to the ladder, which is
                    # one-way from wherever the wide trail left it.
                    stop_price = None
            if power_held:
                stop_price = peak * (1 - cfg.power_hold_trail)
                if bar["open"] <= stop_price:
                    return {"price": bar["open"], "reason": "power_hold_gap", "ts": bar["ts"]}
                if bar["low"] <= stop_price:
                    return {"price": stop_price, "reason": "power_hold_trail", "ts": bar["ts"]}
                peak = max(peak, bar["high"])
                peak_close = max(peak_close, bar["close"])
                continue

            if peak_gain >= cfg.p2_ladder_gain:
                if cfg.p2_eod:
                    # The ladder rung is no longer a resting stop. Keep the
                    # breakeven floor, and optionally a wider crash backstop;
                    # the give-back itself is tested on the close below.
                    candidate = floor_level
                    if cfg.p2_eod_backstop is not None:
                        candidate = max(candidate, peak * (1 - cfg.p2_eod_backstop))
                else:
                    rung = cfg.p2_ladder_trail
                    if (cfg.clean_dd_pct is not None
                            and max_dd_pct <= cfg.clean_dd_pct):
                        rung = cfg.clean_ladder_trail
                    candidate = peak * (1 - rung)
            elif peak_gain >= cfg.p2_arm_gain:
                candidate = floor_level
            elif cfg.p2_unarmed_keeps_p1:
                # Proven but not yet armed: retain the Phase 1 entry-anchored
                # band so a marginal green close cannot loosen the stop.
                p1 = cfg.p1_pct_for_day(day)
                if p1 is not None:
                    candidate = entry * (1 - p1 / 100.0)
            # The always-on base trail rests underneath. max() keeps whichever
            # is tighter, so it only BINDS in the unarmed window (candidate None)
            # or when it is above the give-back floor.
            if base_level is not None:
                candidate = base_level if candidate is None else max(candidate, base_level)
            if candidate is not None:
                stop_price = candidate if stop_price is None else max(stop_price, candidate)

            if stop_price is not None:
                if bar["open"] <= stop_price:
                    return {"price": bar["open"], "reason": "p2_gap", "ts": bar["ts"]}
                if bar["low"] <= stop_price:
                    return {"price": stop_price, "reason": "p2_floor", "ts": bar["ts"]}

            # ── EOD give-back test ───────────────────────────────────────────
            # Evaluated only on the session's final bar, against the close. The
            # anchor includes today's own action (the agent at 15:55 has seen
            # it) but nothing beyond this bar, so there is no look-ahead.
            if cfg.p2_eod and bar["ts"] == last_bar_ts.get(bar["date"]):
                if cfg.p2_eod_anchor == "close":
                    anchor = max(peak_close, bar["close"])
                else:
                    anchor = max(peak, bar["high"])
                if (anchor / entry - 1) * 100.0 >= cfg.p2_ladder_gain:
                    if bar["close"] <= anchor * (1 - cfg.p2_eod_trail):
                        return {"price": bar["close"], "reason": "p2_eod", "ts": bar["ts"]}

        elif not proven:
            pct = cfg.p1_pct_for_day(day)

            # ── The resting broker leg ───────────────────────────────────────
            # Submitted live as an IBKR TRAIL order. Its anchor is the HWM from
            # bars BEFORE this one (`peak`), so testing this bar's low against
            # it is not look-ahead. One-way by construction: a TRAIL anchor
            # never falls, so neither does the stop.
            broker_level: float | None = None
            if pct is not None and cfg.p1_broker_leg:
                slack_level = (entry * (1 - pct / 100.0)
                               * (1 - cfg.p1_backstop_slack))
                if cfg.p1_ratchet:
                    # trailingPercent is solved once, at placement, to put the
                    # stop on `slack_level` while price is ~entry. IBKR then
                    # applies that same percentage to the running HWM.
                    trail_pct = 1.0 - (slack_level / entry)
                    cand = peak * (1.0 - trail_pct)
                    p1_broker_stop = (cand if p1_broker_stop is None
                                      else max(p1_broker_stop, cand))
                else:
                    p1_broker_stop = slack_level
                broker_level = p1_broker_stop

            bot_level = entry * (1 - pct / 100.0) if pct is not None else None

            # A resting order fills on TOUCH; the bot only reacts to a 15-minute
            # CLOSE. So once the ratchet lifts the resting leg to or above the
            # bot's band, the broker is unavoidably first in line on any
            # decline. That inversion IS the defect being measured.
            if broker_level is not None and (bot_level is None
                                             or broker_level >= bot_level):
                if bar["open"] <= broker_level:
                    return {"price": bar["open"], "reason": "p1_ratchet_gap", "ts": bar["ts"]}
                if bar["low"] <= broker_level:
                    return {"price": broker_level, "reason": "p1_ratchet", "ts": bar["ts"]}

            if pct is not None:
                level = bot_level
                # A "hard" day is enforced by a resting broker stop: wick-
                # sensitive, fills at the level (or the open on a gap), and
                # never arms. Otherwise fall back to the configured mechanism.
                hard = (cfg.p1_hard_max_day is not None
                        and day <= cfg.p1_hard_max_day)
                hit = False
                fill = level
                if cfg.p1_touch or hard:
                    if bar["open"] <= level:
                        hit, fill = True, bar["open"]
                    elif bar["low"] <= level:
                        hit, fill = True, level
                elif bar["ts"].minute in CHECK_MINUTES and bar["close"] <= level:
                    hit, fill = True, bar["close"]

                if hit:
                    if hard:
                        return {"price": fill, "reason": "p1_broker", "ts": bar["ts"]}
                    if cfg.mode == "market":
                        return {"price": fill, "reason": "p1_market", "ts": bar["ts"]}
                    armed = {"at": bar["ts"], "peak": fill,
                             "first": True, "reason": "p1"}
                    continue

            # The resting broker leg when it sits BENEATH the bot's band — its
            # designed position, where it is a genuine backstop and the band
            # fires first. Reached only when the band did not fire/arm above.
            if broker_level is not None and bot_level is not None \
                    and broker_level < bot_level:
                if bar["open"] <= broker_level:
                    return {"price": bar["open"], "reason": "p1_backstop_gap", "ts": bar["ts"]}
                if bar["low"] <= broker_level:
                    return {"price": broker_level, "reason": "p1_backstop", "ts": bar["ts"]}

            # The base trail also rests in Phase 1, beneath the entry band.
            # Reached only when the band did not fire/arm on this bar.
            if base_level is not None:
                if bar["open"] <= base_level:
                    return {"price": bar["open"], "reason": "base_gap", "ts": bar["ts"]}
                if bar["low"] <= base_level:
                    return {"price": base_level, "reason": "base_trail", "ts": bar["ts"]}

        peak = max(peak, bar["high"])
        # Fold this bar into the drawdown tracker LAST, so every decision above
        # was made on bars strictly before it. Only bars at/after entry count.
        if bar["ts"] >= trade.buy_ts:
            dd = (entry - bar["low"]) / entry * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd

    return None


def _trade_delta(trade: Trade, cfg: ExitConfig) -> tuple[float, dict | None]:
    """The dollar delta ONE trade would see under `cfg`, vs its realised exit.

    This is the single source of truth for a per-trade result. `score()` sums it
    into an aggregate and `jackknife()` re-sums it across leave-one-out samples;
    keeping both on this one function is what guarantees the two can never drift
    (an earlier design duplicated the simulate/run-on-mark logic and they did).

    Returns (delta, sim). `sim` carries the exit reason for the per-trade table;
    it is None only when the position never exited AND has no run-on bars to be
    marked out at — in which case the delta is 0 (the realised exit stands).
    """
    if cfg.scale_frac is not None:
        sim = simulate_scaleout(trade, cfg)
    elif cfg.proveit:
        sim = simulate_proveit(trade, cfg)
    else:
        sim = simulate(trade, cfg)
    # With a run-on window a configuration can survive past the REAL exit.
    # `sim is None` then means "still open at the end of the extended window",
    # which must be marked out at the last available close. Scoring it at the
    # realised sell price (delta 0) would silently hand every loosened rule the
    # live exit for free — the exact bias the run-on window exists to remove.
    if sim is None and trade.runon_from and trade.runon_from < len(trade.bars):
        sim = {"price": trade.bars[-1]["close"], "reason": "runon_open"}
    delta = 0.0 if sim is None else round(
        (sim["price"] - trade.sell_price) * trade.shares, 2)
    return delta, sim


def score(trades: list[Trade], cfg: ExitConfig) -> dict[str, Any]:
    """Aggregate one configuration into a comparable result.

    `net` is the ALL-IN sum of every per-trade delta — including losers the
    configuration made worse. An earlier version summed only `loser_saved +
    winner_delta`, silently dropping negative deltas on losing trades. That
    flattered every aggressive configuration (the shipped stack scored +$186
    when its true all-in figure is -$272) because tightening a loss rule most
    often makes some losers slightly worse while making a few much better.
    Do not reintroduce a `net` that ignores a sign.
    """
    loser_saved = loser_cost = winner_delta = 0.0
    losers_helped = losers_hurt = winners_hurt = 0
    per_trade = []
    worst_loss = 0.0
    over_300 = 0
    over_500 = 0

    for trade in trades:
        delta, sim = _trade_delta(trade, cfg)
        # Resulting P&L for this trade under `cfg`. This is what answers "how
        # big is my worst loss", which an aggregate net deliberately hides.
        result_pl = trade.profit_loss + delta
        worst_loss = min(worst_loss, result_pl)
        if result_pl < -300:
            over_300 += 1
        if result_pl < -500:
            over_500 += 1
        if abs(delta) > 0.005:
            per_trade.append({
                "ticker": trade.ticker,
                "actual_pl": round(trade.profit_loss, 2),
                "delta": delta,
                "result_pl": round(result_pl, 2),
                "reason": sim["reason"] if sim else None,
            })
        if trade.is_loser:
            if delta > 0:
                loser_saved += delta
                losers_helped += 1
            elif delta < 0:
                loser_cost += delta
                losers_hurt += 1
        else:
            winner_delta += delta
            if delta < 0:
                winners_hurt += 1

    return {
        "label": cfg.label,
        "config": cfg.describe(),
        "loser_saved": round(loser_saved, 2),
        "loser_cost": round(loser_cost, 2),
        "loser_net": round(loser_saved + loser_cost, 2),
        "winner_delta": round(winner_delta, 2),
        "net": round(loser_saved + loser_cost + winner_delta, 2),
        "losers_helped": losers_helped,
        "losers_hurt": losers_hurt,
        "winners_hurt": winners_hurt,
        "worst_loss": round(worst_loss, 2),
        "over_300": over_300,
        "over_500": over_500,
        "per_trade": sorted(per_trade, key=lambda r: r["delta"]),
    }


# ── Candidate sets ────────────────────────────────────────────────────────────

def retired_pre_proveit_config() -> ExitConfig:
    """The RETIRED pre-2026-09-04 ruleset. NOT what the agent runs today.

    ⚠️  This was labelled "SHIPPED" until 2026-09-18 and that label was WRONG.
    The kill-switch, Early Dollar Stop and Thesis Stop it models were all
    replaced by the Prove-It Stop on 2026-09-04
    (decisions/2026-09-04_prove-it-stop.md), and EFFECTIVE_POSITION_SLOTS --
    referenced below -- was DELETED the same day (docs/retired_code.md).

    It is kept only as the historical "what we replaced" reference point. The
    baseline for anything that runs today is live_baseline(); for a stop-only
    comparison with no scale-out it is shipped_proveit(). Using this row as the
    baseline understates every current rule, because it is not a current rule.

    The retired dollar stop was not a flat amount: it resolved to
    (equity / EFFECTIVE_POSITION_SLOTS) x EARLY_DOLLAR_STOP_PCT, i.e.
    (equity / 4) x 6%. At the ~$100K equity these trades were placed under that
    is $1,500, which is what is modelled here. If equity has moved materially
    since, recompute this before quoting the result — a stale figure here makes
    every "vs shipped" comparison wrong.
    See decisions/2026-08-20_slot-derived-early-dollar-stop.md.
    """
    return ExitConfig(
        label="RETIRED pre-ProveIt (1.0% d0 + $1500 dollar stop + 1xATR thesis)",
        pct=1.0, pct_last_day=0,
        dollar=1500.0, dollar_last_day=5,
        atr_mult=1.0, atr_start_day=2, atr_last_day=5,
    )


def headline_configs() -> list[ExitConfig]:
    """The comparisons that decided the shipped parameters, plus neighbours."""
    return [
        retired_pre_proveit_config(),
        # Like-for-like FULL-STACK comparisons. Single-rule rows below measure a
        # rule in isolation, which overstates any rule whose saves are also
        # reachable by a faster rule running alongside it. Only these rows answer
        # "what would the agent as a whole have done".
        ExitConfig("PREV STACK (2.0% days 0-1 + flat $500 + 1xATR)",
                   pct=2.0, pct_last_day=1,
                   dollar=500.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK minus dollar stop (1.0% day 0 + 1xATR)",
                   pct=1.0, pct_last_day=0,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK minus thesis stop (1.0% day 0 + $1500)",
                   pct=1.0, pct_last_day=0,
                   dollar=1500.0, dollar_last_day=5),
        ExitConfig("SUPERSEDED STACK, flat $500 dollar stop",
                   pct=1.0, pct_last_day=0,
                   dollar=500.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("STACK, dollar stop at $1000",
                   pct=1.0, pct_last_day=0,
                   dollar=1000.0, dollar_last_day=5,
                   atr_mult=1.0, atr_start_day=2, atr_last_day=5),
        ExitConfig("Kill-switch only: 1.0% day 0", pct=1.0, pct_last_day=0),
        ExitConfig("Kill-switch only: 0.75% day 0", pct=0.75, pct_last_day=0),
        ExitConfig("Kill-switch only: 1.5% day 0", pct=1.5, pct_last_day=0),
        ExitConfig("Kill-switch only: 1.0% days 0-1", pct=1.0, pct_last_day=1),
        ExitConfig("PREVIOUS: 2.0% days 0-1", pct=2.0, pct_last_day=1),
        ExitConfig("Market-sell instead of arming", pct=1.0, pct_last_day=0, mode="market"),
        ExitConfig("Dollar stop only ($500)", dollar=500.0),
        ExitConfig("Thesis stop only (1xATR)", atr_mult=1.0),
    ]


def basetrail_configs() -> list[ExitConfig]:
    """Cost of tightening the always-on base/disaster trailing stop.

    Every row is the shipped Prove-It Stop with one extra thing: a GTC trailing
    stop resting `base_trail` below the HWM underneath all phases — exactly the
    order execution_agent.place_trailing_stop() keeps live. The `None` row is the
    shipped behaviour as the harness models it today (no explicit base trail).
    The 12% row is the live ATR cap; the 5% row is the proposal.

    This measures ONLY the normal-operation cost: how often a tighter base trail
    fires BEFORE the Prove-It floor and clips a trade. The disconnect-survival
    benefit — the whole reason to tighten it — is a reliability property this
    price-path replay cannot see, so a 5% row that merely matches the None row
    here is a WIN (same P&L when connected, far better when disconnected).
    """
    from dataclasses import replace
    base = shipped_proveit()
    out = [replace(base, label="ProveIt SHIPPED (no explicit base trail)")]
    for bt in (0.12, 0.10, 0.08, 0.07, 0.05):
        out.append(replace(
            base, base_trail=bt,
            label=f"ProveIt SHIPPED + base trail {bt*100:.0f}% (always-on GTC)"))
    return out


def grid_configs() -> list[ExitConfig]:
    """Full sweep. Use when a headline result looks unstable."""
    out = []
    for pct in (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        for last_day in (0, 1, 2):
            for mode in ("armed", "market"):
                out.append(ExitConfig(
                    f"pct={pct} day<={last_day} {mode}",
                    pct=pct, pct_last_day=last_day, mode=mode))
    for dollar in (200, 300, 400, 500, 600, 800):
        for last_day in (0, 1, 3, 5):
            out.append(ExitConfig(
                f"${dollar} day<={last_day}", dollar=float(dollar),
                dollar_last_day=last_day))
    for mult in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        for last_day in (4, 5, 6):
            out.append(ExitConfig(
                f"{mult}xATR days 2-{last_day}", atr_mult=mult,
                atr_last_day=last_day))
    return out


def shipped_proveit() -> ExitConfig:
    """The Prove-It STOP as it runs live -- the stop ONLY, NOT the whole live bot.

    Mirrors the PROVE_IT_* constants in execution_agent.py: Phase 1 is 1.0% below
    entry on day 0 and 3.0% from day 1, enforced on the agent's 15-minute CLOSE
    and then armed; Phase 2 arms at a +2.0% peak and floors at -1.0% of entry.

    ⚠️  THIS IS NOT A FULL MODEL OF THE LIVE BOT, AND MUST NOT BE USED AS THE
    BASELINE FOR ANY EXIT-LOOSENING COMPARISON.

    It omits SCALE-OUT, which is ON in production (SCALE_OUT_ENABLED defaults
    True: 33% of the position is sold at +4%). That omission is not neutral --
    it BIASES loosening experiments in their favour. A wider trail earns its
    keep by capturing upside beyond the exit point; scale-out already banks part
    of that upside. Measured against a no-scale-out baseline, a wider trail gets
    credited for money the real bot ALREADY collects, so its apparent gain is
    inflated.

    This is not hypothetical. This docstring previously read "exactly as it now
    runs live", and on 2026-09-18 that sentence caused a widened trail to be
    reported as +$6,429 and called promising. Re-measured against the true live
    configuration (scale-out included) the same change was worth +$4,308, and
    stripping the three largest trades turned it NEGATIVE (-$1,014) -- which
    reversed the recommendation from "ship it" to "do not ship". The figure was
    formally withdrawn. See decisions/2026-09-18_scaleout-runon-bias-and-
    concentration.md.

    For a full-bot baseline use simulate_scaleout() with the shipped
    SCALE_OUT_* values, or the `--runon` path which does this for you.
    """
    return ExitConfig(
        "ProveIt SHIPPED (P1 1.0%/d0 then 3.0%, close+arm; P2 arm2% floor-1%)",
        proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
        p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0)


def cliff_configs() -> list[ExitConfig]:
    """The protection-cliff test: should a proven-but-unarmed position keep the
    Phase 1 band?

    Shipped, a position that closes above entry becomes "proven", which REMOVES
    the Phase 1 entry-anchored band — but it gains no Phase 2 floor until its
    peak reaches +2%. In that window the only protection is the wide base trail,
    so a close one cent above entry strictly WORSENS protection.

    NTRA (8/26 entry 338.43) is the live proof: it closed $0.27 above entry on
    8/27, peaked at +1.40%, never armed, and exited at -5.2% on 8/31. Its floor
    had fallen from 328.28 (Phase 1) to roughly 308 (base trail).

    The risk of closing the hole is clipping winners that dip below the band
    after a marginal green close but before running. `winners_hurt` is the
    column that decides it.
    """
    out = [shipped_proveit()]

    out.append(replace(
        shipped_proveit(),
        label="ProveIt + unarmed keeps P1 band (CLIFF FIX)",
        p2_unarmed_keeps_p1=True))

    # Does the fix depend on the base trail being present underneath?
    for bt in (0.10, 0.07):
        out.append(replace(
            shipped_proveit(),
            label=f"ProveIt SHIPPED + base trail {bt*100:.0f}%",
            base_trail=bt))
        out.append(replace(
            shipped_proveit(),
            label=f"ProveIt + unarmed keeps P1 + base trail {bt*100:.0f}% (CLIFF FIX)",
            p2_unarmed_keeps_p1=True, base_trail=bt))

    # Arming earlier is the alternative way to close the same hole: it shortens
    # the unprotected window instead of flooring it.
    for arm in (1.5, 1.0, 0.5):
        out.append(replace(
            shipped_proveit(),
            label=f"ProveIt + P2 arms at +{arm}% peak (shorter unarmed window)",
            p2_arm_gain=arm))

    # Both levers together.
    out.append(replace(
        shipped_proveit(),
        label="ProveIt + unarmed keeps P1 + arms at +1.0%",
        p2_unarmed_keeps_p1=True, p2_arm_gain=1.0))

    return out


def day0_configs() -> list[ExitConfig]:
    """Day-0 mechanism test: bot-polled-then-armed vs a resting broker stop.

    The shipped Phase 1 is enforced by the agent's 15-minute cycle, so the day-0
    1% band is sampled 26 times a session rather than watched continuously, and a
    breach ARMS a 0.6% trail instead of selling. The resting IBKR order sits 1%
    wider (PROVE_IT_BACKSTOP_SLACK_PCT) precisely so it cannot pre-empt that.

    The alternative is to put the resting order ON the day-0 level: continuous
    enforcement, but wick-sensitive and with no bounce capture. Widening the hard
    band is included because a touch-stop and a close-stop at the same number are
    not the same rule — the touch version must be given room for noise.
    """
    out = [retired_pre_proveit_config(), shipped_proveit()]

    for pct in (1.0, 1.25, 1.5, 2.0, 2.5):
        out.append(ExitConfig(
            f"ProveIt + BROKER-HARD day 0 @ {pct}% (d1+ unchanged 3.0% close+arm)",
            proveit=True, p1_tiers=((0, pct), (99, 3.0)), p1_touch=False,
            p1_hard_max_day=0,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0))

    # Whole of Phase 1 broker-side, to show whether any benefit is specific to
    # day 0 or is really just "hard stops beat armed exits".
    out.append(ExitConfig(
        "ProveIt + BROKER-HARD all Phase 1 days (1.0%/d0 then 3.0%)",
        proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
        p1_hard_max_day=99,
        p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0))

    # Control: same day-0 band, still bot-polled, but selling at market instead
    # of arming. Separates "continuous watching" from "no bounce capture".
    out.append(ExitConfig(
        "ProveIt + day 0 close-triggered MARKET sell (no arming)",
        proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
        mode="market",
        p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0))
    return out


# ── Reporting ─────────────────────────────────────────────────────────────────

def proveit_configs() -> list[ExitConfig]:
    """The Prove-It Stop sweep.

    Every row here REPLACES the kill-switch, dollar stop and thesis stop with a
    single two-phase rule, so these are whole-stack comparisons against the
    exits that actually happened — directly comparable to the SHIPPED row.

    Two things are being measured:
      1. Phase 1 shape — does widening the tier after day 1 help or hurt, and
         does firing on an intraday TOUCH cost more than waiting for a 15-minute
         CLOSE? (INCY is the motivating case: it wicked to -1.84% on day 1 and
         still finished at -1.09%.)
      2. Phase 2 arming gain — how far must a trade advance before a breakeven
         floor is safe to place? Too low and normal noise stops it out.
    """
    out = [retired_pre_proveit_config(), shipped_proveit(), live_baseline()]

    tier_shapes: list[tuple[str, tuple[tuple[int, float], ...]]] = [
        ("1.0%/d0-1 then 1.5%", ((1, 1.0), (99, 1.5))),
        ("1.0%/d0-1 then 2.0%", ((1, 1.0), (99, 2.0))),
        ("1.0%/d0   then 1.5%", ((0, 1.0), (99, 1.5))),
        ("1.0%/d0   then 2.0%", ((0, 1.0), (99, 2.0))),
        # The LIVE shape. Absent from this grid until 2026-09-18, which meant
        # the sweep could not answer its own headline question ("does the
        # shipped configuration still win?") because the shipped configuration
        # was not in it.
        ("1.0%/d0   then 3.0%", ((0, 1.0), (99, 3.0))),
        ("flat 1.0%",           ((99, 1.0),)),
        ("flat 1.5%",           ((99, 1.5),)),
        ("flat 2.0%",           ((99, 2.0),)),
        ("flat 3.0%",           ((99, 3.0),)),
    ]

    for name, tiers in tier_shapes:
        for touch in (False, True):
            mech = "touch" if touch else "close"
            out.append(ExitConfig(
                f"ProveIt P1 {name} [{mech}] + P2 arm2%",
                proveit=True, p1_tiers=tiers, p1_touch=touch,
                p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=0.0))

    # Phase 2 arming sensitivity, held against the LIVE Phase 1 shape.
    # The floor sweep must include -1.0, which is what PROVE_IT_P2_FLOOR_PCT
    # actually is; before 2026-09-18 it tested only 0.0 and +0.5, so the live
    # floor was never scored.
    for arm in (1.0, 1.5, 2.0, 3.0, 4.0):
        for floor in (-1.0, 0.0, 0.5):
            out.append(ExitConfig(
                f"ProveIt P1 1.0/3.0 [close] + P2 arm{arm}% floor{floor:+.1f}%",
                proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
                p2_enabled=True, p2_arm_gain=arm, p2_floor_pct=floor))

    # Isolate each phase so a headline result cannot be misread as coming from
    # the half that did not actually produce it.
    out.append(ExitConfig(
        "ProveIt PHASE 1 ONLY (1.0/1.5, close)",
        proveit=True, p1_tiers=((1, 1.0), (99, 1.5)), p1_touch=False,
        p2_enabled=False))
    out.append(ExitConfig(
        "ProveIt PHASE 2 ONLY (arm 2%, breakeven floor)",
        proveit=True, p1_tiers=None, p2_enabled=True, p2_arm_gain=2.0))

    return out


def ladder_configs() -> list[ExitConfig]:
    """Phase 2 profit-lock give-back sweep (the `TRAIL_PROFIT_TIERS` trail width).

    Everything except the ladder trail is held at the shipped Prove-It Stop, so
    each row differs from `ProveIt SHIPPED` by exactly one number and the gaps
    between rows are attributable to that number alone.

    The ladder only engages once a position has closed above entry AND peaked at
    `p2_ladder_gain` (+5% shipped), so this sweep is scored on a strict subset of
    the trade history. Read the engagement count before reading the dollars: a
    sweep over a handful of trades describes those trades, it does not estimate
    a parameter.

    Tightening this trail is NOT free. Below roughly a third of a stock's own
    5-minute noise the level sits inside the bid/ask and normal wiggle, so it
    stops behaving as a give-back cap and starts behaving as "sell at the first
    pullback" — the same failure `OCA_EXIT_MIN_TRAIL_PCT` guards against on the
    OCA path. The replay fills exactly at the level with no slippage, which
    flatters tight settings, so treat the tightest rows as an upper bound.
    """
    out = [retired_pre_proveit_config(), shipped_proveit()]

    for trail in (0.005, 0.0075, 0.010, 0.0125, 0.015, 0.020, 0.025, 0.030):
        out.append(ExitConfig(
            f"ProveIt SHIPPED + P2 ladder trail {trail * 100:.2f}%",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            p2_ladder_trail=trail))

    # Trail width and the gain at which it arms are not independent: a tighter
    # trail is more defensible if it engages later, once the position has more
    # cushion. Cross them so the sweep cannot recommend a width that only looks
    # good at one arming gain.
    for gain in (4.0, 5.0, 7.0):
        for trail in (0.005, 0.010, 0.015):
            out.append(ExitConfig(
                f"ProveIt SHIPPED + P2 ladder >={gain:.0f}% @ {trail * 100:.1f}%",
                proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
                p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
                p2_ladder_gain=gain, p2_ladder_trail=trail))

    return out


def p1ratchet_configs() -> list[ExitConfig]:
    """Does the Phase 1 broker leg ratcheting up with price cost real money?

    THE DEFECT. exit_rules.py documents the Phase 1 stop as a FIXED floor
    anchored to ENTRY -- "a breakout that fails on day one is wrong immediately
    and cheaply". It is placed as an IBKR TRAIL order
    (execution_agent.place_protective_stops), and a TRAIL anchor ratchets UP
    with the high water mark. The bot's one-way tightening rule then refuses to
    widen it back. So on any position that rallies before it fades, the order
    written to cap a LOSS climbs into profit and fires as a profit-taker.

    SMTC (2026-09-18) is the worked example, and every number reconciles:
        entry $180.50, day-0 band 1%, backstop slack 1%
        intended resting level   $176.91   (entry -2.0%)
        solved trailingPercent    1.72%    <- matches the logged sell_reason
        HWM reached              $185.30   (09:45)
        ratcheted stop  185.30 x (1-0.0172) = $182.11   (entry +0.89%)
        filled                   $181.88   (entry +0.76%, +$138 net)
    A loss cap sold a winner 23 minutes into the trade.

    THE COMPARISON. Both rows are the shipped Prove-It config and both carry the
    resting broker leg. They differ in ONE property -- whether that leg's anchor
    ratchets -- so the whole gap is attributable to the defect.

      A. ratchet ON   -- what the live bot does today
      B. ratchet OFF  -- what exit_rules.py says it does

    `B - A` is the money the defect costs. Read `winners_hurt` and the per-trade
    deltas before the net: the fix should help WINNERS (they stop being sold
    into strength) while leaving losers untouched or slightly better, because a
    pinned leg sits LOWER than a ratcheted one and so cannot fire earlier.
    If instead the fix shows up as losers getting worse, the ratchet is
    accidentally cutting losses and the trade-off is real -- say so.
    """
    out = [retired_pre_proveit_config(), shipped_proveit()]

    for label, ratchet in (("A: ratchet ON  (live behaviour today)", True),
                           ("B: ratchet OFF (documented intent)",   False)):
        out.append(ExitConfig(
            f"P1 broker leg {label}",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            p1_broker_leg=True, p1_ratchet=ratchet, p1_backstop_slack=0.01))

    # Sensitivity: the defect's cost scales with how wide the slack is, because
    # a wider slack means a larger trailingPercent and so a stop that ratchets
    # further above entry. Confirms the result is not an artefact of 1%.
    for slack in (0.005, 0.02):
        out.append(ExitConfig(
            f"P1 ratchet ON, slack {slack * 100:.1f}%",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            p1_broker_leg=True, p1_ratchet=True, p1_backstop_slack=slack))

    return out


def live_baseline() -> ExitConfig:
    """The FULL live bot: the Prove-It stop AND scale-out, which is on in prod.

    This is the only correct baseline for any exit-LOOSENING experiment.
    shipped_proveit() omits scale-out and therefore over-credits a wider trail
    with upside the real bot already banks -- the error that produced the
    withdrawn +$6,429 figure on 2026-09-18. Mirrors SCALE_OUT_* in
    execution_agent.py (enabled, 33% at +4%).
    """
    return replace(shipped_proveit(),
                   label="LIVE BASELINE (ProveIt + scale 33%@+4%)",
                   scale_frac=0.33, scale_trigger=4.0)


def clean_configs() -> list[ExitConfig]:
    """Does a DRAWDOWN-CONDITIONAL ladder rung beat one flat rung?

    The question this answers. The +5% rung is a single knob serving two
    populations: the few positions that keep running, and the many that stall.
    Widening it for everyone was measured on 2026-09-18 and rejected -- the gain
    was carried by one trade and went negative once the top three were removed.

    The hypothesis here is that the two populations are separable AT THE TIME,
    without lookahead, by how far the position has already fallen below entry.
    On the seven trigger-matched trades the split was clean: FRO (+24.7%),
    LPG (+20.8%) and PSX (+13.2%) never traded more than 3.1% below entry, while
    every trade that stalled had already been 8-13% underwater.

    THE CONFOUND, which decides whether any result here means anything: Phase 1
    already exits at -1%/-3% from entry, so a position that SURVIVES to reach the
    rung may be shallow-drawdown almost by construction. If nearly every trade
    qualifies, this rule is just a flat widening wearing a disguise, and it must
    be judged against the flat widening rows below -- which are included here for
    exactly that reason, not as filler.

    Every row is scored against live_baseline(), scale-out included.
    """
    out = [live_baseline()]

    # Flat widening -- the null hypothesis. If a conditional row cannot beat
    # these, the condition is adding nothing.
    for trail in (0.03, 0.05):
        out.append(replace(live_baseline(),
                           label=f"FLAT rung {trail*100:.0f}% (no condition)",
                           p2_ladder_trail=trail))

    # The conditional rows.
    for dd in (2.0, 3.0, 5.0):
        for trail in (0.03, 0.05, 0.08):
            out.append(replace(
                live_baseline(),
                label=f"CLEAN dd<={dd:.0f}% -> rung {trail*100:.0f}% (else 1.5%)",
                clean_dd_pct=dd, clean_ladder_trail=trail))
    return out


def clean_qualifying_rate(trades: list[Trade], dd_pct: float) -> tuple[int, int]:
    """How many trades would the `clean` condition actually fire on?

    Without this the sweep cannot be read: a conditional rule that qualifies 100%
    of trades is a flat widening, and one that qualifies 0% is a no-op. Both
    would score identically to something they are not.

    Returns (qualified, total) counted at the moment each trade FIRST reaches the
    ladder rung, which is the only point the condition is consulted.
    """
    qualified = total = 0
    for trade in trades:
        entry = trade.buy_price
        peak = entry
        max_dd = 0.0
        reached = False
        for bar in trade.bars:
            if bar["ts"] < trade.buy_ts:
                continue
            peak = max(peak, bar["high"])
            if (peak / entry - 1) * 100.0 >= 5.0:
                reached = True
                break
            dd = (entry - bar["low"]) / entry * 100.0
            max_dd = max(max_dd, dd)
        if reached:
            total += 1
            if max_dd <= dd_pct:
                qualified += 1
    return qualified, total


def runon_configs() -> list[ExitConfig]:
    """"Let winners run" — the loosening direction, scored WITHOUT truncation.

    This is the only sweep in the file that requires `--runon-days`, and the
    reason is a methodological flaw that invalidates every earlier attempt to
    answer this question.

    Every other mode fetches bars only up to the REAL exit. A configuration that
    would have held LONGER than the live rule therefore runs out of price
    history at the moment the live rule sold, `simulate_proveit` returns None,
    and `score()` books a delta of exactly zero — it is handed the realised exit
    price for free. Tighter rules are unaffected (they fire before the truncation
    point, so their fill is real), but looser rules can ONLY pay off in the bars
    that truncation deletes. The result is a sweep that is structurally incapable
    of showing a benefit from holding longer, which is precisely what the
    2026-09-18 `--ladder` run showed: every rung looser than the shipped 1.5%
    scored worse, monotonically, with no upside term in the arithmetic at all.

    With the run-on window the comparison becomes honest in both directions: a
    looser rule keeps trading through the real exit date and is marked out
    either where its own rule fires or at the last available close.

    Three questions, in order:

      A. Is the shipped ladder too tight? Now that a looser trail can actually
         capture post-exit upside, re-run the same widths the truncated sweep
         rejected. A width that still loses here is genuinely too loose.

      B. Is POWER_HOLD_GAIN_PCT = 10% reachable, and does the rule pay? It has
         never fired in live trading and no previous replay could see it.
         Sweeping the trigger down (10% -> 7% -> 5%) answers reachability and
         value together.

      C. What does pure patience cost? `NO EXIT RULE` holds every position to
         the end of the run-on window. It is not a proposal — it is the ceiling,
         and the honest denominator for A and B. If the best rule captures only
         a sliver of it, the ladder is not the binding constraint.
    """
    out = [shipped_proveit()]

    # A. Ladder widths, including ones far looser than the truncated sweep could
    #    fairly score.
    for trail in (0.015, 0.02, 0.03, 0.05, 0.08):
        out.append(ExitConfig(
            f"RunOn: P2 ladder trail {trail * 100:g}%",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            p2_ladder_trail=trail))

    # B. Power hold at the shipped 10% trigger and below it.
    for gain in (10.0, 7.0, 5.0):
        out.append(ExitConfig(
            f"RunOn: SHIPPED + power hold >={gain:.0f}% @ 30% trail",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            power_hold_gain=gain, power_hold_trail=0.30))

    # Power hold with a trail tight enough to actually protect the gain. 30% is
    # a disaster backstop, not a give-back rule; if the wide trail wins only by
    # never firing, that is worth knowing separately.
    for trail in (0.10, 0.15):
        out.append(ExitConfig(
            f"RunOn: SHIPPED + power hold >=10% @ {trail * 100:.0f}% trail",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            power_hold_gain=10.0, power_hold_trail=trail))

    # C. The ceiling. Phase 1 still cuts losers; Phase 2 never sells.
    out.append(ExitConfig(
        "RunOn: CEILING — Phase 1 only, winners never sold",
        proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
        p2_enabled=False))

    return out


def runon_reachability(trades: list[Trade]) -> None:
    """Answer the reachability question directly, before any dollar figure.

    POWER_HOLD_GAIN_PCT was lowered from 20% to 10% purely because no trade in
    the replay had ever reached 20% — a judgement about reachability, not a
    measured optimum, and the comment in exit_rules.py says so. With run-on bars
    the question is finally answerable: how far did each position ACTUALLY get
    within the trigger window, whether or not the bot was still holding it?
    """
    rows = []
    for trade in trades:
        if not trade.runon_from:
            continue
        cutoff = trade.buy_ts.date() + dt.timedelta(days=21)
        peak_in_window = max(
            (b["high"] for b in trade.bars if b["ts"].date() <= cutoff),
            default=trade.buy_price)
        peak_all = max(b["high"] for b in trade.bars)
        held_peak = max((b["high"] for b in trade.bars[:trade.runon_from]),
                        default=trade.buy_price)
        rows.append({
            "ticker": trade.ticker,
            "pl": trade.profit_loss,
            "held_peak": (held_peak / trade.buy_price - 1) * 100.0,
            "peak_21d": (peak_in_window / trade.buy_price - 1) * 100.0,
            "peak_all": (peak_all / trade.buy_price - 1) * 100.0,
        })
    if not rows:
        return
    rows.sort(key=lambda r: -r["peak_21d"])

    print("=" * 92)
    print("POWER HOLD REACHABILITY — peak gain within 21 calendar days of "
          "ENTRY, ignoring when we sold")
    print("=" * 92)
    print(f"{'ticker':<8}{'realised P/L':>14}{'peak WHILE HELD':>18}"
          f"{'peak <=21d':>13}{'peak in window':>17}")
    for row in rows[:20]:
        print(f"  {row['ticker']:<6}{row['pl']:>14,.2f}"
              f"{row['held_peak']:>17.2f}%{row['peak_21d']:>12.2f}%"
              f"{row['peak_all']:>16.2f}%")
    for threshold in (5.0, 7.0, 10.0, 15.0, 20.0):
        hit = sum(1 for r in rows if r["peak_21d"] >= threshold)
        held = sum(1 for r in rows if r["held_peak"] >= threshold)
        print(f"  reached +{threshold:>4.0f}% within 21d: {hit:>3}/{len(rows)} "
              f"trades   (while still held: {held})")
    print()


def ratchet_configs() -> list[ExitConfig]:
    """The GNK/NTRA give-back sweep — the +2% to +5% 'winner rounds to a loss' band.

    Motivation: GNK peaked +2.85% and NTRA +4.59%, both BELOW the +5% profit-lock
    arming gain, so the only Phase-2 protection either had was the give-back floor
    sitting at entry-1% (a loss cap, by design below breakeven to avoid retest
    flushing — see decisions/2026-09-04_prove-it-stop.md). Result: a real winner is
    allowed to round-trip into a ~-1% loss.

    Every row starts from the EXACT shipped Prove-It config (shipped_proveit) and
    changes ONE give-back lever, so each gap is attributable to that number alone:

      A. Raise the Phase-2 floor from entry-1% toward breakeven / above it.
         This is the direct answer to "stop giving back below +5%", but it fights
         the anti-retest-flush rationale, so winners_hurt is the number that
         decides it.
      B. Arm the tight 1.5% profit-lock ladder EARLIER (+5% -> +4% / +3%), so a
         +3-4.6% peak engages a real trailing lock instead of only the loss floor.

    Read `harmed` (losers_hurt + winners_hurt) and the per-trade deltas before the
    net: a floor raised into normal noise pays for itself by shaking out trades
    that would have recovered, and on ~30 trades one shaken-out winner can carry
    the whole net.
    """
    out = [retired_pre_proveit_config(), shipped_proveit()]

    # A. Give-back floor: entry-1% (shipped) -> -0.5% -> breakeven -> +0.5%.
    for floor in (-1.0, -0.5, 0.0, 0.5):
        out.append(ExitConfig(
            f"Ratchet A: floor {floor:+.1f}% (arm2%, ladder>=5%)",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=floor))

    # B. Earlier profit-lock: arm the tight 1.5% ladder at +3% / +4% instead of +5%.
    for gain in (3.0, 4.0, 5.0):
        out.append(ExitConfig(
            f"Ratchet B: ladder>={gain:.0f}% @1.5% (floor-1%)",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
            p2_ladder_gain=gain, p2_ladder_trail=0.015))

    # A+B combined: breakeven floor AND an earlier +3% ladder — the most
    # aggressive winner-protection, most likely to clip. Measure it explicitly.
    for gain in (3.0, 4.0):
        out.append(ExitConfig(
            f"Ratchet A+B: floor 0.0% + ladder>={gain:.0f}% @1.5%",
            proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
            p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=0.0,
            p2_ladder_gain=gain, p2_ladder_trail=0.015))

    return out


def scale_configs() -> list[ExitConfig]:
    """Partial scale-out sweep — the one lever that can cut give-back WITHOUT the
    winner-clip tax that sank every stop-level ratchet.

    Baseline is the shipped Prove-It stop (whole position, no scale). Every scale
    row books `frac` of the position at a +trigger% limit and lets the remainder
    ride the SAME Prove-It rule; the `+be` rows additionally lift the remainder's
    floor to breakeven once the scale has filled (a 'free trade' on the runner).

    Read this against its own limitation (see simulate_scaleout): the remainder
    cannot run past each trade's actual sell date, so the net UNDERSTATES the
    runner's upside. If scale-out still holds net roughly level with SHIPPED here,
    that is the floor of its value, and it is buying give-back protection on the
    fraction for free. `harmed` and the per-trade deltas still decide it.
    """
    out = [shipped_proveit()]

    for trigger in (3.0, 4.0, 5.0):
        for frac in (0.25, 0.33, 0.50):
            out.append(ExitConfig(
                f"Scale {int(frac*100)}% @ +{trigger:.0f}% | remainder ProveIt",
                proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
                p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
                scale_frac=frac, scale_trigger=trigger))
            out.append(ExitConfig(
                f"Scale {int(frac*100)}% @ +{trigger:.0f}% | remainder breakeven",
                proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
                p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
                scale_frac=frac, scale_trigger=trigger, scale_be_remainder=True))

    return out


def eod_configs() -> list[ExitConfig]:
    """End-of-day give-back vs the shipped intraday trail.

    These are NOT tighter settings of the shipped rule — they are a different
    mechanism. The shipped 1.5% is a resting IBKR trail triggered by the LOW of
    any bar, so it cannot be tightened far without firing on wicks. An EOD test
    reads only the close, which is why a much tighter band is arguable at all.

    What the sweep must expose, and why each arm is here:

      * `anchor` — an EOD rule anchored to the highest intraday HIGH measures a
        close against a price that may only have existed inside a wick, which
        smuggles the wick sensitivity straight back in. Anchoring to the highest
        CLOSE is the internally consistent choice. Both are run because the live
        `high_water_mark` column stores the intraday high, so the intraday
        anchor is what a naive implementation would actually ship.
      * `backstop` — between two closes an EOD-only rule offers no ladder
        protection whatsoever. A position can round-trip a 9% gain intraday and
        the rule will not look. Rows with a backstop keep a wider resting stop
        underneath; rows without show what the unprotected version really costs.

    The shipped intraday ladder is included so every row is a like-for-like
    comparison rather than a comparison against the raw realised exits.
    """
    out = [shipped_proveit(),
           ExitConfig(
               "INTRADAY ladder 1.5% (shipped rung)",
               proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
               p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
               p2_ladder_trail=0.015)]

    for anchor in ("close", "intraday"):
        for trail in (0.005, 0.0075, 0.010, 0.015, 0.020):
            for backstop in (None, 0.03):
                back = "none" if backstop is None else f"{backstop * 100:.0f}%"
                out.append(ExitConfig(
                    f"EOD {trail * 100:.2f}% [{anchor} anchor, backstop {back}]",
                    proveit=True, p1_tiers=((0, 1.0), (99, 3.0)), p1_touch=False,
                    p2_enabled=True, p2_arm_gain=2.0, p2_floor_pct=-1.0,
                    p2_eod=True, p2_eod_trail=trail, p2_eod_anchor=anchor,
                    p2_eod_backstop=backstop))

    return out


# ── Slot opportunity cost ─────────────────────────────────────────────────────
#
# Every other mode in this file scores each trade in ISOLATION: the delta for a
# "hold longer" rule is (later_price - real_sell_price) * shares, credited as if
# the extra holding time were free. It is not. The book holds at most
# MAX_POSITIONS positions at once, so a winner held N extra days occupies one of
# five slots for those N days, and during that window the bot could NOT have
# taken some of the entries it historically did. Those foregone entries had their
# own P&L. The honest value of "let winners run" is therefore:
#
#     (extra upside captured on the held winners)
#   - (P&L of the real entries that a still-occupied slot would have blocked)
#
# The 2026-09-18 run-on analysis flagged this as "the decisive term" and left it
# unmodelled; the ladder-5% result there was +$5,685 naive but "slot opportunity
# cost is entirely unmodelled". This model supplies exactly that term.
#
# It is a GREEDY entry-ordered portfolio simulation, not a global optimiser: it
# walks the real entries in the order the bot actually bought them and, at each
# entry, takes the position only if a slot is free under the counterfactual exit
# rule, otherwise the entry is blocked. That mirrors how the live agent operates
# (buy when a trigger fires and a slot is open) and avoids a fragile combinatorial
# cascade. It is a first-order model; see the printed caveats.


@dataclass
class Position:
    """A distinct portfolio slot occupant: one entry and the shares bought under
    it, with every scale-out partial of that same entry collapsed back in.

    trade_history records a scaled-out position as several rows that share an
    entry but sell at different times (e.g. NTRA RT1/RT2/RT3, TRV's same-day
    partial + remainder). Left as separate rows they inflate concurrency — two
    partials of one position look like two occupied slots — and the whole slot
    model turns on concurrency being counted correctly. A slot is occupied by a
    TICKER, so same-ticker rows whose holding intervals overlap are one position.
    """
    ticker: str
    buy_ts: dt.datetime
    real_sell_ts: dt.datetime
    shares: int
    buy_price: float
    real_sell_price: float
    realized_pl: float
    sim_trade: Trade  # representative Trade (widest bar window) for replaying cfg


def merge_positions(trades: list[Trade]) -> list[Position]:
    """Collapse scale-out partials into parent positions.

    Two trades of the same ticker belong to one position iff their
    [buy_ts, real_sell_ts] holding intervals overlap. The live bot never holds
    two distinct positions in the same ticker at once (a slot is keyed by
    ticker), so any same-ticker overlap IS a partial, never a second position.
    Sequential re-entries (sell #1 strictly before buy #2) do not overlap and
    stay separate — which is correct, they are distinct slot occupancies.
    """
    positions: list[Position] = []
    by_ticker: dict[str, list[Trade]] = {}
    for t in trades:
        by_ticker.setdefault(t.ticker, []).append(t)

    for ticker, rows in by_ticker.items():
        rows.sort(key=lambda t: t.buy_ts)
        cluster: list[Trade] = []
        cluster_end: dt.datetime | None = None
        for t in rows:
            if cluster and t.buy_ts <= cluster_end:
                cluster.append(t)
                cluster_end = max(cluster_end, t.sell_ts)
            else:
                if cluster:
                    positions.append(_fold_cluster(cluster))
                cluster = [t]
                cluster_end = t.sell_ts
        if cluster:
            positions.append(_fold_cluster(cluster))

    positions.sort(key=lambda p: p.buy_ts)
    return positions


def _fold_cluster(cluster: list[Trade]) -> Position:
    """Aggregate one cluster of same-position partials into a Position."""
    shares = sum(t.shares for t in cluster)
    # Share-weighted entry and exit, so the delta math in the portfolio sim lines
    # up with a single blended fill. For a true scale-out every partial shares
    # the same entry, so this reduces to that entry.
    wentry = sum(t.buy_price * t.shares for t in cluster) / shares
    wexit = sum(t.sell_price * t.shares for t in cluster) / shares
    # The representative trade for replaying a config is the partial that sold
    # LAST — it carries the widest bar window (its run-on reaches furthest past
    # the exit), which is exactly the history a "hold longer" rule needs.
    rep = max(cluster, key=lambda t: len(t.bars))
    sim_trade = replace(
        rep,
        buy_ts=min(t.buy_ts for t in cluster),
        sell_ts=max(t.sell_ts for t in cluster),
        shares=shares,
        buy_price=wentry,
        sell_price=wexit,
        profit_loss=sum(t.profit_loss for t in cluster),
    )
    return Position(
        ticker=rep.ticker,
        buy_ts=sim_trade.buy_ts,
        real_sell_ts=sim_trade.sell_ts,
        shares=shares,
        buy_price=wentry,
        real_sell_price=wexit,
        realized_pl=sum(t.profit_loss for t in cluster),
        sim_trade=sim_trade,
    )


def slot_concurrency(positions: list[Position]) -> int:
    """Max simultaneously-held positions across the real timeline. After merging
    partials this MUST be <= MAX_POSITIONS; a higher number means the merge was
    incomplete or the data has an anomaly, and the caller reports it loudly."""
    events: list[tuple[dt.datetime, int]] = []
    for p in positions:
        events.append((p.buy_ts, +1))
        # A sell frees the slot; order a same-instant sell BEFORE a buy so a
        # hand-off on the same timestamp does not read as a phantom overlap.
        events.append((p.real_sell_ts, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _sim_position_exit(pos: Position, cfg: ExitConfig) -> dict:
    """Counterfactual exit for one position under `cfg`: the fill price, the
    timestamp the slot frees, and the P&L delta vs the real blended exit."""
    trade = pos.sim_trade
    if cfg.scale_frac is not None:
        sim = simulate_scaleout(trade, cfg)
    elif cfg.proveit:
        sim = simulate_proveit(trade, cfg)
    else:
        sim = simulate(trade, cfg)
    if sim is None:
        # Nothing fired. With a run-on window the position is still open at the
        # end of the extended history: mark it out at the last close, slot held
        # to the last bar. Without run-on it exits at the real fill (delta 0).
        if trade.runon_from and trade.runon_from < len(trade.bars):
            sim = {"price": trade.bars[-1]["close"], "reason": "runon_open",
                   "ts": trade.bars[-1]["ts"]}
        else:
            sim = {"price": pos.real_sell_price, "reason": "held",
                   "ts": pos.real_sell_ts}
    exit_ts = sim.get("ts") or pos.real_sell_ts
    delta = round((sim["price"] - pos.real_sell_price) * pos.shares, 2)
    return {"exit_ts": exit_ts, "price": sim["price"], "reason": sim["reason"],
            "delta": delta, "cf_pl": pos.realized_pl + delta}


def _run_portfolio(order: list[Position], exit_ts_of: dict[int, dt.datetime],
                   pl_of: dict[int, float], capacity: int) -> dict:
    """Greedy capacity-constrained walk of the entry stream in buy order.

    At each entry, slots whose exit has already passed are freed; if a slot is
    then free the position is TAKEN (its P&L counts and it holds a slot until its
    exit), otherwise it is BLOCKED (counts nothing). Returns the realised total
    and the taken/blocked splits.
    """
    held: list[dt.datetime] = []  # exit timestamps of currently-occupied slots
    total = 0.0
    taken: list[Position] = []
    blocked: list[Position] = []
    for pos in order:
        held = [ts for ts in held if ts > pos.buy_ts]
        if len(held) < capacity:
            held.append(exit_ts_of[id(pos)])
            total += pl_of[id(pos)]
            taken.append(pos)
        else:
            blocked.append(pos)
    return {"total": round(total, 2), "taken": taken, "blocked": blocked}


def score_slotcost(positions: list[Position], cfg: ExitConfig) -> dict[str, Any]:
    """Score one configuration WITH the slot opportunity cost charged.

    baseline   : real exits, every position taken (the book never exceeded the
                 cap, so this reproduces the realised total and anchors the A/B).
    counterfac : cfg exits under the MAX_POSITIONS constraint. Holding winners
                 longer keeps slots busy, which can block later real entries.

    slot_aware_net = counterfac_total - baseline_total
    naive_net      = sum of per-position deltas, i.e. what the isolated score()
                     would credit if every entry could always be taken.
    slot_cost      = naive_net - slot_aware_net = the cf P&L of blocked entries.
    """
    order = sorted(positions, key=lambda p: p.buy_ts)

    base_exit = {id(p): p.real_sell_ts for p in positions}
    base_pl = {id(p): p.realized_pl for p in positions}
    baseline = _run_portfolio(order, base_exit, base_pl, MAX_POSITIONS)

    exits = {id(p): _sim_position_exit(p, cfg) for p in positions}
    cf_exit = {id(p): exits[id(p)]["exit_ts"] for p in positions}
    cf_pl = {id(p): exits[id(p)]["cf_pl"] for p in positions}
    counterfac = _run_portfolio(order, cf_exit, cf_pl, MAX_POSITIONS)

    naive_net = round(sum(exits[id(p)]["delta"] for p in positions), 2)
    slot_aware_net = round(counterfac["total"] - baseline["total"], 2)
    blocked = counterfac["blocked"]
    blocked_rows = sorted(
        ({"ticker": p.ticker,
          "cf_pl": round(cf_pl[id(p)], 2),
          "buy_ts": p.buy_ts.date().isoformat()} for p in blocked),
        key=lambda r: r["cf_pl"])

    return {
        "label": cfg.label,
        "config": cfg.describe(),
        "baseline_total": baseline["total"],
        "cf_total": counterfac["total"],
        "naive_net": naive_net,
        "slot_aware_net": slot_aware_net,
        "slot_cost": round(naive_net - slot_aware_net, 2),
        "n_blocked": len(blocked),
        "blocked": blocked_rows,
        "baseline_blocked": len(baseline["blocked"]),
    }


def report_slotcost(results: list[dict], positions: list[Position],
                    concurrency: int, top: int | None = None) -> None:
    baseline_total = results[0]["baseline_total"] if results else 0.0
    print()
    print("=" * 100)
    print("SLOT OPPORTUNITY COST — 'let winners run', with the blocked entries "
          "charged against it")
    print("=" * 100)
    print(f"Distinct positions (partials merged) : {len(positions)}")
    print(f"Peak concurrent positions (real)     : {concurrency}  "
          f"(cap MAX_POSITIONS={MAX_POSITIONS})")
    if concurrency > MAX_POSITIONS:
        print(f"  ⚠  peak {concurrency} EXCEEDS the cap — partial merge is "
              f"incomplete or the data has an anomaly. Slot costs below are")
        print(f"     overstated; investigate before trusting any row.")
    else:
        print(f"  ✓  within cap: the merge is consistent with the live book "
              f"never holding more than {MAX_POSITIONS} positions.")
    anomalous = [r for r in results if r["baseline_blocked"] > 0]
    if anomalous:
        print(f"  ⚠  baseline (real exits) blocked {anomalous[0]['baseline_blocked']} "
              f"entrie(s) — the timeline disagrees with reality; treat with care.")
    print(f"Realised total (baseline anchor)     : ${baseline_total:,.2f}")
    print("=" * 100)
    print()
    print("naive_net  = upside from holding longer, slots assumed FREE (what the")
    print("             isolated per-trade score credits).")
    print("slot_cost  = net counterfactual P&L of the real entries a longer hold")
    print("             would have BLOCKED. POSITIVE = the blocked entries were net")
    print("             winners, so the naive gain was partly paid back. NEGATIVE =")
    print("             the blocked entries were net losers, so blocking them AVOIDED")
    print("             losses and slot_net exceeds naive_net.")
    print("slot_net   = naive_net - slot_cost = the HONEST value of the change.")
    print()
    header = (f"{'configuration':<46}{'naive_net':>12}{'slot_cost':>12}"
              f"{'slot_net':>12}{'blocked':>9}")
    print(header)
    print("-" * len(header))
    ordered = sorted(results, key=lambda r: r["slot_aware_net"], reverse=True)
    if top:
        ordered = ordered[:top]
    for res in ordered:
        print(f"{res['label'][:45]:<46}"
              f"{res['naive_net']:>12,.0f}"
              f"{res['slot_cost']:>12,.0f}"
              f"{res['slot_aware_net']:>12,.0f}"
              f"{res['n_blocked']:>9}")
    print()
    print("  A large POSITIVE slot_cost means the naive 'winners run' number was")
    print("  mostly paid back by blocked winners — treat that config with suspicion.")
    print()
    # Concentration + blocked detail for the best NON-baseline row, mirroring the
    # standing rule that a result carried by one trade has not won.
    non_base = [r for r in ordered if r["naive_net"] != 0.0 or r["n_blocked"]]
    best = non_base[0] if non_base else (ordered[0] if ordered else None)
    if best:
        print(f"Blocked-entry detail for: {best['label']}")
        print(f"  slot_net ${best['slot_aware_net']:+,.2f}  "
              f"= naive ${best['naive_net']:+,.2f} - slot_cost "
              f"${best['slot_cost']:+,.2f}  over {best['n_blocked']} blocked entrie(s)")
        if not best["blocked"]:
            print("  (no entry was blocked — this config holds no longer than the "
                  "real book, so it costs no slots)")
        for row in best["blocked"]:
            print(f"    {row['ticker']:<8} entry {row['buy_ts']}   "
                  f"forgone cf P/L ${row['cf_pl']:>10,.2f}")
    print()


def report(results: list[dict], trades: list[Trade], top: int | None = None,
           detail_label: str | None = None) -> None:
    losers = [t for t in trades if t.is_loser]
    total_loss = sum(t.profit_loss for t in losers)
    print()
    print("=" * 92)
    print(f"Closed trades replayed : {len(trades)}  "
          f"({len(losers)} losers, {len(trades) - len(losers)} winners)")
    print(f"Total realised loss    : ${total_loss:,.2f}")
    if len(trades) < 30:
        print()
        print(f"  ⚠  n={len(trades)} is small. Treat differences under ~$500, or driven by")
        print("     fewer than 3 trades, as noise. Check the per-trade deltas below.")
    print("=" * 92)
    print()
    print("All figures are deltas against the exits that ACTUALLY happened.")
    print("A positive net means the configuration would have made more money.")
    print()
    header = (f"{'configuration':<50}{'losers':>9}{'winners':>9}"
              f"{'NET':>10}{'worst':>9}{'>300':>6}{'harmed':>8}")
    print(header)
    print("-" * len(header))

    ordered = sorted(results, key=lambda r: r["net"], reverse=True)
    if top:
        ordered = ordered[:top]
    for res in ordered:
        harmed = res["losers_hurt"] + res["winners_hurt"]
        print(f"{res['label'][:49]:<50}"
              f"{res['loser_net']:>9,.0f}"
              f"{res['winner_delta']:>9,.0f}"
              f"{res['net']:>10,.0f}"
              f"{res.get('worst_loss', 0):>9,.0f}"
              f"{res.get('over_300', 0):>6}"
              f"{harmed:>8}")

    print()
    print("  worst = largest single-trade loss under that configuration")
    print("  >300  = number of trades losing more than $300")
    print()
    # AGENTS.md requires every review to answer "is any result carried by a
    # single trade?". That question is about the configuration being CONSIDERED,
    # which is rarely the top row -- the top row is usually an unshippable
    # ceiling. Without this the concentration check silently gets skipped, or
    # gets answered from the wrong config's numbers.
    best = ordered[0]
    if detail_label:
        matches = [r for r in ordered
                   if detail_label.lower() in r["label"].lower()]
        if not matches:
            print(f"  !! --detail '{detail_label}' matched no configuration. "
                  f"Showing the top row instead.")
        else:
            best = matches[0]
    print(f"Per-trade detail for: {best['label']}")
    print("(watch for a result carried by one trade):")
    if not best["per_trade"]:
        print("  (no trade would have exited differently)")
    rows = best["per_trade"]
    for row in rows:
        sign = "+" if row["delta"] >= 0 else ""
        result = row.get("result_pl")
        tail = f"   -> ${result:>9,.2f}" if result is not None else ""
        print(f"  {row['ticker']:<8} actual ${row['actual_pl']:>10,.2f}   "
              f"delta {sign}${row['delta']:>10,.2f}{tail}   [{row['reason']}]")
    if rows:
        total = sum(r["delta"] for r in rows)
        gains = sorted((r for r in rows if r["delta"] > 0),
                       key=lambda r: -r["delta"])
        print()
        print(f"  CONCENTRATION  net ${total:+,.2f} over {len(rows)} changed trades")
        if total > 0 and gains:
            top = gains[0]
            print(f"    largest single contributor: {top['ticker']} "
                  f"${top['delta']:+,.2f} ({top['delta'] / total * 100:.0f}% of net)")
            cum = 0.0
            for n, r in enumerate(gains, 1):
                cum += r["delta"]
                if cum >= total:
                    print(f"    top {n} trade(s) account for 100% of the net")
                    break
            ex = total - top["delta"]
            print(f"    net excluding the largest: ${ex:+,.2f}")
            print(f"    trades helped {len(gains)}, harmed "
                  f"{len([r for r in rows if r['delta'] < 0])}")
    print()


def jackknife(trades: list[Trade], configs: list[ExitConfig],
              baseline: ExitConfig) -> list[dict[str, Any]]:
    """Leave-one-out fragility test of every challenger against `baseline`.

    The standing failure mode of every loosening result so far is that it is
    "carried by one trade" — ladder 5% beats shipped by +$5,685 but ECO alone is
    +$3,794 of it (decisions/2026-09-18_runon-window-winners-run.md). AGENTS.md
    makes "is any result carried by a single trade?" a mandatory review question.
    This answers it arithmetically instead of by eyeballing the per-trade table.

    `net` is exactly the sum of every trade's delta (see score()), so the edge of
    a challenger over the baseline is additive per trade:

        edge_i = delta(challenger, trade_i) - delta(baseline, trade_i)
        full_edge = Σ edge_i = challenger.net - baseline.net

    Because it is additive, leave-one-out is closed-form — dropping trade j simply
    removes edge_j from the sum, so the worst single trade to lose is the one with
    the largest positive edge. We report:

      • full_edge                — the headline advantage over shipped
      • edge after dropping the single best-contributing trade  (leave-one-out)
      • edge after dropping the best THREE  (AGENTS.md: an edge must be spread
        over 3+ trades to count)
      • the three trades carrying the most edge, with their share

    A challenger is only a real candidate if full_edge > ~$500 AND it stays
    positive after leave-one-out AND no single trade is most of it.
    """
    base_deltas = [_trade_delta(t, baseline)[0] for t in trades]

    def _tid(t: Trade) -> str:
        return f"{t.ticker} {t.buy_ts:%y-%m-%d %H:%M}"

    out = []
    for cfg in configs:
        if cfg.label == baseline.label:
            continue
        cand_deltas = [_trade_delta(t, cfg)[0] for t in trades]
        edges = [(round(c - b, 2), _tid(t))
                 for c, b, t in zip(cand_deltas, base_deltas, trades)]
        full_edge = round(sum(e for e, _ in edges), 2)
        # Sorted most-favorable-to-the-challenger first: these are the trades
        # whose removal hurts the edge most.
        ranked = sorted(edges, key=lambda e: e[0], reverse=True)
        drop1 = round(full_edge - ranked[0][0], 2) if ranked else full_edge
        drop3 = round(full_edge - sum(e for e, _ in ranked[:3]), 2)
        top_contrib = [{"trade": tid, "edge": e} for e, tid in ranked[:3]]
        top1_share = (ranked[0][0] / full_edge * 100.0
                      if full_edge > 0 and ranked else None)
        # Per-ROW concentration understates a multi-leg position: NTRA exits in
        # five round trips, so no single row can be >50% of the edge even when
        # the NAME carries all of it. Aggregate by ticker for the honest check.
        by_ticker: dict[str, float] = {}
        for (e, _), t in zip(edges, trades):
            by_ticker[t.ticker] = round(by_ticker.get(t.ticker, 0.0) + e, 2)
        ranked_names = sorted(by_ticker.items(), key=lambda kv: kv[1], reverse=True)
        top_name, top_name_edge = (ranked_names[0] if ranked_names else ("", 0.0))
        top_name_share = (top_name_edge / full_edge * 100.0
                          if full_edge > 0 else None)
        n_positive = sum(1 for e, _ in edges if e > 0.005)
        n_negative = sum(1 for e, _ in edges if e < -0.005)
        out.append({
            "label": cfg.label,
            "full_edge": full_edge,
            "drop1": drop1,          # leave-one-out worst case
            "drop3": drop3,          # leave-three-out worst case
            "top_contrib": top_contrib,
            "top1_share": round(top1_share, 1) if top1_share is not None else None,
            "top_name": top_name,
            "top_name_edge": top_name_edge,
            "top_name_share": (round(top_name_share, 1)
                               if top_name_share is not None else None),
            "n_helped": n_positive,
            "n_hurt": n_negative,
        })
    return sorted(out, key=lambda r: r["full_edge"], reverse=True)


def report_jackknife(trades: list[Trade], configs: list[ExitConfig],
                     baseline: ExitConfig, top: int | None = None) -> list[dict]:
    results = jackknife(trades, configs, baseline)
    losers = [t for t in trades if t.is_loser]
    print()
    print("=" * 92)
    print("JACKKNIFE (leave-one-out) — is any loosening edge carried by one trade?")
    print("=" * 92)
    print(f"Closed trades replayed : {len(trades)}  "
          f"({len(losers)} losers, {len(trades) - len(losers)} winners)")
    print(f"Baseline               : {baseline.label}")
    if len(trades) < 30:
        print()
        print(f"  ⚠  n={len(trades)} is small. Even a leave-one-out that survives is")
        print("     thin evidence until the sample and a second regime grow.")
    print("=" * 92)
    print()
    print("Every figure is a delta vs the BASELINE above (not vs realised exits).")
    print("  full   = edge over baseline across all trades (= challenger.net - baseline.net)")
    print("  -1     = edge after DROPPING the single best-contributing trade")
    print("  -3     = edge after dropping the best THREE trades")
    print("  top1%  = share of the edge carried by its single best trade")
    print("A challenger only counts if it clears ~$500 AND stays positive at -1")
    print("AND no single trade is most of it (AGENTS.md: spread over 3+ trades).")
    print()
    header = (f"{'challenger configuration':<44}{'full':>9}{'-1':>9}"
              f"{'-3':>9}{'top1%':>7}{'helped':>8}{'hurt':>6}")
    print(header)
    print("-" * len(header))
    ordered = results[:top] if top else results
    for res in ordered:
        share = f"{res['top1_share']:.0f}" if res['top1_share'] is not None else "-"
        print(f"{res['label'][:43]:<44}"
              f"{res['full_edge']:>9,.0f}"
              f"{res['drop1']:>9,.0f}"
              f"{res['drop3']:>9,.0f}"
              f"{share:>7}"
              f"{res['n_helped']:>8}"
              f"{res['n_hurt']:>6}")
    print()

    # Verdict on the best positive-edge challenger — the one a review would
    # actually consider shipping — stated in the four-question language.
    candidates = [r for r in results if r["full_edge"] > 0]
    if not candidates:
        print("No challenger beats the baseline. Nothing to jackknife further.")
        print()
        return results
    best = candidates[0]
    print(f"Best challenger: {best['label']}")
    print(f"  full edge over baseline    : ${best['full_edge']:+,.2f}")
    print(f"  after dropping best 1 trade : ${best['drop1']:+,.2f}   (leave-one-out)")
    print(f"  after dropping best 3 trades: ${best['drop3']:+,.2f}")
    if best["top_contrib"]:
        print("  edge carried by:")
        for c in best["top_contrib"]:
            print(f"     {c['trade']:<20} ${c['edge']:+,.2f}")
    if best["top_name_share"] is not None:
        print(f"  single biggest NAME: {best['top_name']} "
              f"${best['top_name_edge']:+,.2f} "
              f"({best['top_name_share']:.0f}% of the edge across all its legs)")
    print(f"  helped {best['n_helped']} trades, hurt {best['n_hurt']}")
    print()
    reasons = []
    if best["full_edge"] < 500:
        reasons.append(f"edge ${best['full_edge']:,.0f} is inside the ~$500 noise bar")
    if best["drop1"] <= 0:
        reasons.append("edge goes NON-POSITIVE after dropping one trade — carried by it")
    elif best["top1_share"] is not None and best["top1_share"] >= 50:
        reasons.append(f"one trade is {best['top1_share']:.0f}% of the edge")
    # The decisive check for multi-leg positions: is the edge really one NAME?
    # NTRA carrying 93% across three of its legs is "carried by one trade" in
    # every sense that matters, even though no single row is >50%.
    if best["top_name_share"] is not None and best["top_name_share"] >= 50:
        reasons.append(f"{best['top_name']} alone is {best['top_name_share']:.0f}% "
                       f"of the edge (${best['top_name_edge']:,.0f}) — one name, not an edge")
    # AGENTS.md: an edge must be spread over 3+ trades. If removing the best
    # three collapses it below the noise bar, it IS those three trades — the
    # single most common way a loosening result flatters itself here.
    if best["drop3"] < 500:
        reasons.append(f"edge collapses to ${best['drop3']:,.0f} after removing its "
                       f"best 3 trades — it IS those trades, not a repeatable edge")
    if best["n_helped"] < 3:
        reasons.append(f"only {best['n_helped']} trades benefit (need 3+)")
    if reasons:
        print("  VERDICT: NOT SHIPPABLE —")
        for r in reasons:
            print(f"    • {r}")
    else:
        print("  VERDICT: SURVIVES the jackknife — edge stays positive after")
        print("    leave-one-out, clears the noise bar, and is spread over 3+")
        print("    trades. Still gate on slot cost (--slotcost) and a 2nd regime")
        print("    before shipping, and promote via shadow first.")
    print()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--detail", metavar="SUBSTRING", default=None,
                        help="show the per-trade breakdown for the configuration "
                             "whose label contains SUBSTRING, instead of the "
                             "top-scoring one (case-insensitive)")
    parser.add_argument("--grid", action="store_true",
                        help="run the full parameter sweep instead of the headline set")
    parser.add_argument("--proveit", action="store_true",
                        help="run the two-phase Prove-It Stop sweep")
    parser.add_argument("--cliff", action="store_true",
                        help="test whether a proven-but-unarmed position should "
                             "keep the Phase 1 band (the protection cliff)")
    parser.add_argument("--day0", action="store_true",
                        help="compare day-0 Phase 1 enforcement: bot poll + arm "
                             "vs a resting broker stop")
    parser.add_argument("--ladder", action="store_true",
                        help="sweep the Phase 2 profit-lock give-back trail "
                             "(TRAIL_PROFIT_TIERS width) and its arming gain")
    parser.add_argument("--ratchet", action="store_true",
                        help="the GNK/NTRA +2-5%% give-back sweep: raise the "
                             "Phase-2 floor toward breakeven and/or arm the "
                             "profit-lock ladder earlier")
    parser.add_argument("--scale", action="store_true",
                        help="partial scale-out sweep: book a fraction at a "
                             "profit target, let the remainder ride (cuts "
                             "give-back without the winner-clip tax)")
    parser.add_argument("--eod", action="store_true",
                        help="compare an end-of-day close-based give-back "
                             "against the shipped intraday trail")
    parser.add_argument("--basetrail", action="store_true",
                        help="measure the normal-operation cost of tightening "
                             "the always-on base/disaster trailing stop (12%% vs 5%%)")
    parser.add_argument("--p1ratchet", action="store_true",
                        help="measure the Phase 1 broker leg defect: the "
                             "resting IBKR TRAIL order ratchets its anchor up "
                             "with price, turning a loss cap into a "
                             "profit-taker (ON vs OFF)")
    parser.add_argument("--clean", action="store_true",
                        help="drawdown-conditional ladder rung: does splitting "
                             "the +5%% rung by prior drawdown beat one flat "
                             "rung? Scored against the FULL live bot "
                             "(Prove-It + scale-out). Use with --runon-days.")
    parser.add_argument("--runon", action="store_true",
                        help="\"let winners run\": sweep the LOOSENING direction "
                             "(ladder width, power hold) with price history "
                             "extended past the real exit, so holding longer "
                             "can actually be credited")
    parser.add_argument("--runon-days", type=int, default=30, metavar="N",
                        help="calendar days of price history to fetch past the "
                             "real exit (default 30; implied by --runon)")
    parser.add_argument("--slotcost", action="store_true",
                        help="re-score the \"let winners run\" sweep with the SLOT "
                             "OPPORTUNITY COST charged: a longer hold keeps a slot "
                             "busy and blocks later real entries. Merges scale-out "
                             "partials into parent positions, validates concurrency "
                             "<= MAX_POSITIONS, then runs a 5-slot portfolio sim. "
                             "Implies a run-on window.")
    parser.add_argument("--jackknife", action="store_true",
                        help="leave-one-out fragility test of the loosening sweep: "
                             "for every challenger, report its edge over SHIPPED "
                             "after dropping its single best (and best three) "
                             "trades. Answers the mandatory 'carried by one trade?' "
                             "question arithmetically. Implies a run-on window.")
    parser.add_argument("--top", type=int, default=25,
                        help="rows to print when using --grid (default 25)")
    parser.add_argument("--json", metavar="PATH",
                        help="also write full results as JSON")
    parser.add_argument("--insecure", action="store_true",
                        help="skip TLS verification for Supabase (local trust-store issues)")
    args = parser.parse_args()

    api_key = _env("FMP_API_KEY")

    print("Loading closed trades from Supabase...", file=sys.stderr)
    trades = load_trades(verify_tls=not args.insecure)
    print(f"Fetching 5-minute bars for {len(trades)} trades...", file=sys.stderr)
    runon_days = args.runon_days if (args.runon or args.slotcost
                                     or args.jackknife) else 0
    if runon_days:
        print(f"  (run-on window: +{runon_days} calendar days past each exit)",
              file=sys.stderr)
    trades = hydrate(trades, api_key, runon_days=runon_days)
    if not trades:
        sys.exit("No trades with usable price history — nothing to replay.")

    # ── Slot opportunity cost is a portfolio-level model, not a per-trade one,
    #    so it has its own scoring and reporting path and returns early.
    if args.slotcost:
        positions = merge_positions(trades)
        concurrency = slot_concurrency(positions)
        print(f"Merged {len(trades)} trade rows into {len(positions)} distinct "
              f"positions; peak concurrency {concurrency}.", file=sys.stderr)
        # The loosening sweep is exactly what needs a slot charge; re-score it.
        slot_results = [score_slotcost(positions, cfg) for cfg in runon_configs()]
        report_slotcost(slot_results, positions, concurrency, top=args.top)
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({
                    "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "n_positions": len(positions),
                    "peak_concurrency": concurrency,
                    "max_positions": MAX_POSITIONS,
                    "results": slot_results,
                }, fh, indent=2)
            print(f"Full results written to {args.json}")
        return

    # ── Jackknife shares the loosening sweep with --runon but answers a
    #    different question (fragility, not ranking), so it has its own early
    #    path. Baseline is shipped_proveit(), which runon_configs() puts first.
    if args.jackknife:
        configs = runon_configs()
        baseline = configs[0]
        jk = report_jackknife(trades, configs, baseline, top=args.top)
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({
                    "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "n_trades": len(trades),
                    "baseline": baseline.label,
                    "results": jk,
                }, fh, indent=2)
            print(f"Full results written to {args.json}")
        return

    if args.clean:
        print("\n── qualifying rate for the `clean` condition ──"
              "──────────────────────────", file=sys.stderr)
        for _dd in (2.0, 3.0, 5.0):
            q, t = clean_qualifying_rate(trades, _dd)
            pct = (q / t * 100.0) if t else 0.0
            note = ""
            if t and pct >= 90:
                note = "  <-- ~everything qualifies: this is a FLAT widening"
            elif t and pct <= 10:
                note = "  <-- almost nothing qualifies: near no-op"
            print(f"   dd<={_dd:.0f}%: {q}/{t} trades that reach +5% "
                  f"qualify ({pct:.0f}%){note}", file=sys.stderr)
        print(file=sys.stderr)
        configs = clean_configs()
    elif args.runon:
        runon_reachability(trades)
        configs = runon_configs()
    elif args.cliff:
        configs = cliff_configs()
    elif args.p1ratchet:
        configs = p1ratchet_configs()
    elif args.day0:
        configs = day0_configs()
    elif args.basetrail:
        configs = basetrail_configs()
    elif args.eod:
        configs = eod_configs()
    elif args.ladder:
        configs = ladder_configs()
    elif args.ratchet:
        configs = ratchet_configs()
    elif args.scale:
        configs = scale_configs()
    elif args.proveit:
        configs = proveit_configs()
    elif args.grid:
        configs = grid_configs()
    else:
        configs = headline_configs()
    results = [score(trades, cfg) for cfg in configs]
    report(results, trades,
           top=args.top if (args.grid or args.proveit or args.ladder
                            or args.ratchet or args.scale or args.eod
                            or args.cliff or args.p1ratchet
                            or args.clean or args.runon) else None,
           detail_label=args.detail)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                "n_trades": len(trades),
                "results": results,
            }, fh, indent=2)
        print(f"Full results written to {args.json}")


if __name__ == "__main__":
    main()
