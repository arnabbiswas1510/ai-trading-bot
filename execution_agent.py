import os
import sys
import argparse
import atexit
import datetime
import re
import signal
import time
from collections import deque
import requests
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from ib_insync import IB, Stock, MarketOrder, Order
from telegram_notifier import TelegramNotifier, DELIVERY_FAIL_MARKER
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


# --- Log tee + Supabase log shipping/purge moved to agent_logging.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Imported here so
#     the import-time bootstrap below (TeeLogger instantiation + stdout swap)
#     and every test that uses execution_agent.<name> keep resolving. ---
from agent_logging import (
    TeeLogger,
    _ship_diag,
    _purge_agent_logs,
    flush_logs_to_supabase,
    flush_logs_quietly,
)


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


# Ship whatever is buffered when the process goes away. `docker stop` sends
# SIGTERM, whose default disposition kills Python WITHOUT running atexit, so the
# last cycle's logs would be lost in exactly the scenario most worth reading: a
# container that keeps restarting.
#
# These hooks are registered by main_loop(), NOT at import. Importing this
# module must stay side-effect-free at exit: other tooling imports it to read
# configuration and prints machine-readable output to stdout, and an
# import-time atexit hook attempting a Supabase round trip would both stall
# those callers and corrupt their output.
def _flush_logs_on_shutdown(signum=None, frame=None):
    try:
        flush_logs_quietly()
    except Exception:
        pass
    if signum is not None:
        # Restore the default disposition and re-raise, so this hook only buys
        # time to flush and does not change how the agent actually terminates.
        try:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        except Exception:
            os._exit(143)   # 128 + SIGTERM


def install_shutdown_log_flush():
    """Register the shutdown flush hooks. Called once, from main_loop()."""
    atexit.register(_flush_logs_on_shutdown)
    try:
        signal.signal(signal.SIGTERM, _flush_logs_on_shutdown)
    except (ValueError, OSError):
        pass    # not the main thread — atexit still covers us

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
from config import MAX_POSITIONS, STOP_LOSS_PCT, MAX_LOSS_PCT, COOLING_OFF_DAYS, BUY_PRICE_DRIFT_TOLERANCE  # noqa: E402  (single source of truth; set via .env)
import cooling_off  # noqa: E402  (reason-aware re-entry block, single source)

# ── Extracted modules (2026-09-18) ────────────────────────────────────────────
# These names are re-exported into this module's namespace ON PURPOSE. The test
# suite patches them as `execution_agent.<name>` (214 call sites), and the
# orchestrators below resolve them from these globals -- so importing by name
# here keeps every existing patch point working. Do NOT convert these to
# `import exit_rules` + `exit_rules.foo()` call sites without re-pointing the
# tests: the patches would silently become no-ops.
# See decisions/2026-09-18_execution-agent-split.md.
from market_calendar import (  # noqa: F401  (re-exported for patch compatibility)
    _is_rth_now, _nyse_holidays, trading_days_between,
)
from indicators import (  # noqa: F401  (re-exported for patch compatibility)
    MOMENTUM_HEALTH_RS_WEIGHT, MOMENTUM_HEALTH_VOL_WEIGHT, MOMENTUM_HEALTH_SENT_WEIGHT,
    calculate_sma, calculate_ema, compute_rsi,
    detect_candlestick_reversals, compute_momentum_health_score,
)
from exit_rules import (  # noqa: F401  (re-exported for patch compatibility)
    TRAIL_PROFIT_TIERS, PROVE_IT_ENABLED, PROVE_IT_P1_DAY0_PCT,
    PROVE_IT_P1_LATER_PCT, PROVE_IT_P1_DAY0_LAST_DAY, PROVE_IT_P2_ARM_GAIN_PCT,
    PROVE_IT_P2_FLOOR_PCT, PROVE_IT_BACKSTOP_SLACK_PCT, OCA_EXIT_ENABLED,
    OCA_EXIT_SETTLE_MINUTE, OCA_EXIT_ATR_FRACTION, OCA_EXIT_MIN_TRAIL_PCT,
    OCA_EXIT_MAX_TRAIL_PCT, OCA_EXIT_DEFAULT_ATR_PCT, OCA_EXIT_UPPER_ATR_FRACTION,
    OCA_EXIT_MIN_UPPER_PCT, OCA_EXIT_MAX_UPPER_PCT, OCA_EXIT_DEFAULT_FLOOR_PCT,
    OCA_EXIT_DEFAULT_EXPIRY_DAYS, SMART_EXIT_FOR_RULES, POWER_HOLD_ENABLED,
    POWER_HOLD_GAIN_PCT, POWER_HOLD_TRIGGER_DAYS, POWER_HOLD_DURATION_DAYS,
    POWER_HOLD_TRAIL_PCT, hard_stop_price, safe_hard_stop, _position_atr_pct,
    resolve_oca_trail_pct, resolve_oca_limit_price, prove_it_is_proven,
    prove_it_p1_threshold_pct, prove_it_stop_level, prove_it_trail_pct,
    _compute_dynamic_trail_pct, is_power_hold_active, sell_state_code,
    _infer_exit_type,
)
from exit_shadow import compute_exit_shadows

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

