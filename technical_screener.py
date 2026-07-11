import os
import requests
import datetime
import pandas as pd
from supabase import create_client, Client
from telegram_notifier import TelegramNotifier
from zoneinfo import ZoneInfo
from scoring import compute_rs_score   # pure function — no external deps

# Sourced safely from environment variables
raw_api_key = os.environ.get("FMP_API_KEY")
FMP_API_KEY = raw_api_key.strip().strip("'\"") if raw_api_key else None

raw_supabase_url = os.environ.get("SUPABASE_URL")
SUPABASE_URL = raw_supabase_url.strip().strip("'\"") if raw_supabase_url else None

raw_supabase_key = os.environ.get("SUPABASE_KEY")
if raw_supabase_key:
    cleaned_key = raw_supabase_key.strip().strip("'\"")
    if cleaned_key != raw_supabase_key:
        print("⚠️ SUPABASE_KEY environment variable had leading/trailing whitespace, newlines, or quotes which were stripped.")
    SUPABASE_KEY = cleaned_key
else:
    SUPABASE_KEY = None
FMP_BASE_URL = "https://financialmodelingprep.com"

# ── Technical screener configuration (set in .env) ──────────────────────────────
SMA_WINDOW          = int(os.environ.get("SMA_WINDOW", 50))
VOLUME_AVG_WINDOW   = int(os.environ.get("VOLUME_AVG_WINDOW", 50))
VOLUME_SURGE_MIN    = float(os.environ.get("VOLUME_SURGE_MIN", 1.20))
ROLLING_HIGH_WINDOW = int(os.environ.get("ROLLING_HIGH_WINDOW", 252))
PIVOT_PROXIMITY     = float(os.environ.get("PIVOT_PROXIMITY", 0.95))
MIN_PRICE_HISTORY   = int(os.environ.get("MIN_PRICE_HISTORY", 50))
FMP_HISTORY_DAYS    = int(os.environ.get("FMP_HISTORY_DAYS", 380))


def compute_quality_score(volume_surge_ratio: float, pivot_dist_pct: float,
                          current_close: float, sma_50: float) -> int:
    """
    Composite quality score 0-100 for a breakout trigger.

    Weights:
      Volume surge   40% -- normalised against 3x average (>=3x -> full marks)
      Pivot proximity 40% -- distance from 52-week high (0% -> full marks, -5% -> zero)
      SMA margin      20% -- how far above 50-day SMA (capped at 10% above)

    Kept unchanged for backwards-compat; also stored as technical_score in
    the new 5-component final_score system.
    """
    vol_norm       = min(volume_surge_ratio / 3.0, 1.0)
    prox_norm      = max(0.0, 1.0 + (pivot_dist_pct / 5.0))
    sma_margin_pct = ((current_close - sma_50) / sma_50 * 100.0) if sma_50 > 0 else 0.0
    sma_norm       = min(max(sma_margin_pct / 10.0, 0.0), 1.0)
    score          = (vol_norm * 40.0) + (prox_norm * 40.0) + (sma_norm * 20.0)
    return int(round(score))


def fetch_spy_return_12w() -> float:
    """
    Fetch SPY's 12-week (approx. 60 trading days) price return as a percentage.
    Returns 0.0 on failure -- rs_score will be 50 (neutral) for all tickers.
    Called once per screener run, before the main ticker loop.
    """
    try:
        to_date   = datetime.datetime.now(ZoneInfo('America/New_York')).date()
        from_date = to_date - datetime.timedelta(days=100)   # extra calendar buffer
        url = (f"{FMP_BASE_URL}/stable/historical-price-eod/full"
               f"?symbol=SPY&from={from_date}&to={to_date}&apikey={FMP_API_KEY}")
        resp = fetch_with_retry_sync(url)
        if resp is None or resp.status_code != 200:
            print("⚠️ Could not fetch SPY history for RS calculation.")
            return 0.0
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) < 2:
            return 0.0
        df_spy = pd.DataFrame(data)
        df_spy['date'] = pd.to_datetime(df_spy['date'])
        df_spy = df_spy.sort_values('date', ascending=True).reset_index(drop=True)
        lookback   = min(60, len(df_spy) - 1)
        price_now  = float(df_spy.iloc[-1]['close'])
        price_then = float(df_spy.iloc[-1 - lookback]['close'])
        if price_then <= 0:
            return 0.0
        return round(((price_now / price_then) - 1.0) * 100.0, 2)
    except Exception as e:
        print(f"⚠️ SPY RS fetch failed: {e}")
        return 0.0


