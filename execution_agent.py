import os
import sys
import argparse
import datetime
import time
import requests
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from ib_insync import IB, Stock, MarketOrder, Order
from telegram_notifier import TelegramNotifier
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import trigger_audit
import schema_guard
try:
    from flex_query_sync import fetch_trade_confirms_for_ticker
except ImportError:
    # flex_query_sync not available in test environments — provide no-op stub
    def fetch_trade_confirms_for_ticker(ticker: str) -> None:
        return None


# ── Persistent log tee: writes every print() to both Docker stdout ─────────────
# and a daily rotating file at /app/logs/execution_YYYY-MM-DD.log.
# Files survive container restarts/recreations because /app/logs is a
# bind-mounted host directory (/opt/trading-bot/logs on the server).
class TeeLogger:
    """Mirrors stdout to a daily rotating log file without touching print() calls."""

    KEEP_DAYS = 7

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._real_stdout = sys.__stdout__
        self._log_file = None
        self._current_date: str | None = None
        self._open_today()
        self._purge_old_logs()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _open_today(self):
        today = self._today()
        if today == self._current_date:
            return
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
        path = os.path.join(self.log_dir, f"execution_{today}.log")
        self._log_file = open(path, "a", encoding="utf-8", buffering=1)
        self._current_date = today
        # Print banner so every log file is self-describing
        self._log_file.write(
            f"\n{'='*60}\n"
            f" Execution Agent — session started {datetime.datetime.now().isoformat()}\n"
            f"{'='*60}\n"
        )
        # Purge old logs on every daily rotation — guarantees cleanup even if
        # the agent runs for months without a container restart.
        self._purge_old_logs()

    def _purge_old_logs(self):
        """Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.

        Uses the date string embedded in the filename instead of mtime.
        ISO dates sort lexicographically, so a plain '<' comparison is correct.
        Purge runs at startup AND at every midnight rotation, so old logs are
        always cleaned up within 24 hours of expiry.
        """
        try:
            cutoff = (
                datetime.datetime.now() - datetime.timedelta(days=self.KEEP_DAYS)
            ).strftime("%Y-%m-%d")  # e.g. "2026-07-02" — files on this date and earlier are removed
            for fname in os.listdir(self.log_dir):
                if not (fname.startswith("execution_") and fname.endswith(".log")):
                    continue
                date_str = fname[len("execution_"):-len(".log")]  # "2026-07-02"
                if len(date_str) == 10 and date_str < cutoff:
                    os.remove(os.path.join(self.log_dir, fname))
                    self._real_stdout.write(
                        f"[TeeLogger] Purged log older than {self.KEEP_DAYS} days: {fname}\n"
                    )
        except Exception:
            pass  # never let purge errors crash the agent

    # ── file-like interface ─────────────────────────────────────────────────

    def write(self, data: str):
        self._open_today()          # auto-rotate at midnight
        self._real_stdout.write(data)
        if self._log_file:
            self._log_file.write(data)

    def flush(self):
        self._real_stdout.flush()
        if self._log_file:
            self._log_file.flush()

    # Propagate attribute lookups to real stdout for compatibility
    def __getattr__(self, name):
        return getattr(self._real_stdout, name)


fmp_session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504, 429], connect=3, read=3)
fmp_session.mount('https://', HTTPAdapter(max_retries=retries))
fmp_session.mount('http://', HTTPAdapter(max_retries=retries))

# Load environment variables
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

# Install TeeLogger immediately after env load so every subsequent print() is
# captured. LOG_DIR defaults to /app/logs — the bind-mounted host directory.
# Falls back to a system temp dir if /app/logs is not writable (e.g. in CI or
# unit tests where the container path does not exist).
_LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
try:
    _tee = TeeLogger(_LOG_DIR)
    sys.stdout = _tee
    sys.stderr = _tee
except (PermissionError, OSError):
    import tempfile
    _LOG_DIR = os.path.join(tempfile.gettempdir(), "execution_agent_logs")
    _tee = TeeLogger(_LOG_DIR)
    sys.stdout = _tee
    sys.stderr = _tee

FMP_API_KEY = os.getenv("FMP_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
IB_GATEWAY_HOST = os.getenv("IB_GATEWAY_HOST", "localhost")
IB_GATEWAY_PORT = int(os.getenv("IB_GATEWAY_PORT", 4000))  # 4000 = live gateway; paper = 7497

# ── Strategy configuration (set in .env) ──────────────────────────────────────
# Maximum concurrent open positions. Each slot gets an equal share of available cash.
# 5 rather than 4: across both backtest universes 5 matched or beat 4 on CAGR and
# lowered max drawdown, and it roughly halves the strategy's dependence on a
# handful of outlier trades (top-10 trades fall from 109% -> 92% of total P/L on
# the growth universe, 98% -> 74% on the broad one). The CAGR/drawdown gaps
# themselves are inside the noise floor; the concentration reduction is not.
from config import MAX_POSITIONS, STOP_LOSS_PCT, COOLING_OFF_DAYS  # noqa: E402  (single source of truth; set via .env)
# ── Exit & hold parameters ──────────────────────────────────────────────────
# Base trailing stop, measured from the position's PEAK (not from entry — this
# is not O'Neil's 7-8% hard stop from cost, it is much tighter in practice).
#
# Widened 0.07 -> 0.10 on 2026-08-04. 4-slot portfolio CAGR (full / worst period),
# measured with every other shipped exit setting active:
#     7%   BROAD +16.6/-1.2   GROWTH +22.5/+13.9
#     10%  BROAD +29.6/+11.9  GROWTH +36.7/+17.0
#     12%  BROAD +27.5/+17.0  GROWTH +46.4/+19.2
#     14%  BROAD +30.9/+14.5  GROWTH +34.5/ +8.3
# 10-12% is a broad optimum on both universes; 10 is the conservative end of it.
# 7% was stopping out of positions that went on to work — the cost showed up as
# a lower payoff ratio, not a higher loss rate.
#
# Max per-trade loss rises from 7% to 10%. Hold time barely moves (avg 9d -> 12d,
# max 60d either way) because the plateau exit, not the stop, bounds hold length.

# Upper bound for the ATR-derived per-position stop. Lowered 0.14 -> 0.12: 14%
# measured worse than the 10-12% band on both universes, clearly so on the
# growth names (+34.5 vs +46.4 full period).
ATR_STOP_MAX_PCT         = float(os.getenv("ATR_STOP_MAX_PCT", 0.12))
# ── Dynamic trailing stop tightening tiers ───────────────────────────────────
# Lever 1 (profit): unrealized gain % → trail %.
#
# Live-trade review showed a persistent pattern: modest winners were making new
# highs and then round-tripping a large share of the open profit before the sell
# rules reacted. On the 20 closed trades available on 2026-08-20, the 9 winners
# gave back $8,071 from their high-water marks before exit (avg $897, median
# 4.03% below the peak at sale). A simple HWM profit-lock beat the current exits:
# arm once the trade is up +5%, then cap give-back to 1.5% from the peak.
#
# This is intentionally aggressive. The rule is not trying to protect +20% to
# +50% leaders; it is trying to stop 4-9% winners from decaying into 0-4% exits.
# If the tightened screener later starts producing true power-hold leaders, this
# ladder must be revisited together with POWER_HOLD. Until then, bank the first
# leg rather than hoping a modest winner becomes an outlier.
#
# Entries are (threshold, trail_pct), listed highest-threshold-first.
TRAIL_PROFIT_TIERS: list[tuple[float, float]] = [
    ( 5.0, 0.015),   # ≥ 5% gain  → 1.5% trail from HWM
    ( 0.0, None),    # < 5%       → no change (base STOP_LOSS_PCT applies)
]
# The time lever that used to sit here (TRAIL_TIME_TIERS) is retired — see
# docs/retired_code.md. Tightening a stop purely because time has passed
# penalises a position for still working.
# Trading days a stock is ineligible for re-entry after being sold. At 1 day a
# stock that just hit its trailing stop was buyable the next morning while still
# technically broken. 4-slot portfolio sim, CAGR (full / worst period):
#     1 day   BROAD +16.6/-1.2   GROWTH +18.0/+9.2
#     7 days  BROAD +16.6/-1.2   GROWTH +22.5/+13.9
# A modest, consistent gain and no downside in either universe.

MIN_POSITION_SIZE        = float(os.getenv("MIN_POSITION_SIZE", 5000.0))
TRIGGER_LOOKBACK_DAYS    = int(os.getenv("TRIGGER_LOOKBACK_DAYS", 3))
MAX_PIVOT_EXTENSION      = float(os.getenv("MAX_PIVOT_EXTENSION", 0.05))  # skip if price > 5% above pivot
# Floor for the same check: skip if price has fallen this far BELOW the pivot.
# Without it the buy zone was open-ended downward, so a stale trigger whose
# breakout had already failed was still eligible. Small buffer so ordinary
# noise around the pivot doesn't reject a valid entry.
MAX_PIVOT_BREAKDOWN      = float(os.getenv("MAX_PIVOT_BREAKDOWN", 0.02))  # skip if price > 2% below pivot
# Hard volume surge gate — independent of AI score. A surge below this multiple
# of the 50-day avg volume means money is NOT confirming the move and is not a
# valid CAN SLIM breakout signal regardless of how the AI scores the setup.
#
# Applies to CONFIRMED breakouts only. The screener reuses the `volume_surge`
# column to carry a 3-day volume CONTRACTION ratio on PRE_BREAKOUT rows, where a
# LOW value is the desirable signal. An earlier revision applied this gate to
# every trigger type, which inverted pre-breakout selection — see
# decisions/2026-08-19_volume-gate-inversion.md.
MIN_VOL_SURGE_GATE       = float(os.getenv("MIN_VOL_SURGE_GATE", 0.75))
# For PRE_BREAKOUT triggers, reject if the stock is still too far below its
# 52-week pivot (pivot_distance_pct stored by the screener). This is distinct
# from the intraday extension check above, which only measures drift from
# yesterday's close — not from the actual 52W high the stock needs to breach.
MAX_PRE_BREAKOUT_PIVOT_DIST = float(os.getenv("MAX_PRE_BREAKOUT_PIVOT_DIST", 0.05))  # 5% below 52W high
# Minimum quality floor applied in buy loop to avoid low-conviction entries.
MIN_TRIGGER_SCORE        = int(os.getenv("MIN_TRIGGER_SCORE", 60))
# Pre-breakout setups are less confirmed; require a higher floor unless marked as
# relaxed quota-fill candidates by the screener.
MIN_PRE_BREAKOUT_SCORE   = int(os.getenv("MIN_PRE_BREAKOUT_SCORE", 65))
# Controlled relaxation floor used only for PRE_BREAKOUT_RELAXED triggers.
MIN_RELAXED_TRIGGER_SCORE = int(os.getenv("MIN_RELAXED_TRIGGER_SCORE", 58))
# Flat cash reserve per buy order: absorbs the 15-20 min lag between IBKR delayed
# price and actual fill price. $1,000 covers ~4% movement on a $25K position.
PRICE_SAFETY_RESERVE     = float(os.getenv("PRICE_SAFETY_RESERVE", 1000.0))

# The EMA-21 exit that used to be configured here is retired — see
# docs/retired_code.md. Prove-It Phase 2 is tighter than a 1% undercut of a
# 21-day average at every gain level, so it could never fire first.

# ── Momentum Health Score (Mₜ) — live conviction for held positions ────────────
# Computed EOD from live RS, volume ratio, and real sentiment (FMP news + GPT).
# Weights: RS decay 40%, Volume ratio 35%, Sentiment 25%.
# Used by Rank & Replace (Day 7+) to compare trigger vs held position quality.
MOMENTUM_HEALTH_RS_WEIGHT   = float(os.getenv("MOMENTUM_HEALTH_RS_WEIGHT",   0.40))
MOMENTUM_HEALTH_VOL_WEIGHT  = float(os.getenv("MOMENTUM_HEALTH_VOL_WEIGHT",  0.35))
MOMENTUM_HEALTH_SENT_WEIGHT = float(os.getenv("MOMENTUM_HEALTH_SENT_WEIGHT", 0.25))
# Minimum score gap (trigger Mₜ vs held Mₜ) to auto-swap in Rank & Replace (Day 7+).
RANK_REPLACE_THRESHOLD      = int(os.getenv("RANK_REPLACE_THRESHOLD", 15))
# Lower bar to rotate out of a position whose Day 3 breakout verdict was FAIL:
# the breakout already failed to confirm, so less evidence is needed to replace it.
RANK_REPLACE_FAIL_THRESHOLD = int(os.getenv("RANK_REPLACE_FAIL_THRESHOLD", 5))

# ── Staleness (feeds Rank & Replace) ──────────────────────────────────────────
# A position that has gone this many TRADING days without making a new high
# water mark counts as STALE. Capital is finite (MAX_POSITIONS slots) so a position that has
# stopped advancing costs the return the slot could earn elsewhere, even while
# it sits comfortably above its trailing stop and therefore trips no other exit.
#
# Staleness no longer sells to cash on its own — the Plateau Exit it used to
# drive is retired (docs/retired_code.md). With the Prove-It give-back floor in
# place, holding dead money is nearly free, so staleness now only DISCOUNTS the
# Rank & Replace margin to RANK_REPLACE_FAIL_THRESHOLD. The slot is released
# when somewhere better to put the money actually exists, not merely because
# this position stopped moving.
#
# Judged on portfolio CAGR with the 4-slot constraint, NOT per-trade expectancy.
# Per trade a plateau exit looks harmful (+1.01% -> +0.87% expectancy) because it
# truncates some winners; with slots modelled it is clearly positive, because the
# freed slot is redeployed. Per-trade expectancy is the wrong metric whenever
# capital, not ideas, is the binding constraint.
#
# 3-year 4-slot simulation, screener-passing universe (the population actually
# traded), CAGR by period:
#     off      full +15.9%   P1 +17.7   P2 +13.1   P3  +5.2
#     10 days  full +20.9%   P1 +24.0   P2 +17.7   P3 +13.7   <- better in ALL
# 8-15 days forms a smooth plateau (+19.0 / +20.9 / +23.1 / +17.2), so the exact
# value is not a knife edge. 10 was chosen over the 12 that maximised the full
# period because it had the best worst-period result.
#
# 5 days scored highest on the broad universe (+20.8% vs +10.1%) but was WORSE
# than no plateau exit on the screener universe (+15.5% vs +15.9%) and turned a
# period negative. It was a single-universe artifact; the disagreement between
# universes is exactly what ruled it out.
#
# Gated to Day 7+ so it can never fire during the breakout consolidation phase,
# and suppressed by the 8-week power-hold rule.
#
# NO LONGER A STANDALONE EXIT (2026-09-04). Selling a stalled position to CASH
# is the wrong destination: the premise "a stalled position blocks a fresh
# breakout" is only true when a fresh breakout actually exists, and with the
# Prove-It give-back floor holding dead money costs almost nothing. The
# staleness signal now discounts the Rank & Replace swap threshold instead, so
# it can only act when there is somewhere better to put the money.
# See docs/retired_code.md and decisions/2026-09-04_prove-it-stop.md.
STALE_EXIT_DAYS             = int(os.getenv("STALE_EXIT_DAYS", 10))
STALE_EXIT_MIN_DAYS_HELD    = int(os.getenv("STALE_EXIT_MIN_DAYS_HELD", 7))

# ── Breakout Verdict ──────────────────────────────────────────────────────────
# Day 3 EOD verdict: position must close >= +1% above entry AND have Day 3 volume
# >= 75% of 20-day average. The verdict is now purely an input to Rank & Replace,
# which rotates FAIL positions on a smaller score gap than PASS ones. It no
# longer arms any exit of its own (the Intraday Loss Minimiser it used to feed is
# retired — see docs/retired_code.md).
BREAKOUT_VERDICT_MIN_GAIN    = float(os.getenv("BREAKOUT_VERDICT_MIN_GAIN",    0.01))  # 1% above entry
BREAKOUT_VERDICT_MIN_VOL_PCT = float(os.getenv("BREAKOUT_VERDICT_MIN_VOL_PCT", 0.75)) # 75% of 20d avg

# ── The Prove-It Stop ─────────────────────────────────────────────────────────
# ONE question governs every loss-cutting exit: has this position ever CLOSED
# above the price we paid?
#
#   PHASE 1 — unproven. The breakout has not confirmed. Anchor to ENTRY.
#             Day 0:  1.0% below entry   (a breakout that fails on day one is
#                                         wrong immediately and cheaply)
#             Day 1+: 3.0% below entry   (a confirmed-but-slow name needs room
#                                         to shake out before it works)
#
#   PHASE 2 — proven. It closed above entry, so it earned patience. Anchor to
#             the PEAK.
#             peak gain >= 2.0%: floor at 1.0% BELOW entry — a trade that went
#                                green is never allowed to become a real loss
#             gain      >= 5.0%: 1.5% trail from the high water mark
#                                (TRAIL_PROFIT_TIERS, unchanged)
#
# WHY THIS REPLACES FIVE RULES
# The kill-switch, Thesis Stop, Early Dollar Stop, EMA-21 exit and Plateau exit
# were five different answers to two questions this asks once. Each carried its
# own window, its own anchor and its own threshold, and they raced each other:
# the Thesis Stop and Early Dollar Stop never fired ONCE in 30 closed trades
# because the kill-switch always got there first — but the kill-switch stopped
# looking after day 0, which is precisely how NBIX (-$2,261), DELL (-$1,283),
# RSI (-$1,390) and HWM (-$1,463) were allowed to run.
#
# EVIDENCE (5-minute replay of all 30 closed trades, reproducing live mechanics:
# 15-minute checks, arm_exit() 0.6% trail, 3.25h deadline)
#     what actually happened      -$6,548
#     rules shipped before this   -$4,069
#     Prove-It                    +$5,410   <- zero winners cut short
# Worst single loss falls from -$2,002 to -$1,140, and the -$1,140 is APH, an
# overnight gap that no stop of any kind can prevent. Every intraday bleed is
# cut small: NBIX -$2,261 -> -$230, CDNA -$1,539 -> +$256, RSI -$1,390 -> -$197.
#
# WHY PHASE 1 WIDENS AFTER DAY 0 RATHER THAN TIGHTENING
# Counter-intuitive but measured. Holding the tight 1.0% band through day 1 costs
# roughly $1,500-2,000 in winner damage: CPAY closed -2.24% on day 1 and low
# -2.88%, then ran to +8.95%. Day 0 is the only day on which the failing and
# working populations separate cleanly.
#
# WHY THE PHASE 2 FLOOR SITS 1% BELOW ENTRY, NOT AT IT
# An exact-breakeven floor flushes any position that pokes green and immediately
# retests entry. CPAY did exactly that on day 4 (high +3.60%, low -0.41%) and an
# at-entry floor sold it for $0, forfeiting +$1,189. One percent of slack is the
# difference between the floor protecting winners and clipping them: it turns
# CPAY into +$1,907 while still catching FRO and CDNA.
#
# See decisions/2026-09-04_prove-it-stop.md.
PROVE_IT_ENABLED           = os.getenv("PROVE_IT_ENABLED", "true").lower() == "true"
# Phase 1 — entry-anchored, applied while the position is unproven.
PROVE_IT_P1_DAY0_PCT       = float(os.getenv("PROVE_IT_P1_DAY0_PCT",       0.01))  # 1.0%
PROVE_IT_P1_LATER_PCT      = float(os.getenv("PROVE_IT_P1_LATER_PCT",      0.03))  # 3.0%
PROVE_IT_P1_DAY0_LAST_DAY  = int(os.getenv("PROVE_IT_P1_DAY0_LAST_DAY",       0))
# Phase 2 — peak gain that arms the give-back floor, and where the floor sits
# relative to entry (negative = below entry).
PROVE_IT_P2_ARM_GAIN_PCT   = float(os.getenv("PROVE_IT_P2_ARM_GAIN_PCT",   0.02))  # +2.0%
PROVE_IT_P2_FLOOR_PCT      = float(os.getenv("PROVE_IT_P2_FLOOR_PCT",     -0.01))  # -1.0%
# How far BELOW the Phase 1 trigger the resting IBKR stop is parked.
#
# Phase 1 is enforced by the bot: on the 15-minute cycle it arms a tight 0.6%
# trailing exit (arm_exit()) rather than selling at what is often a local trough.
# The replay shows that armed exit beats an immediate market sell by roughly
# $600 across the sample, so the bot must get first refusal.
#
# But the bot only looks every 15 minutes and cannot act at all when it is down
# or the market gaps. So a GTC order rests at the broker one slack-width below
# the same level: wide enough that it never front-runs the armed exit, tight
# enough to cap an overnight gap. Belt and braces, in that order.
PROVE_IT_BACKSTOP_SLACK_PCT = float(os.getenv("PROVE_IT_BACKSTOP_SLACK_PCT", 0.01))

# ── Armed Trailing Exit (Day 0-6 loss-cutting) ─────────────────────────────────
# When the Prove-It Stop fires, we do NOT sell instantly at the trigger price — that price is
# often a local trough. Instead we "arm" the exit: place a tight IBKR native
# trailing stop that rides any bounce toward the best price reached since the
# trigger, while a hard deadline forces a market sell if it hasn't already
# closed out. This bounds the extra hold time so we never wait indefinitely
# (and risk deeper losses) chasing a better exit.
ARMED_EXIT_TRAIL_PCT      = float(os.getenv("ARMED_EXIT_TRAIL_PCT",      0.006))  # 0.6%
ARMED_EXIT_DEADLINE_HOURS = float(os.getenv("ARMED_EXIT_DEADLINE_HOURS", 3.25))   # ~half a trading day

# ── Smart OCA Managed Exit (queue-driven, see migrations/add_exit_requests.sql) ─
# A row in `exit_requests` asks the agent to exit a named position via an IBKR
# OCA pair rather than a market dump:
#     upper leg = LMT sell at an optimistic recovery target
#     lower leg = TRAIL sell that ratchets up behind any bounce
# One cancels the other. The agent drains the queue every monitoring cycle, so a
# request made at 11:00 acts at 11:00 — "first thing in the morning" is just the
# special case where the request was queued overnight.
#
# The legs are NOT placed at 09:30. The opening auction has the widest spreads
# and the wildest prints of the session; a limit computed off a 09:30 tick is
# computed off noise. We wait until the tape settles.
OCA_EXIT_ENABLED          = os.getenv("OCA_EXIT_ENABLED", "true").lower() == "true"
OCA_EXIT_SETTLE_MINUTE    = int(os.getenv("OCA_EXIT_SETTLE_MINUTE", 45))   # place from 09:45 ET
# Trail sizing for stop_mode='ATR_AUTO'. A trail tighter than the stock's own
# noise fires on the first random wiggle, which just reproduces "sell now" with
# extra steps and forfeits the upper leg entirely.
OCA_EXIT_ATR_FRACTION     = float(os.getenv("OCA_EXIT_ATR_FRACTION",     0.33))
OCA_EXIT_MIN_TRAIL_PCT    = float(os.getenv("OCA_EXIT_MIN_TRAIL_PCT",    0.015))  # 1.5%
OCA_EXIT_MAX_TRAIL_PCT    = float(os.getenv("OCA_EXIT_MAX_TRAIL_PCT",    0.040))  # 4.0%
OCA_EXIT_DEFAULT_ATR_PCT  = float(os.getenv("OCA_EXIT_DEFAULT_ATR_PCT",  3.0))
# Upper-leg sizing for limit_mode='ATR_AUTO' — the default for a bare insert.
#
# BREAKEVEN was the original default, but it anchors the target to the ENTRY
# price, so the bounce required is proportional to how much the position is
# already down: a name 5.5% underwater needs a 5.9% rally before the leg can
# fill, which is exactly when you least want to wait. ATR_AUTO anchors to the
# CURRENT price instead, so entry drops out of the maths and the target is
# always about half a day's move away — reachable regardless of the loss, and
# self-scaling to each stock's own volatility.
#
# Clamped at both ends: a very quiet name would otherwise get a target inside
# the spread, and a very volatile one a target no realistic bounce reaches.
OCA_EXIT_UPPER_ATR_FRACTION = float(os.getenv("OCA_EXIT_UPPER_ATR_FRACTION", 0.50))
OCA_EXIT_MIN_UPPER_PCT    = float(os.getenv("OCA_EXIT_MIN_UPPER_PCT",    0.0075))  # 0.75%
OCA_EXIT_MAX_UPPER_PCT    = float(os.getenv("OCA_EXIT_MAX_UPPER_PCT",    0.050))   # 5.0%
# Backstop applied in software each cycle: an OCA can sit unfilled indefinitely
# while the position bleeds, so bound both the price and the time.
OCA_EXIT_DEFAULT_FLOOR_PCT = float(os.getenv("OCA_EXIT_DEFAULT_FLOOR_PCT", 0.05))  # 5% below placement
OCA_EXIT_DEFAULT_EXPIRY_DAYS = int(os.getenv("OCA_EXIT_DEFAULT_EXPIRY_DAYS", 3))

# Route the *discretionary* Day 7+ exits through the Smart OCA queue instead of
# selling at market on whichever 15-minute tick happened to notice.
#
# Scoped to Day 7+ non-urgent rules ON PURPOSE. The Prove-It Stop
# (kill-switch, dollar stop, thesis stop) keep arm_exit(): a placed OCA
# suspends the automated ladder for up to OCA_EXIT_DEFAULT_EXPIRY_DAYS, which
# is exactly the wrong trade for a position that is actively failing.
# See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.
SMART_EXIT_FOR_RULES = os.getenv("SMART_EXIT_FOR_RULES", "true").lower() == "true"

# ── O'Neil 8-Week Hold Rule ───────────────────────────────────────────────────
# From "How to Make Money in Stocks": a stock that gains 20%+ within 3 weeks of a
# proper breakout is behaving like a genuine market leader and should be held for
# at least 8 weeks rather than trimmed on the first wobble.
#
# This is the mechanism that would let a position become the outsized winner
# CAN SLIM expectancy depends on. While a position is in its power-hold window we
# suppress the DISCRETIONARY exits (Prove-It Stop, Rank & Replace) AND widen the
# trailing stop to POWER_HOLD_TRAIL_PCT (see below). The trailing stop is never
# removed — it remains the disaster backstop, so this bounds opportunity cost,
# never risk.
#
# TRIGGER LOWERED 20% -> 10% (2026-09-04). At +20% the rule was unreachable in
# practice: it never armed once across 30 closed trades, because it never armed
# once across ANY closed trade. The realised winner distribution tops out well
# below the level the rule was calibrated for — the +20%-in-3-weeks leader it was
# built to protect is a population this screener has not yet produced.
#
# A rule that cannot fire protects nothing. 10% sits inside the observed
# distribution (MPC +6.4%, LPG +7.0%, CPAY +9.0% peak) without being trivially
# easy to reach, so it can begin to bind on the genuinely strong names while
# still requiring roughly double the peak of a typical winner.
#
# ⚠️ UNVALIDATED. This threshold has no replay behind it — the 30-trade sample
# contains no position that reached +10% within 21 days, so the change is a
# judgement call about reachability, not a measured optimum. It is the first
# thing to re-examine at the next exit-parameter review.
POWER_HOLD_ENABLED        = os.getenv("POWER_HOLD_ENABLED", "true").lower() == "true"
POWER_HOLD_GAIN_PCT       = float(os.getenv("POWER_HOLD_GAIN_PCT", 10.0))
POWER_HOLD_TRIGGER_DAYS   = int(os.getenv("POWER_HOLD_TRIGGER_DAYS", 21))   # 3 weeks
POWER_HOLD_DURATION_DAYS  = int(os.getenv("POWER_HOLD_DURATION_DAYS", 56))  # 8 weeks
# Trail width applied WHILE a position is power-held, replacing the profit ladder.
#
# Without this the rule was self-defeating: TRAIL_PROFIT_TIERS tightens the trail
# well below the gain that arms the power hold — under the ladder in force at the
# time, to 6.5% at the then-current +20% trigger — so the ladder strangled the
# leaders the rule exists to protect. Instrumenting the
# backtest showed the rule armed on 9% (growth) / 6% (broad) of trades and then
# *100% of those still exited on the trailing stop*, making it inert.
#
# The current HWM profit lock makes this worse, not better: it clamps to 1.5% from
# the peak at only +5% gain, so by the time a position reaches POWER_HOLD_GAIN_PCT
# it is already on the tightest rung. Bypassing the ladder while power-held is
# therefore load-bearing — see decisions/2026-08-20_hwm-profit-lock-first-leg.md.
#
# Widening the trail while power-held recovers the intended behaviour. The effect
# is large, monotonic in the trail width, and consistent across both universes
# (growth +27.0% -> +66.3% CAGR, broad +27.4% -> +44.5% at 0.30). Crucially it
# does NOT increase risk: the rule only arms after a position is already well up,
# so the worst trade is unchanged at -10% and max drawdown is flat (17.6% / 14.5%).
# NOTE: those figures were measured with the +20% trigger. The move to +10% widens
# the trail on a weaker class of position and is NOT covered by that backtest.
# 0.30 is chosen over removing the stop entirely (+76.6% / +48.8%) to retain a
# disaster backstop, since the upside rests on very few trades.
POWER_HOLD_TRAIL_PCT      = float(os.getenv("POWER_HOLD_TRAIL_PCT", 0.30))

# ── CANSLIM "M" — market direction gate ───────────────────────────────────────
# Both benchmarks must close above their SMA-200 by MARKET_DIRECTION_BUFFER_PCT,
# and at least one SMA-200 must be non-falling over MARKET_DIRECTION_SLOPE_DAYS.
# Grid-tested over 4,940 sessions (2007-2026): this configuration sits out 67.8%
# of the worst-5% forward-20d windows vs 59.3% for the old bare SPY>SMA200 rule.
# A 50>200 requirement and an "either index" (OR) combination were both tested
# and rejected — see decisions/2026-08-22_market-direction-gate-spy-qqq.md.
MARKET_DIRECTION_FILTER_ENABLED = os.getenv("MARKET_DIRECTION_FILTER_ENABLED", "true").lower() == "true"
MARKET_DIRECTION_SMA_WINDOW     = int(os.getenv("MARKET_DIRECTION_SMA_WINDOW", 200))
MARKET_DIRECTION_TICKERS        = [t.strip().upper() for t in
                                   os.getenv("MARKET_DIRECTION_TICKERS", "SPY,QQQ").split(",")
                                   if t.strip()]
MARKET_DIRECTION_BUFFER_PCT     = float(os.getenv("MARKET_DIRECTION_BUFFER_PCT", 0.01))
MARKET_DIRECTION_SLOPE_DAYS     = max(1, int(os.getenv("MARKET_DIRECTION_SLOPE_DAYS", 20)))
MARKET_DIRECTION_MAX_STALE_DAYS = int(os.getenv("MARKET_DIRECTION_MAX_STALE_DAYS", 5))

# ── Telegram notifications ─────────────────────────────────────────────────────
notifier = TelegramNotifier(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
)


# ── NYSE trading-day calendar ─────────────────────────────────────────────────
def _nyse_holidays(year: int) -> set:
    """Return the set of NYSE market holidays for a given year.

    Computed algorithmically — no external package required.
    Includes the observed (Mon/Fri substitute) date when a holiday falls on a
    weekend, matching the NYSE official schedule.
    """
    from calendar import monthcalendar, MONDAY, THURSDAY

    def _observed(d: datetime.date) -> datetime.date:
        """Shift Sat → Fri, Sun → Mon for observed holiday."""
        if d.weekday() == 5:  # Saturday
            return d - datetime.timedelta(days=1)
        if d.weekday() == 6:  # Sunday
            return d + datetime.timedelta(days=1)
        return d

    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
        """Return the nth occurrence of weekday (0=Mon..6=Sun) in month/year."""
        weeks = monthcalendar(year, month)
        hits = [w[weekday] for w in weeks if w[weekday] != 0]
        return datetime.date(year, month, hits[n - 1])

    def _last_weekday(year: int, month: int, weekday: int) -> datetime.date:
        """Return the last occurrence of weekday in month/year."""
        weeks = monthcalendar(year, month)
        hits = [w[weekday] for w in weeks if w[weekday] != 0]
        return datetime.date(year, month, hits[-1])

    holidays = set()

    # New Year's Day — Jan 1 (observed)
    holidays.add(_observed(datetime.date(year, 1, 1)))
    # MLK Day — 3rd Monday in January
    holidays.add(_nth_weekday(year, 1, MONDAY, 3))
    # Presidents' Day — 3rd Monday in February
    holidays.add(_nth_weekday(year, 2, MONDAY, 3))
    # Good Friday — 2 days before Easter Sunday
    # Easter via Anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d_, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d_ - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    easter_month = (h + l_ - 7 * m + 114) // 31
    easter_day   = ((h + l_ - 7 * m + 114) % 31) + 1
    easter = datetime.date(year, easter_month, easter_day)
    holidays.add(easter - datetime.timedelta(days=2))  # Good Friday
    # Memorial Day — last Monday in May
    holidays.add(_last_weekday(year, 5, MONDAY))
    # Juneteenth — Jun 19 (observed), added from 2022
    if year >= 2022:
        holidays.add(_observed(datetime.date(year, 6, 19)))
    # Independence Day — Jul 4 (observed)
    holidays.add(_observed(datetime.date(year, 7, 4)))
    # Labor Day — 1st Monday in September
    holidays.add(_nth_weekday(year, 9, MONDAY, 1))
    # Thanksgiving — 4th Thursday in November
    holidays.add(_nth_weekday(year, 11, THURSDAY, 4))
    # Christmas — Dec 25 (observed)
    holidays.add(_observed(datetime.date(year, 12, 25)))

    return holidays


def trading_days_between(start: datetime.date, end: datetime.date) -> int:
    """Count NYSE trading days in the half-open interval [start, end).

    Weekends and NYSE market holidays are excluded.  This is used for plateau
    detection so a 3-day weekend (e.g. Labor Day) doesn't artificially advance
    the stall counter.

    Args:
        start: The earlier date (inclusive).
        end:   The later date (exclusive — typically 'today').

    Returns:
        Number of trading days between start and end (>= 0).
    """
    if end <= start:
        return 0
    # Pre-compute holidays for all years in range
    years = range(start.year, end.year + 1)
    holidays: set = set()
    for y in years:
        holidays |= _nyse_holidays(y)

    count = 0
    current = start
    one_day = datetime.timedelta(days=1)
    while current < end:
        if current.weekday() < 5 and current not in holidays:  # Mon–Fri, not a holiday
            count += 1
        current += one_day
    return count


# Global unhandled exception hook
def global_exception_handler(exctype, value, tb):
    if issubclass(exctype, KeyboardInterrupt):
        sys.__excepthook__(exctype, value, tb)
        return
    import traceback
    tb_str = "".join(traceback.format_exception(exctype, value, tb))
    print(f"CRITICAL: Unhandled exception caught by global hook:\n{tb_str}")
    notifier.notify_exception("GLOBAL UNCAUGHT EXCEPTION", value)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

# Initialize Supabase client
supabase: Client = None

def get_supabase_client() -> Client:
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase

def get_live_price(ticker: str) -> float:
    """Fetch current price of a ticker from FMP."""
    url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={FMP_API_KEY}"
    try:
        res = fmp_session.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0))
    except Exception as e:
        notifier.notify_exception(f"get_live_price() — execution_agent.py", e)
        print(f"❌ Error fetching price for {ticker} from FMP: {e}")
    return 0.0