# ── Partial Scale-Out (winner give-back reducer) ───────────────────────────────
# The winner->loser problem: a position runs to +4-5%, then fades back through
# entry before any stop fires, turning a green trade red. Every attempt to fix
# this by moving the STOP LEVEL failed on the 33-trade replay — a tighter level
# is symmetric and taxes the fat winners the book depends on.
#
# The fix changes QUANTITY, not level. When a position's PEAK gain first reaches
# +SCALE_OUT_TRIGGER_PCT, sell SCALE_OUT_FRACTION of the shares at market and let
# the remainder ride the UNCHANGED Prove-It stop. Booking part of the gain is a
# guaranteed realised profit that a later fade cannot erase, while the untouched
# stop on the remainder means the winners are not clipped.
#
# +4% / 33% was chosen on the 33-trade exit_rule_replay --scale sweep: net-free
# vs shipped (-$64, deep in noise), lowest harmed count, benefit spread over 3
# trades rather than carried by one. It rescues only the faders that peak >=+4%
# (not GNK/FRO, which peak lower — an entry-quality problem tracked separately).
# Small sample: this is a PROVISIONAL decision, logged in
# decisions/provisional_decisions.json for revisit at >=50 trades.
# See decisions/2026-09-08_partial-scale-out.md.
SCALE_OUT_ENABLED       = os.getenv("SCALE_OUT_ENABLED", "true").lower() == "true"
SCALE_OUT_TRIGGER_PCT   = float(os.getenv("SCALE_OUT_TRIGGER_PCT", 0.04))   # +4% peak gain
SCALE_OUT_FRACTION      = float(os.getenv("SCALE_OUT_FRACTION",    0.33))   # sell 33%

# ── Exit-rule shadow logger (measurement only — never places an order) ─────────
# Logs, every monitor cycle, what two register-tracked exit CANDIDATES would do
# to each open position: Q1 arm@+3% (exit-parameters-proveit) and Q2 5% give-back
# trail (ladder-width-runon). Feeds forward, live evidence into those reviews
# that the 5-minute single-regime backtest cannot produce. Gated + fully
# exception-wrapped so it can never disturb trading. See exit_shadow.py.
EXIT_SHADOW_LOG_ENABLED = os.getenv("EXIT_SHADOW_LOG_ENABLED", "true").lower() == "true"

# ── Armed Trailing Exit (Day 0-6 loss-cutting) ─────────────────────────────────
# When the Prove-It Stop fires, we do NOT sell instantly at the trigger price — that price is
# often a local trough. Instead we "arm" the exit: place a tight IBKR native
# trailing stop that rides any bounce toward the best price reached since the
# trigger, while a hard deadline forces a market sell if it hasn't already
# closed out. This bounds the extra hold time so we never wait indefinitely
# (and risk deeper losses) chasing a better exit.
ARMED_EXIT_TRAIL_PCT      = float(os.getenv("ARMED_EXIT_TRAIL_PCT",      0.006))  # 0.6%
ARMED_EXIT_DEADLINE_HOURS = float(os.getenv("ARMED_EXIT_DEADLINE_HOURS", 3.25))   # ~half a trading day


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




