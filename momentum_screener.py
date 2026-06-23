"""
momentum_screener.py — Secondary breakout screener with relaxed thresholds.

Runs after technical_screener.py in daily_technical.yml (GitHub Actions).
Only activates when today's daily_triggers count < MAX_POSITIONS.

Two-pass technical logic:
  Pass 1: Relaxed fundamentals + STANDARD technical thresholds
          (VOLUME_SURGE_MIN, PIVOT_PROXIMITY)
  Pass 2: Relaxed fundamentals + RELAXED technical thresholds
          (MOMENTUM_VOLUME_SURGE_MIN, MOMENTUM_PIVOT_PROXIMITY)
          — only runs if Pass 1 doesn't yield enough candidates

Results written to momentum_triggers table.
"""

import os
import asyncio
import datetime
import httpx
import requests
import pandas as pd
from supabase import create_client, Client
from telegram_notifier import TelegramNotifier
from zoneinfo import ZoneInfo

# ── Credentials ────────────────────────────────────────────────────────────────
raw_api_key    = os.environ.get("FMP_API_KEY")
API_KEY        = raw_api_key.strip().strip("'\"") if raw_api_key else None
raw_supa_url   = os.environ.get("SUPABASE_URL")
SUPABASE_URL   = raw_supa_url.strip().strip("'\"") if raw_supa_url else None
raw_supa_key   = os.environ.get("SUPABASE_KEY")
SUPABASE_KEY   = raw_supa_key.strip().strip("'\"") if raw_supa_key else None
FMP_BASE_URL   = "https://financialmodelingprep.com"

# ── Primary technical thresholds (shared with technical_screener.py) ───────────
MAX_POSITIONS      = int(os.environ.get("MAX_POSITIONS", 4))
VOLUME_SURGE_MIN   = float(os.environ.get("VOLUME_SURGE_MIN", 1.40))
PIVOT_PROXIMITY    = float(os.environ.get("PIVOT_PROXIMITY", 0.98))
SMA_WINDOW         = int(os.environ.get("SMA_WINDOW", 50))
VOLUME_AVG_WINDOW  = int(os.environ.get("VOLUME_AVG_WINDOW", 50))
ROLLING_HIGH_WINDOW = int(os.environ.get("ROLLING_HIGH_WINDOW", 252))
MIN_PRICE_HISTORY  = int(os.environ.get("MIN_PRICE_HISTORY", 50))
FMP_HISTORY_DAYS   = int(os.environ.get("FMP_HISTORY_DAYS", 380))

# ── Relaxed momentum thresholds (separate MOMENTUM_* namespace) ────────────────
MOMENTUM_MIN_Q_EPS_GROWTH  = float(os.environ.get("MOMENTUM_MIN_Q_EPS_GROWTH", 0.10))
MOMENTUM_MIN_INST_HOLDERS  = int(os.environ.get("MOMENTUM_MIN_INST_HOLDERS", 3))
MOMENTUM_VOLUME_SURGE_MIN  = float(os.environ.get("MOMENTUM_VOLUME_SURGE_MIN", 1.20))
MOMENTUM_PIVOT_PROXIMITY   = float(os.environ.get("MOMENTUM_PIVOT_PROXIMITY", 0.95))
MOMENTUM_TRIGGER_PRUNE_DAYS = int(os.environ.get("MOMENTUM_TRIGGER_PRUNE_DAYS", 56))
API_CONCURRENCY            = int(os.environ.get("API_CONCURRENCY", 10))

# ── Telegram ───────────────────────────────────────────────────────────────────
notifier = TelegramNotifier(
    bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    chat_ids=os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
)

# ── Supabase client ────────────────────────────────────────────────────────────
_supabase_client: Client = None

def get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY.")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


# ── Helpers ────────────────────────────────────────────────────────────────────

def count_daily_triggers_today() -> int:
    """How many daily_triggers rows exist for today? Used to decide if we run."""
    client = get_supabase_client()
    today = datetime.date.today().isoformat()
    res = client.table("daily_triggers").select("ticker", count="exact").eq("triggered_at", today).execute()
    return res.count if res.count is not None else len(res.data or [])