def build_ibkr_price_map(ib: IB) -> dict:
    """Return {symbol: PortfolioItem} for the current account's open positions.

    Reads ib.portfolio(), which is a NON-BLOCKING read of the in-memory account
    update stream — deliberately NOT ib.reqTickers(), which blocks indefinitely
    when the ushmds data farm is down. Safe to call once per monitoring cycle and
    pass into get_position_price() so every position is priced from a single
    consistent broker snapshot.
    """
    try:
        return {p.contract.symbol: p for p in ib.portfolio()}
    except Exception as e:
        print(f"   ⚠️ Could not read IBKR portfolio for pricing: {e}")
        return {}


def get_position_price(ib: IB, ticker: str, ib_map: dict | None = None) -> tuple:
    """IBKR-first live price for an OPEN position, with FMP fallback.

    Live trades are executed against IBKR, so exit rules and account valuation
    must be decided on IBKR's own mark — the same PortfolioItem.marketPrice the
    dashboard and reconcile_with_ibkr() already use. Pricing exits off a second
    source (FMP) is what caused fill-vs-decision mismatches in the past.

    FMP is retained ONLY as a fallback for when IBKR has no usable mark (data
    farm down, or the position has not yet appeared in the account-update
    stream). This is safe because the IBKR read here is ib.portfolio() — a
    non-blocking in-memory lookup — never the blocking ib.reqTickers() path.

    Args:
        ib:      connected IB handle.
        ticker:  symbol to price.
        ib_map:  optional precomputed {symbol: PortfolioItem} from
                 build_ibkr_price_map(ib); built on demand when omitted.

    Returns:
        (price: float, source: str) where source is 'ibkr' or 'fmp'.
        price is 0.0 only when BOTH sources fail.
    """
    if ib_map is None:
        ib_map = build_ibkr_price_map(ib)

    item = ib_map.get(ticker)
    if item is not None:
        mp = getattr(item, "marketPrice", None)
        try:
            mp = float(mp) if mp is not None else 0.0
        except (TypeError, ValueError):
            mp = 0.0
        # NaN-safe: NaN != NaN.
        if mp == mp and mp > 0:
            return mp, "ibkr"

    fmp_price = get_live_price(ticker)
    if fmp_price > 0:
        print(f"   ↩️ {ticker}: IBKR mark unavailable — FMP fallback ${fmp_price:.2f}")
    return fmp_price, "fmp"


def fetch_historical_closes_with_dates(ticker: str, window: int) -> list:
    """Fetch historical daily close prices and dates from FMP (oldest first)."""
    # Fetch window * 4 + 20 calendar days to guarantee sufficient trading days
    lookback_days = window * 4 + 20
    to_date = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    from_date = to_date - datetime.timedelta(days=lookback_days)
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={from_date}&to={to_date}"
           f"&apikey={FMP_API_KEY}")
    try:
        r = fmp_session.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                # Return sorted by date ascending (oldest first)
                return sorted(data, key=lambda x: x["date"])
            else:
                print(f"⚠️ Empty historical data response for {ticker} from FMP.")
        else:
            print(f"⚠️ FMP historical API returned status code {r.status_code} for {ticker}.")
    except Exception as e:
        notifier.notify_exception(f"fetch_historical_closes_with_dates() — execution_agent.py", e)
        print(f"❌ Error fetching historical prices for {ticker} from FMP: {e}")
    return []

