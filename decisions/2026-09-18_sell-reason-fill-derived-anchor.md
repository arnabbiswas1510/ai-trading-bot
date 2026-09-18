# Sell reasons derive the stop anchor from the fill, not the stored peak

- **Date:** 2026-09-18
- **Status:** Accepted
- **Area:** `_exit_context_suffix()` and `insert_trade_history()` in
  `execution_agent.py`; `extractReasonFacts()` / `unrecordedFields()` in
  `frontend/src/lib/exitDetails.js`; `trade_history.sell_reason` column width
- **Scope:** **Diagnostics only. No trading rule changes behaviour.**

## Context

On 2026-09-18 four positions — SMTC, TEN, DHT and TWLO — were closed on day 0 by
a Phase 1 Prove-It stop that had been placed at IBKR as a ratcheting `TRAIL`
order instead of the static floor every document describes. The stop climbed
with the intraday high and fired at or above the entry price, converting a loss
cap into a profit-taker. That defect and its fix are
`decisions/2026-09-18_phase1-static-backstop.md`.

This ADR is about why it took weeks to notice.

Each of those exits wrote a `sell_reason` that looked entirely healthy:

```
Trailing stop (IBKR GTC TRAIL order) — trail 1.72%, HWM $180.99 set 2026-09-18,
implied trigger $177.88, day 0 of hold, peak +0.27%
```

SMTC entered at $180.50. A trigger of **$177.88 is below entry** — exactly what a
functioning −2% loss cap should read like. The order had in fact fired at
**$181.88, +0.76% above entry**. Every number in the string was computed
correctly and the conclusion it invited was the opposite of the truth.

The cause is a timing mismatch that no amount of care in the arithmetic can fix:

- `highest_price` on `portfolio_positions` is refreshed on the **15-minute
  monitor cycle**.
- The resting IBKR order updates its anchor **continuously**.

A position that runs up and turns over between two cycles is closed against a
peak the agent never observed. SMTC's real high was **$185.30**; the agent had
recorded **$180.99**. `HWM × (1 − trail)` is then not an approximation of the
trigger — it is a number describing a peak that never existed.

`decisions/2026-09-18_phase1-static-backstop.md` (line 152) recorded this as
"misleads any human reading `sell_reason` — tracked separately". This is that
separate tracking, closed.

## Decision

**1. Reconstruct the anchor from the fill.**

A trailing stop fills *at its own trigger*. The fill price is the one value
known exactly, so the anchor the broker was actually using is recoverable:

```
anchor = fill ÷ (1 − trail)
```

No extra API call, no re-fetch of historical bars. Checked against all four real
exits it lands within cents:

| | entry | stored HWM | reconstructed | real high |
|---|---|---|---|---|
| SMTC | 180.50 | 180.99 | **185.06** | 185.30 |
| TEN | 52.25 | 53.18 | **53.54** | 53.55 |
| TWLO | 241.38 | 243.80 | **246.31** | 246.31 |

When the reconstruction exceeds the stored peak by more than 0.1%, the stored
peak is known to be stale. The agent then **suppresses `implied trigger`** —
the figure that caused the misreading — and writes what it can defend:

```
stored HWM STALE (cycle-delayed) — fill implies peak $185.06,
actual trigger $181.88
```

**2. Always record where the stop sat relative to entry.**

```
stop sat at entry +0.76%
```

This is the single most diagnostic number on a stopped-out trade, and it needs
no reconstruction at all — both prices are stored. A value at or above zero
means whatever fired was **taking profit**, whichever rule believed it was
capping a loss. Had this field existed, the ratcheting defect would have been
obvious on the first trade rather than the tenth.

**3. Correct the recorded peak when the fill proves it low.**

`peak +0.27% recorded but >=+2.53% implied by fill` — the stored excursion is
kept (it is what the agent saw, and other analysis depends on it) but is no
longer allowed to stand as the whole story.

**4. Apply the inference only to trailing-order fills.**

`_exit_context_suffix()` also serves manual closes, which fill at a price with
no relationship to the trail. Reconstructing an anchor from one of those would
manufacture a "stale HWM" claim out of an unrelated number. The inference is
therefore gated on `broker_trail_fill=True`, which only the trailing-stop caller
passes. Manual closes keep the plain `implied trigger`.

**5. Widen the reason columns and make the insert overflow-proof.**

The richer string does not fit. `sell_reason` was `varchar(200)`; live rows
already reached **190**, and `migrations/20260915_fix_nbix_reconstructed_sell.sql`
records one at **exactly 200**. The worst case now is 311.

This is not a cosmetic limit. Every caller **deletes the position from
`portfolio_positions` before inserting the history row**, and that delete has
already committed. Postgres raises on a `varchar` overflow rather than
truncating, so an over-long reason would abort the insert *after* the position
was destroyed — **losing the trade record entirely**. A diagnostic string must
never be able to lose a trade.

Two changes, deliberately belt-and-braces:

- `migrations/20260918_widen_sell_reason.sql` converts `sell_reason` and
  `buy_reason` to `text` (binary-coercible, no table rewrite, no long lock).
- `insert_trade_history()` retries once with the reason truncated at a comma
  boundary if the database rejects the row, and re-raises anything that is not a
  length problem. **Deployment ordering therefore cannot cause data loss** — the
  agent is correct whether or not the migration has been applied.

## Consequences

- Exits closed between two monitor cycles are now self-explaining. The diagnosis
  that previously required re-fetching 5-minute bars is in the log line.
- `frontend/src/lib/exitDetails.js` surfaces `High-water mark status`,
  `Peak implied by fill`, `Stop trigger (from fill)` and `Stop vs entry`. A
  fill-derived trigger satisfies the trigger-recorded check in
  `unrecordedFields()` — it is a *better* record than the HWM-derived one, so a
  correctly diagnosed exit must not be reported as undocumented.
- Historical `sell_reason` strings keep parsing unchanged; the new facts are
  appended, and the comma-separated per-field regex parser tolerates that.
- **No trading behaviour changes.** Every threshold, stop and gate is untouched.

## What this does not fix

The stale high-water mark itself. The agent still observes the market every 15
minutes and will still miss intraday peaks; this change makes the *record*
honest about it rather than making the observation better. Closing that gap
means either a shorter cycle or reading the broker's own trailing anchor back
from IBKR, both of which are larger changes with live-connection cost.

## Verification

- `tests/test_exit_context.py` — 16 new tests pin all four real 2026-09-18
  reconstructions, the entry-relative stop, the manual-close exemption, and that
  a *fresh* HWM is unaffected (so normal exits do not gain a false warning).
- `tests/test_trade_history_insert.py` — 11 new tests, including a fake client
  that raises on overflow exactly as Postgres does, proving the trade record
  survives a narrow column.
- `frontend/scripts/test-exit-details.mjs` — 4 new cases pinning the format from
  the parser side.
- Both suites were verified non-vacuous by reverting each change and confirming
  the corresponding tests fail.
- Full suite: 866 passed.