async def fetch_with_retry(client: httpx.AsyncClient, url: str,
                           retries: int = 3, backoff: float = 1.0):
    import asyncio as _asyncio
    for i in range(retries):
        try:
            res = await client.get(url, timeout=10)
            if res.status_code == 200:
                return res
            elif res.status_code == 429:
                wait = backoff * (2 ** i)
                print(f"⚠️ Rate limited (429). Retrying in {wait}s...")
                await _asyncio.sleep(wait)
            else:
                return res
        except Exception as e:
            if i == retries - 1:
                raise e
            import asyncio as _a2; await _a2.sleep(backoff * (2 ** i))
    return None


# Hardcoded fallback if FMP S&P 500 endpoint is restricted
FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "LLY", "AVGO", "JPM",
    "V", "UNH", "MA", "XOM", "HD", "PG", "COST", "JNJ", "ORCL", "ABBV", "BAC",
    "MRK", "NFLX", "CVX", "AMD", "CRM", "PEP", "ADBE", "TMO", "WMT", "WFC", "KO",
    "DIS", "ACN", "CSCO", "QCOM", "LIN", "PM", "GE", "INTC", "TXN", "MS", "INTU",
    "AMGN", "ISRG", "CAT", "SPGI", "IBM", "HON", "AXP", "GS", "COP", "BKNG",
    "AMAT", "PLTR", "LRCX", "TJX", "ADI", "MDLZ", "MDT", "VRTX", "CI", "C",
    "SBUX", "ADP", "SYK", "REGN", "ANET", "DE", "EL", "CB", "MMC", "GILD",
    "PANW", "TMUS", "MU", "CRWD", "BSX", "LMT", "SMCI", "CELH", "COIN", "ELF",
    "MSTR", "DKNG", "ON", "ARM", "SOFI", "HOOD", "BABA", "PDD",
    "UBER", "ABNB", "SNOW", "WDAY", "DDOG", "NET", "MELI", "SE",
    "SHOP", "SQ", "MAR", "HLT", "RCL", "CCL", "NCLH", "CMG", "SHW",
    "PH", "ETN", "GEHC", "MCK", "COR", "CAH", "CNC", "HUM", "EW", "DXCM",
    "ABT", "A", "KEYS", "FTNT", "FSLR", "ENPH", "MCHP", "MPWR", "NXPI",
    "KLAC", "ASML", "TSM", "GRAB", "GPUS", "QS"
]


async def get_sp500_tickers(client: httpx.AsyncClient) -> list[str]:
    url = f"{FMP_BASE_URL}/stable/sp500-constituent?apikey={API_KEY}"
    try:
        res = await fetch_with_retry(client, url)
        if res and res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data and "symbol" in data[0]:
                symbols = [d["symbol"] for d in data if d.get("symbol")]
                print(f"✅ Fetched {len(symbols)} S&P 500 tickers from FMP.")
                return symbols
    except Exception as e:
        print(f"⚠️ S&P 500 fetch failed: {e}. Using fallback list.")
    print(f"⚠️ Using hardcoded fallback list ({len(FALLBACK_TICKERS)} tickers).")
    return FALLBACK_TICKERS