def calculate_sma(closes: list, window: int) -> float | None:
    """Compute Simple Moving Average."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window

def calculate_ema(closes: list, window: int) -> float | None:
    """Compute Exponential Moving Average."""
    if len(closes) < window:
        return None
    alpha = 2 / (window + 1)
    # Start with SMA of the first 'window' closes
    ema = sum(closes[:window]) / window
    # Apply recursive EMA formula to subsequent closes
    for price in closes[window:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return ema

def _matches_account(obj, target_account: str | None) -> bool:
    """Return True if obj belongs to target_account, or if obj has no account string set (e.g. test mocks)."""
    if not target_account:
        return True
    acc = getattr(obj, "account", None)
    if acc is None or not isinstance(acc, str):
        return True
    return acc == target_account


def get_own_cash(ib: IB, account: str = None) -> float:
    """Return only the agent's own (non-borrowed) cash balance in USD.

    Reads ``TotalCashValue`` — IBKR's signed sum of all cash in the account.
    Filters by target account (`account` param or `get_ibkr_account(ib)`) to
    prevent reading cash balances from other sub-accounts under the same login.

    Returns:
        float: own cash in USD (>= 0.0).  Returns 0.0 if a margin loan is
               detected OR if the IBKR query fails.
    """
    try:
        target_account = account or get_ibkr_account(ib)
        account_values = ib.accountValues()

        total_cash = None
        net_liq    = None

        for av in account_values:
            if not _matches_account(av, target_account):
                continue
            if av.currency != "USD":
                continue
            if av.tag == "TotalCashValue":
                total_cash = float(av.value)
            elif av.tag == "NetLiquidation":
                net_liq = float(av.value)

        if total_cash is None:
            print(f"⚠️ get_own_cash(): TotalCashValue tag not found for account {target_account}. Returning 0.")
            return 0.0

        if total_cash < 0:
            # Negative TotalCashValue = margin loan is active.
            # Hard block: return 0 so the buy loop skips all purchases.
            margin_loan = abs(total_cash)
            print(
                f"🚨 MARGIN LOAN DETECTED [{target_account}]: TotalCashValue = ${total_cash:,.2f} "
                f"(margin borrowed: ${margin_loan:,.2f}). "
                f"Returning 0 — no new buys until loan is repaid."
            )
            return 0.0

        # Cap own_cash at NetLiquidation as a sanity guard.
        if net_liq is not None and total_cash > net_liq > 0:
            print(f"⚠️ get_own_cash(): TotalCashValue (${total_cash:,.2f}) > NetLiquidation "
                  f"(${net_liq:,.2f}) for {target_account}. Capping to NetLiquidation.")
            return round(net_liq, 2)

        return round(total_cash, 2)

    except Exception as e:
        notifier.notify_exception(f"get_own_cash() — execution_agent.py", e)
        print(f"❌ Error querying own cash from IBKR: {e}")
    return 0.0


def get_margin_loan(ib: IB, account: str = None) -> float:
    """Return the current margin loan amount in USD (0.0 if no loan).

    A positive return value means IBKR has lent this amount to the account.
    Filters by target account (`account` param or `get_ibkr_account(ib)`).
    """
    try:
        target_account = account or get_ibkr_account(ib)
        for av in ib.accountValues():
            if not _matches_account(av, target_account):
                continue
            if av.tag == "TotalCashValue" and av.currency == "USD":
                raw = float(av.value)
                return round(abs(raw), 2) if raw < 0 else 0.0
    except Exception as e:
        print(f"⚠️ get_margin_loan(): could not fetch TotalCashValue: {e}")
    return 0.0


def get_net_liquidation(ib: IB, account: str = None) -> float:
    """Return total account equity (cash + position market value) in USD.

    Reads IBKR's ``NetLiquidation`` tag, filtered to the target account the same
    way get_own_cash() does, so a second sub-account under the same login cannot
    inflate the figure.

    Used to size the Early Dollar Stop as a share of equity rather than a fixed
    dollar amount, so the cap tracks account growth instead of silently becoming
    a tighter percentage every time the account gets larger.

    Returns:
        float: equity in USD, or 0.0 if the tag is missing or the query fails.
               Callers MUST treat 0.0 as "unknown" and skip the rule rather than
               computing a zero-dollar threshold, which would exit everything.
    """
    try:
        target_account = account or get_ibkr_account(ib)
        for av in ib.accountValues():
            if not _matches_account(av, target_account):
                continue
            if av.tag == "NetLiquidation" and av.currency == "USD":
                value = float(av.value)
                return round(value, 2) if value > 0 else 0.0
        print(f"⚠️ get_net_liquidation(): NetLiquidation tag not found for {target_account}.")
    except Exception as e:
        notifier.notify_exception("get_net_liquidation() — execution_agent.py", e)
        print(f"❌ Error querying net liquidation from IBKR: {e}")
    return 0.0


def get_available_cash(ib: IB) -> float:
    """Deprecated alias for get_own_cash().

    DEPRECATED: Previously read AvailableFunds (which includes margin lending).
    Now delegates to get_own_cash() which reads TotalCashValue and hard-blocks
    when a margin loan is active. Kept (with a regression test in
    tests/test_margin_safety.py) so any old call site automatically gets the
    margin-safe value without code changes. All new code should call
    get_own_cash() directly.
    """
    return get_own_cash(ib)


# ─────────────────────────────────────────────────────────────────────────────
# IBKR Order Management Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_ibkr_account(ib: IB) -> str:
    """
    Returns the configured IBKR live account.
    Priority: IBKR_ACCOUNT env var → U12941651 (if present) → first live account (U...) → accounts[0].
    Raises if both live and paper (DU...) accounts are visible without
    IBKR_ACCOUNT being set — prevents accidentally trading on the wrong account.
    """
    accounts = ib.managedAccounts()
    if not accounts:
        raise ValueError("No IBKR accounts found for this login.")

    # Explicit override always wins
    env_account = os.getenv("IBKR_ACCOUNT")
    if env_account:
        if env_account not in accounts:
            raise ValueError(
                f"IBKR_ACCOUNT='{env_account}' not in managed accounts {accounts}. "
                "Check your .env file."
            )
        return env_account

    # Default to primary trading account U12941651 if available
    if "U12941651" in accounts:
        return "U12941651"

    # Prefer live accounts (U...) over paper (DU...)
    live_accounts   = [acc for acc in accounts if acc.startswith('U') and not acc.startswith('DU')]
    paper_accounts  = [acc for acc in accounts if acc.startswith('DU')]

    if paper_accounts and not live_accounts:
        # Only paper accounts visible — warn loudly but continue
        print(
            f"⚠️  WARNING: Only paper account(s) found: {paper_accounts}. "
            "Set IBKR_ACCOUNT=<live_account_id> in .env to trade live."
        )
        return paper_accounts[0]

    if paper_accounts and live_accounts:
        # Both exist — refuse to guess, require explicit config
        raise ValueError(
            f"Both live {live_accounts} and paper {paper_accounts} accounts visible. "
            "Set IBKR_ACCOUNT=<live_account_id> in .env to avoid ambiguity."
        )

    return live_accounts[0] if live_accounts else accounts[0]

def TrailingStopOrder(action: str, totalQuantity: float,
                     trailingPercent: float = None,
                     trailStopPrice: float = None, **kwargs) -> Order:
    """
    Factory for IBKR TRAIL order type.
    `ib_insync` 0.9.x does not export a TrailingStopOrder helper,
    but the underlying Order dataclass supports it via orderType='TRAIL'.
    """
    o = Order()
    o.action = action
    o.orderType = 'TRAIL'
    o.totalQuantity = totalQuantity
    if trailingPercent is not None:
        o.trailingPercent = trailingPercent
    if trailStopPrice is not None:
        o.trailStopPrice = trailStopPrice
    for k, v in kwargs.items():
        setattr(o, k, v)
    return o

def place_trailing_stop(ib: IB, contract, shares: int, stop_loss_pct: float) -> tuple:
    """
    Places a GTC Trailing Stop for an open stock position.
    Trails stop_loss_pct% below the running peak price.

    IBKR tracks the high-water mark internally (tick-by-tick) -- no HWM
    parameter is needed. Winners run freely until the stop fires or EOD
    plateau rotation acts.

    Returns (group_label, confirmed_trail_pct) where confirmed_trail_pct
    is the trailingPercent IBKR echoed back on the Trade object (as a
    decimal, e.g. 0.091 for 9.1%). Falls back to stop_loss_pct if the
    echo is unavailable.
    """
    import time as _time
    group = f"TS_{contract.symbol}_{int(_time.time())}"

    stop = TrailingStopOrder('SELL', shares,
                             trailingPercent=round(stop_loss_pct * 100, 2))
    stop.tif = 'GTC'
    stop.account = get_ibkr_account(ib)
    trade = ib.placeOrder(contract, stop)

    # Read back the confirmed trailingPercent from the echoed Trade order.
    # IBKR populates trade.order.trailingPercent synchronously after placeOrder.
    try:
        confirmed_pct_raw = getattr(trade.order, 'trailingPercent', None)
        if confirmed_pct_raw and float(confirmed_pct_raw) > 0:
            confirmed_trail_pct = float(confirmed_pct_raw) / 100.0
        else:
            confirmed_trail_pct = stop_loss_pct  # fallback to calculated
    except Exception:
        confirmed_trail_pct = stop_loss_pct

    print(f"   \U0001f6e1\ufe0f  IBKR trailing stop placed: {confirmed_trail_pct*100:.2f}% trail (confirmed)")
    return group, confirmed_trail_pct


def arm_exit(ib: IB, client: Client, ticker: str, shares: int, current_price: float,
             reason: str, now_ny: datetime.datetime) -> None:
    """
    Arms a Day 0-6 loss-cutting exit instead of selling immediately at the
    trigger price (often a local trough).

    Replaces any existing sell order with a tight ARMED_EXIT_TRAIL_PCT IBKR
    native trailing stop, which tracks the price tick-by-tick and rides any
    bounce toward the best price reached since arming. A hard deadline
    (ARMED_EXIT_DEADLINE_HOURS, checked every monitoring cycle in
    monitor_portfolio_intraday) forces a market sell if the trail hasn't
    already fired — this bounds the extra hold time so we never wait
    indefinitely chasing a better exit.
    """
    try:
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)
        place_trailing_stop(ib, contract, shares, ARMED_EXIT_TRAIL_PCT)

        try:
            client.table("portfolio_positions").update({
                "exit_armed":        True,
                "exit_armed_at":     now_ny.isoformat(),
                "exit_armed_reason": reason,
                "exit_armed_price":  round(float(current_price), 4),
            }).eq("ticker", ticker).execute()
        except Exception as db_err:
            # PGRST204 = column missing in schema cache (migration not yet run).
            # The tight IBKR trailing stop is already placed and will still
            # protect the position; only the deadline bookkeeping is degraded
            # until migrations/add_armed_exit_columns.sql is applied.
            if "PGRST204" in str(db_err) or "exit_armed" in str(db_err):
                print(f"   ⚠️ {ticker}: exit_armed columns missing — run migrations/add_armed_exit_columns.sql. "
                      f"Trailing stop placed but deadline won't be tracked.")
            else:
                raise

        msg = (
            f"\U0001f3af <b>{ticker}</b> exit armed (Day 0-6 loss-cutting): {reason}\n"
            f"Tight trail: {ARMED_EXIT_TRAIL_PCT*100:.2f}% from price at arm-time (${current_price:.2f})\n"
            f"Deadline: forced sell in {ARMED_EXIT_DEADLINE_HOURS:.2f}h if not already stopped out."
        )
        notifier._send(msg)
        print(f"   \U0001f3af {ticker}: exit armed — {reason} "
              f"(tight {ARMED_EXIT_TRAIL_PCT*100:.2f}% trail, deadline {ARMED_EXIT_DEADLINE_HOURS:.2f}h)")
    except Exception as arm_err:
        notifier.notify_exception("arm_exit() — execution_agent.py", arm_err)
        print(f"   ⚠️ {ticker}: failed to arm exit: {arm_err}")


def _position_atr_pct(pos: dict) -> tuple[float, str]:
    """
    The ATR percent both OCA legs are sized from, with its provenance.

    Note this is `entry_atr_pct` — the ATR recorded when the position was
    opened, not today's. It is the only ATR the position row carries. For a
    name whose volatility has since expanded this sizes both legs slightly
    tight; the hard floor and expiry backstops bound that.
    """
    atr_pct = pos.get("entry_atr_pct")
    if not atr_pct or float(atr_pct) <= 0:
        return OCA_EXIT_DEFAULT_ATR_PCT, "default (no ATR on record)"
    return float(atr_pct), "entry_atr_pct"


def resolve_oca_trail_pct(pos: dict, stop_mode: str, stop_value) -> tuple[float, str]:
    """
    Resolves the OCA lower leg's trailing percent.

    'ATR_AUTO' scales the trail to the stock's own volatility. This matters more
    than it looks: a 1% trail on a name with a 7% average true range fires on
    the first tick of ordinary noise, which cancels the upper leg and turns the
    whole OCA into an expensive market order.
    """
    if stop_mode == "TRAIL_PCT" and stop_value:
        return float(stop_value) / 100.0, f"fixed {float(stop_value):.2f}%"

    atr_pct, source = _position_atr_pct(pos)

    raw = (atr_pct / 100.0) * OCA_EXIT_ATR_FRACTION
    trail = max(OCA_EXIT_MIN_TRAIL_PCT, min(OCA_EXIT_MAX_TRAIL_PCT, raw))
    note = f"auto: {OCA_EXIT_ATR_FRACTION:.0%} of {atr_pct:.2f}% ATR ({source})"
    if abs(trail - raw) > 1e-9:
        note += f", clamped to {trail*100:.2f}%"
    return trail, note


def resolve_oca_limit_price(pos: dict, limit_mode: str, limit_value,
                            ref_price: float, limit_cap=None) -> float | None:
    """
    Resolves the OCA upper leg's limit price from stored *intent*.

    Requests are frequently queued outside market hours, so a literal price
    captured at request time would be stale by the time it is placed. Only
    'ABS' pins an absolute price; everything else is resolved here against the
    live reference price or the position's entry.

    'ATR_AUTO' is the default and the one to reach for on a force sell: it
    targets the current price plus a fraction of the stock's ATR, so the leg is
    reachable within about half a session no matter how far underwater the
    position is. BREAKEVEN, by contrast, demands a bounce proportional to the
    loss already taken.

    limit_cap is the ceiling on the resolved target, and exists because
    PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the
    open means the better the gap, the greedier the target becomes, so it never
    takes the gift it was waiting for. Capping at (typically) breakeven turns
    "sell 4.5% above wherever it opens" into "sell 4.5% above the open, but
    never hold out for more than breakeven" — which is what an exit plan
    actually wants. A capped target that lands below the market is fine: a SELL
    limit cannot fill under its limit price, so it simply fills at the better
    prevailing bid.
    """
    entry = float(pos.get("buy_price") or 0)
    mode = (limit_mode or "ATR_AUTO").upper()

    if mode == "NONE":
        return None
    elif mode == "ATR_AUTO":
        # Anchored to the CURRENT price, not the entry, so the target stays
        # reachable no matter how far underwater the position is. See the
        # OCA_EXIT_UPPER_ATR_FRACTION comment for why this is the default.
        if not ref_price:
            return None
        atr_pct, _ = _position_atr_pct(pos)
        raw  = (atr_pct / 100.0) * OCA_EXIT_UPPER_ATR_FRACTION
        frac = max(OCA_EXIT_MIN_UPPER_PCT, min(OCA_EXIT_MAX_UPPER_PCT, raw))
        price = ref_price * (1 + frac)
    elif mode == "ABS":
        price = float(limit_value) if limit_value else None
    elif mode == "BREAKEVEN":
        price = entry or None
    elif mode == "PCT_FROM_ENTRY":
        price = entry * (1 + float(limit_value or 0) / 100.0) if entry else None
    elif mode == "PCT_FROM_PRICE":
        price = ref_price * (1 + float(limit_value or 0) / 100.0) if ref_price else None
    else:
        return None

    if price and limit_cap and float(limit_cap) > 0:
        price = min(price, float(limit_cap))
    return price


def place_oca_exit(ib: IB, contract, shares: int, limit_price: float | None,
                   trail_pct: float, account: str) -> tuple[str, list]:
    """
    Places the OCA exit pair on an open position:

        upper leg  LMT  SELL @ limit_price   -- the optimistic recovery target
        lower leg  TRAIL SELL trail_pct      -- ratchets up behind any bounce

    The lower leg is deliberately a TRAIL and not a static STP. With a static
    stop, a position that rallies most of the way to the limit and then fades
    still exits at the original stop, surrendering the entire move. A trail
    follows the advance and banks whatever the bounce actually delivered, which
    is the only reason waiting for the upper leg is worth the risk at all.

    ocaType=1 (CANCEL_WITH_BLOCK) is required, not cosmetic: in a cash account
    two SELL orders for the same shares are otherwise liable to be rejected as
    exceeding the position, and a partial fill on one leg must reduce the other
    rather than leave a naked short.

    Returns (oca_group, [trades]).
    """
    group = f"OCA_{contract.symbol}_{int(time.time())}"
    trades = []

    if limit_price and limit_price > 0:
        lmt = Order()
        lmt.action        = 'SELL'
        lmt.orderType     = 'LMT'
        lmt.totalQuantity = shares
        lmt.lmtPrice      = round(float(limit_price), 2)
        lmt.tif           = 'GTC'
        lmt.account       = account
        lmt.ocaGroup      = group
        lmt.ocaType       = 1
        lmt.transmit      = True
        trades.append(ib.placeOrder(contract, lmt))

    trail = TrailingStopOrder('SELL', shares,
                              trailingPercent=round(trail_pct * 100, 2))
    trail.tif      = 'GTC'
    trail.account  = account
    trail.ocaGroup = group
    trail.ocaType  = 1
    trail.transmit = True
    trades.append(ib.placeOrder(contract, trail))

    ib.sleep(1)
    return group, trades


def enqueue_smart_exit(client: Client, ticker: str, reason: str,
                       requested_by: str = "auto") -> bool:
    """
    Route an automated sell rule through the Smart OCA Exit queue.

    Rather than calling place_oca_exit() from each rule, the rule writes the
    same row a human would write with request_exit.py. One placement path, one
    audit trail, one set of backstops — and every rule automatically inherits
    ATR_AUTO sizing, the hard floor and the expiry.

    Only non-urgent Day 7+ rules should call this. Loss-cutting rules must not:
    an OCA suspends the automated ladder for up to `expires_after_days`, which
    is the wrong trade for a position that is actively failing.
    See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.

    Returns True when the exit is owned by the queue (freshly enqueued, or a
    request was already in flight) and the caller must NOT also market-sell.
    Returns False when enqueueing failed, in which case the caller must fall
    back to execute_sell() — never leave a triggered sell rule unexecuted.
    """
    try:
        client.table("exit_requests").insert({
            "ticker":       ticker.upper(),
            "limit_mode":   "ATR_AUTO",
            "stop_mode":    "ATR_AUTO",
            "note":         reason[:500],
            "requested_by": requested_by,
        }).execute()
        print(f"   🎯 {ticker}: queued Smart OCA exit ({requested_by}) — {reason}")
        return True
    except Exception as e:
        msg = str(e)
        # Unique partial index idx_exit_requests_one_active: a request is
        # already in flight for this ticker. That request owns the exit, so
        # this is success, not failure — the rule must stand down either way.
        if "23505" in msg or "idx_exit_requests_one_active" in msg or "duplicate key" in msg.lower():
            print(f"   🎯 {ticker}: exit request already in flight — leaving it to the queue.")
            return True
        if "42P01" in msg or "PGRST205" in msg:
            print(f"   ⚠️ exit_requests table missing — run migrations/add_exit_requests.sql. "
                  f"Falling back to a market sell for {ticker}.")
            return False
        notifier.notify_exception(f"enqueue_smart_exit({ticker}) — execution_agent.py", e)
        return False


def process_exit_requests(ib: IB) -> None:
    """
    Drains the `exit_requests` queue — the Smart OCA Managed Exit.

    Runs on every monitoring cycle rather than only at the open, because an
    exit decision made at 11:00 should not wait until the next session. The
    settle window (OCA_EXIT_SETTLE_MINUTE) only defers requests that arrived
    before the market opened; anything queued intraday is placed at once.

    Two states are handled:
      PENDING -> cancel the existing GTC trail, place the OCA pair, mark PLACED.
      PLACED  -> detect a fill, or enforce the software backstops (hard floor
                 and expiry). An OCA left alone can sit unfilled indefinitely
                 while the position bleeds, so both bounds are mandatory.
    """
    if not OCA_EXIT_ENABLED:
        return

    client = get_supabase_client()
    try:
        rows = (client.table("exit_requests")
                .select("*")
                .in_("status", ["PENDING", "PLACED"])
                .execute().data) or []
    except Exception as e:
        # Table absent (migration not yet applied) must not take down the loop —
        # every other risk rule still needs to run this cycle.
        msg = str(e)
        if "42P01" in msg or "exit_requests" in msg or "PGRST205" in msg:
            print("   ⚠️ exit_requests table missing — run migrations/add_exit_requests.sql")
        else:
            notifier.notify_exception("process_exit_requests() — execution_agent.py", e)
        return

    if not rows:
        return

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.datetime.now(tz)
    print(f"🎯 Smart OCA Exit queue: {len(rows)} request(s) in flight")

    try:
        holdings = {p["ticker"].upper(): p for p in
                    (client.table("portfolio_positions").select("*").execute().data or [])}
    except Exception as e:
        notifier.notify_exception("process_exit_requests() — execution_agent.py", e)
        return

    account = get_ibkr_account(ib)

    for req in rows:
        ticker = (req.get("ticker") or "").upper()
        rid    = req.get("id")
        try:
            pos = holdings.get(ticker)
            if not pos:
                # Already gone — either a leg filled and reconcile_with_ibkr()
                # archived it, or it was sold by another path.
                _close_exit_request(client, rid, "FILLED" if req["status"] == "PLACED" else "CANCELLED",
                                    outcome="LIMIT_OR_TRAIL" if req["status"] == "PLACED" else None,
                                    note="position no longer held")
                print(f"   ✅ {ticker}: position closed — exit request #{rid} resolved.")
                continue

            shares = int(pos["shares"])
            current_price, _ = get_position_price(ib, ticker)
            if current_price <= 0:
                print(f"   ⚠️ {ticker}: no price this cycle — deferring exit request #{rid}.")
                continue

            # ── PENDING: place the OCA ──────────────────────────────────────
            if req["status"] == "PENDING":
                # MARKET mode is not an OCA at all — it is a force sell routed
                # through the queue so it does not require stopping the agent.
                # "Get me out now" and "get me out well" are different
                # intents; conflating them would make every urgent exit wait
                # on a bounce that may never come.
                if (req.get("stop_mode") or "").upper() == "MARKET":
                    reason = req.get("note") or "Queued market exit (request_exit.py --now)"
                    reason = f"Smart Exit Queue — {reason}"
                    print(f"🚨 {ticker}: queued MARKET exit firing — {reason}")
                    cancel_ticker_sell_orders(ib, ticker)
                    ib.sleep(1)
                    buy_price  = float(pos["buy_price"])
                    buy_reason = pos.get("buy_reason", "Unknown")
                    try:
                        buy_date = datetime.datetime.fromisoformat(
                            pos["buy_date"].replace('Z', '+00:00'))
                    except Exception:
                        buy_date = now_ny
                    ok = execute_sell(ib, client, ticker, shares, buy_price, buy_date,
                                      buy_reason, current_price, reason, pos_row=pos)
                    if ok:
                        _close_exit_request(client, rid, "FILLED", outcome="MARKET",
                                            filled_price=current_price,
                                            note="queued market exit")
                    else:
                        # execute_sell() only returns False when it could not
                        # confirm the position left IBKR. Leave PENDING so the
                        # next cycle retries rather than orphaning the position.
                        print(f"   ⚠️ {ticker}: market exit not confirmed — retrying next cycle.")
                    continue

                if now_ny.hour == 9 and now_ny.minute < OCA_EXIT_SETTLE_MINUTE:
                    print(f"   ⏳ {ticker}: waiting for the tape to settle "
                          f"(placing from 09:{OCA_EXIT_SETTLE_MINUTE:02d} ET).")
                    continue

                trail_pct, trail_note = resolve_oca_trail_pct(
                    pos, (req.get("stop_mode") or "ATR_AUTO").upper(), req.get("stop_value"))
                limit_price = resolve_oca_limit_price(
                    pos, req.get("limit_mode"), req.get("limit_value"), current_price,
                    req.get("limit_cap"))

                if limit_price and limit_price <= current_price:
                    # The target is already met. Keep the leg: a SELL limit can
                    # never fill BELOW its limit price, so a marketable one fills
                    # at the better prevailing bid (limit 489.89 into a 495 market
                    # fills near 495). Dropping it here would decline the exact
                    # price the request asked for, and leave the position riding
                    # on the trail alone after its goal had already been reached.
                    # It is also safer than a market order: if the bid collapses
                    # before the fill, the order rests at the limit instead of
                    # chasing the drop down.
                    print(f"   ⚡ {ticker}: limit ${limit_price:.2f} is already marketable "
                          f"vs ${current_price:.2f} — target met, expect an immediate fill "
                          f"at or above the limit.")

                contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(contract)
                # The existing GTC trailing stop MUST go first. Left in place it
                # is a third competing SELL outside the OCA group, so a fill on
                # it would not cancel the OCA legs.
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)

                group, _ = place_oca_exit(ib, contract, shares, limit_price, trail_pct, account)

                floor_pct = float(req.get("hard_floor_pct") or OCA_EXIT_DEFAULT_FLOOR_PCT * 100) / 100.0
                stop_ref  = current_price * (1 - trail_pct)

                client.table("exit_requests").update({
                    "status": "PLACED",
                    "oca_group": group,
                    "placed_at": now_ny.isoformat(),
                    "placed_price": round(current_price, 4),
                    "placed_limit_price": round(limit_price, 2) if limit_price else None,
                    "placed_stop_price": round(stop_ref, 2),
                    "placed_trail_pct": round(trail_pct * 100, 4),
                    "updated_at": now_ny.isoformat(),
                }).eq("id", rid).execute()

                entry = float(pos.get("buy_price") or 0)
                lim_txt = (f"${limit_price:.2f} ({(limit_price/entry-1)*100:+.2f}% vs entry)"
                           if limit_price else "none (trail only)")
                print(f"   🎯 {ticker}: OCA placed — limit {lim_txt}, "
                      f"trail {trail_pct*100:.2f}% ({trail_note}), floor {floor_pct*100:.2f}%")
                notifier._send(
                    f"🎯 <b>{ticker}</b> Smart OCA exit placed\n"
                    f"Limit: {lim_txt}\n"
                    f"Trail: {trail_pct*100:.2f}% (anchor ~${stop_ref:.2f})\n"
                    f"Floor: ${current_price*(1-floor_pct):.2f} · expires in "
                    f"{int(req.get('expires_after_days') or OCA_EXIT_DEFAULT_EXPIRY_DAYS)}d"
                )
                continue

            # ── PLACED: enforce the software backstops ──────────────────────
            placed_price = float(req.get("placed_price") or current_price)
            floor_pct = float(req.get("hard_floor_pct") or OCA_EXIT_DEFAULT_FLOOR_PCT * 100) / 100.0
            floor_level = placed_price * (1 - floor_pct)

            try:
                placed_at = datetime.datetime.fromisoformat(
                    str(req["placed_at"]).replace("Z", "+00:00")).astimezone(tz)
            except Exception:
                placed_at = now_ny
            expiry_days = int(req.get("expires_after_days") or OCA_EXIT_DEFAULT_EXPIRY_DAYS)
            days_open = trading_days_between(placed_at.date(), now_ny.date()) - 1

            buy_price  = float(pos["buy_price"])
            buy_reason = pos.get("buy_reason", "Unknown")
            try:
                buy_date = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
            except Exception:
                buy_date = now_ny

            breach = current_price <= floor_level
            expired = days_open >= expiry_days

            if breach or expired:
                why = (f"hard floor ${floor_level:.2f} breached" if breach
                       else f"expired after {days_open} trading day(s)")
                reason = (f"Smart OCA Exit — {why}; closing at market "
                          f"(limit ${req.get('placed_limit_price') or 0:.2f} never filled)")
                print(f"🚨 {ticker}: Smart OCA backstop firing — {why}")
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                ok = execute_sell(ib, client, ticker, shares, buy_price, buy_date,
                                  buy_reason, current_price, reason, pos_row=pos)
                if ok:
                    _close_exit_request(client, rid, "FILLED",
                                        outcome="FLOOR" if breach else "EXPIRY",
                                        filled_price=current_price, note=why)
                else:
                    print(f"   ⚠️ {ticker}: backstop sell not confirmed — retrying next cycle.")
                continue

            lim = req.get("placed_limit_price")
            lim_txt = f"limit ${float(lim):.2f}" if lim else "trail only"
            print(f"   🎯 {ticker}: OCA live — ${current_price:.2f} | {lim_txt} | "
                  f"floor ${floor_level:.2f} | day {days_open}/{expiry_days}")

        except Exception as req_err:
            notifier.notify_exception(f"process_exit_requests() {ticker} — execution_agent.py", req_err)
            try:
                client.table("exit_requests").update({
                    "last_error": str(req_err)[:500],
                    "updated_at": now_ny.isoformat(),
                }).eq("id", rid).execute()
            except Exception:
                pass
            print(f"   ⚠️ {ticker}: exit request #{rid} failed this cycle: {req_err}")


def _close_exit_request(client: Client, rid, status: str, outcome: str | None = None,
                        filled_price: float | None = None, note: str | None = None) -> None:
    """Terminal-state writer for an exit_requests row."""
    payload = {"status": status, "updated_at": datetime.datetime.now(
        ZoneInfo("America/New_York")).isoformat()}
    if outcome:
        payload["outcome"] = outcome
    if filled_price:
        payload["filled_price"] = round(float(filled_price), 4)
        payload["filled_at"] = payload["updated_at"]
    if note:
        payload["note"] = note
    try:
        client.table("exit_requests").update(payload).eq("id", rid).execute()
    except Exception as e:
        print(f"   ⚠️ Could not close exit request #{rid}: {e}")


def get_oca_managed_tickers(client: Client) -> set:
    """
    Tickers whose exit is currently owned by a Smart OCA request.

    The automated ladder (the Prove-It Stop and Rank & Replace) must not act on
    these. Those rules call execute_sell()/arm_exit(),
    both of which cancel every open SELL order for the ticker — which would
    silently destroy the OCA the user explicitly asked for. The OCA plus its
    floor and expiry backstops fully govern the position instead.
    """
    if not OCA_EXIT_ENABLED:
        return set()
    try:
        rows = (client.table("exit_requests")
                .select("ticker,status")
                .eq("status", "PLACED")
                .execute().data) or []
        # Re-assert the predicate in Python. The server-side .eq() already
        # filters, but this makes the function total: any row that is not
        # unambiguously a PLACED exit_requests row can never suspend the
        # automated exit ladder. Wrongly suspending it would leave a position
        # with no stop at all.
        return {
            str(r["ticker"]).upper() for r in rows
            if isinstance(r, dict) and r.get("status") == "PLACED" and r.get("ticker")
        }
    except Exception:
        return set()


def prove_it_is_proven(pos: dict, highest_unrealized_pct: float = 0.0) -> bool:
    """
    Has this position ever CLOSED above the price we paid?

    This single question selects the Prove-It phase, so it is the most
    load-bearing predicate in the exit ladder. `closed_above_entry` is latched
    True by the EOD block the first time a close prints above entry and is never
    cleared afterwards — a breakout confirms only once.

    Fails SAFE. A missing column (migration not yet applied) reads as None, which
    must never be treated as "unproven": that would apply the tight Phase 1 band
    to a working position. When the latch is unavailable, every available sign
    that the position has traded above entry counts as proof, which is
    deliberately more generous than the close-based latch it stands in for.
    """
    latch = pos.get("closed_above_entry")
    if latch is not None:
        return bool(latch)
    try:
        buy_price = float(pos.get("buy_price") or 0)
    except (TypeError, ValueError):
        return True
    if buy_price <= 0:
        return True
    return (
        highest_unrealized_pct > 0
        or float(pos.get("hwm_price") or 0) > buy_price
        or float(pos.get("intraday_high_today") or 0) > buy_price
    )


def prove_it_p1_threshold_pct(days_held: int) -> float:
    """
    Phase 1 band for a given day of the hold, as a positive fraction below entry.

    Widens after the entry day rather than tightening. A breakout that fails on
    day 0 is wrong immediately; from day 1 the failing and working populations
    overlap, and holding the tight band through day 1 costs far more in clipped
    winners than it saves in cut losers.
    """
    if days_held <= PROVE_IT_P1_DAY0_LAST_DAY:
        return PROVE_IT_P1_DAY0_PCT
    return PROVE_IT_P1_LATER_PCT


def prove_it_stop_level(pos: dict, buy_price: float, days_held: int,
                        highest_unrealized_pct: float) -> tuple[float | None, str]:
    """
    The price at which this position should be protected right now, and which
    phase produced it.

    Phase 1 (unproven) anchors to ENTRY: the breakout has not confirmed, so the
    only meaningful reference is what we paid. Phase 2 (proven) anchors to the
    give-back floor once the peak gain has armed it: the trade went green, so it
    is never allowed to become a real loss. Above +5% the profit ladder in
    TRAIL_PROFIT_TIERS takes over and is tighter than either.

    Returns (None, phase) when no Prove-It level applies — an unarmed Phase 2
    position is governed by the base trailing stop alone.
    """
    if not PROVE_IT_ENABLED or buy_price <= 0:
        return None, "disabled"
    if prove_it_is_proven(pos, highest_unrealized_pct):
        if highest_unrealized_pct < PROVE_IT_P2_ARM_GAIN_PCT * 100.0:
            return None, "phase2-unarmed"
        return buy_price * (1.0 + PROVE_IT_P2_FLOOR_PCT), "phase2"
    return buy_price * (1.0 - prove_it_p1_threshold_pct(days_held)), "phase1"


def prove_it_trail_pct(level: float | None, current_price: float,
                       phase: str) -> float | None:
    """
    Trailing % that parks the resting IBKR stop on `level`.

    IBKR's trailingPercent is measured from the high water mark, and the anchor
    RESETS whenever the order is cancelled and re-placed — which is exactly what
    the tightening block does. So the percentage must be solved against the
    CURRENT price, not a historical peak, or the stop lands somewhere nobody
    intended.

    In Phase 1 the resting order is a backstop behind the bot's armed exit, so it
    sits PROVE_IT_BACKSTOP_SLACK_PCT wider and must never fire first. In Phase 2
    the resting order IS the mechanism, so it sits exactly on the floor.
    """
    if level is None or current_price <= 0:
        return None
    if phase == "phase1":
        level = level * (1.0 - PROVE_IT_BACKSTOP_SLACK_PCT)
    if level >= current_price:
        # Already at or through the level. Nothing sane to place; the bot-side
        # exit is what acts here.
        return None
    return round(1.0 - (level / current_price), 4)


def _compute_dynamic_trail_pct(
    unrealized_pct: float,
    calendar_days: int,
    current_pct: float,
    prove_it_pct: float | None = None,
) -> float | None:
    """
    Returns a tighter trailing stop % if the position has crossed a new tier,
    otherwise returns None (no change needed).

    Two independent levers — the tighter of the two always wins:
      Lever 1 (profit):   unrealized gain % -> TRAIL_PROFIT_TIERS
      Lever 2 (Prove-It): the trail % that pins the resting IBKR stop at the
                          current Prove-It level (see prove_it_trail_pct())

    `calendar_days` is retained for signature stability and for callers that
    still report it; the time lever it fed (TRAIL_TIME_TIERS) is retired — see
    docs/retired_code.md.

    One-way only: result is always strictly less than current_pct.
    Never loosens a stop (a position at 5% trail stays at 5% even if it
    briefly dips below a profit tier threshold).

    That one-way rule is what turns the Prove-It lever into a FIXED floor rather
    than a trail. As price rises, the % needed to keep the stop at the floor
    grows, is looser than what is already placed, and is therefore rejected —
    so the stop stays put. As price falls back toward the floor the required %
    shrinks, is tighter, and is applied — pinning the stop exactly on the floor.
    """
    # Profit lever: find highest threshold the gain has crossed
    profit_trail: float | None = None
    for threshold, pct in TRAIL_PROFIT_TIERS:
        if unrealized_pct >= threshold:
            profit_trail = pct
            break

    candidates = [p for p in (profit_trail, prove_it_pct) if p is not None]
    if not candidates:
        return None

    new_pct = min(candidates)   # tighter of the two levers
    return new_pct if new_pct < current_pct else None


def is_power_hold_active(pos: dict, calendar_days: int) -> bool:
    """
    O'Neil 8-week hold rule.

    True while a position is inside its protected window: it gained
    POWER_HOLD_GAIN_PCT or more within POWER_HOLD_TRIGGER_DAYS of entry, and is
    still within POWER_HOLD_DURATION_DAYS of entry.

    Callers must use this to suppress DISCRETIONARY exits, and to widen the
    trailing stop to POWER_HOLD_TRAIL_PCT. The trailing stop is never suspended,
    so a protected position can still be stopped out if it genuinely breaks down.

    NOTE: the `power_hold` column must be migrated (migrations/add_power_hold.sql).
    Without it the flag cannot persist, so the fallback below only holds while
    calendar_days <= POWER_HOLD_TRIGGER_DAYS — the rule would silently expire at
    day 21 instead of day 56, losing most of its intended effect.
    """
    if not POWER_HOLD_ENABLED:
        return False
    if calendar_days > POWER_HOLD_DURATION_DAYS:
        return False

    # The qualifying run must have happened inside the trigger window. Once the
    # flag is set we keep honouring it, so a later pullback cannot cancel it.
    if pos.get("power_hold"):
        return True

    peak_gain = float(pos.get("highest_unrealized_pct") or 0.0)
    return peak_gain >= POWER_HOLD_GAIN_PCT and calendar_days <= POWER_HOLD_TRIGGER_DAYS


def maybe_arm_power_hold(client: Client, pos: dict, calendar_days: int) -> bool:
    """
    Persists the power-hold flag the first time a position qualifies.

    Returns True if the position is (now) power-held. Degrades gracefully if the
    column has not been migrated yet — the in-memory evaluation still applies.
    """
    if not POWER_HOLD_ENABLED or pos.get("power_hold"):
        return bool(pos.get("power_hold")) and calendar_days <= POWER_HOLD_DURATION_DAYS

    peak_gain = float(pos.get("highest_unrealized_pct") or 0.0)
    qualifies = (
        peak_gain >= POWER_HOLD_GAIN_PCT
        and calendar_days <= POWER_HOLD_TRIGGER_DAYS
    )
    if not qualifies:
        return False

    ticker = pos.get("ticker")
    pos["power_hold"] = True
    try:
        client.table("portfolio_positions").update({"power_hold": True}).eq("ticker", ticker).execute()
    except Exception as e:
        # PGRST204 = column missing (migration not yet run). Not a bug — the rule
        # still applies in-memory for this cycle.
        print(f"   ⚠️ {ticker}: could not persist power_hold flag ({e}). Rule still applied this cycle.")

    print(f"   🏆 {ticker}: 8-WEEK HOLD ARMED — +{peak_gain:.1f}% within {calendar_days}d. "
          f"Discretionary exits suppressed until day {POWER_HOLD_DURATION_DAYS}.")
    try:
        notifier._send(
            f"🏆 <b>{ticker}</b> qualified for the O'Neil 8-week hold rule\n"
            f"Peak gain +{peak_gain:.1f}% within {calendar_days} days of entry.\n"
            f"Discretionary exits suppressed until day {POWER_HOLD_DURATION_DAYS}; "
            f"trailing stop widened to {POWER_HOLD_TRAIL_PCT*100:.0f}% as the disaster backstop."
        )
    except Exception:
        pass
    return True


def cancel_ticker_sell_orders(ib: IB, ticker: str) -> int:
    """Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sells)."""
    cancelled = 0
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.action == 'SELL'
                and trade.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')):
            try:
                ib.cancelOrder(trade.order)
                cancelled += 1
            except Exception:
                pass
    if cancelled:
        print(f"   🗑️  Cancelled {cancelled} open SELL order(s) for {ticker}")
    return cancelled


def handle_mock_sell(ticker: str, price: float, reason: str):
    """Executes a mock sale event directly on Supabase, bypassing IBKR."""
    print(f"🧪 Initiating mock sale for {ticker} at price ${price:.2f} (Reason: {reason})...")
    client = get_supabase_client()
    
    # Fetch existing position
    res = client.table("portfolio_positions").select("*").eq("ticker", ticker.upper()).execute()
    if not res.data:
        print(f"❌ No active position found in Supabase for {ticker.upper()}")
        sys.exit(1)
        
    pos = res.data[0]
    shares = int(pos["shares"])
    buy_price = float(pos["buy_price"])
    buy_date = pos["buy_date"]
    buy_reason = pos.get("buy_reason", "Unknown")
    
    # Calculate returns
    sell_price = price
    profit_loss = round((sell_price - buy_price) * shares, 2)
    percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)
    
    # Insert into trade history
    trade_log = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "buy_reason": buy_reason,
        "sell_price": sell_price,
        "sell_reason": reason,
        "profit_loss": profit_loss,
        "percent_return": percent_return
    }
    
    try:
        # Delete from portfolio
        client.table("portfolio_positions").delete().eq("ticker", ticker.upper()).execute()
        # Insert into history
        client.table("trade_history").insert(trade_log).execute()
        print(f"✅ Mock sale complete! Ticker {ticker} removed and logged to trade_history.")
        print(f"   Return: {percent_return}% | PnL: ${profit_loss:.2f}")
    except Exception as e:
        notifier.notify_exception(f"handle_mock_sell() — execution_agent.py", e)
        print(f"❌ Database error during mock sale execution: {e}")
        sys.exit(1)

def _exit_context_suffix(pos: dict, sell_price: float) -> str:
    """
    Build a human- and machine-readable summary of the risk state a position was
    in at the moment it was closed.

    Broker-side exits are discovered after the fact: the GTC trailing order fires
    at IBKR without consulting the agent, so the only record written used to be
    the bare label "Trailing stop (IBKR GTC TRAIL order)". That is true but
    useless for review — it does not say what trail was in force, what peak the
    trail was anchored to, how long the position had been held, or how far it had
    run before it turned. All of those live on the `portfolio_positions` row,
    which is deleted moments later, so they are lost permanently unless captured
    here.

    Returns an empty string when the row carries nothing worth recording, so a
    sparse position never produces a reason string full of dangling em-dashes.
    """
    parts: list[str] = []

    hwm = pos.get("hwm_price")
    trail_pct = pos.get("stop_loss_pct")

    try:
        hwm = float(hwm) if hwm is not None else None
    except (TypeError, ValueError):
        hwm = None
    try:
        trail_pct = float(trail_pct) if trail_pct is not None else None
    except (TypeError, ValueError):
        trail_pct = None

    if trail_pct is not None:
        parts.append(f"trail {trail_pct * 100:.2f}%")

    if hwm is not None and hwm > 0:
        hwm_date = pos.get("hwm_date")
        parts.append(f"HWM ${hwm:.2f}" + (f" set {hwm_date}" if hwm_date else ""))
        if trail_pct is not None:
            # The price the resting order would have been sitting at. Labelled
            # "implied" because the agent never observed the broker's actual
            # trigger — it is reconstructed from the trail and the peak we know.
            parts.append(f"implied trigger ${hwm * (1 - trail_pct):.2f}")

    days_held = pos.get("days_held")
    if days_held is not None:
        parts.append(f"day {days_held} of hold")

    peak_pct = pos.get("highest_unrealized_pct")
    if peak_pct is not None:
        try:
            parts.append(f"peak {float(peak_pct):+.2f}%")
        except (TypeError, ValueError):
            pass

    if pos.get("exit_armed"):
        armed_price = pos.get("exit_armed_price")
        armed_bits = "armed"
        if armed_price:
            try:
                armed_bits += f" at ${float(armed_price):.2f}"
            except (TypeError, ValueError):
                pass
        armed_reason = pos.get("exit_armed_reason")
        if armed_reason:
            armed_bits += f" ({armed_reason})"
        parts.append(armed_bits)

    if pos.get("power_hold"):
        parts.append("power hold active")

    if not parts:
        return ""
    return " — " + ", ".join(parts)


# Set once when Supabase rejects the IBKR valuation columns, so the warning is
# printed a single time per process instead of on every 15-minute cycle.
_IBKR_VALUATION_COLUMNS_MISSING = False


def _sync_ibkr_position_values(client: Client, ib_map: dict, tickers) -> int:
    """
    Persist IBKR's own valuation of each open position onto portfolio_positions.

    The read-only web container has no brokerage access, so without these columns
    the dashboard had to value positions as shares (Supabase) x price (FMP quote).
    That never matched the broker, and it added a live third-party price to an
    IBKR cash balance refreshed only once per agent cycle — mixing two vintages
    of data in one total.

    Values come from ib.portfolio() PortfolioItem objects, which read the account
    update stream. This deliberately does NOT use ib.reqTickers(), which blocks
    indefinitely when the ushmds data farm is down.

    Degrades gracefully when migrations/add_ibkr_position_values.sql has not been
    applied: PGRST204 disables the write for the rest of the process rather than
    failing reconciliation.

    Returns the number of positions whose valuation was written.
    """
    global _IBKR_VALUATION_COLUMNS_MISSING
    if _IBKR_VALUATION_COLUMNS_MISSING:
        return 0

    synced_at = datetime.datetime.now(ZoneInfo("America/New_York")).isoformat()
    written = 0

    for ticker in tickers:
        item = ib_map.get(ticker)
        # The positions() fallback path yields Position objects, which carry no
        # valuation. Skip them rather than writing a price derived from our own
        # cost basis — a stale broker mark is recoverable, a fabricated one is not.
        market_price = getattr(item, "marketPrice", None)
        if market_price is None or float(market_price) <= 0:
            continue

        payload = {
            "current_price":  round(float(market_price), 4),
            "market_value":   round(float(getattr(item, "marketValue", 0) or 0), 2),
            "unrealized_pnl": round(float(getattr(item, "unrealizedPNL", 0) or 0), 2),
            "ibkr_synced_at": synced_at,
        }
        try:
            client.table("portfolio_positions").update(payload).eq("ticker", ticker).execute()
            written += 1
        except Exception as e:
            msg = str(e)
            if "PGRST204" in msg or "column" in msg.lower():
                _IBKR_VALUATION_COLUMNS_MISSING = True
                print("   ⚠️  IBKR valuation columns missing — run "
                      "migrations/add_ibkr_position_values.sql. Dashboard will show "
                      "cost basis until then.")
                return written
            print(f"   ⚠️  Could not write IBKR valuation for {ticker}: {e}")

    if written:
        print(f"   💵 Synced IBKR valuation for {written} position(s).")
    return written


def reconcile_with_ibkr(ib: IB):
    """
    Full bidirectional reconciliation between IBKR actual positions and Supabase ledger.
    Runs every monitoring cycle (every 15 min during market hours).

    Case 1 — In Supabase, NOT in IBKR:
        Position was closed manually in TWS. Log to trade_history and remove from portfolio.

    Case 2 — In IBKR, NOT in Supabase:
        Position was opened manually in TWS. Insert into portfolio with computed stop/target.

    Case 3 — In both, but share count differs:
        Partial fill or manual adjustment. Update share count in Supabase.
    """
    print("🔄 Running IBKR ↔ Supabase reconciliation...")
    client = get_supabase_client()

    # ── Sync live balance to Supabase (Do this FIRST) ──────────────────────
    try:
        tz = ZoneInfo("America/New_York")
        today_str = datetime.datetime.now(tz).date().strftime("%Y-%m-%d")

        target_account = get_ibkr_account(ib)

        # own_cash: only our deposited money (TotalCashValue ≥ 0).
        # margin_loan: borrowed amount when TotalCashValue < 0 (0 if no loan).
        own_cash    = get_own_cash(ib, target_account)
        margin_loan = get_margin_loan(ib, target_account)
        # ibkr_cash_balance historically stored AvailableFunds; we now write own_cash
        # so the column remains meaningful (own money only, no margin).
        cash_balance = own_cash

        db_pos = client.table("portfolio_positions").select(
            "ticker,shares,buy_price"
        ).execute().data or []

        # Position value for the account rollup uses IBKR's own mark first
        # (PortfolioItem.marketPrice) so account_balances agrees with both the
        # dashboard and the exit logic. FMP is only a per-ticker fallback, and
        # cost basis is the final fallback. get_position_price() reads the
        # non-blocking ib.portfolio() cache — never the blocking reqTickers().
        ib_price_map = build_ibkr_price_map(ib)
        pos_value = 0.0
        for p in db_pos:
            price, src = get_position_price(ib, p["ticker"], ib_price_map)
            if price <= 0:
                price = float(p["buy_price"])   # final fallback: cost basis
            pos_value += int(p["shares"]) * price

        net_liq = cash_balance + pos_value

        if net_liq > 0:
            upsert_payload = {
                "date":                 today_str,
                "ibkr_cash_balance":    round(cash_balance, 2),
                "ibkr_positions_value": round(pos_value, 2),
                "ibkr_total_value":     round(net_liq, 2),
                "ibkr_own_cash":        round(own_cash, 2),
                "ibkr_margin_loan":     round(margin_loan, 2),
            }
            client.table("account_balances").upsert(upsert_payload).execute()
            margin_note = f" ⚠️ MARGIN LOAN: ${margin_loan:,.2f}" if margin_loan > 0 else ""
            print(f"   💰 Balance synced [{target_account}]: own_cash=${cash_balance:,.2f} "
                  f"positions=${pos_value:,.2f} net_liq=${net_liq:,.2f} "
                  f"({len(db_pos)} position(s)){margin_note}")
    except Exception as e:
        notifier.notify_exception("reconcile_with_ibkr() cash sync — execution_agent.py", e)
        print(f"   ❌ Could not sync cash balance: {e}")



    # ── Fetch IBKR positions via portfolio() with positions() fallback ───────
    # portfolio() reads from the in-memory account cache which may be empty
    # after a reconnect. We call reqPositions() first (unconditional TWS push)
    # to populate ib.positions(), then prefer portfolio() for richer data but
    # fall back to positions() if portfolio() is still empty.
    try:
        try:
            ib.reqPositions()
            ib.sleep(2)   # let event loop populate ib.positions()
        except Exception as _rp_err:
            print(f"   ⚠️  reqPositions() failed (non-fatal): {_rp_err}")

        target_account = get_ibkr_account(ib)
        ib_raw = [
            p for p in ib.portfolio()
            if _matches_account(p, target_account)
        ]

        if not ib_raw:
            _pos_fallback = [
                p for p in ib.positions()
                if p.contract.secType == "STK" and p.position > 0 and _matches_account(p, target_account)
            ]
            if _pos_fallback:
                print(f"   ⚠️  portfolio() empty — using positions() fallback "
                      f"({len(_pos_fallback)} position(s)).")
                ib_map = {p.contract.symbol: p for p in _pos_fallback}
            else:
                ib_map = {}   # genuinely empty — guard below will handle
        else:
            for p in ib_raw:
                if p.contract.secType == "STK" and int(p.position) < 0:
                    msg = (f"🚨 SHORT POSITION DETECTED: {p.contract.symbol} "
                           f"has {int(p.position)} shares. Close this immediately in TWS!")
                    print(msg)
                    try:
                        notifier.notify_error(msg)
                    except Exception:
                        pass
            ib_map = {
                p.contract.symbol: p
                for p in ib_raw
                if p.contract.secType == "STK" and int(p.position) > 0
            }
    except Exception as e:
        notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
        print(f"❌ Could not fetch IBKR positions during reconciliation: {e}")
        return

    ib_tickers = set(ib_map.keys())

    # ── Fetch Supabase positions ────────────────────────────────────────────
    try:
        res = client.table("portfolio_positions").select("*").execute()
        supabase_positions = res.data or []
    except Exception as e:
        notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
        print(f"❌ Could not fetch Supabase positions during reconciliation: {e}")
        return

    supabase_map = {p["ticker"]: p for p in supabase_positions}
    supabase_tickers = set(supabase_map.keys())

    # ── Safety guard: empty IBKR response while Supabase has positions ──────
    # ib.portfolio() transiently returns [] when account data hasn't finished
    # loading after a reconnect. Without this guard, Case 1 would delete every
    # Supabase position on a false "not in IBKR" signal.
    #
    # However: if portfolio() is empty but reqExecutions() shows a confirmed
    # SLD fill for one of our tickers, that fill is real and must be processed
    # regardless. We handle confirmed fills individually here, then return to
    # skip bulk reconciliation (which is still unsafe on an empty read).
    if not ib_tickers and supabase_tickers:
        print(f"   ⚠️  IBKR returned empty portfolio but Supabase has "
              f"{len(supabase_tickers)} position(s). "
              f"Checking executions for confirmed fills before skipping...")

        # Check each Supabase ticker for a confirmed SLD fill
        try:
            all_fills = ib.reqExecutions()
        except Exception as _fe:
            print(f"   ⚠️  reqExecutions() failed: {_fe}")
            all_fills = []

        confirmed_sold = set()
        for fill in all_fills:
            if (fill.contract.secType == "STK"
                    and fill.execution.side == "SLD"
                    and fill.contract.symbol in supabase_tickers):
                confirmed_sold.add(fill.contract.symbol)

        if confirmed_sold:
            print(f"   🔍 Confirmed SLD fills found for: {confirmed_sold} — processing individually.")
            # Temporarily set ib_map to empty (no live positions for these tickers)
            # so Case 1 logic below handles them. We restrict candidates_to_delete
            # to only confirmed fills to avoid false deletions on the others.
            candidates_to_delete = confirmed_sold
            changes = 0
            # Fall through to Case 1 loop with restricted candidates
        else:
            print(f"   ℹ️  No confirmed SLD fills found — skipping reconcile to prevent false deletion.")
            return

    else:
        candidates_to_delete = supabase_tickers - ib_tickers
        changes = 0

    # ── Case 1: In Supabase but NOT in IBKR ─────────────────────────────────
    # IBKR is the single source of truth: it manages trailing stops via GTC
    # TRAIL orders. Any position missing from IBKR portfolio was legitimately
    # closed (trailing stop fired or manual TWS close). Guard 1 above (empty
    # portfolio) is the only transient-glitch guard needed.
    for ticker in candidates_to_delete:
        pos = supabase_map[ticker]
        print(f"   ✅ {ticker}: position closed in IBKR — archiving to trade_history.")

        # ── Determine sell price — three-tier lookup ──────────────────────
        #
        # Tier 1: ibkr_fills Supabase table (real-time hook writes fills here
        #         the instant they happen — durable across restarts)
        # Tier 2: reqExecutions() TWS session cache (fast path for fills that
        #         arrived in the current session, < few minutes old)
        # Tier 3: Flex Query TradeConfirm (IBKR Transaction History API,
        #         on-demand, 5-10 min lag — requires IBKR_FLEX_EXEC_QUERY_ID)
        # Fallback: PRICE_UNCERTAIN alert — Telegram + flagged sell_reason
        #
        # This architecture was introduced 2026-07-21 after the RSI incident
        # where a trailing stop fill from 2026-07-17 was recorded at the wrong
        # day's FMP price because reqExecutions() lost the fill over a weekend.
        sell_price        = 0.0
        sell_price_source = "unknown"
        sell_date_fill    = None
        has_sld_fill      = False

        # ── Tier 1: ibkr_fills (persistent Supabase table) ───────────────
        try:
            sb_fills_res = client.table("ibkr_fills") \
                .select("exec_id,shares,price,fill_time") \
                .eq("ticker", ticker).eq("side", "SLD") \
                .order("fill_time", desc=False) \
                .execute()
            sb_fills = sb_fills_res.data or []
            if sb_fills:
                total_qty  = sum(float(f["shares"]) for f in sb_fills)
                sell_price = (
                    sum(float(f["shares"]) * float(f["price"]) for f in sb_fills)
                    / total_qty
                ) if total_qty > 0 else 0.0
                exec_ids   = ", ".join(f["exec_id"] for f in sb_fills)
                # Full timestamp, not just the date. `ibkr_fills.fill_time` is
                # written from `fill.execution.time.isoformat()` (a tz-aware UTC
                # instant), so the precision is already there — truncating it
                # with [:10] threw it away and stamped every exit at midnight.
                # Midnight is EARLIER than the entry for any position bought and
                # closed the same session, which made same-day exits compute a
                # negative holding period.
                #
                # The LAST fill is used, not the first: this row records a
                # *closed* position, and it is not closed until the final share
                # is sold. `sb_fills` is ordered fill_time ascending by the query.
                sell_date_fill = sb_fills[-1]["fill_time"]
                sell_price_source = (
                    f"ibkr_fills (persistent DB, {len(sb_fills)} fill(s), "
                    f"execIds: {exec_ids})"
                )
                has_sld_fill = True
                print(f"        💾 Tier 1 — ibkr_fills: {len(sb_fills)} fill(s) → "
                      f"weighted avg ${sell_price:.4f} on {sell_date_fill}")
        except Exception as ex:
            print(f"        ⚠️  ibkr_fills lookup failed (non-fatal): {ex}")

        # ── Tier 2: reqExecutions() session cache ─────────────────────────
        if not has_sld_fill:
            try:
                session_fills = ib.reqExecutions()
                sell_fills = [
                    f for f in session_fills
                    if f.contract.symbol == ticker and f.execution.side == "SLD"
                ]
                if sell_fills:
                    total_qty  = sum(f.execution.shares for f in sell_fills)
                    sell_price = (
                        sum(f.execution.shares * f.execution.price for f in sell_fills)
                        / total_qty
                    ) if total_qty > 0 else 0.0
                    exec_ids   = ", ".join(f.execution.execId for f in sell_fills)
                    sell_fills_sorted = sorted(sell_fills, key=lambda f: f.execution.time)
                    # Keep the time: `execution.time` is a tz-aware UTC instant,
                    # and .date() discarded it. See the Tier 1 note above — the
                    # last fill is the moment the position actually closed.
                    sell_date_fill    = sell_fills_sorted[-1].execution.time.isoformat()
                    sell_price_source = (
                        f"reqExecutions session cache weighted avg "
                        f"({len(sell_fills)} fill(s), execIds: {exec_ids})"
                    )
                    has_sld_fill = True
                    print(f"        📡 Tier 2 — reqExecutions: {len(sell_fills)} fill(s) → "
                          f"weighted avg ${sell_price:.4f} on {sell_date_fill}")
            except Exception as ex:
                notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", ex)
                print(f"        ⚠️  reqExecutions() failed for {ticker}: {ex}")

        # ── Tier 3: Flex Query TradeConfirm (IBKR Transaction History) ────
        if not has_sld_fill:
            print(f"        🔍 Tier 3 — trying Flex TradeConfirm for {ticker}...")
            flex_data = fetch_trade_confirms_for_ticker(ticker)
            if flex_data:
                sell_price        = flex_data["sell_price"]
                sell_date_fill    = flex_data["sell_date"]
                sell_price_source = flex_data["source"]
                has_sld_fill      = True
                print(f"        📋 Tier 3 — Flex TradeConfirm: "
                      f"weighted avg ${sell_price:.4f} on {sell_date_fill}")

        # If no SLD fill (e.g. manual TWS close or stale session), do a single
        # double-check to rule out a transient partial portfolio read.
        if not has_sld_fill:
            ib.sleep(3)
            _ib_recheck = {
                p.contract.symbol: p for p in ib.portfolio()
                if p.contract.secType == "STK" and int(p.position) > 0
            }
            if ticker in _ib_recheck:
                print(f"        ⚠️  {ticker} reappeared on double-check — skipping (transient IBKR glitch).")
                continue
            print(f"        ℹ️  No SLD fill in current TWS session — "
                  f"fills from prior sessions are NOT in cache.")

        # Cancel any remaining SELL orders for this ticker (cleanup)
        cancel_ticker_sell_orders(ib, ticker)

        # ── FIX (Bug 2 & 4): FMP fallback — flag as PRICE_UNCERTAIN ─────────
        # When fills are not in the current session (e.g. trailing stop fired
        # over a weekend), the FMP live price is the WRONG DAY's price.
        # We still record it (to avoid a zero-price row) but prefix with
        # PRICE_UNCERTAIN so the record is visibly flagged for manual correction.
        if sell_price <= 0:
            fmp_price = get_live_price(ticker)
            if fmp_price > 0:
                sell_price        = fmp_price
                sell_price_source = (
                    "⚠️ PRICE_UNCERTAIN — FMP live quote used as fallback "
                    "(fill not in current TWS session; actual fill may differ — "
                    "verify against IBKR transaction history)"
                )
                notifier.notify_error(
                    f"⚠️ {ticker} sell price is UNCERTAIN\n"
                    f"reqExecutions() found no fills in the current TWS session.\n"
                    f"Using FMP live price ${fmp_price:.2f} as a placeholder.\n"
                    f"Check IBKR transaction history and correct manually."
                )
                print(f"        ⚠️  PRICE_UNCERTAIN: using FMP ${fmp_price:.2f} as placeholder. "
                      f"Verify in IBKR transaction history.")

        # Fallback 2: buy_price (last resort — prevents a zero-price DB row)
        if sell_price <= 0:
            sell_price        = float(pos["buy_price"])
            sell_price_source = "buy_price (no price source available)"

        print(f"        Sell price source: {sell_price_source} → ${sell_price:.2f}")

        shares     = int(pos["shares"])
        buy_price  = float(pos["buy_price"])
        buy_date   = pos["buy_date"]
        buy_reason = pos.get("buy_reason", "Unknown")
        profit_loss    = round((sell_price - buy_price) * shares, 2)
        percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)

        # ── FIX (Bug 4): correct sell_reason based on whether a fill was found ─
        #
        # Record the exit CONTEXT, not just the exit label. The bare string
        # "Trailing stop (IBKR GTC TRAIL order)" is all that used to be written
        # here, which left the dashboard unable to answer the first question an
        # operator asks about a stopped-out trade: what was the trail, and what
        # peak was it anchored to? Every one of those numbers is sitting on the
        # `pos` row we are about to delete, so discarding them was gratuitous —
        # once the row is gone they are unrecoverable.
        #
        # These are appended to the free-text reason rather than added as new
        # columns so that no schema migration is required and the existing
        # `trade_history` shape is untouched; the dashboard parses them back out.
        if has_sld_fill:
            sell_reason = "Trailing stop (IBKR GTC TRAIL order)" + _exit_context_suffix(pos, sell_price)
        else:
            sell_reason = "Manual close in IBKR (reconciled) — PRICE_UNCERTAIN" + _exit_context_suffix(pos, sell_price)

        # ── FIX (Bug 3): write explicit sell_date from fill timestamp ─────────
        trade_log = {
            "ticker":         ticker,
            "shares":         shares,
            "buy_price":      buy_price,
            "buy_date":       buy_date,
            "buy_reason":     buy_reason,
            "sell_price":     sell_price,
            "sell_reason":    sell_reason,
            "sell_date":      sell_date_fill,   # None when fill not in session (Supabase auto-stamps)
            "profit_loss":    profit_loss,
            "percent_return": percent_return,
        }
        try:
            # Delete from portfolio FIRST, independently of trade history
            client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            changes += 1
            print(f"        ✅ Removed {ticker} from Supabase portfolio.")
            
            try:
                # Then try to insert to trade_history
                client.table("trade_history").insert(trade_log).execute()
                print(f"        ✅ Logged to history. PnL: ${profit_loss:+.2f} ({percent_return:+.2f}%)")
                _write_breakout_learning_row(
                    client=client,
                    ticker=ticker,
                    buy_date=datetime.datetime.fromisoformat(str(buy_date).replace("Z", "+00:00")),
                    reason=sell_reason,
                    pos_row=pos,
                    market_regime="neutral",
                    percent_return=percent_return,
                )
                notifier.notify_manual_close(
                    ticker=ticker, shares=shares, buy_price=buy_price,
                    sell_price=sell_price, sell_price_source=sell_price_source,
                    buy_date=buy_date
                )
            except Exception as e:
                notifier.notify_exception(f"reconcile_with_ibkr() (trade_history insert) — execution_agent.py", e)
                print(f"        ❌ DB error adding {ticker} to trade_history: {e}")
        except Exception as e:
            notifier.notify_exception(f"reconcile_with_ibkr() (portfolio delete) — execution_agent.py", e)
            print(f"        ❌ DB error removing {ticker} from portfolio: {e}")

    # ── Case 2: In IBKR but NOT in Supabase (manual buy / opened in TWS) ───
    for ticker in ib_tickers - supabase_tickers:
        ib_pos = ib_map[ticker]
        shares = int(ib_pos.position)
        avg_cost = round(float(ib_pos.averageCost), 2)   # PortfolioItem uses averageCost (Bug #5)

        if avg_cost <= 0:
            print(f"   ⚠️  {ticker}: in IBKR with zero avg cost — skipping.")
            continue

        print(f"   ⚠️  {ticker}: in IBKR but not in Supabase — manual buy detected.")

        stop_loss = round(avg_cost * (1 - STOP_LOSS_PCT), 2)
        buy_date = datetime.datetime.now(datetime.timezone.utc).isoformat()

        position_data = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": avg_cost,
            "buy_date": buy_date,
            "buy_reason": "Manual IBKR order (reconciled)",
            "buy_source": "daily_triggers",   # Bug fix: always set buy_source to prevent NULL
            "hwm_date":  datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "hwm_price": avg_cost,   # initialised to buy price; ratchets up in monitor loop
            "entry_rs_score": _get_entry_rs(ticker, None),   # live-fetched so Rule 1 has a baseline
        }
        try:
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"        ✅ Added to Supabase: {shares} shares @ ${avg_cost} "
                  f"| Trail: {STOP_LOSS_PCT*100:.2f}% (IBKR-managed)")
            changes += 1
        except Exception as e:
            notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
            print(f"        ❌ DB error adding {ticker} to Supabase: {e}")

    # ── Case 3: In both, but share count mismatch (partial fill / adjustment)
    for ticker in ib_tickers & supabase_tickers:
        ib_shares = int(ib_map[ticker].position)
        db_shares = int(supabase_map[ticker]["shares"])
        if ib_shares != db_shares:
            print(f"   ⚠️  {ticker}: share count mismatch — IBKR: {ib_shares}, Supabase: {db_shares}. Correcting.")
            try:
                client.table("portfolio_positions").update({"shares": ib_shares}).eq("ticker", ticker).execute()
                print(f"        ✅ Updated to {ib_shares} shares.")
                changes += 1
            except Exception as e:
                notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
                print(f"        ❌ DB error updating shares for {ticker}: {e}")

    # ── Persist IBKR's own valuation for every position we agree exists ──────
    # Written after the share-count correction above so market_value is stored
    # alongside a share count IBKR has already confirmed.
    _sync_ibkr_position_values(client, ib_map, ib_tickers & supabase_tickers)

    if changes == 0:
        print("   ✅ Supabase and IBKR are in sync. No changes needed.")
    else:
        print(f"   🔄 Reconciliation complete — {changes} correction(s) applied.")


def _fetch_market_closes(ticker: str) -> list[tuple[str, float]]:
    """Sorted (date, close) daily history for `ticker`, oldest first.

    Returns an empty list on any transport, status or payload problem so that
    every failure mode reaches the caller identically and is treated as bearish.
    """
    to_date   = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    from_date = to_date - datetime.timedelta(
        days=int((MARKET_DIRECTION_SMA_WINDOW + MARKET_DIRECTION_SLOPE_DAYS) * 1.6) + 60)
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={FMP_API_KEY}")
    r = fmp_session.get(url, timeout=10)
    if r.status_code != 200:
        print(f"⚠️ Market direction: HTTP {r.status_code} for {ticker}.")
        return []
    data = r.json()
    if not isinstance(data, list):
        print(f"⚠️ Market direction: unexpected payload for {ticker}.")
        return []
    rows = []
    for d in data:
        try:
            close = float(d["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close > 0 and d.get("date"):
            rows.append((str(d["date"])[:10], close))
    rows.sort(key=lambda x: x[0])
    return rows


def _index_is_bullish(ticker: str) -> tuple[bool, bool] | None:
    """Per-index verdict as ``(above_sma, slope_ok)``, or None if data is unusable.

    Bullish requires the latest close to sit more than MARKET_DIRECTION_BUFFER_PCT
    above the SMA-200. The buffer is deliberately asymmetric-free: the same
    threshold governs entry and exit, so the gate is a simple line with a
    dead-band rather than a hysteresis loop.
    """
    rows = _fetch_market_closes(ticker)
    needed = MARKET_DIRECTION_SMA_WINDOW + MARKET_DIRECTION_SLOPE_DAYS
    if len(rows) < needed:
        print(f"⚠️ Market direction: only {len(rows)} sessions for {ticker}, "
              f"need {needed}.")
        return None

    last_date = datetime.date.fromisoformat(rows[-1][0])
    today_ny  = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    if (today_ny - last_date).days > MARKET_DIRECTION_MAX_STALE_DAYS:
        print(f"⚠️ Market direction: {ticker} data stale (last {last_date}).")
        return None

    closes = [c for _, c in rows]
    w = MARKET_DIRECTION_SMA_WINDOW
    sma_now  = sum(closes[-w:]) / w
    sma_then = sum(closes[-w - MARKET_DIRECTION_SLOPE_DAYS:
                          -MARKET_DIRECTION_SLOPE_DAYS]) / w
    latest   = closes[-1]

    above = latest > sma_now * (1 + MARKET_DIRECTION_BUFFER_PCT)
    slope_ok = sma_then > 0 and (sma_now - sma_then) / sma_then >= 0
    print(f"📊 {ticker}: ${latest:.2f} vs SMA{w} ${sma_now:.2f} "
          f"(+{MARKET_DIRECTION_BUFFER_PCT * 100:.1f}% buffer) "
          f"{'above' if above else 'below'}, "
          f"SMA{w} slope {MARKET_DIRECTION_SLOPE_DAYS}d "
          f"{'flat/up' if slope_ok else 'down'}")
    return bool(above), bool(slope_ok)


def is_market_bullish() -> bool:
    """CANSLIM 'M' (Market Direction) filter — fail-closed.

    Bullish requires **every** benchmark in MARKET_DIRECTION_TICKERS to close
    above its SMA-200 by MARKET_DIRECTION_BUFFER_PCT, and **at least one** of
    those SMA-200s to be non-falling over MARKET_DIRECTION_SLOPE_DAYS.

    Every failure mode — HTTP error, malformed payload, insufficient history,
    stale data, unhandled exception — returns False. Standing down costs a day
    of opportunity; buying into an undiagnosed bear market costs capital.
    MARKET_DIRECTION_FILTER_ENABLED=false is the only bypass.
    """
    if not MARKET_DIRECTION_FILTER_ENABLED:
        return True
    if not MARKET_DIRECTION_TICKERS:
        print("⚠️ Market direction: no benchmarks configured → BEAR (fail-closed).")
        return False
    try:
        above_all, slope_any = True, False
        for ticker in MARKET_DIRECTION_TICKERS:
            verdict = _index_is_bullish(ticker)
            if verdict is None:
                print(f"⚠️ Market direction: {ticker} unusable → BEAR (fail-closed).")
                return False
            above, slope_ok = verdict
            above_all = above_all and above
            slope_any = slope_any or slope_ok
        bullish = above_all and slope_any
        print(f"📊 Market direction [{'+'.join(MARKET_DIRECTION_TICKERS)}]: "
              f"→ {'BULL ↑' if bullish else 'BEAR ↓'}")
        return bullish
    except Exception as e:
        notifier.notify_exception(f"is_market_bullish() — execution_agent.py", e)
        print(f"⚠️ Market direction check failed: {e}. Defaulting to BEAR (fail-closed).")
        return False

def fetch_ibkr_delayed_price(ib: IB, contract) -> tuple:
    """Fetch the current price for a contract using IBKR delayed market data (type 3).

    Prefers the ask price; falls back to last traded price.
    Always restores live market data mode (type 1) after the call.

    Returns:
        (price: float, method: str) where method is 'ask', 'last', or '' on failure.
        price is 0.0 when no valid price is available.
    """
    ibkr_price   = 0.0
    price_method = ""
    try:
        ib.reqMarketDataType(3)          # Switch to delayed data (free, 15-20 min lag)
        _tickers = ib.reqTickers(contract)
        if _tickers:
            _t    = _tickers[0]
            _ask  = _t.ask  if _t.ask  == _t.ask  and _t.ask  > 0 else 0.0
            _last = _t.last if _t.last == _t.last and _t.last > 0 else 0.0
            _p    = _ask if _ask > 0 else _last
            if _p > 0:
                ibkr_price   = _p
                price_method = "ask" if _ask > 0 else "last"
    except Exception as _de:
        print(f"   ⚠️ IBKR delayed price failed: {_de}")
    finally:
        ib.reqMarketDataType(1)          # Always restore live mode
    return ibkr_price, price_method


_schema_alert_sent = False


def assert_schema_ok(client) -> bool:
    """Verify risk-rule columns exist. Returns False when new buys must be blocked.

    Alerts once per degradation episode rather than every 15-minute cycle, and
    sends a recovery notice when the migration is applied, so the Telegram signal
    stays meaningful.
    """
    global _schema_alert_sent
    try:
        report = schema_guard.check_schema(client)
    except Exception as e:
        # A probe failure must not stop trading — that would turn a monitoring
        # concern into an outage. Log and allow the cycle to proceed.
        print(f"   ⚠️ Schema check failed to run ({e}) — continuing without it.")
        return True

    if report.degraded:
        print("🚨 SCHEMA DEGRADED — new buys blocked this cycle:")
        for table, col, why in report.missing_critical:
            print(f"   • MISSING {table}.{col} — {why}")
        print(f"   Fix: run {schema_guard.REPAIR_SCRIPT} in the Supabase SQL Editor.")
        if not _schema_alert_sent:
            try:
                notifier.notify_error(report.summary())
            except Exception:
                pass
            _schema_alert_sent = True
        return False

    if report.missing_advisory:
        for table, why in report.missing_advisory:
            print(f"   ⚠️ Analytics table missing: {table} — {why}")

    if _schema_alert_sent:
        print("✅ Schema restored — new buys re-enabled.")
        try:
            notifier.notify_error(
                "✅ *Schema restored*\nAll risk-rule columns are present again. "
                "New buys are re-enabled."
            )
        except Exception:
            pass
        _schema_alert_sent = False
    return True


def run_market_open_buys(ib: IB):
    """Checks for daily breakout triggers and executes buy orders at market open."""
    print("⏳ Running Market Open Buy checks...")
    client = get_supabase_client()

    # ── Schema degradation hard block ─────────────────────────────────────────
    # If a column a live risk rule depends on is missing, that rule is silently
    # inert (see schema_guard). Opening NEW positions while the controls meant to
    # protect them are impaired is the specific mistake this prevents. Existing
    # positions continue to be monitored and exited normally.
    #
    # Re-checked every cycle (it is a handful of LIMIT 1 queries), so applying the
    # migration clears this automatically without restarting the container.
    if not assert_schema_ok(client):
        return

    # ── Margin-loan hard block ────────────────────────────────────────────────
    # Before evaluating any triggers, verify we are investing only our own money.
    # If TotalCashValue is negative, IBKR has lent us money and we must not buy
    # anything until the margin balance is restored to zero.
    margin_loan = get_margin_loan(ib)
    if margin_loan > 0:
        msg = (
            f"🚨 *MARGIN LOAN ACTIVE — Buys Blocked*\n"
            f"IBKR TotalCashValue is negative.\n"
            f"Margin borrowed: ${margin_loan:,.2f}\n"
            f"No new positions will be opened until the loan is fully repaid.\n"
            f"Action: check IBKR account and repay or close positions to reduce margin."
        )
        print(f"🚨 MARGIN LOAN ACTIVE (${margin_loan:,.2f} borrowed). "
              f"All buys blocked for this cycle.")
        try:
            notifier.notify_error(msg)
        except Exception:
            pass
        return

    # ── Market direction hard gate (fail-closed on data errors) ─────────────────
    if MARKET_DIRECTION_FILTER_ENABLED and not is_market_bullish():
        print("📊 Market bearish (benchmark below SMA-200 buffer, falling SMA-200, "
              "or data unavailable). Standing down from new buys.")
        return

    
    # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
    tz = ZoneInfo("America/New_York")
    today_ny = datetime.datetime.now(tz).date()
    today_str = today_ny.strftime("%Y-%m-%d")
    recent_date = (today_ny - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    
    try:
        triggers_res = client.table("daily_triggers").select("*").gte("triggered_at", recent_date).execute()
        triggers = triggers_res.data
        # Sort by final_score (quality + AI bonus) descending.
        # Falls back to quality_score, then ai_rating, then 0 if columns not yet populated.
        triggers.sort(
            key=lambda x: x.get("final_score") or x.get("quality_score") or x.get("ai_rating") or 0,
            reverse=True
        )
    except Exception as e:
        notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
        print(f"❌ Failed to fetch daily triggers: {e}")
        return

    if not triggers:
        print(f"😴 No primary breakouts in the last {TRIGGER_LOOKBACK_DAYS} days.")
        
    # Get current holdings in portfolio_positions
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        holdings = portfolio_res.data
        active_tickers = [h["ticker"] for h in holdings]
    except Exception as e:
        notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
        print(f"❌ Failed to fetch portfolio positions: {e}")
        return


    # Check portfolio cap.
    stock_holdings = holdings
    if len(stock_holdings) >= MAX_POSITIONS:
        print(f"❌ Portfolio is fully invested with {len(stock_holdings)} stock positions. Standing down.")
        # Every trigger today is foregone purely for lack of a slot. These rows
        # are what makes the opportunity cost of MAX_POSITIONS measurable.
        trigger_audit.record_decisions_bulk(
            client, triggers, "SKIPPED", trigger_audit.SLOTS_FULL,
            detail=f"Portfolio full at {len(stock_holdings)}/{MAX_POSITIONS} before cycle",
            slots_free=0,
        )
        return

    cycle_cash_spent = 0.0
    initial_own_cash = get_own_cash(ib)

    for trigger in triggers:
        ticker = trigger["ticker"]
        
        # Refresh active holdings from Supabase at top of loop
        try:
            portfolio_res = client.table("portfolio_positions").select("*").execute()
            holdings = portfolio_res.data or []
            active_tickers = [p["ticker"] for p in holdings]
        except Exception:
            pass

        # Don't buy a stock we already hold
        if ticker in active_tickers:
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.ALREADY_HELD,
                detail="Already an open position")
            continue

        # ── Cooling-off period: skip tickers sold within the last 3 days ────────
        # Prevents re-buying a stock that was just stopped out (trailing stop)
        try:
            cooling_cutoff = (today_ny - datetime.timedelta(days=COOLING_OFF_DAYS)).isoformat()
            recent_sell_res = client.table("trade_history").select("ticker").eq("ticker", ticker).gte("sell_date", cooling_cutoff).execute()
            if recent_sell_res.data:
                print(f"   ⏳ {ticker} sold within last {COOLING_OFF_DAYS} days — cooling-off period active. Skipping.")
                trigger_audit.record_trigger_decision(
                    client, trigger, "SKIPPED", trigger_audit.COOLING_OFF,
                    detail=f"Sold within {COOLING_OFF_DAYS}d (cutoff {cooling_cutoff})")
                continue
        except Exception as cool_err:
            notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", cool_err)
            print(f"   ⚠️ Cooling-off check failed for {ticker}: {cool_err} — allowing buy.")

        # ── AI veto: skip D-grade tickers (low-conviction AI rating < 30) ────────
        ai_grade = trigger.get("ai_grade")
        if ai_grade == "D":
            print(f"   🚫 {ticker} vetoed by AI evaluator (D-grade, conviction < 30). Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.AI_VETO,
                detail="D-grade, conviction < 30")
            continue
        if ai_grade:
            print(f"   🟢 {ticker} AI grade: {ai_grade} | "
                  f"quality={trigger.get('quality_score', 'N/A')} | "
                  f"final={trigger.get('final_score', 'N/A')}")

        # 🛡️ Final score floor (quality guardrail) ──────────────────────────────
        trigger_type = str(trigger.get("trigger_type") or "BREAKOUT")

        # FAIL CLOSED on un-vetted triggers.
        # A trigger only carries a final_score once ai_evaluator.py has rated it.
        # Previously this fell back to quality_score (a pure technical score),
        # which silently let AI-skipped triggers through the gate while bypassing
        # every AI guardrail (sub-$15 cap, low-volume/small-cap penalties, the
        # slow-mover ATR cap, and sentiment/news screening). When the evaluator
        # drops tickers, those buys must be skipped, not waved through.
        candidate_score = (
            trigger.get("adjusted_score")
            if trigger.get("adjusted_score") is not None
            else trigger.get("final_score")
        )
        if candidate_score is None:
            print(
                f"   🚫 {ticker} {trigger_type} has no AI-evaluated score "
                f"(final_score is NULL — ai_evaluator.py did not rate it). "
                f"Skipping: refusing to buy on technicals alone."
            )
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.NO_AI_SCORE,
                detail="final_score NULL — not rated by ai_evaluator.py")
            continue

        if trigger_type == "PRE_BREAKOUT_RELAXED":
            min_score = MIN_RELAXED_TRIGGER_SCORE
        elif trigger_type == "PRE_BREAKOUT":
            min_score = max(MIN_TRIGGER_SCORE, MIN_PRE_BREAKOUT_SCORE)
        else:
            min_score = MIN_TRIGGER_SCORE

        if float(candidate_score) < float(min_score):
            print(f"   🚫 {ticker} {trigger_type} score {candidate_score} < floor {min_score}. Skipping.")
            # The single most valuable rejection to record: these are the
            # near-miss candidates whose outcomes are needed to test whether the
            # score floor is set anywhere near the right level.
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.SCORE_FLOOR,
                detail=f"score {candidate_score} < floor {min_score}",
                candidate_score=float(candidate_score), min_score=float(min_score))
            continue

        # Size the position as an equal share of remaining capital across unfilled slots.
        # Deduct cash spent on filled orders in the current cycle from initial_own_cash.
        available_cash = max(0.0, initial_own_cash - cycle_cash_spent)
        live_own_cash = get_own_cash(ib)
        available_cash = min(available_cash, live_own_cash)

        stock_held_count = len(holdings)
        remaining_slots = max(1, MAX_POSITIONS - stock_held_count)
        print(f"💰 Own Cash (margin-free) in IBKR: ${available_cash:,.2f} (initial: ${initial_own_cash:,.2f}, spent this cycle: ${cycle_cash_spent:,.2f})")
        position_size = available_cash / remaining_slots
        print(f"   Position sizing: ${available_cash:,.2f} / {remaining_slots} slot(s) = ${position_size:,.2f} per position (${PRICE_SAFETY_RESERVE:,.0f} safety reserve applied at share count)")

        # Double check active holdings size again
        if stock_held_count >= MAX_POSITIONS:
            print(f"🚫 Portfolio capacity ({MAX_POSITIONS} stocks) reached during loop. Skipping further buys.")
            trigger_audit.record_decisions_bulk(
                client, triggers[triggers.index(trigger):], "SKIPPED",
                trigger_audit.SLOTS_FULL,
                detail=f"Capacity reached mid-cycle at {stock_held_count}/{MAX_POSITIONS}",
                slots_free=0)
            break

        if available_cash < MIN_POSITION_SIZE:
            print(f"🚫 Insufficient cash to buy {ticker} (floor: ${MIN_POSITION_SIZE:,.0f}). Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.INSUFFICIENT_CASH,
                detail=f"available ${available_cash:,.0f} < floor ${MIN_POSITION_SIZE:,.0f}",
                available_cash=available_cash, slots_free=remaining_slots)
            continue

        # ── Hard volume surge gate (AI-independent) ──────────────────────────────
        # CAN SLIM requires above-average volume to confirm a breakout. Below
        # MIN_VOL_SURGE_GATE× the 50-day avg, institutional money is not
        # participating — do not buy regardless of AI score.
        #
        # CONFIRMED BREAKOUTS ONLY. The `volume_surge` column is overloaded: the
        # screener stores today's volume / 50d avg for BREAKOUT rows (higher is
        # better, gated at VOLUME_SURGE_MIN=1.50), but for PRE_BREAKOUT rows it
        # stores the 3-day volume CONTRACTION ratio (technical_screener.py:417),
        # where LOWER is better and the screener already requires < 1.00. A coil
        # tightening on drying volume is the constructive setup CAN SLIM wants.
        # Applying a minimum to that number inverts the selection: it rejects the
        # tightest coils and admits the loosest. Do not gate pre-breakouts here.
        trigger_vol_surge = float(trigger.get("volume_surge") or 0)
        if trigger_type == "BREAKOUT" and trigger_vol_surge < MIN_VOL_SURGE_GATE:
            print(f"   🚫 {ticker} volume surge {trigger_vol_surge:.2f}x < gate {MIN_VOL_SURGE_GATE:.2f}x "
                  f"— institutional money not confirming. Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.SCORE_FLOOR,
                detail=f"vol_surge {trigger_vol_surge:.2f}x < MIN_VOL_SURGE_GATE {MIN_VOL_SURGE_GATE:.2f}x")
            continue

        # ── PRE_BREAKOUT 52W pivot distance gate ────────────────────────────────
        # For PRE_BREAKOUT triggers the screener stores pivot_distance_pct as the
        # distance from the 52-week high. If the stock is still more than
        # MAX_PRE_BREAKOUT_PIVOT_DIST below that high it has not meaningfully
        # set up — buying it is speculation, not a breakout trade.
        if trigger_type in ("PRE_BREAKOUT", "PRE_BREAKOUT_RELAXED"):
            stored_pivot_dist = trigger.get("pivot_distance_pct")
            if stored_pivot_dist is not None:
                pivot_dist = float(stored_pivot_dist)
                if pivot_dist < -(MAX_PRE_BREAKOUT_PIVOT_DIST * 100):
                    print(f"   🚫 {ticker} PRE_BREAKOUT is {abs(pivot_dist):.1f}% below 52W pivot "
                          f"(max {MAX_PRE_BREAKOUT_PIVOT_DIST*100:.0f}%). Too far from breakout. Skipping.")
                    trigger_audit.record_trigger_decision(
                        client, trigger, "SKIPPED", trigger_audit.BELOW_PIVOT,
                        detail=f"PRE_BREAKOUT {abs(pivot_dist):.1f}% below 52W pivot "
                               f"(max {MAX_PRE_BREAKOUT_PIVOT_DIST*100:.0f}%)")
                    continue
            
        # Buy reason tags the trigger source
        buy_reason = f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x"
        buy_source = "daily_triggers"

        print(f"🚀 Execution Trigger: Initiating purchase for {ticker}...")

        # ── Qualify contract first so we can request IBKR's live price ────────
        # Contract must be qualified before reqTickers(); done here (not inside
        # the order try block) so the price is available for share sizing.
        contract = Stock(ticker, 'SMART', 'USD')
        try:
            ib.qualifyContracts(contract)
        except Exception as _qe:
            print(f"   ⚠️ Contract qualification failed for {ticker}: {_qe}. Halting buy loop.")
            notifier.notify_buy_failure(ticker=ticker, shares=0, error=_qe)
            notifier.notify_buy_loop_halted(ticker=ticker, reason=str(_qe))
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.LOOP_HALTED,
                detail=f"Contract qualification failed: {_qe}"[:500])
            break

        # -- Get price from IBKR (delayed market data) --
        # FMP's /stable/quote returns yesterday's close at market open, lagging
        # actual prices by 5-10%+ for gap-up stocks -- the root cause of Error 201.
        #
        # IBKR delayed market data (reqMarketDataType=3) is free for all accounts
        # and returns actual IBKR traded prices with a 15-20 min lag.
        ibkr_price, price_method = fetch_ibkr_delayed_price(ib, contract)

        if ibkr_price > 0:
            current_price = ibkr_price
            price_source  = f"IBKR ({price_method})"
        else:
            # IBKR delayed price unavailable — fall back to previous close from screener.
            # Do NOT use FMP here: FMP /stable/quote returns yesterday's close at market
            # open, causing the same 5-10% lag issue we're trying to avoid.
            current_price = float(trigger["close_price"])
            price_source  = "prev close (IBKR delayed unavailable)"
        if current_price <= 0:
            print(f"   ⚠️ No valid price for {ticker} — skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.NO_PRICE,
                detail=f"no valid price (source: {price_source})")
            continue
        print(f"   📡 {ticker} price: ${current_price:.2f} (source: {price_source})")

        # ── CANSLIM pivot extension check ────────────────────────────────────
        pivot_price = float(trigger["close_price"])
        extension_pct = (current_price - pivot_price) / pivot_price if pivot_price > 0 else 0
        if extension_pct > MAX_PIVOT_EXTENSION:
            print(f"   ⛔ {ticker} is {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
                  f"— extended beyond {MAX_PIVOT_EXTENSION*100:.0f}% buy zone. Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.EXTENDED_ABOVE_PIVOT,
                detail=f"{extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
                       f"(max {MAX_PIVOT_EXTENSION*100:.0f}%)",
                price=current_price, extension_pct=extension_pct)
            continue
        # Floor check. The gate above is a CEILING only, so a trigger that has
        # since collapsed below its pivot still passed — with
        # TRIGGER_LOOKBACK_DAYS=3 the bot could buy a 3-day-old breakout that had
        # already failed. A breakout that gives back its pivot is a failed
        # breakout, and buying it is buying a breakdown.
        if extension_pct < -MAX_PIVOT_BREAKDOWN:
            print(f"   ⛔ {ticker} has fallen {abs(extension_pct)*100:.1f}% BELOW pivot "
                  f"${pivot_price:.2f} — breakout failed, not a valid entry. Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.BELOW_PIVOT,
                detail=f"{abs(extension_pct)*100:.1f}% below pivot ${pivot_price:.2f}",
                price=current_price, extension_pct=extension_pct)
            continue
        print(f"   ✅ {ticker} within buy zone: {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
              f"(max {MAX_PIVOT_EXTENSION*100:.0f}%)")

        # Subtract the flat safety reserve before dividing to stay within available
        # cash even if the 15-20 min delayed IBKR price lags the actual fill price.
        shares = int((position_size - PRICE_SAFETY_RESERVE) / current_price)
        if shares <= 0:
            print(f"⚠️ Price of {ticker} (${current_price:.2f}) is too high for the computed position size (${position_size:,.0f}). Skipping.")
            trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", trigger_audit.SHARES_ZERO,
                detail=f"price ${current_price:.2f} too high for position size ${position_size:,.0f}",
                price=current_price, available_cash=available_cash, shares=0)
            continue

        # Place market buy order on IBKR
        try:
            # Note: contract already qualified above
            # 1. Market Order Entry
            order = MarketOrder('BUY', shares)
            order.tif = 'DAY'   # explicit DAY prevents IBKR error 10349 (preset TIF warning)
            order.account = get_ibkr_account(ib)
            
            print(f"   Submitting Market Order for {shares} shares of {ticker}...")
            trade = ib.placeOrder(contract, order)

            print(f"   Waiting for fill on {shares} shares of {ticker}...")
            for _ in range(60):
                ib.sleep(1)
                status = trade.orderStatus.status
                filled_so_far = int(trade.orderStatus.filled)
                if status == 'Filled':
                    break
                elif status in ('Cancelled', 'Inactive'):
                    if filled_so_far == 0:
                        # Grace period: fill confirmation may still be in-flight
                        # (race condition where IBKR warning/cancel arrives before fill ack)
                        ib.sleep(2)
                        if int(trade.orderStatus.filled) > 0:
                            print(f"   ℹ️ {ticker}: fill arrived after cancel event — proceeding with position.")
                    break

            if trade.orderStatus.status != 'Filled':
                print(f"   ⚠️ {ticker} order not fully filled or was rejected. Cancelling remaining.")
                ib.cancelOrder(order)
                ib.sleep(2)

            actual_shares = int(trade.orderStatus.filled)
            if actual_shares == 0:
                reject_msgs = [entry.message for entry in trade.log if getattr(entry, 'message', '')]
                reject_msg = " | ".join(reject_msgs) if reject_msgs else "No explicit IBKR message (Order timed out, zero liquidity, or halted)"

                print(f"   ⚠️ {ticker} order had 0 shares filled. Reason: {reject_msg}")
                notifier.notify_buy_failure(ticker=ticker, shares=shares,
                    error=f"IBKR Log: {reject_msg}")
                # Stop the entire buy loop — do NOT attempt the next ranked stock.
                # Skipping to the next ticker would change portfolio construction
                # priority and is worse than halting for manual intervention.
                notifier.notify_buy_loop_halted(ticker=ticker, reason=reject_msg)
                trigger_audit.record_trigger_decision(
                    client, trigger, "SKIPPED", trigger_audit.BUY_FAILED,
                    detail=f"0 shares filled: {reject_msg}"[:500],
                    candidate_score=candidate_score, price=current_price,
                    shares=0)
                break

            fill_price = round(trade.orderStatus.avgFillPrice, 2)
            if fill_price <= 0:
                fill_price = current_price

            actual_cost = actual_shares * fill_price
            cycle_cash_spent += actual_cost
            print(f"   💳 Cycle cash spent updated: +${actual_cost:,.2f} (total spent this cycle: ${cycle_cash_spent:,.2f})")

            # Calculate dynamic stop loss percentage (2.5x ATR, fallback to STOP_LOSS_PCT)
            # Floor: 7% (never tighter than static, protects against bad fills)
            # Cap:  14% (prevents runaway stops on extremely volatile names)
            trigger_atr_pct = trigger.get("atr_pct")
            if trigger_atr_pct and float(trigger_atr_pct) > 0:
                atr_derived = round((2.5 * float(trigger_atr_pct)) / 100.0, 4)
                # Band tracks STOP_LOSS_PCT rather than hard-coding 0.07, so
                # widening the base stop cannot be silently undone here.
                pos_stop_loss_pct = round(max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT, atr_derived)), 4)
                stop_method = f"ATR-based ({float(trigger_atr_pct):.2f}% ATR × 2.5)"
            else:
                pos_stop_loss_pct = STOP_LOSS_PCT
                stop_method = "static fallback"

            stop_loss_val = round(fill_price * (1 - pos_stop_loss_pct), 2)

            # ── Record position in Supabase FIRST ─────────────────────────────
            # CRITICAL: insert BEFORE place_trailing_stop() so that any exception
            # from stop placement cannot leave the position phantom-filled in IBKR
            # but absent from the DB. A missing DB entry fools the capacity check
            # into allowing extra buy orders (which IBKR then cancels for
            # insufficient buying power). Recording first makes this atomic from
            # the capacity-counting perspective.
            position_data = {
                "ticker":     ticker,
                "shares":     actual_shares,
                "buy_price":  fill_price,
                "buy_reason": f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
                "buy_source": buy_source,
                "stop_loss_pct": pos_stop_loss_pct,
                "hwm_date":   datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
                "highest_unrealized_pct": 0.0,
                # ── Entry conviction snapshot (all 5-component scores) ─────────
                # Copied from the daily_triggers row so the Open Positions UI and
                # future rotation analysis have the full picture at entry time.
                "entry_quality_score":    trigger.get("quality_score"),
                "entry_ai_rating":        trigger.get("ai_rating"),
                "entry_ai_grade":         trigger.get("ai_grade"),
                "entry_final_score":      trigger.get("final_score"),
                "entry_technical_score":  trigger.get("technical_score"),
                "entry_liquidity_score":  trigger.get("liquidity_score"),
                "entry_rs_score":         _get_entry_rs(ticker, trigger.get("rs_score")),
                "entry_sentiment_score":  trigger.get("sentiment_score"),
                "entry_atr_pct":          trigger.get("atr_pct"),
                "entry_est_days_target":  trigger.get("est_days_to_target"),
                "entry_score_rationale":  trigger.get("score_rationale"),
                # new: breakout signal baselines for PARAM_DRIFT analysis
                "entry_volume_surge":         trigger.get("volume_surge"),
                "entry_pivot_distance_pct":   trigger.get("pivot_distance_pct"),
                # hwm_price starts at fill price; ratchets up in monitor_portfolio_intraday
                "hwm_price": fill_price,
            }
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"✅ Successfully bought {actual_shares} shares of {ticker} at ${fill_price:.2f}.")
            print(f"   Stop-Loss: ${stop_loss_val} | Trail: {pos_stop_loss_pct*100:.2f}% (IBKR-managed)")

            # The positive class for the counterfactual: this trigger's score is
            # paired with an actual outcome, while the SKIPPED rows are not.
            trigger_audit.record_trigger_decision(
                client, trigger, "BOUGHT", trigger_audit.BOUGHT,
                detail=f"Filled {actual_shares} @ ${fill_price:.2f}",
                candidate_score=candidate_score, min_score=min_score,
                price=fill_price, extension_pct=extension_pct,
                available_cash=available_cash, shares=actual_shares)

            # Update loop capacity state immediately after DB write.
            # Must happen before notify_buy so the tracker is correct even if
            # the Telegram call raises an exception.
            active_tickers.append(ticker)
            portfolio_res = client.table("portfolio_positions").select("ticker").execute()
            holdings = portfolio_res.data or []
            slot_used = len(holdings)

            # ── Attach Trailing Stop (isolated try/except) ────────────────────
            # Wrapped separately so a stop-placement failure never prevents the
            # position from being recorded above or the loop from continuing.
            try:
                place_trailing_stop(ib, contract, actual_shares, pos_stop_loss_pct)
            except Exception as stop_err:
                print(f"   ⚠️ Trailing stop placement failed for {ticker}: {stop_err} — position recorded, manual stop required.")
                notifier.notify_exception("place_trailing_stop() — execution_agent.py", stop_err)

            # Notify all configured Telegram recipients
            notifier.notify_buy(
                ticker=ticker, shares=actual_shares, fill_price=fill_price,
                stop_loss=stop_loss_val,
                trail_pct=pos_stop_loss_pct,
                stop_method=stop_method,
                volume_surge=float(trigger.get("volume_surge", 0)),
                pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                slot_used=slot_used, max_slots=MAX_POSITIONS
            )

        except Exception as order_err:
            notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", order_err)
            print(f"❌ Failed to execute order for {ticker}: {order_err}")
            notifier.notify_buy_failure(ticker=ticker, shares=shares, error=order_err)
            # Stop the entire buy loop — same reasoning as the 0-fill case above.
            notifier.notify_buy_loop_halted(ticker=ticker, reason=str(order_err))
            break


def _get_entry_rs(ticker: str, trigger_rs_score) -> int | None:
    """Return entry_rs_score for a newly opened position.

    Prefers the rs_score already in the trigger row (written by ai_evaluator.py).
    Falls back to a live FMP fetch if the trigger has no rs_score (e.g. the
    AI evaluator hadn't run yet when the buy was executed, or this is a manual
    reconcile buy). This guarantees every position has an RS baseline so that
    Rule 1 (RS Decay) is never permanently blind due to a NULL entry_rs_score.

    Returns None only if the live fetch also fails (FMP API down) — callers must
    handle None gracefully (Rule 1 will skip that position and Rule 2 still applies).
    """
    if trigger_rs_score is not None:
        return int(trigger_rs_score)
    live = _fetch_current_rs(ticker)
    if live is not None:
        print(f"   📊 {ticker}: entry_rs_score backfilled live ({live}) — trigger had no rs_score")
    return live


def _fetch_ohlcv(ticker: str, days: int = 100) -> list:
    """Fetch OHLCV rows from FMP for the last `days` calendar days.

    Returns a list of dicts sorted ascending by date, each containing at minimum:
    {'date': str, 'open': float, 'high': float, 'low': float,
     'close': float, 'volume': int}
    Returns [] on any failure. Shared by _get_market_regime and the EOD metrics
    loop so we don't duplicate FMP calls.
    """
    try:
        tz_o    = ZoneInfo("America/New_York")
        to_date = datetime.datetime.now(tz_o).date()
        from_dt = to_date - datetime.timedelta(days=days)
        url = (
            "https://financialmodelingprep.com/stable/historical-price-eod/full"
            f"?symbol={ticker}&from={from_dt}&to={to_date}&apikey={FMP_API_KEY}"
        )
        r = fmp_session.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data or not isinstance(data, list):
            return []
        return sorted(data, key=lambda x: x["date"])
    except Exception as _e:
        print(f"   ⚠️ _fetch_ohlcv({ticker}) failed: {_e}")
        return []


def fetch_held_position_sentiment(ticker: str) -> int:
    """Fetch live sentiment score (1-100) for a held position using FMP news + GPT-4o-mini.

    Calls FMP /api/v3/stock_news (limit=8, 1 credit) and asks GPT-4o-mini to score
    headline tone on a 1-100 scale. Falls back to 50 (neutral) on any failure.
    Called once per position at EOD (3:45 PM) — ~4 calls/day, ~80/month.
    """
    import json as _json_sent
    from openai import OpenAI as _OpenAI

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key or not FMP_API_KEY:
        return 50   # graceful degradation: neutral score

    # ── 1. Fetch headlines ───────────────────────────────────────────────────
    try:
        url = (f"https://financialmodelingprep.com/api/v3/stock_news"
               f"?tickers={ticker}&limit=8&apikey={FMP_API_KEY}")
        r = fmp_session.get(url, timeout=8)
        if r.status_code != 200:
            return 50
        headlines = [item.get("title", "") for item in r.json() if item.get("title")]
    except Exception as _e:
        print(f"   ⚠️ fetch_held_position_sentiment({ticker}) news fetch failed: {_e}")
        return 50

    if not headlines:
        return 50

    # ── 2. Score with GPT-4o-mini ────────────────────────────────────────────
    try:
        ai = _OpenAI(api_key=openai_key)
        headlines_text = "\n".join(f"- {h}" for h in headlines[:8])
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Output ONLY valid JSON."},
                {"role": "user", "content": (
                    f"Score the overall news sentiment for ${ticker} based on these recent headlines.\n\n"
                    f"{headlines_text}\n\n"
                    "Return a single JSON object: {{\"sentiment\": <integer 1-100>}}\n"
                    "80-100=very positive, 40-60=neutral/mixed, 1-39=negative."
                )},
            ],
            max_tokens=30,
        )
        result = _json_sent.loads(resp.choices[0].message.content)
        score = int(result.get("sentiment", 50))
        score = max(1, min(100, score))
        print(f"   📰 {ticker}: live sentiment score {score}/100 ({len(headlines)} headlines)")
        return score
    except Exception as _ge:
        print(f"   ⚠️ fetch_held_position_sentiment({ticker}) GPT failed: {_ge}")
        return 50


def compute_rsi(closes: list, period: int = 14) -> list:
    """Wilder's smoothed RSI from a list of closing prices.

    Returns a list of RSI values the same length as closes (first `period`
    values are None — insufficient history). Uses Wilder's exponential
    smoothing (alpha = 1/period), consistent with TradingView / standard
    charting platforms.

    Pure function — no side effects, no I/O.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    rsi = [None] * period  # first `period` values have no RSI

    # ── Seed: simple average of first `period` gains/losses ──────────────────
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avgs(ag, al):
        if al == 0:
            return 100.0
        return round(100.0 - (100.0 / (1.0 + ag / al)), 2)

    rsi.append(_rsi_from_avgs(avg_gain, avg_loss))

    # ── Wilder's smoothing for remaining bars ─────────────────────────────────
    alpha = 1.0 / period
    for i in range(period + 1, len(closes)):
        delta    = closes[i] - closes[i - 1]
        g        = max(delta, 0.0)
        l        = max(-delta, 0.0)
        avg_gain = avg_gain * (1 - alpha) + g * alpha
        avg_loss = avg_loss * (1 - alpha) + l * alpha
        rsi.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi


