# Configuration Reference

All properties are set via environment variables (`.env` file or Docker environment). No defaults are hardcoded in ways that would silently produce wrong behavior — missing critical variables cause startup failures.

---

## Execution Agent (`execution_agent.py`)

### IBKR Connection

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `IB_GATEWAY_HOST` | `localhost` | string | Hostname of the ib-gateway container |
| `IB_GATEWAY_PORT` | `7497` | int | API port of the ib-gateway (paper: 7497, live: 4004) |

### Position & Risk Management

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `MAX_POSITIONS` | `4` | int | Maximum concurrent open positions |
| `MIN_POSITION_SIZE` | `5000.0` | float | Minimum USD floor per position; buy skipped if cash below this |
| `STOP_LOSS_PCT` | `0.07` | float | Trailing stop percentage — 7% below peak (IBKR-managed) |
| `PLATEAU_DAYS` | `10` | int | Days without a new intraday HWM before plateau rotation eligibility |
| `COOLING_OFF_DAYS` | `3` | int | Days before a stopped-out ticker can be re-bought |

### Buy Trigger Gating

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `TRIGGER_LOOKBACK_DAYS` | `3` | int | Days back to look for valid triggers (covers weekends/holidays) |
| `MAX_PIVOT_EXTENSION` | `0.05` | float | Skip if current price >5% above pivot breakout close |

### Moving Average Exit

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `EXIT_MA_TRIGGER_ENABLED` | `true` | bool | Master enable/disable for MA exit |
| `EXIT_MA_TYPE` | `EMA` | string | `EMA` or `SMA` |
| `EXIT_MA_WINDOW` | `21` | int | Lookback window in trading days |
| `EXIT_MA_BUFFER_PCT` | `0.01` | float | Buffer below MA before exit triggers (1% default) |
| `EXIT_MA_EOD_ONLY` | `true` | bool | Restrict MA check to 3:45–4:00 PM ET only |

### Market Direction Filter (CANSLIM "M")

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | bool | Enable SPY SMA200 bear market gate |
| `MARKET_DIRECTION_TICKER` | `SPY` | string | Ticker used to gauge market direction |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | int | SMA window (O'Neil standard: 200-day) |

### Credentials & APIs

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase service role key |
| `FMP_API_KEY` | Yes | Financial Modeling Prep API key (live prices, MA history) |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token for notifications |
| `TELEGRAM_CHAT_IDS` | Yes | Comma-separated list of Telegram chat IDs |

---

## Technical Screener (`technical_screener.py`)

Runs as a separate daily GitHub Actions job. Scans the watchlist for CANSLIM volume breakout conditions and writes results to `daily_triggers`.

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `SMA_WINDOW` | `50` | int | SMA window for above-MA filter |
| `VOLUME_AVG_WINDOW` | `50` | int | Rolling average window for volume surge calculation |
| `VOLUME_SURGE_MIN` | `1.40` | float | Minimum volume surge ratio to qualify (1.4x = 40% above avg) |
| `ROLLING_HIGH_WINDOW` | `252` | int | Trading days for 52-week high calculation |
| `PIVOT_PROXIMITY` | `0.98` | float | Close must be >= `rolling_high x 0.98` (within 2% of high) |
| `MIN_PRICE_HISTORY` | `50` | int | Minimum trading days of history required to process a ticker |
| `FMP_HISTORY_DAYS` | `380` | int | Calendar days of price history requested from FMP |
| `TRIGGER_PRUNE_DAYS` | `56` | int | Days before old trigger records are pruned |
| `FMP_API_KEY` | required | string | FMP API key |
| `SUPABASE_URL` | required | string | Supabase project URL |
| `SUPABASE_KEY` | required | string | Supabase service role key |
| `TELEGRAM_BOT_TOKEN` | optional | string | Telegram notifications |
| `TELEGRAM_CHAT_IDS` | optional | string | Comma-separated chat IDs |

---

## Supabase Schema

### `portfolio_positions` table

Represents currently open positions managed by the bot.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid | Primary key |
| `ticker` | text | Stock symbol (e.g. `AAPL`) |
| `shares` | integer | Number of shares held |
| `buy_price` | numeric | Average fill price at purchase |
| `buy_date` | timestamptz | Timestamp of purchase (set at insert) |
| `buy_reason` | text | Human-readable buy rationale (e.g. "Vol Surge 2.1x") |
| `buy_source` | text | Trigger source: `daily_triggers` or `momentum_triggers` |
| `stop_loss` | numeric | Reference stop price at time of buy (`fill x 0.93`); IBKR manages the live trail |
| `hwm_date` | date | **Date of last observed intraday price high.** Plateau clock: if `today - hwm_date >= PLATEAU_DAYS`, position is eligible for EOD rotation |
| `oca_group` | text | IBKR OCA group name for the trailing stop order; used by self-healing to avoid double-placing |

> [!NOTE]
> **Removed columns (no longer written or read):**
> `high_water_mark` (price), `profit_target`, `is_power_hold`, `power_hold_expiry`.
> These are safe to drop from the schema (see `migrations/add_hwm_date.sql`).

### `trade_history` table

Archived closed positions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid | Primary key |
| `ticker` | text | Stock symbol |
| `shares` | integer | Number of shares sold |
| `buy_price` | numeric | Entry fill price |
| `buy_date` | text | ISO date of purchase |
| `buy_reason` | text | Buy rationale |
| `sell_price` | numeric | Exit fill price |
| `sell_reason` | text | Why it was sold (e.g. "Plateau Rotation — no new HWM in 12 days") |
| `sell_date` | text | ISO date of sale — used for cooling-off period checks |
| `profit_loss` | numeric | `(sell_price - buy_price) x shares` |
| `percent_return` | numeric | `(sell_price / buy_price - 1) x 100` |

### `daily_triggers` table

Output of the technical screener. Refreshed daily (full replace).

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | text | Stock symbol |
| `close_price` | numeric | Closing price at breakout date (used as pivot price) |
| `volume_surge` | numeric | Volume as multiple of 50-day average |
| `sma_50` | numeric | 50-day SMA at time of breakout |
| `rolling_high_52w` | numeric | 52-week rolling high price |
| `pivot_distance_pct` | numeric | % distance of close from 52-week high (negative = below) |
| `triggered_at` | date | Date the breakout was detected |
| `ai_rating` | numeric | AI-generated quality score (used to sort buy priority) |
| `retention_period` | text | How long to keep this trigger (e.g. `1d`, `2d`) |

### `watchlist` table

Input to the technical screener. Populated by the fundamental screener (separate process).

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | text | Stock symbol |
| `created_at` | timestamptz | When this watchlist entry was added |
| (other fundamental columns) | varies | See `fundamental_screener.md` |

---

## Migrations

| File | Purpose |
|------|---------|
| `migrations/add_hwm_date.sql` | Adds `hwm_date DATE` column to `portfolio_positions`; backfills existing positions to `CURRENT_DATE`; comments out Phase 2 drops (run separately after verifying stability) |

---

## `force_buy.py` Properties

Manual buy script (run directly in the execution-agent container). Inherits most config from `.env` but reads a subset:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | Position cap |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum position size |
| `STOP_LOSS_PCT` | `0.07` | Trailing stop percentage |
| `COOLING_OFF_DAYS` | `3` | Cooling-off period |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Trigger lookback |
| `MAX_PIVOT_EXTENSION` | `0.05` | Pivot extension gate |
| `IB_GATEWAY_HOST` | `ib-gateway` | Gateway hostname |
| `IB_GATEWAY_PORT` | `4004` | Gateway port |