async def passes_relaxed_fundamentals(ticker: str, client: httpx.AsyncClient,
                                      semaphore: asyncio.Semaphore) -> bool:
    """
    Lightweight fundamental gate:
      - Q EPS growth >= MOMENTUM_MIN_Q_EPS_GROWTH  (default 10%)
      - Institutional holders >= MOMENTUM_MIN_INST_HOLDERS  (default 3)
        (If institutional endpoint is restricted, gate is skipped)
      - No annual EPS requirement (relaxed vs CANSLIM)
    """
    async with semaphore:
        try:
            growth_url = f"{FMP_BASE_URL}/stable/financial-growth?symbol={ticker}&limit=1&apikey={API_KEY}"
            res = await fetch_with_retry(client, growth_url)
            if res is None or res.status_code != 200:
                return False
            data = res.json()
            if not isinstance(data, list) or not data:
                return False
            q_eps = data[0].get("epsgrowth", 0) or 0
            try:
                q_eps = float(q_eps)
            except (ValueError, TypeError):
                q_eps = 0.0
            if q_eps < MOMENTUM_MIN_Q_EPS_GROWTH:
                return False

            # Institutional holder check (skip gate if endpoint restricted)
            inst_url = f"https://financialmodelingprep.com/api/v3/institutional-holder/{ticker}?apikey={API_KEY}"
            inst_res = await fetch_with_retry(client, inst_url)
            if inst_res and inst_res.status_code == 200:
                holders = inst_res.json()
                if isinstance(holders, list):
                    count = len([h for h in holders if isinstance(h, dict) and h.get("holder")])
                    if count < MOMENTUM_MIN_INST_HOLDERS:
                        return False

            return True
        except Exception:
            return False


def check_technical_breakout_with_thresholds(ticker: str,
                                              vol_surge_min: float,
                                              pivot_proximity: float) -> dict | None:
    """
    Technical breakout check — same logic as technical_screener.py but
    with configurable thresholds for the two-pass momentum strategy.
    """
    try:
        to_date   = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=FMP_HISTORY_DAYS)
        url = (f"{FMP_BASE_URL}/stable/historical-price-eod/full"
               f"?symbol={ticker}&from={from_date}&to={to_date}&apikey={API_KEY}")

        import time
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    break
                elif r.status_code == 429:
                    time.sleep(2 ** attempt)
                else:
                    return None
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(2 ** attempt)
        else:
            return None

        data = r.json()
        if not data or not isinstance(data, list):
            return None

        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=True).reset_index(drop=True)

        if len(df) < MIN_PRICE_HISTORY:
            return None

        df["sma_50"]       = df["close"].rolling(window=SMA_WINDOW).mean()
        df["avg_vol_50"]   = df["volume"].rolling(window=VOLUME_AVG_WINDOW).mean()
        win                = min(ROLLING_HIGH_WINDOW, len(df))
        df["rolling_high"] = df["high"].rolling(window=win, min_periods=min(MIN_PRICE_HISTORY, win)).max()

        row          = df.iloc[-1]
        close        = row["close"]
        sma_50       = row["sma_50"]
        avg_vol      = row["avg_vol_50"]
        rolling_high = row["rolling_high"]
        vol_ratio    = row["volume"] / avg_vol if avg_vol > 0 else 0

        if (close > sma_50
                and vol_ratio >= vol_surge_min
                and close >= rolling_high * pivot_proximity):
            pivot_dist = ((close / rolling_high) - 1.0) * 100.0 if rolling_high > 0 else 0.0
            
            today_ny = datetime.datetime.now(ZoneInfo("America/New_York")).date().strftime("%Y-%m-%d")
            
            return {
                "ticker":             ticker,
                "close_price":        float(round(close, 2)),
                "volume_surge":       float(round(vol_ratio, 2)),
                "sma_50":             float(round(sma_50, 2)),
                "rolling_high_52w":   float(round(rolling_high, 2)),
                "pivot_distance_pct": float(round(pivot_dist, 2)),
                "triggered_at":       today_ny,
            }
    except Exception as e:
        print(f"⚠️ Technical error for {ticker}: {e}")
    return None


def write_momentum_triggers(triggers: list[dict]) -> None:
    if not triggers:
        print("😴 No momentum breakouts to write.")
        return
    client = get_supabase_client()
    print(f"📤 Writing {len(triggers)} momentum triggers to Supabase...")
    client.table("momentum_triggers").insert(triggers).execute()

    prune_cutoff = (datetime.date.today() - datetime.timedelta(days=MOMENTUM_TRIGGER_PRUNE_DAYS)).isoformat()
    client.table("momentum_triggers").delete().lt("triggered_at", prune_cutoff).execute()
    print("✅ momentum_triggers written and pruned.")