def detect_candlestick_reversals(ohlcv: list, hwm_price: float) -> int:
    """Detect bearish reversal candles on the last 3 bars near the plateau zone.

    Returns the total Mₜ penalty to subtract (0, -8, -15, or -20).

    Location filter: only applies when current close >= hwm_price * 0.97.
    Reversal candles during deep pullbacks (> 3% from HWM) are noise.

    Shooting Star / Pin Bar (penalty -8):
      - Upper shadow > 2× lower shadow
      - Close < open  (bearish body)
      - Upper shadow > 60% of full candle range

    Bearish Engulfing (penalty -15):
      - Today's open > yesterday's close   (gap up / opens above)
      - Today's close < yesterday's open   (body engulfs prior body)
      - Today's volume > 20-day avg volume (institutional confirmation)

    Both detected: -20 pts (capped).
    """
    if len(ohlcv) < 22:      # need 20-day vol baseline + 2 candles
        return 0

    # Location filter — only care when near the HWM
    current_close = float(ohlcv[-1].get("close", 0))
    if hwm_price <= 0 or current_close < hwm_price * 0.97:
        return 0

    vols  = [float(r.get("volume", 0)) for r in ohlcv]
    avg20 = sum(vols[-21:-1]) / 20 if sum(vols[-21:-1]) > 0 else 0

    shooting_star = False
    engulfing     = False

    # ── Shooting Star / Pin Bar: check last 3 bars ────────────────────────────
    for i in range(-3, 0):
        bar = ohlcv[i]
        o = float(bar.get("open",  0))
        h = float(bar.get("high",  0))
        l = float(bar.get("low",   0))
        c = float(bar.get("close", 0))
        full_range   = h - l
        if full_range <= 0:
            continue
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        if (c < o
                and upper_shadow > 2 * max(lower_shadow, 0.0001)
                and upper_shadow / full_range > 0.60):
            shooting_star = True
            break

    # ── Bearish Engulfing: last 2 bars ────────────────────────────────────────
    if len(ohlcv) >= 2:
        prev   = ohlcv[-2]
        curr   = ohlcv[-1]
        prev_o = float(prev.get("open",  0))
        prev_c = float(prev.get("close", 0))
        curr_o = float(curr.get("open",  0))
        curr_c = float(curr.get("close", 0))
        curr_v = float(curr.get("volume", 0))
        if (prev_c > prev_o          # prior bar bullish
                and curr_o > prev_c  # today gapped up
                and curr_c < prev_o  # today engulfs prior body
                and avg20 > 0
                and curr_v > avg20): # volume confirmation
            engulfing = True

    if shooting_star and engulfing:
        return -20
    if engulfing:
        return -15
    if shooting_star:
        return -8
    return 0