AGENT_LOG_RETENTION_DAYS = int(os.getenv("AGENT_LOG_RETENTION_DAYS", 14))
# Routine INFO/TRADE lines are the bulk of the volume and lose their value
# quickly -- nobody debugs a healthy cycle from three weeks ago. Expiring them
# sooner is what lets the full firehose be shipped without the table growing
# without bound. Errors keep the longer window because they are what a
# post-mortem actually needs.
AGENT_LOG_INFO_RETENTION_DAYS = int(os.getenv("AGENT_LOG_INFO_RETENTION_DAYS", 3))
# Absolute ceiling, enforced regardless of age. This is the backstop against a
# runaway loop filling the table between two age-based purges -- age retention
# alone cannot bound a burst.
AGENT_LOG_MAX_ROWS = int(os.getenv("AGENT_LOG_MAX_ROWS", 250000))
# Levels that expire on the SHORT window. Everything else keeps the long one.
_SHORT_RETENTION_LEVELS = ("INFO", "TRADE")

_last_log_purge_at: datetime.datetime | None = None
_LOG_PURGE_INTERVAL = datetime.timedelta(hours=1)


# (ship/purge/flush definitions now live in agent_logging.py — imported above)


def _count_open_positions():
    """Best-effort count of open positions for the disconnect alert.

    Returns None (not 0) on any failure so the alert says "positions are
    UNMONITORED" rather than falsely implying an empty book. Never raises: the
    alert must fire even if Supabase is also unreachable.
    """
    try:
        client = get_supabase_client()
        res = client.table("portfolio_positions").select("ticker").execute()
        return len(res.data or [])
    except Exception:
        return None






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

# ── IBKR + FMP price / account-value access → ibkr_data.py ────────────────────
# Re-exported so all historical execution_agent.<name> call-sites and test
# patches keep resolving. See ibkr_data.py for the ea.-prefix safety note.
from ibkr_data import (  # noqa: E402
    get_live_price,
    _MarkContract,
    _IBKRMark,
    _IBKR_PRICE_MAP_CACHE,
    _IBKR_PRICE_MAP_TTL,
    build_ibkr_price_map,
    _compute_ibkr_price_map,
    _pnl_single_price_map,
    ibkr_target_positions,
    get_position_price,
    fetch_historical_closes_with_dates,
    _matches_account,
    _ibkr_avg_cost,
    get_own_cash,
    get_margin_loan,
    get_net_liquidation,
    get_available_cash,
    get_ibkr_account,
)

# --- Order & exit primitives moved to orders.py (see
#     decisions/2026-09-27_execution-agent-modular-split.md). Re-exported here
#     so tests that patch execution_agent.<name> keep resolving. ---
from orders import (
    TrailingStopOrder,
    place_trailing_stop,
    place_protective_stops,
    arm_exit,
    place_oca_exit,
    enqueue_smart_exit,
    process_exit_requests,
    _close_exit_request,
    get_oca_managed_tickers,
    maybe_arm_power_hold,
    SELL_STATE_LABELS,
    SELL_STATE_SUPPRESS_NOTIFY,
    maybe_notify_sell_state,
    cancel_ticker_sell_orders,
    handle_mock_sell,
)

# --- Trade-history & exit-context moved to trade_history.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from trade_history import (
    SELL_REASON_RUNAWAY_LIMIT,
    SELL_REASON_LEGACY_LIMIT,
    _clamp_reason,
    insert_trade_history,
    _exit_context_suffix,
)


# Set when Supabase rejects the IBKR valuation columns, purely so the warning is
# printed a single time per process instead of on every 15-minute cycle.
#
# This deliberately does NOT gate the write itself. It used to: the flag short-
# circuited _sync_ibkr_position_values() for the rest of the process lifetime,
# which meant applying the migration had no effect on a running agent and the
# dashboard kept showing cost basis until someone restarted the container --
# with nothing anywhere saying so. Graceful degradation has to be able to
# un-degrade. The retry costs one rejected request per cycle while the columns
# are genuinely absent, which is the right price for self-healing.
_IBKR_VALUATION_WARNING_SHOWN = False


# --- Broker reconciliation moved to reconciliation.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from reconciliation import _sync_ibkr_position_values


# ── IBKR fill + commission capture ───────────────────────────────────────────
# These are module-level (not closures inside main_loop) so they can be unit
# tested without a live IB connection, and so the commission attribution used at
# sell time is the same code path that wrote the rows.

