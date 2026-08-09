# Drop stale and dead columns from `portfolio_positions`

- **Date:** 2026-08-09
- **Status:** Accepted

## Problem

`portfolio_positions` had grown to 50 columns, several of which were never read
by any decision path. One of them was actively misleading.

**`stop_loss` (absolute price) was a correctness trap.** It was written once at
insert as `fill_price * (1 - trail%)` and *never updated again* — a grep for any
update path returns nothing. Meanwhile:

- the real stop **ratchets upward** with the high-water mark inside IBKR, and
- `stop_loss_pct` **is** kept current (`execution_agent.py:2540`, when
  `_compute_dynamic_trail_pct()` tightens the trail).

So the absolute price drifted further from reality every day a position rose.
Nothing consumed it for a decision: the only read was `backend/database.py`
serialising it into the API payload, and the dashboard never referenced
`pos.stop_loss` — it already derived the true level as
`hwm_price * (1 - stop_loss_pct)` (`DashboardView.jsx:231, 966`), which matches
IBKR's own calculation.

This was not merely cosmetic. During the session that produced this ADR, the
stored values were reported as the live stops for two open positions — **$78.13
(DXCM) and $156.21 (NBIX) — when the actual IBKR-managed levels were $78.55 and
$157.72.** A field that looks authoritative, is exposed over the API, and is
wrong will keep causing that error.

## Decision

Drop nine columns. Each was verified to have zero decision-logic readers.

| Column | Why it goes |
|---|---|
| `stop_loss` | Stale mirror of a broker-managed value; UI already derives it |
| `tv_exchange`, `ib_exchange`, `currency`, `fmp_ticker` | Written by `tv_api_screener.py` into the **`watchlist`** table only (`tv_api_screener.py:205-230`); never written on `portfolio_positions`, NULL on every row |
| `highest_rs_score` | Write-only (`force_buy.py:214`); never read |
| `hwm_rs_score` | Zero references outside its own migration |
| `analysis_date` | Zero references outside its own migration |
| `oca_group` | Written by `force_buy.py`, but the self-healing path that was meant to consume it queries `ib.openTrades()` directly (`execution_agent.py:2608-2614`); NULL on every row |

### Deliberately kept

`stop_loss_pct` is the **single source of truth for the trail** — it is ratcheted
tighter by `_compute_dynamic_trail_pct()` and re-applied on self-heal. Dropping
it would break exits outright.

Also kept: `hwm_price`/`hwm_date`, `highest_unrealized_pct`, `power_hold`,
`intraday_high_today`, the `exit_armed*` state machine, and
`param_drift`/`analysis_reason`/`analysis_ai_grade` (read by the dashboard and
`execution_agent.py:3011-3012`).

`entry_volume_surge` and `entry_pivot_distance_pct` are write-only today but are
retained on purpose as the entry-conviction audit trail used to tune the
screener retrospectively.

## Implementation

- `migrations/drop_stale_position_columns.sql` — the DROP statements, wrapped in
  a transaction, with a backup command in the header. **Not auto-applied.**
- `execution_agent.py` — stop writing `stop_loss` in the buy path and the
  reconcile path; the reconcile log line now prints the trail % instead of a
  price that would immediately be wrong.
- `force_buy.py` — stop writing `stop_loss`, `highest_rs_score`, `oca_group`.
- `backend/database.py` — stop serialising `stop_loss` into the API payload.
- `tests/conftest.py`, `tests/test_reconcile.py` — fixture no longer fabricates
  the dropped fields; the Case-2 test now asserts `stop_loss` is *absent* rather
  than checking its value.
- `docs/buy_logic.md` — corrected the documented insert payload.

`notify_buy(stop_loss=...)` is unchanged: at fill time HWM equals entry, so the
number in the buy alert is correct. It is a computed argument, not a column read.

## Consequences

**Positive**
- The stop shown in the UI and the stop resting at IBKR can no longer disagree,
  because only one representation now exists.
- Nine fewer columns to reason about; the four `watchlist` strays no longer
  imply `portfolio_positions` tracks exchange/currency metadata.

**Negative / accepted risks**
- The DROP is irreversible. The migration must be run manually after taking the
  snapshot named in its header.
- Any external consumer reading `stop_loss` off the API would need to switch to
  `hwm_price * (1 - stop_loss_pct)`. The bundled dashboard already does.
- Historical entry RS (`highest_rs_score`) is discarded; it was never read, and
  `entry_rs_score` remains as the entry-time baseline.

## Verification

- `python3 -m pytest tests/ -q` → **245 passed**.
- Residual-reference grep for every dropped column across `.py`/`.jsx` (excluding
  migrations and `tv_api_screener.py`'s watchlist writes) → clean.