def compute_momentum_health_score(
    pos: dict,
    ohlcv: list,
    live_sentiment: int = 50,
    days_held: int = 0,
) -> tuple[float, dict]:
    """Live Momentum Health Score Mₜ (0–100) for a held position.

    Returns (score, debug_info) where debug_info has keys:
      rs_component, vol_component, sentiment_component,
      rsi_penalty, candle_penalty, raw_score, final_score.

    Formula:
      Mₜ_raw = 0.40 * RS + 0.35 * Vol + 0.25 * Sentiment
      Mₜ     = max(0, Mₜ_raw - RSI_divergence_penalty - candle_reversal_penalty)

    Day 7+ only: RSI divergence and candlestick penalties activate after
    days_held >= 7. Before that they are 0 (breakout consolidation phase).

    RS component (0-100):
        (live_rs / entry_rs) * 100, capped at 100. Default 50 if no baseline.

    Volume component (0-100):
        V_ratio = today_vol / 20-day_avg_vol
        ≥ 1.5x → 100 | 1.0-1.5x → 50-100 | 0.5-1.0x → 0-50 | < 0.5x → 0

    Sentiment component (0-100):
        live_sentiment from GPT-4o-mini / FMP stock_news.

    RSI Divergence penalty (Day 7+, applied post-blend):
        Price made higher high vs 5 days ago, but RSI made lower high.
        Gap < 5 RSI pts → -10 | 5-15 pts → -18 | > 15 pts → -25

    Candlestick Reversal penalty (Day 7+, applied post-blend):
        Shooting star/pin bar → -8 | Bearish engulfing (vol) → -15 | Both → -20
        Only when price is within 3% of HWM (near plateau top).
    """
    # ── RS component ─────────────────────────────────────────────────────────
    entry_rs = pos.get("entry_rs_score")
    live_rs  = pos.get("live_rs_score")
    if entry_rs and entry_rs > 0 and live_rs is not None:
        rs_ratio     = live_rs / entry_rs
        rs_component = min(100.0, rs_ratio * 100.0)
    else:
        rs_component = 50.0

    # ── Volume component ─────────────────────────────────────────────────────
    vol_component = 50.0
    if len(ohlcv) >= 21:
        vols      = [float(r.get("volume", 0)) for r in ohlcv]
        avg20     = sum(vols[-21:-1]) / 20
        today_vol = vols[-1]
        if avg20 > 0:
            v_ratio = today_vol / avg20
            if v_ratio >= 1.5:
                vol_component = 100.0
            elif v_ratio >= 1.0:
                vol_component = 50.0 + (v_ratio - 1.0) / 0.5 * 50.0
            elif v_ratio >= 0.5:
                vol_component = (v_ratio - 0.5) / 0.5 * 50.0
            else:
                vol_component = 0.0

    # ── Sentiment component ───────────────────────────────────────────────────
    sentiment_component = float(max(1, min(100, live_sentiment)))

    # ── Weighted blend ───────────────────────────────────────────────────────
    raw_score = (
        MOMENTUM_HEALTH_RS_WEIGHT   * rs_component +
        MOMENTUM_HEALTH_VOL_WEIGHT  * vol_component +
        MOMENTUM_HEALTH_SENT_WEIGHT * sentiment_component
    )

    # ── Day 7+ penalty signals ───────────────────────────────────────────────
    rsi_penalty    = 0
    candle_penalty = 0

    if days_held >= 7 and len(ohlcv) >= 20:
        closes = [float(r.get("close", 0)) for r in ohlcv]
        rsi_vals = compute_rsi(closes, period=14)

        # RSI divergence: price up, RSI down (compare today vs 5 days ago)
        lookback = 5
        if (len(rsi_vals) >= lookback + 1
                and rsi_vals[-1] is not None
                and rsi_vals[-1 - lookback] is not None):
            price_now  = closes[-1]
            price_then = closes[-1 - lookback]
            rsi_now    = rsi_vals[-1]
            rsi_then   = rsi_vals[-1 - lookback]

            # Bearish divergence: price higher but RSI lower
            if price_now > price_then and rsi_now < rsi_then:
                div_gap = rsi_then - rsi_now  # positive number
                if div_gap > 15:
                    rsi_penalty = 25
                elif div_gap >= 5:
                    rsi_penalty = 18
                else:
                    rsi_penalty = 10

        # Candlestick reversal near HWM plateau
        hwm_price = float(pos.get("hwm_price") or pos.get("buy_price") or 0)
        candle_penalty_raw = detect_candlestick_reversals(ohlcv, hwm_price)
        candle_penalty = abs(candle_penalty_raw)  # stored as positive for subtraction

    penalty_total = rsi_penalty + candle_penalty
    final_score   = max(0.0, raw_score - penalty_total)

    debug = {
        "rs_component":        round(rs_component, 1),
        "vol_component":       round(vol_component, 1),
        "sentiment_component": round(sentiment_component, 1),
        "rsi_penalty":         -rsi_penalty,
        "candle_penalty":      -candle_penalty,
        "raw_score":           round(raw_score, 1),
        "final_score":         round(final_score, 1),
    }
    return round(final_score, 1), debug