# --- Fill ingestion & commission accounting moved to fills.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from fills import (
    extract_fill_commission,
    _fill_sink_failure,
    persist_fill,
    update_fill_commission,
    sum_fill_commission,
    LOT_FILL_LOOKBACK_MINUTES,
    lot_buy_basis_from_fills,
    has_prior_round_trip,
    _iso_shift_minutes,
    trade_commission,
    record_buy_commission,
    record_trade_commissions,
)


from reconciliation import reconcile_with_ibkr


# ── Market Direction ('M') filter → market_regime.py ──────────────────────────
# Re-exported so execution_agent.is_market_bullish / fetch_ibkr_delayed_price /
# _index_is_bullish / _fetch_market_closes remain patchable at their historical
# call-resolution point. See market_regime.py for the ea.-prefix safety note.
from market_regime import (  # noqa: E402
    _fetch_market_closes,
    _index_is_bullish,
    is_market_bullish,
    fetch_ibkr_delayed_price,
)


_schema_alert_sent = False


# --- Market-open buying + schema/size gates moved to buying.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from buying import (
    assert_schema_ok,
    equity_capped_position_size,
    run_market_open_buys,
)


# ── Research-data fetchers → sentiment.py ─────────────────────────────────────
# Re-exported so tests that patch execution_agent._fetch_ohlcv / _fetch_current_rs
# (and callers still living here) keep resolving them. See sentiment.py safety note.
from sentiment import (  # noqa: E402
    _get_entry_rs,
    _fetch_ohlcv,
    fetch_held_position_sentiment,
    _get_market_regime,
    _fetch_current_rs,
    check_volume_distribution,
)


# --- Intraday monitoring orchestrator moved to monitoring.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from monitoring import (
    monitor_portfolio_intraday,
    _build_failed_params_snapshot,
    _write_breakout_learning_row,
)


# --- Sell execution moved to selling.py
#     (decisions/2026-09-27_execution-agent-modular-split.md). Re-exported. ---
from selling import (
    execute_sell,
    execute_scale_out,
)