async def main():
    if not API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing FMP_API_KEY, SUPABASE_URL, or SUPABASE_KEY.")
        return

    # ── Gate: only run if daily_triggers are insufficient ─────────────────────
    today_primary_count = count_daily_triggers_today()
    needed = MAX_POSITIONS - today_primary_count
    if needed <= 0:
        print(f"✅ daily_triggers already has {today_primary_count} triggers "
              f"(>= MAX_POSITIONS={MAX_POSITIONS}). Momentum screener not needed.")
        return

    print(f"📊 daily_triggers today: {today_primary_count}. "
          f"Need {needed} more to fill {MAX_POSITIONS} slots. Running momentum screener...")

    semaphore = asyncio.Semaphore(API_CONCURRENCY)

    async with httpx.AsyncClient() as http:
        # Step 1: get universe
        universe = await get_sp500_tickers(http)

        # Step 2: filter by relaxed fundamentals (async, concurrent)
        print(f"🔬 Checking relaxed fundamentals for {len(universe)} tickers "
              f"(EPS >= {MOMENTUM_MIN_Q_EPS_GROWTH*100:.0f}%, "
              f"holders >= {MOMENTUM_MIN_INST_HOLDERS})...")
        tasks   = [passes_relaxed_fundamentals(t, http, semaphore) for t in universe]
        results = await asyncio.gather(*tasks)
        qualified = [t for t, ok in zip(universe, results) if ok]
        print(f"✅ {len(qualified)} tickers passed relaxed fundamentals.")

    if not qualified:
        print("⚠️ No tickers passed relaxed fundamentals. Exiting.")
        return

    # Step 3a — Pass 1: standard technical thresholds
    print(f"\n📈 Pass 1: standard technical thresholds "
          f"(vol surge >= {VOLUME_SURGE_MIN}x, pivot proximity >= {PIVOT_PROXIMITY})...")
    pass1_results = []
    for ticker in qualified:
        result = check_technical_breakout_with_thresholds(
            ticker, VOLUME_SURGE_MIN, PIVOT_PROXIMITY)
        if result:
            print(f"   🔥 Pass 1 breakout: {ticker} — surge {result['volume_surge']}x, "
                  f"pivot dist {result['pivot_distance_pct']:.1f}%")
            pass1_results.append(result)

    print(f"Pass 1 found {len(pass1_results)} breakout(s).")

    if len(pass1_results) >= needed:
        # Pass 1 alone is enough — write top N
        write_momentum_triggers(pass1_results[:needed])
        return

    # Step 3b — Pass 2: relaxed technical thresholds (only if needed)
    still_needed = needed - len(pass1_results)
    print(f"\n📈 Pass 2: relaxed technical thresholds "
          f"(vol surge >= {MOMENTUM_VOLUME_SURGE_MIN}x, "
          f"pivot proximity >= {MOMENTUM_PIVOT_PROXIMITY}) — need {still_needed} more...")

    pass1_tickers = {r["ticker"] for r in pass1_results}
    pass2_results = []
    for ticker in qualified:
        if ticker in pass1_tickers:
            continue  # already found in pass 1
        result = check_technical_breakout_with_thresholds(
            ticker, MOMENTUM_VOLUME_SURGE_MIN, MOMENTUM_PIVOT_PROXIMITY)
        if result:
            print(f"   🔥 Pass 2 breakout: {ticker} — surge {result['volume_surge']}x, "
                  f"pivot dist {result['pivot_distance_pct']:.1f}%")
            pass2_results.append(result)
            if len(pass2_results) >= still_needed:
                break  # got enough

    print(f"Pass 2 found {len(pass2_results)} additional breakout(s).")

    combined = pass1_results + pass2_results
    if combined:
        write_momentum_triggers(combined[:needed])
    else:
        print("😴 No momentum breakouts found in either pass. Cash will remain idle.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        notifier.notify_exception("main() — momentum_screener.py", e)
        raise