def _get_market_regime() -> str:
    """Return current market regime based on SPY vs its 21-day EMA.

    'uptrend'    — SPY close > 21-day EMA (healthy market, consolidations more forgiving)
    'correction' — SPY close < 21-day EMA (all stalls more suspect)
    'neutral'    — SPY within 0.5% of 21-day EMA
    Returns 'neutral' on any API failure.
    """
    try:
        spy_ohlcv = _fetch_ohlcv("SPY", days=40)
        if len(spy_ohlcv) < 22:
            return "neutral"
        closes = [float(r["close"]) for r in spy_ohlcv]
        # 21-day EMA
        k      = 2 / (21 + 1)
        ema21  = closes[0]
        for c in closes[1:]:
            ema21 = c * k + ema21 * (1 - k)
        spy_now = closes[-1]
        diff_pct = (spy_now / ema21 - 1) * 100
        if diff_pct > 0.5:
            return "uptrend"
        if diff_pct < -0.5:
            return "correction"
        return "neutral"
    except Exception:
        return "neutral"


def _fetch_current_rs(ticker: str) -> int | None:
    """Fetch the stock's current 12-week return vs SPY and return its live RS score.

    Uses the same FMP endpoint as the screener — no new dependency.
    Returns None on any API failure (caller must treat as 'no data, skip Tier 1').
    Called once per position per EOD cycle (~4 FMP calls/day total).

    NOTE: scoring.py and technical_screener.py are NOT available in the
    execution agent container (Dockerfile.agent only copies execution_agent.py).
    The RS formula is inlined here verbatim from scoring.compute_rs_score.
    SPY baseline defaults to 0.0 — acceptable because this is used only to
    detect *decay* in RS (entry_rs_score vs live_rs_score), not absolute rank.
    """
    def _rs_from_excess(stock_12w: float, spy_12w: float = 0.0) -> int:
        """Inline of scoring.compute_rs_score — no external module needed."""
        excess = stock_12w - spy_12w
        if excess >= 10:
            return 100
        elif excess >= 0:
            return int(50 + excess * 5)
        elif excess >= -10:
            return max(0, int(50 + excess * 5))
        else:
            return 0

    try:
        tz_rs     = ZoneInfo("America/New_York")
        to_date   = datetime.datetime.now(tz_rs).date()
        from_date = to_date - datetime.timedelta(days=100)
        url = (
            "https://financialmodelingprep.com/stable/historical-price-eod/full"
            f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={FMP_API_KEY}"
        )
        r = fmp_session.get(url, timeout=10)
        if r.status_code != 200:
            print(f"   ⚠️ FMP historical API returned status code {r.status_code} for {ticker}.")
            return None
        data = r.json()
        if not data or not isinstance(data, list) or len(data) < 2:
            return None
        closes = sorted(data, key=lambda x: x["date"])
        lookback = min(60, len(closes) - 1)
        p_now  = float(closes[-1]["close"])
        p_then = float(closes[-1 - lookback]["close"])
        if p_then <= 0:
            return None
        stock_12w = round(((p_now / p_then) - 1.0) * 100.0, 2)
        return _rs_from_excess(stock_12w)  # SPY baseline = 0.0 (decay detection only)
    except Exception as _e:
        print(f"   ⚠️ _fetch_current_rs({ticker}) failed: {_e}")
        return None