def main_loop():
    """Main daemon loop running inside the Docker container."""
    install_shutdown_log_flush()
    print("==================================================")
    print("       CANSLIM Local Trade Execution Agent        ")
    print("==================================================")

    # ── Telegram delivery self-test ───────────────────────────────────────
    # Runs BEFORE the IB connect retry loop, because a broken alert channel is
    # precisely what stops the operator from learning that anything below this
    # line went wrong. On 2026-09-18 the channel died after the 06:00 restart
    # and six trade events went unannounced; nothing in the logs said so.
    # getMe separates "token revoked" from "cannot reach Telegram" -- the two
    # causes need different fixes. Never fatal: a bot that trades without
    # alerts is bad, but one that refuses to guard open positions is worse.
    _tg_ok, _tg_detail = notifier.verify_delivery()
    if _tg_ok:
        print(f"✅ Telegram delivery verified: {_tg_detail}")
        notifier._send(
            f"🤖 <b>Execution agent started</b>\n"
            f"Alert channel verified ({_tg_detail}).\n"
            f"🕒 {datetime.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S ET')}"
        )
    else:
        print(f"{DELIVERY_FAIL_MARKER} STARTUP: Telegram delivery FAILED its "
              f"self-test: {_tg_detail}", file=sys.stderr)
        print(f"{DELIVERY_FAIL_MARKER} STARTUP: trade events will execute but "
              f"will NOT be announced. Fix the alert channel.", file=sys.stderr)
        print(f"⚠️  Telegram delivery self-test FAILED: {_tg_detail}")

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
    _down_since = time.time()      # when the current outage started (for alert duration)
    while True:
        try:
            ib.connect(IB_GATEWAY_HOST, IB_GATEWAY_PORT, clientId=1)
            print("✅ Connected to IBKR Gateway successfully!")

            # ── Real-time fill persistence hook (Layer 1) ─────────────────
            # Write every IBKR fill to ibkr_fills table the instant it fires.
            # This makes fills durable across session resets and container
            # restarts, eliminating the reqExecutions() session-cache problem
            # that caused RSI's sell price to be recorded incorrectly (2026-07-17).
            #
            # Two handlers are required, not one. IBKR sends the execution and
            # its commission as separate messages: execDetailsEvent carries price
            # and quantity, commissionReportEvent carries the fee. Registering
            # only the first is why every commission stored here was zero and
            # every P&L figure in the dashboard was gross.
            def _persist_fill_to_supabase(trade, fill):
                """execDetailsEvent handler — persists each fill immediately."""
                try:
                    if persist_fill(get_supabase_client(), fill):
                        print(f"   💾 Fill persisted: {fill.contract.symbol} "
                              f"{fill.execution.side} {fill.execution.shares:.0f}sh "
                              f"@ ${fill.execution.price:.4f} "
                              f"(execId: {fill.execution.execId})")
                except Exception as _fe:
                    # Non-fatal for trading — but NOT silent. This handler
                    # print()ed and nothing else, so 62 consecutive RLS denials
                    # left ibkr_fills empty for six weeks without a single alert
                    # while Tier 1 of the sell-price ladder was inert. A write
                    # sink nobody reads must escalate its own failures.
                    _fill_sink_failure("ibkr_fills", fill.contract.symbol, _fe)

            def _persist_commission_to_supabase(trade, fill, report):
                """commissionReportEvent handler — the only source of the fee."""
                try:
                    commission = getattr(report, "commission", None)
                    exec_id = getattr(report, "execId", None) \
                        or getattr(getattr(fill, "execution", None), "execId", None)
                    # The fill row may not exist yet if execDetails lost its race
                    # or was rejected; upsert it first so the fee always lands.
                    persist_fill(get_supabase_client(), fill)
                    if update_fill_commission(get_supabase_client(), exec_id, commission):
                        print(f"   🧾 Commission recorded: {fill.contract.symbol} "
                              f"${float(commission):.2f} (execId: {exec_id})")
                except Exception as _ce:
                    _fill_sink_failure("ibkr_fills commission",
                                       fill.contract.symbol, _ce)

            ib.execDetailsEvent += _persist_fill_to_supabase
            ib.commissionReportEvent += _persist_commission_to_supabase
            print("   🔗 execDetailsEvent + commissionReportEvent hooks registered "
                  "(fills and commissions will be persisted to ibkr_fills).")

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
                # Autoheal has had enough time to fix this — something is wrong.
                # Fire the loud, consequence-stating disconnect alert rather than a
                # generic exception: exits/stops are not running while we cannot
                # reach the gateway, and this must not read as a Telegram hiccup.
                notifier.notify_ibkr_disconnected(
                    attempts=_attempt,
                    minutes=int((time.time() - _down_since) / 60),
                    positions_unmonitored=_count_open_positions(),
                    market_open=_is_rth_now(),
                    error=e,
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
                    flush_logs_quietly()
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
                    # Ship buffered log lines LAST, so anything the cycle above
                    # logged reaches Supabase before the 15-minute sleep rather
                    # than sitting in memory where a container restart loses it.
                    flush_logs_quietly()
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

            # Off-hours ships too. Overnight is when the IBKR daily logoff, the
            # autoheal restart and the 6am health check happen -- the things
            # most likely to be broken by morning and least likely to be
            # observed live.
            flush_logs_quietly()

            time.sleep(sleep_secs)   # use time.sleep — ib.sleep() throws on a dead socket during long off-hours waits
            
        except KeyboardInterrupt:
            print("\nShutting down execution agent.")
            flush_logs_quietly()    # last chance — the buffer dies with the process
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
            flush_logs_quietly()    # a disconnect loop is exactly what needs reading remotely
            time.sleep(60)
        except Exception as loop_err:
            print(f"❌ Error in main execution loop: {loop_err}")
            notifier.notify_exception("main_loop() — execution_agent.py", loop_err)
            flush_logs_quietly()    # ship the traceback before the retry sleep
            time.sleep(60)   # use time.sleep — ib.sleep() throws on a dead socket
            
        # Reconnection failsafe
        if not ib.isConnected():
            print("Reconnecting to IB Gateway...")
            if _connect_silent_attempts == 0:
                _down_since = time.time()   # mark the start of this outage
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
                    # Loud disconnect alert — the broker link dropped mid-session
                    # and did not come back, so exits/stops are offline for the
                    # open book. See notify_ibkr_disconnected for why this is not
                    # a generic notify_exception.
                    notifier.notify_ibkr_disconnected(
                        attempts=_connect_silent_attempts,
                        minutes=int((time.time() - _down_since) / 60),
                        positions_unmonitored=_count_open_positions(),
                        market_open=_is_rth_now(),
                        error=e,
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