# Module-level default — overwritten in __main__ block after fetch_spy_return_12w().
# Kept at module scope so check_technical_breakout() can reference it as a global.
_SPY_12W_RETURN: float = 0.0


# ── Telegram notifications ─────────────────────────────────────────────────────
notifier = TelegramNotifier(
    bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
)

# Lazy Initialize Supabase Client
supabase_client: Client = None

def get_supabase_client() -> Client:
    global supabase_client
    if supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase_client


def get_watchlist_from_supabase():
    try:
        client = get_supabase_client()
        timestamps_res = client.table("watchlist").select("created_at").order("created_at", desc=True).limit(1).execute()
        if not timestamps_res.data:
            return []
        latest_ts   = timestamps_res.data[0]["created_at"]
        latest_date = datetime.date.fromisoformat(latest_ts.split('T')[0])
        today       = datetime.datetime.now(datetime.timezone.utc).date()
        if latest_date != today:
            print(f"⚠️ Watchlist was last updated on {latest_date}, not today ({today}). Aborting.")
            return []
        response = client.table("watchlist").select("ticker").execute()
        return [row['ticker'] for row in response.data]
    except Exception as e:
        print(f"❌ Failed to fetch watchlist from Supabase: {e}")
        raise e


def fetch_with_retry_sync(url, retries=3, backoff=1.0):
    import time
    for i in range(retries):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                sleep_time = backoff * (2 ** i)
                print(f"⚠️ Rate limited (429) on FMP API. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                return res
        except Exception as e:
            if i == retries - 1:
                raise e
            time.sleep(backoff * (2 ** i))
    return None


def check_technical_breakout(ticker):
    try:
        to_date   = datetime.datetime.now(ZoneInfo('America/New_York')).date()
        from_date = to_date - datetime.timedelta(days=FMP_HISTORY_DAYS)
        url = (f"{FMP_BASE_URL}/stable/historical-price-eod/full"
               f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={FMP_API_KEY}")
        response = fetch_with_retry_sync(url)

        if response is None or response.status_code != 200:
            print(f"⚠️ FMP API error ({response.status_code if response else 'failed'}) for {ticker}")
            return None

        data = response.json()
        if not data or not isinstance(data, list):
            return None

        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date', ascending=True).reset_index(drop=True)

        if len(df) < MIN_PRICE_HISTORY:
            print(f"⚠️ Insufficient price history for {ticker} (minimum {MIN_PRICE_HISTORY} days required)")
            return None

        df['sma_50']         = df['close'].rolling(window=SMA_WINDOW).mean()
        df['avg_volume_50']  = df['volume'].rolling(window=VOLUME_AVG_WINDOW).mean()
        window_size          = min(ROLLING_HIGH_WINDOW, len(df))
        df['rolling_high_52w'] = df['high'].rolling(
            window=window_size,
            min_periods=min(MIN_PRICE_HISTORY, window_size)
        ).max()

        today_row     = df.iloc[-1]
        current_close = today_row['close']
        sma_50        = today_row['sma_50']
        today_volume  = today_row['volume']
        avg_vol_50    = today_row['avg_volume_50']

        is_above_50ma     = current_close > sma_50
        volume_surge_ratio = today_volume / avg_vol_50 if avg_vol_50 > 0 else 0
        has_volume_surge   = volume_surge_ratio >= VOLUME_SURGE_MIN
        is_breaking_high   = current_close >= (today_row['rolling_high_52w'] * PIVOT_PROXIMITY)

        if is_above_50ma and has_volume_surge and is_breaking_high:
            rolling_high  = today_row['rolling_high_52w']
            pivot_dist    = ((current_close / rolling_high) - 1.0) * 100.0 if rolling_high > 0 else 0.0
            quality_score = compute_quality_score(volume_surge_ratio, pivot_dist, current_close, sma_50)

            # 12-week stock return (reuses already-fetched price history — zero extra API calls)
            stock_12w_return = 0.0
            try:
                lookback   = min(60, len(df) - 1)
                p_now      = float(df.iloc[-1]['close'])
                p_then     = float(df.iloc[-1 - lookback]['close'])
                if p_then > 0:
                    stock_12w_return = round(((p_now / p_then) - 1.0) * 100.0, 2)
            except Exception:
                pass   # stays 0.0 -> rs_score = 50 (neutral)

            rs       = compute_rs_score(stock_12w_return, _SPY_12W_RETURN)
            today_ny = datetime.datetime.now(ZoneInfo("America/New_York")).date().strftime("%Y-%m-%d")

            # ── ATR-14 (swing-trade velocity) ────────────────────────────────
            # True Range = max(H-L, |H-prevC|, |L-prevC|)
            # ATR% = 14-day avg ATR as % of current price
            # est_days_to_target = trading days to reach 25% at avg ATR pace
            # Zero extra API calls — high/low/close already in df.
            atr_pct            = 0.0
            est_days_to_target = 999   # sentinel = "unreachable within swing horizon"
            try:
                df['prev_close'] = df['close'].shift(1)
                df['tr'] = (
                    pd.concat([
                        df['high'] - df['low'],
                        (df['high'] - df['prev_close']).abs(),
                        (df['low']  - df['prev_close']).abs(),
                    ], axis=1)
                ).max(axis=1)
                atr_14 = df['tr'].rolling(window=14).mean().iloc[-1]
                if current_close > 0 and atr_14 == atr_14:   # NaN guard
                    atr_pct = round((atr_14 / current_close) * 100.0, 2)
                    if atr_pct > 0:
                        est_days_to_target = int(round(25.0 / atr_pct))
            except Exception:
                pass   # stay at defaults on any error

            return {
                "ticker":              ticker,
                "close_price":         float(round(current_close, 2)),
                "volume_surge":        float(round(volume_surge_ratio, 2)),
                "sma_50":              float(round(sma_50, 2)),
                "rolling_high_52w":    float(round(rolling_high, 2)),
                "pivot_distance_pct":  float(round(pivot_dist, 2)),
                "quality_score":       quality_score,   # kept for backwards-compat
                "technical_score":     quality_score,   # alias for 5-component formula
                "avg_volume_50":       int(avg_vol_50) if avg_vol_50 == avg_vol_50 else 0,
                "rs_score":            rs,
                "atr_pct":             atr_pct,           # daily ATR as % of price
                "est_days_to_target":  est_days_to_target, # trading days to +25% at ATR pace
                "triggered_at":        today_ny,
            }

    except Exception as e:
        print(f"❌ Error processing technical indicators for {ticker}: {e}")
    return None


def write_triggers_to_supabase(triggers):
    if not triggers:
        print("😴 No breakouts found today. Database insertion skipped.")
        return
    try:
        client = get_supabase_client()
        from retention_helper import increment_retention

        print("[*] Querying existing daily triggers...")
        existing_res = client.table("daily_triggers").select("ticker, retention_period").execute()
        existing_map = {row["ticker"]: row for row in (existing_res.data or [])}

        for t in triggers:
            ticker = t["ticker"]
            t["retention_period"] = (
                increment_retention(existing_map[ticker].get("retention_period"))
                if ticker in existing_map else "1d"
            )

        print("🧹 Truncating daily_triggers table...")
        client.table("daily_triggers").delete().neq("ticker", "DUMMY_NEVER_MATCH").execute()

        print(f"📤 Pushing {len(triggers)} breakouts to 'daily_triggers'...")
        client.table("daily_triggers").insert(triggers).execute()
        print("✅ Breakouts replaced successfully.")
    except Exception as e:
        print(f"❌ Failed to log breakout signals: {e}")
        raise e


if __name__ == "__main__":
    if not FMP_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables. Please check FMP_API_KEY, SUPABASE_URL, and SUPABASE_KEY.")
        exit(1)

    # Fetch SPY 12-week return once — single FMP call, used as RS baseline for all tickers
    _SPY_12W_RETURN = fetch_spy_return_12w()
    print(f"📈 SPY 12w return: {_SPY_12W_RETURN:+.2f}% (RS baseline)")

    try:
        print("⏳ Synchronizing cloud fundamental watchlist data...")
        watchlist = get_watchlist_from_supabase()

        if not watchlist:
            print("💭 Target tracking watchlist is empty or could not be retrieved.")
            notifier.notify_breakouts_detected([])
        else:
            print(f"🔍 Analyzing {len(watchlist)} assets for volume breakouts...")
            active_triggers = []
            for ticker in watchlist:
                trigger_data = check_technical_breakout(ticker)
                if trigger_data:
                    print(f"🔥 Breakout: {ticker}  ${trigger_data['close_price']}  "
                          f"Vol:{trigger_data['volume_surge']}x  RS:{trigger_data['rs_score']}")
                    active_triggers.append(trigger_data)

            write_triggers_to_supabase(active_triggers)
            notifier.notify_breakouts_detected(active_triggers)
    except Exception as e:
        notifier.notify_exception("main block — technical_screener.py", e)
        raise