def check_volume_distribution(ticker: str, ohlcv: list) -> bool:
    """
    Check if the stock closed sideways/down on above-average volume
    on at least 2 of the last 3 trading days.
    """
    if len(ohlcv) < 54:  # Need 50 days baseline + 3 check days + 1 day for prev_close
        return False
        
    closes = [float(r["close"]) for r in ohlcv]
    volumes = [float(r.get("volume", 0)) for r in ohlcv]
    
    distribution_days = 0
    # Check last 3 trading days
    for i in range(-3, 0):
        # 50-day average volume up to day i (excluding day i)
        hist_vols = volumes[i-50:i]
        if not hist_vols:
            continue
        avg_vol = sum(hist_vols) / len(hist_vols)
        
        day_close = closes[i]
        prev_close = closes[i-1]
        day_vol = volumes[i]
        
        # Sideways/down (close is <= previous close * 1.002) on above-average volume
        if day_close <= prev_close * 1.002 and day_vol > avg_vol:
            distribution_days += 1
            
    return distribution_days >= 2


def monitor_portfolio_intraday(ib: IB):
    """Monitors open positions: updates hwm_date, self-heals trailing stops,
    applies the MA exit, and runs EOD plateau rotation."""
    print("🔍 Running Intraday Portfolio Monitoring...")
    client = get_supabase_client()

    # ── Fetch open positions ────────────────────────────────────────────────────
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        positions = portfolio_res.data or []
    except Exception as e:
        notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
        print(f"❌ Could not fetch portfolio positions: {e}")
        return

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.datetime.now(tz)
    today_ny = now_ny.date()
    # Track intraday prices per-ticker in memory so hwm_date comparisons are
    # relative to the last price we polled (not the stored HWM price, which
    # IBKR now owns).
    intraday_peak: dict = {}
    # Positions whose exit is owned by a Smart OCA request. Every automated rule
    # below funnels into execute_sell()/arm_exit(), and both cancel all open SELL
    # orders for the ticker — which would wipe out the OCA. Skip them entirely;
    # process_exit_requests() governs these positions.
    oca_managed = get_oca_managed_tickers(client)

    # Single consistent IBKR price snapshot for this cycle. Every position is
    # priced from PortfolioItem.marketPrice (the broker's own mark we trade
    # against) via get_position_price(); FMP is only a per-ticker fallback when
    # a mark is missing. Read once here — ib.portfolio() is a non-blocking
    # in-memory lookup, so this does not risk the reqTickers() stall.
    ib_price_map = build_ibkr_price_map(ib)

    active_positions = []
    for pos in positions:
        ticker     = pos["ticker"]
        shares     = int(pos["shares"])
        buy_price  = float(pos["buy_price"])
        buy_reason = pos.get("buy_reason", "Unknown")
        try:
            buy_date = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
            buy_date_d = buy_date.date()
        except Exception:
            buy_date_d = today_ny

        # Calculate trading days held
        days_held = trading_days_between(buy_date_d, today_ny)

        # IBKR-first price: use the broker's own mark (PortfolioItem.marketPrice)
        # that we actually fill against, so exit decisions and fills agree. Falls
        # back to FMP only when IBKR has no usable mark for this ticker. The map
        # was built once above from the non-blocking ib.portfolio() cache.
        current_price, price_source = get_position_price(ib, ticker, ib_price_map)
        if current_price <= 0:
            print(f"   ⚠️ Could not fetch price for {ticker} — skipping this cycle.")
            active_positions.append(pos)
            continue

        pos_stop_loss_pct = float(pos.get("stop_loss_pct") or STOP_LOSS_PCT)

        if ticker in oca_managed:
            print(f"   🎯 {ticker}: Smart OCA exit active — automated exit rules "
                  f"suspended (OCA + floor/expiry backstops govern this position).")
            active_positions.append(pos)
            continue

        print(f"   Monitoring {ticker}: Current: ${current_price:.2f} ({price_source}) | Entry: ${buy_price:.2f} "
              f"| Held: {days_held}d | IBKR Trail: {pos_stop_loss_pct*100:.2f}%")

        # ── Armed Trailing Exit deadline check ───────────────────────────────────
        # A Day 0-6 sell signal already fired for this position and it was armed
        # with a tight IBKR trailing stop (see arm_exit()) instead of an instant
        # market sell, so it can capture a better exit price on any bounce.
        # If that trail hasn't already fired by the deadline, force the sell now
        # — we never hold longer than this bound chasing a better price.
        if pos.get("exit_armed"):
            try:
                armed_at = datetime.datetime.fromisoformat(pos["exit_armed_at"].replace('Z', '+00:00'))
            except Exception:
                armed_at = now_ny
            hours_armed = (now_ny - armed_at).total_seconds() / 3600.0
            if hours_armed >= ARMED_EXIT_DEADLINE_HOURS:
                reason = (
                    f"Armed Exit Deadline — {pos.get('exit_armed_reason', 'armed exit')} "
                    f"not stopped out after {hours_armed:.2f}h, forcing sell"
                )
                print(f"🚨 {ticker}: Armed Exit Deadline firing — {reason}")
                execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason)
            else:
                print(f"   \U0001f3af {ticker}: exit armed {hours_armed:.2f}h ago "
                      f"({pos.get('exit_armed_reason')}) — awaiting trail or deadline.")
                active_positions.append(pos)
            continue

        # ── Calculate current unrealized percentage ──
        unrealized_pct = round(((current_price / buy_price) - 1.0) * 100.0, 4)

        # ── Update highest_unrealized_pct in Supabase & memory ──
        prev_highest = float(pos.get("highest_unrealized_pct") or 0.0)
        highest_unrealized_pct = max(prev_highest, unrealized_pct)

        # ── Update hwm_date, hwm_price and highest_unrealized_pct when a new intraday high is seen ────
        stored_hwm = float(pos.get("hwm_price") or buy_price)
        prev_peak = max(stored_hwm, intraday_peak.get(ticker, buy_price))
        hwm_updated = False
        if current_price > prev_peak:
            intraday_peak[ticker] = current_price
            hwm_updated = True

        if hwm_updated or highest_unrealized_pct > prev_highest:
            try:
                update_payload = {
                    "highest_unrealized_pct": round(highest_unrealized_pct, 4)
                }
                if hwm_updated:
                    update_payload["hwm_date"]  = today_ny.isoformat()
                    update_payload["hwm_price"] = round(float(current_price), 4)

                client.table("portfolio_positions").update(update_payload).eq("ticker", ticker).execute()
                pos["highest_unrealized_pct"] = highest_unrealized_pct
            except Exception as e:
                err_str = str(e)
                # PGRST204 = column missing in schema cache (migration not yet run).
                # Degrade gracefully: write only hwm_date / hwm_price which always exist.
                # Do NOT fire Telegram — this is a deploy-time setup issue, not a bug.
                if "PGRST204" in err_str or "highest_unrealized_pct" in err_str:
                    print(f"   ⚠️ {ticker}: highest_unrealized_pct column missing — run migration. "
                          f"Writing hwm only.")
                    if hwm_updated:
                        try:
                            client.table("portfolio_positions").update({
                                "hwm_date":  today_ny.isoformat(),
                                "hwm_price": round(float(current_price), 4),
                            }).eq("ticker", ticker).execute()
                        except Exception as _inner:
                            print(f"   ⚠️ Could not update hwm for {ticker}: {_inner}")
                else:
                    notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
                    print(f"   ⚠️ Could not update hwm/peak metrics for {ticker}: {e}")


        # ── Dynamic trailing stop tightening ─────────────────────────────────────
        # Compute calendar days (not trading days) — time lever uses calendar.
        calendar_days = (today_ny - buy_date_d).days

        # ── O'Neil 8-Week Hold Rule ──────────────────────────────────────────────
        # Evaluated before the discretionary exits below so a qualifying leader is
        # protected from being trimmed on ordinary volatility. The trailing stop
        # placed with the position is untouched and still protects the downside.
        pos["highest_unrealized_pct"] = highest_unrealized_pct
        power_held = maybe_arm_power_hold(client, pos, calendar_days) or \
            is_power_hold_active(pos, calendar_days)
        if power_held:
            print(f"   🏆 {ticker}: power-hold active (day {calendar_days} of "
                  f"{POWER_HOLD_DURATION_DAYS}) — discretionary exits suppressed.")

        # ── The Prove-It Stop ────────────────────────────────────────────────────
        # Resolve the level this position is protected at right now, then act on
        # it two ways: the bot arms a tight trailing exit if price is already
        # through the level, and the resting IBKR order is pinned to it below.
        #
        # Suppressed while power-held, which widens the trail deliberately. There
        # is no real conflict — power-hold requires a large peak gain, so such a
        # position is always proven and far above the Phase 2 floor.
        prove_it_level, prove_it_phase = (None, "power-hold") if power_held else \
            prove_it_stop_level(pos, buy_price, days_held, highest_unrealized_pct)

        if (prove_it_level is not None
                and current_price <= prove_it_level
                and not pos.get("exit_armed")):
            if prove_it_phase == "phase1":
                band_pct = prove_it_p1_threshold_pct(days_held) * 100.0
                reason = (
                    f"Prove-It Stop (Phase 1 — unproven) — Day {days_held}, "
                    f"never closed above entry and price "
                    f"{unrealized_pct:.2f}% <= -{band_pct:.1f}% of entry "
                    f"(${prove_it_level:.2f})"
                )
            else:
                reason = (
                    f"Prove-It Stop (Phase 2 — give-back floor) — Day {days_held}, "
                    f"peak +{highest_unrealized_pct:.2f}% gave back to "
                    f"{unrealized_pct:.2f}%, at or below the "
                    f"{PROVE_IT_P2_FLOOR_PCT * 100:+.1f}% floor "
                    f"(${prove_it_level:.2f}). A green trade does not become a loss."
                )
            print(f"🚨 {ticker}: Prove-It Stop triggered — arming exit — {reason}")
            arm_exit(ib, client, ticker, shares, current_price, reason, now_ny)
            notifier.notify_prove_it_stop(
                ticker, buy_price, current_price, days_held,
                prove_it_phase, prove_it_level, highest_unrealized_pct,
            )
            active_positions.append(pos)
            continue

        # While power-held the profit ladder is bypassed entirely: the HWM profit
        # lock would otherwise clamp the trail to 1.5% from the peak from +5% gain
        # onward, long before the POWER_HOLD_GAIN_PCT that arms this rule, which made the rule
        # inert (every armed position still exited on the trail). Widen to
        # POWER_HOLD_TRAIL_PCT so the leader can actually run.
        if power_held:
            new_trail_pct = (
                POWER_HOLD_TRAIL_PCT
                if pos_stop_loss_pct < POWER_HOLD_TRAIL_PCT
                else None
            )
        else:
            new_trail_pct = _compute_dynamic_trail_pct(
                unrealized_pct, calendar_days, pos_stop_loss_pct,
                prove_it_pct=prove_it_trail_pct(
                    prove_it_level, current_price, prove_it_phase
                ),
            )
        if new_trail_pct is not None:
            prev_trail_pct = pos_stop_loss_pct
            widened = new_trail_pct > prev_trail_pct
            try:
                _contract_tighten = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_contract_tighten)
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _, confirmed_trail = place_trailing_stop(
                    ib, _contract_tighten, shares, new_trail_pct
                )
                client.table("portfolio_positions").update(
                    {"stop_loss_pct": confirmed_trail}
                ).eq("ticker", ticker).execute()
                pos_stop_loss_pct = confirmed_trail   # update in-memory for self-heal below
                verb = "widened (power hold)" if widened else "tightened"
                icon = "\U0001f3c6" if widened else "\U0001f512"
                msg = (
                    f"{icon} <b>{ticker}</b> trail {verb}: "
                    f"{prev_trail_pct * 100:.1f}% → {confirmed_trail * 100:.1f}%\n"
                    f"Gain: +{unrealized_pct:.1f}% | Days held: {calendar_days}d\n"
                    f"New stop floor: ${current_price * (1 - confirmed_trail):.2f}"
                )
                notifier._send(msg)
                print(f"   {icon} {ticker}: trail {verb} "
                      f"{prev_trail_pct * 100:.1f}% → {confirmed_trail * 100:.1f}% "
                      f"(+{unrealized_pct:.1f}% gain, {calendar_days}d held)")
            except Exception as _tighten_err:
                notifier.notify_exception(
                    "monitor_portfolio_intraday() trail update", _tighten_err
                )
                print(f"   ⚠️ {ticker}: trail update failed: {_tighten_err}")

        # ── Self-healing: ensure trailing stop exists for this position ─────────
        # GTC trailing stops survive IBKR gateway restarts, but may be absent for
        # positions opened before this feature or after a full account reset.
        _open_sells = [
            t for t in ib.openTrades()
            if t.contract.symbol == ticker
            and t.order.action == 'SELL'
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')
        ]

        if len(_open_sells) < 1:
            print(f"   🔧 {ticker}: No trailing stop in IBKR — re-placing (self-healing).")
            try:
                cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _heal_contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_heal_contract)
                # Anchor from current price — IBKR tracks HWM from here onward.
                _grp, _confirmed = place_trailing_stop(ib, _heal_contract, shares, pos_stop_loss_pct)
            except Exception as _heal_err:
                notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", _heal_err)
                print(f"   ⚠️ Self-healing failed for {ticker}: {_heal_err}")

        # Trailing stop is fully managed by IBKR. reconcile_with_ibkr() (Case 1)
        # detects when it fires and archives the position to trade_history.

        # Position remained active
        active_positions.append(pos)

    positions = active_positions

    # ── EOD Block (3:45–4:00 PM ET) ─────────────────────────────────────────────
    # Runs once per day at 3:45 PM. Implements:
    # 1. Update EOD metrics (days_held, days_since_hwm, volume_distribution_flag, live_rs_score, Mₜ)
    # 2. Day 3 Breakout Verdict (PASS/FAIL based on price +1% + volume >= 75% avg)
    # 3. Rank & Replace Swaps (Day 7+ only: auto-swap if Mₜ gap > 15pts vs best trigger)
    #
    now_eod = datetime.datetime.now(tz)
    is_eod_window = (now_eod.hour == 15 and now_eod.minute >= 45)

    if is_eod_window:
        today_eod = datetime.datetime.now(tz).date()

        # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
        try:
            recent_date = (datetime.datetime.now(tz) - datetime.timedelta(days=TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            triggers_res = client.table("daily_triggers") \
                .select("*") \
                .gte("triggered_at", recent_date) \
                .execute()
            held_tickers = {p["ticker"] for p in positions}
            fresh_triggers = [
                t for t in (triggers_res.data or [])
                if t["ticker"] not in held_tickers
            ]
            fresh_triggers.sort(
                key=lambda x: x.get("final_score") or x.get("quality_score") or x.get("ai_rating") or 0,
                reverse=True
            )
            fresh_tickers = {t["ticker"] for t in fresh_triggers}
            best_trigger       = fresh_triggers[0] if fresh_triggers else None
            best_trigger_score = (best_trigger.get("final_score") or 0) if best_trigger else 0
            best_ticker        = best_trigger["ticker"] if best_trigger else None
        except Exception:
            fresh_triggers     = []
            fresh_tickers      = set()
            best_trigger_score = 0
            best_ticker        = None

        market_regime = _get_market_regime()

        # 1. Update EOD metrics for all open positions
        for pos in positions:
            ticker_m = pos["ticker"]
            hwm_str  = (pos.get("hwm_date") or str(today_eod))[:10]
            hwm_d_m  = datetime.date.fromisoformat(hwm_str)
            dsm      = trading_days_between(hwm_d_m, today_eod)
            
            try:
                buy_date_m = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
                buy_date_d_m = buy_date_m.date()
            except Exception:
                buy_date_d_m = today_eod
            days_held_m = trading_days_between(buy_date_d_m, today_eod)
            
            live_rs  = _fetch_current_rs(ticker_m)

            ohlcv = _fetch_ohlcv(ticker_m, days=100)
            vol_dist = check_volume_distribution(ticker_m, ohlcv)

            # ── Live sentiment re-score for Mₜ (FMP news + GPT-4o-mini) ──────
            # Only run at EOD — ~4 calls/day, negligible cost.
            live_sentiment = fetch_held_position_sentiment(ticker_m)

            # ── Compute live Momentum Health Score Mₜ ────────────────────────
            # Temporarily inject live_rs into pos dict so compute_momentum_health_score
            # can read it. The real DB write happens in update_payload below.
            pos["live_rs_score"] = live_rs
            mt_score, mt_debug = compute_momentum_health_score(
                pos, ohlcv, live_sentiment, days_held=days_held_m
            )

            rsi_p    = mt_debug["rsi_penalty"]
            candle_p = mt_debug["candle_penalty"]
            penalties_str = ""
            if rsi_p != 0:
                penalties_str += f", RSI div {rsi_p:+.0f}"
            if candle_p != 0:
                penalties_str += f", candle {candle_p:+.0f}"

            print(f"   📊 {ticker_m}: Mₜ={mt_score:.1f} "
                  f"(RS {pos.get('entry_rs_score')}→{live_rs}, "
                  f"vol_dist={vol_dist}, sent={live_sentiment}"
                  f"{penalties_str})")

            # ── Day 3 Breakout Verdict (evaluated once at EOD of Day 3) ──────
            # PASS: close >= entry×1.01 AND Day 3 volume >= 75% of 20-day avg
            # FAIL: either condition not met → activates Intraday Loss Minimiser
            if days_held_m == 3 and pos.get("breakout_verdict") is None:
                current_price_m, _ = get_position_price(ib, ticker_m)
                buy_price_m     = float(pos["buy_price"])
                price_pass      = current_price_m > buy_price_m * (1 + BREAKOUT_VERDICT_MIN_GAIN)

                # _fetch_ohlcv() returns bars sorted ASCENDING (oldest first), so
                # ohlcv[-1] is today and ohlcv[-21:-1] is the prior 20 sessions.
                # This previously read ohlcv[0] / ohlcv[1:21] — i.e. a bar from
                # ~100 days ago compared against the 20 days after it. Both sides
                # were stale, so the ratio was ~1.0 and vol_pass was almost always
                # spuriously True, meaning the Day 3 verdict effectively tested
                # price only and FAIL almost never fired.
                day3_vol  = ohlcv[-1]["volume"] if ohlcv else None
                avg_vol   = (sum(b["volume"] for b in ohlcv[-21:-1]) / 20) if len(ohlcv) >= 21 else None
                vol_pass  = (day3_vol >= avg_vol * BREAKOUT_VERDICT_MIN_VOL_PCT) if (day3_vol and avg_vol) else True

                verdict = "PASS" if (price_pass and vol_pass) else "FAIL"
                try:
                    client.table("portfolio_positions").update(
                        {"breakout_verdict": verdict}
                    ).eq("ticker", ticker_m).execute()
                    pos["breakout_verdict"] = verdict
                except Exception as _ve:
                    print(f"   \u26a0\ufe0f Could not write breakout_verdict for {ticker_m}: {_ve}")

                icon = "\u2705" if verdict == "PASS" else "\u274c"
                price_icon = "\u2705" if price_pass else "\u274c"
                vol_icon = "\u2705" if vol_pass else "\u274c"
                vol_str = f"{day3_vol/avg_vol:.2f}\u00d7" if day3_vol and avg_vol else "N/A"
                print(
                    f"   {icon} {ticker_m}: Day 3 verdict = {verdict} "
                    f"(price {price_icon} "
                    f"{((current_price_m/buy_price_m)-1)*100:+.2f}%, "
                    f"vol {vol_icon} "
                    f"{vol_str})"
                )
                if verdict == "FAIL":
                    notifier.notify_breakout_verdict_fail(
                        ticker_m, buy_price_m, current_price_m, price_pass, vol_pass
                    )

            try:
                # ── Follow-through latch: the Prove-It phase discriminator ───
                # Latches True the first time the position CLOSES above entry,
                # and is never cleared — a breakout confirms only once.
                # Phase 1 applies only while this is False, which is what
                # confines the entry-anchored band to breakouts that never
                # followed through (unlike the old Intraday Loss Minimiser,
                # which cut working positions).
                # Once True it is never cleared — a breakout confirms only once.
                closed_above = bool(pos.get("closed_above_entry"))
                if not closed_above:
                    try:
                        eod_price, _ = get_position_price(ib, ticker_m)
                        if eod_price and eod_price > float(pos["buy_price"]):
                            closed_above = True
                            print(f"   ✅ {ticker_m}: closed above entry — thesis stop disarmed.")
                    except Exception as _cae:
                        print(f"   ⚠️ Could not evaluate follow-through for {ticker_m}: {_cae}")

                update_payload = {
                    "days_since_hwm":           dsm,
                    "days_held":                days_held_m,
                    "live_rs_score":            live_rs,
                    "volume_distribution_flag": vol_dist,
                    "top_trigger_score":        best_trigger_score if fresh_tickers else None,
                    "momentum_health_score":    mt_score,
                    "live_sentiment_score":     live_sentiment,
                    "closed_above_entry":       closed_above,
                }
                client.table("portfolio_positions").update(update_payload).eq("ticker", ticker_m).execute()
                pos["days_since_hwm"]           = dsm
                pos["days_held"]                = days_held_m
                pos["live_rs_score"]            = live_rs
                pos["volume_distribution_flag"] = vol_dist
                pos["top_trigger_score"]        = best_trigger_score if fresh_tickers else None
                pos["momentum_health_score"]    = mt_score
                pos["live_sentiment_score"]     = live_sentiment
                pos["closed_above_entry"]       = closed_above
            except Exception as _me:
                # PGRST204 = closed_above_entry column missing (migration not run).
                # Retry without it so the rest of the EOD metrics still persist.
                if "PGRST204" in str(_me) or "closed_above_entry" in str(_me):
                    print(f"   ⚠️ {ticker_m}: closed_above_entry column missing — run "
                          f"migrations/add_closed_above_entry.sql. Writing other metrics.")
                    try:
                        update_payload.pop("closed_above_entry", None)
                        client.table("portfolio_positions").update(
                            update_payload).eq("ticker", ticker_m).execute()
                    except Exception as _me2:
                        print(f"   ⚠️ Could not update EOD plateau metrics for {ticker_m}: {_me2}")
                else:
                    print(f"   ⚠️ Could not update EOD plateau metrics for {ticker_m}: {_me}")

        # 2. Rank & Replace Swaps (Day 7+ only)
        # Uses live Mₜ (momentum_health_score) as the comparator.
        # Only runs for positions held >= 7 days that passed the Day 3 verdict.
        if fresh_triggers and best_ticker and len(positions) >= MAX_POSITIONS:
            for pos in positions:
                ticker_m  = pos["ticker"]
                days_held_rr = pos.get("days_held") or 0
                verdict_rr   = pos.get("breakout_verdict")

                # Rotate out of stalled Day 7+ positions. A FAIL verdict marks a
                # breakout that never confirmed, which makes it a BETTER rotation
                # candidate, not a worse one — yet it was previously excluded
                # here (`verdict_rr != "PASS"`), so the weakest positions were the
                # only ones that could never be swapped out. That exclusion made
                # some sense when a FAIL verdict handed the position to the
                # Intraday Loss Minimiser; with the minimiser disabled it left
                # FAIL positions with no rotation path at all.
                #
                # FAIL positions now rotate on a LOWER score gap than PASS ones,
                # since less evidence should be needed to abandon a breakout that
                # already failed to confirm.
                if days_held_rr < 7:
                    continue
                swap_threshold = (RANK_REPLACE_THRESHOLD if verdict_rr == "PASS"
                                  else RANK_REPLACE_FAIL_THRESHOLD)

                # ── Staleness discount (absorbs the retired Plateau Exit) ─────
                # A position that has not made a new high in STALE_EXIT_DAYS
                # trading days has stopped working. That used to trigger a sale
                # to CASH, which was the wrong destination — it gave up the
                # position's optionality whether or not anything better existed.
                #
                # The signal is real, so it is kept; only its consequence
                # changes. Staleness now lowers the bar for ROTATION, so a
                # stalled name is abandoned readily when a genuinely better
                # breakout has appeared and held indefinitely when none has.
                # See docs/retired_code.md.
                stale_days_rr = 0
                if days_held_rr >= STALE_EXIT_MIN_DAYS_HELD:
                    try:
                        _hwm_raw_rr = pos.get("hwm_date")
                        if _hwm_raw_rr:
                            stale_days_rr = trading_days_between(
                                datetime.date.fromisoformat(str(_hwm_raw_rr)[:10]),
                                today_eod,
                            )
                    except Exception as _stale_err:
                        print(f"   ⚠️ Could not evaluate staleness for {ticker_m}: {_stale_err}")
                is_stale_rr = stale_days_rr >= STALE_EXIT_DAYS
                if is_stale_rr:
                    swap_threshold = min(swap_threshold, RANK_REPLACE_FAIL_THRESHOLD)

                # Never rotate out of a position protected by the 8-week hold rule:
                # a recent 20%-in-3-weeks leader is exactly what we want to keep.
                _rr_cal_days = (today_eod - datetime.datetime.fromisoformat(
                    pos["buy_date"].replace('Z', '+00:00')
                ).date()).days
                if is_power_hold_active(pos, _rr_cal_days):
                    print(f"   🏆 Rank & Replace skipped for {ticker_m} — 8-week hold active.")
                    continue

                mt = pos.get("momentum_health_score")
                comparator_score = mt if mt is not None else (
                    pos.get("entry_final_score") or pos.get("entry_quality_score") or 0
                )

                if best_trigger_score > comparator_score + swap_threshold:
                    mt_label = f"M\u209c={comparator_score:.1f}" if mt is not None else f"entry={comparator_score}"
                    stale_label = (f", stale {stale_days_rr}d — swap bar lowered to "
                                   f"{swap_threshold}pts" if is_stale_rr else "")
                    reason = (
                        f"Rank & Replace Swap (Day 7+) — replaced with superior breakout {best_ticker} "
                        f"(New trigger: {best_trigger_score} vs held {mt_label}{stale_label})"
                    )
                    print(f"\U0001f504 Rank & Replace: {ticker_m} ({mt_label}) \u2192 {best_ticker} ({best_trigger_score})")

                    shares_rr    = int(pos["shares"])
                    buy_price_rr = float(pos["buy_price"])
                    buy_date_rr  = datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
                    buy_reason_rr = pos.get("buy_reason", "Unknown")
                    current_price_rr, _ = get_position_price(ib, ticker_m)

                    # Deliberately a MARKET sell, not a Smart OCA exit, even
                    # though this is the least urgent rule in the ladder.
                    # Rank & Replace is a *swap*: the sell exists only to fund
                    # the named replacement buy on the very next line. An OCA
                    # may not fill for up to OCA_EXIT_DEFAULT_EXPIRY_DAYS, so
                    # routing it through the queue would decouple the two
                    # halves — cash stays tied up, the slot stays occupied, and
                    # the trigger being rotated into is likely gone by the time
                    # the sell completes. A better exit price is not worth
                    # losing the entry it was taken for.
                    # See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.
                    sold = execute_sell(ib, client, ticker_m, shares_rr, buy_price_rr,
                                        buy_date_rr, buy_reason_rr, current_price_rr, reason,
                                        pos_row=pos, market_regime=market_regime)
                    if sold:
                        print("   Slot freed. Running buy loop to fill slot...")
                        run_market_open_buys(ib)
                        break


def _infer_exit_type(reason: str) -> str:
    """Classify an exit reason into the breakout_learnings exit_type bucket."""
    r_lower = str(reason or "").lower()
    if "rank & replace" in r_lower or "rank and replace" in r_lower:
        return "rank_replace"
    if "time-stop" in r_lower or ("mandatory" in r_lower and "time" in r_lower):
        return "time_stop"
    if "break-even" in r_lower or "hwm break" in r_lower:
        return "break_even"
    if "ema" in r_lower or "moving average" in r_lower:
        return "ma_exit"
    if "hard stop" in r_lower:
        return "hard_stop"
    if "stop" in r_lower:
        return "stop_loss"
    if "rotation" in r_lower or "param" in r_lower or "drift" in r_lower:
        return "rotation"
    return "manual"


def _build_failed_params_snapshot(pos_row: dict | None, percent_return: float) -> dict:
    """
    Build a failure-parameter snapshot for breakout_learnings.

    Preferred source is `param_drift` (if present and parseable). If absent, use
    entry-time trigger parameters so the learning loop remains populated for exits
    reconciled from broker-managed stops.
    """
    import json as _json

    if not pos_row:
        return {}

    raw = pos_row.get("param_drift")
    if isinstance(raw, dict) and raw:
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except Exception:
            pass

    failed = percent_return < 0
    trigger_type = str(pos_row.get("entry_trigger_type") or "BREAKOUT").upper()
    snapshot = {
        "_meta": {"trigger_type": trigger_type, "source": "entry_snapshot"},
    }
    for key, source_key in (
        ("volume_surge", "entry_volume_surge"),
        ("rs_score", "entry_rs_score"),
        ("technical_score", "entry_technical_score"),
        ("pivot_distance_pct", "entry_pivot_distance_pct"),
    ):
        val = pos_row.get(source_key)
        if val is None:
            continue
        snapshot[key] = {"entry": val, "failed": failed}
    return snapshot


def _write_breakout_learning_row(client: Client, ticker: str, buy_date, reason: str,
                                 pos_row: dict | None, market_regime: str,
                                 percent_return: float) -> None:
    """Persist a single breakout_learnings row. Non-fatal by design."""
    try:
        if not pos_row:
            return
        buy_dt_d = buy_date.date() if hasattr(buy_date, "date") else buy_date
        days_held = trading_days_between(
            buy_dt_d, datetime.datetime.now(ZoneInfo("America/New_York")).date()
        )
        client.table("breakout_learnings").insert({
            "ticker":            ticker,
            "buy_date":          buy_dt_d.isoformat(),
            "exit_date":         datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "exit_type":         _infer_exit_type(reason),
            "entry_final_score": pos_row.get("entry_final_score"),
            "failed_params":     _build_failed_params_snapshot(pos_row, percent_return),
            "lesson_text":       pos_row.get("analysis_reason") or reason,
            "market_regime":     market_regime,
            "days_held":         days_held,
            "pnl_pct":           percent_return,
        }).execute()
        print(f"   📚 {ticker}: breakout_learnings row written (pnl={percent_return:+.1f}%)")
    except Exception as _le:
        print(f"   ⚠️ Could not write breakout_learnings for {ticker}: {_le}")


def execute_sell(ib: IB, client: Client, ticker: str, shares: int, buy_price: float,
                 buy_date, buy_reason: str, current_price: float, reason: str,
                 pos_row: dict | None = None,
                 market_regime: str = "neutral") -> bool:
    """Executes a market sell order on IBKR and archives the transaction in Supabase.

    CRITICAL INVARIANT: Supabase position is ONLY deleted after confirming via
    ib.portfolio() that the position is truly gone from IBKR. This prevents phantom
    deletions when market orders are cancelled/rejected (e.g. paper trading no-data).

    pos_row: the portfolio_positions dict for this ticker (used to write breakout_learnings).
    market_regime: 'uptrend' | 'correction' | 'neutral' at time of sell.
    """
    try:
        # Cancel any open trailing stop SELL orders before placing
        # explicit sell (stale rotation) to avoid duplicate fills.
        cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)

        # Place sell order
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        order = MarketOrder('SELL', shares)
        order.account = get_ibkr_account(ib)
        trade = ib.placeOrder(contract, order)
        
        print(f"   Placing market sell order for {shares} shares of {ticker}...")
        
        # Wait up to 60 seconds for fill
        for _ in range(30):
            ib.sleep(2)
            if trade.orderStatus.status == 'Filled':
                break

        # ── CRITICAL: verify fill via ib.portfolio() BEFORE touching Supabase ──
        # MarketOrders can be cancelled (e.g. paper-trading no live market data)
        # without raising a Python exception. We MUST confirm the position is
        # actually gone from IBKR before removing it from Supabase.
        ib_after = {
            p.contract.symbol: p for p in ib.portfolio()
            if p.contract.secType == "STK" and int(p.position) > 0
        }
        if ticker in ib_after:
            print(f"   ⚠️  SELL NOT CONFIRMED: {ticker} still in IBKR portfolio after sell attempt.")
            print(f"       Order status: {trade.orderStatus.status}. Cancelling order — Supabase record PRESERVED.")
            try:
                ib.cancelOrder(trade.order)
            except Exception:
                pass
            return False  # ← EXIT WITHOUT DELETING FROM SUPABASE

        # Sell confirmed — position is gone from IBKR
        fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0
        if fill_price <= 0:
            fill_price = current_price
            
        profit_loss = round((fill_price - buy_price) * shares, 2)
        percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
        
        # Log to trade history
        trade_log = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date.isoformat(),
            "buy_reason": buy_reason,
            "sell_price": fill_price,
            "sell_reason": reason,
            "profit_loss": profit_loss,
            "percent_return": percent_return
        }
        
        # Database transaction — only reached after confirmed IBKR fill
        client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
        client.table("trade_history").insert(trade_log).execute()

        # ── Write to breakout_learnings for future screener feedback ─────────────
        _write_breakout_learning_row(
            client=client,
            ticker=ticker,
            buy_date=buy_date,
            reason=reason,
            pos_row=pos_row,
            market_regime=market_regime,
            percent_return=percent_return,
        )

        print(f"✅ Closed Position: Sold {shares} shares of {ticker} at ${fill_price:.2f}.")
        print(f"   PnL: ${profit_loss} ({percent_return}%) | Reason: {reason}")
        notifier.notify_sell(
            ticker=ticker, shares=shares, buy_price=buy_price,
            buy_date=buy_date.isoformat(), fill_price=fill_price, reason=reason
        )
        return True
        
    except Exception as e:
        print(f"❌ Error executing sell order for {ticker}: {e}")
        notifier.notify_exception(f"execute_sell({ticker}) — execution_agent.py", e)
        return False


def main_loop():
    """Main daemon loop running inside the Docker container."""
    print("==================================================")
    print("       CANSLIM Local Trade Execution Agent        ")
    print("==================================================")
    print(f"Connecting to IB Gateway at {IB_GATEWAY_HOST}:{IB_GATEWAY_PORT}...")
    
    ib = IB()
    # Retry loop — keeps the container alive while IB Gateway is initialising or
    # re-authenticating after the daily reset.
    # Autoheal monitors the gateway health check and restarts the container automatically
    # if the API port is down. We suppress Telegram for the first AUTOHEAL_ALERT_AFTER
    # attempts to give autoheal time to act (~18 min with backoff). After that threshold
    # we fire ONE alert, meaning autoheal itself may have failed.
    AUTOHEAL_ALERT_AFTER = 6   # ~18 min: 30+60+120+300+300+300s of backoff
    _retry_delays = [30, 60, 120, 300]  # backoff schedule in seconds
    _attempt = 0
    _connect_silent_attempts = 0   # consecutive silent (pre-threshold) failures
    while True:
        try:
            ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
            print("✅ Connected to IBKR Gateway successfully!")

            # ── Real-time fill persistence hook (Layer 1) ─────────────────
            # Write every IBKR fill to ibkr_fills table the instant it fires.
            # This makes fills durable across session resets and container
            # restarts, eliminating the reqExecutions() session-cache problem
            # that caused RSI's sell price to be recorded incorrectly (2026-07-17).
            def _persist_fill_to_supabase(trade, fill):
                """execDetailsEvent handler — persists each fill immediately."""
                try:
                    exec_id = fill.execution.execId
                    if not exec_id:
                        return
                    supabase.table("ibkr_fills").upsert({
                        "exec_id":    exec_id,
                        "ticker":     fill.contract.symbol,
                        "side":       fill.execution.side,    # 'BOT' or 'SLD'
                        "shares":     fill.execution.shares,
                        "price":      fill.execution.price,
                        "commission": getattr(fill.commissionReport, 'commission', 0) or 0,
                        "fill_time":  fill.execution.time.isoformat(),
                        "order_id":   fill.execution.orderId,
                        "account_id": fill.execution.acctNumber,
                    }, on_conflict="exec_id").execute()
                    print(f"   💾 Fill persisted: {fill.contract.symbol} "
                          f"{fill.execution.side} {fill.execution.shares:.0f}sh "
                          f"@ ${fill.execution.price:.4f} (execId: {exec_id})")
                except Exception as _fe:
                    # Non-fatal — don't crash the agent on a DB write error
                    print(f"   ⚠️  fill_persist: failed to write {fill.contract.symbol} fill: {_fe}")

            ib.execDetailsEvent += _persist_fill_to_supabase
            print("   🔗 execDetailsEvent hook registered (fills will be persisted to ibkr_fills).")

            # ── Schema assertion at boot ──────────────────────────────────────
            # Surface missing risk-rule columns immediately rather than waiting
            # for the first buy cycle, so the operator learns at deploy time that
            # a rule is inert. Never fatal: monitoring and exits must keep running.
            try:
                _boot_report = schema_guard.check_schema(get_supabase_client())
                print(f"   🧬 {_boot_report.summary().splitlines()[0]}")
                if _boot_report.degraded:
                    for _t, _c, _w in _boot_report.missing_critical:
                        print(f"      • MISSING {_t}.{_c} — {_w}")
                    print(f"      Fix: run {schema_guard.REPAIR_SCRIPT} in the Supabase SQL Editor.")
            except Exception as _sce:
                print(f"   ⚠️ Boot schema check failed to run: {_sce}")

            # Prime positions cache unconditionally via reqPositions().
            # Unlike reqAccountUpdates(), reqPositions() does not require
            # managedAccounts() to be populated — it forces IBKR to push
            # all current Position objects, populating ib.positions().
            try:
                ib.reqPositions()
                ib.sleep(3)   # let event loop process incoming Position items
                _pos_count = len([p for p in ib.positions()
                                  if p.contract.secType == 'STK' and p.position > 0])
                print(f"   📡 Positions primed: {_pos_count} STK position(s) in cache.")
            except Exception as _prime_err:
                print(f"   ⚠️  Positions prime failed (non-fatal): {_prime_err}")
            _connect_silent_attempts = 0
            break
        except Exception as e:
            delay = _retry_delays[min(_attempt, len(_retry_delays) - 1)]
            _connect_silent_attempts += 1
            _attempt += 1
            if _connect_silent_attempts >= AUTOHEAL_ALERT_AFTER:
                # Autoheal has had enough time to fix this — something is wrong
                notifier.notify_exception(
                    f"main_loop() — IB Gateway still unreachable after "
                    f"{_connect_silent_attempts} attempts (~18 min). "
                    f"Autoheal may have failed.",
                    e,
                )
                _connect_silent_attempts = 0   # reset so we don't spam every attempt after threshold
            else:
                print(f"⚠️ IB Gateway unreachable (attempt {_attempt}) — "
                      f"autoheal watching, no alert for {AUTOHEAL_ALERT_AFTER - _connect_silent_attempts} more attempts.")
            print(f"❌ Cannot connect to IB Gateway: {e}")
            print(f"   Retrying in {delay}s... (attempt {_attempt})")
            time.sleep(delay)

    while True:
        try:
            tz = ZoneInfo("America/New_York")
            now = datetime.datetime.now(tz)
            today_str = now.strftime("%Y-%m-%d")

            if now.weekday() < 5:
                # SENTINEL: if /app/run_buys_now.txt exists, force-run buy logic immediately
                if os.path.exists("/app/run_buys_now.txt"):
                    os.remove("/app/run_buys_now.txt")
                    print("🎯 Force buy sentinel detected — running run_market_open_buys NOW")
                    reconcile_with_ibkr(ib)
                    run_market_open_buys(ib)
                    ib.sleep(900)
                    continue

                is_market_open = (
                    (now.hour == 9 and now.minute >= 30)
                    or (10 <= now.hour < 16)
                )

                # 1. Buy check + intraday monitoring (runs every 15 min while market is open)
                # has_bought_today removed: run_market_open_buys is idempotent — it exits
                # immediately when the portfolio is full or cash is insufficient.
                # Removing this gate means a force-sell that frees a slot is filled the
                # same day rather than waiting until the next morning.
                if is_market_open:
                    reconcile_with_ibkr(ib)        # Sync IBKR → Supabase before checks
                    process_exit_requests(ib)       # Smart OCA managed exits (before monitor:
                                                    # it decides which tickers monitor must skip)
                    run_market_open_buys(ib)        # No-op when portfolio is full
                    monitor_portfolio_intraday(ib)  # Trailing stops, MA exits, plateau rotation
                    # Drain again: the Day 7+ rules above enqueue rather than
                    # market-sell, and a triggered exit must not idle as PENDING
                    # for a further 15 minutes (unprotected — PENDING does not
                    # suspend the ladder, but the trail is still live) before its
                    # OCA goes out. Idempotent: a no-op when nothing was queued.
                    process_exit_requests(ib)
                    ib.sleep(900)
                    continue

            # ── Smart sleep: wake exactly at 9:30 AM ET ─────────────────────────────
            # Compute seconds until next 9:30 AM ET (today or tomorrow if already past)
            next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            if now >= next_open:
                # After today's open/close — aim for tomorrow, skip weekends
                next_open += datetime.timedelta(days=1)
                while next_open.weekday() >= 5:  # skip Sat(5) / Sun(6)
                    next_open += datetime.timedelta(days=1)

            secs_to_open = int((next_open - now).total_seconds())

            if secs_to_open <= 5400:  # within 90 min of next open → sleep precisely
                sleep_secs = max(secs_to_open + 30, 60)  # +30s buffer, never < 1 min
                print(f"⏰ Market opens at 9:30 AM ET — sleeping {sleep_secs // 60}m {sleep_secs % 60}s (until {next_open.strftime('%H:%M:%S')})")
            else:
                sleep_secs = 1800  # check every 30 min during deep off-hours
                print(f"😴 Market is closed. Checking in 30 min... (Current Time: {now.strftime('%H:%M:%S')})")

            time.sleep(sleep_secs)   # use time.sleep — ib.sleep() throws on a dead socket during long off-hours waits
            
        except KeyboardInterrupt:
            print("\nShutting down execution agent.")
            ib.disconnect()
            break
        except (ConnectionError, TimeoutError) as loop_err:
            # Gateway resets (IBKR nightly logoff, autoheal restart) produce ConnectionError
            # or TimeoutError. These are expected and autoheal handles them automatically.
            # Suppress Telegram -- reconnect failsafe below fires after the threshold.
            if "Socket disconnect" in str(loop_err):
                print(f"Warning: IBKR socket disconnected (daily reset) -- reconnecting silently.")
            else:
                print(f"Error: IBKR connection/timeout in main loop: {loop_err} -- autoheal watching, no alert.")
            time.sleep(60)
        except Exception as loop_err:
            print(f"❌ Error in main execution loop: {loop_err}")
            notifier.notify_exception("main_loop() — execution_agent.py", loop_err)
            time.sleep(60)   # use time.sleep — ib.sleep() throws on a dead socket
            
        # Reconnection failsafe
        if not ib.isConnected():
            print("Reconnecting to IB Gateway...")
            try:
                ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
                ib.reqPositions()  # re-subscribe after reconnect
                ib.sleep(3)
                print("Reconnected to IBKR Gateway successfully!")
                _connect_silent_attempts = 0   # reset threshold counter on success
            except Exception as e:
                _connect_silent_attempts += 1
                print(f"Reconnection failed (attempt {_connect_silent_attempts}): {e}")
                if _connect_silent_attempts >= AUTOHEAL_ALERT_AFTER:
                    notifier.notify_exception(
                        f"main_loop() -- reconnect -- gateway still down after "
                        f"{_connect_silent_attempts} attempts (~18 min). "
                        f"Autoheal may have failed.",
                        e,
                    )
                    _connect_silent_attempts = 0   # reset so we dont spam after each threshold
                time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CANSLIM Local execution agent CLI.")
    parser.add_argument("--mock-sell", type=str, help="Mock close a position in Supabase (e.g. AAPL)")
    parser.add_argument("--price", type=float, help="Mock sale price (required with --mock-sell)")
    parser.add_argument("--reason", type=str, default="Mock exit", help="Mock sale reason")
    
    args = parser.parse_args()
    
    if args.mock_sell:
        if not args.price:
            print("❌ Error: --price is required when mocking a sale.")
            sys.exit(1)
        handle_mock_sell(args.mock_sell, args.price, args.reason)
    else:
        if not FMP_API_KEY:
            print("❌ Error: FMP_API_KEY environment variable is not set.")
            sys.exit(1)
        main_loop()
