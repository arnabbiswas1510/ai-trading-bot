# Fall back to a labelled FMP quote on the dashboard, not to cost basis

- **Date:** 2026-09-04
- **Status:** Accepted
- **Supersedes in part:** `decisions/2026-09-03_ibkr-sourced-position-values.md`
  (the cost-basis fallback and the "name only" restriction on the FMP call)

## Context

`decisions/2026-09-03_ibkr-sourced-position-values.md` removed FMP from
dashboard position values entirely. Position value became IBKR's own
`marketPrice` / `marketValue` / `unrealizedPNL`, persisted onto
`portfolio_positions` by `reconcile_with_ibkr()`. When no broker mark existed,
the dashboard fell back to **cost basis**. The FMP call survived only to resolve
a company name; its price field was explicitly discarded.

That was the right call for the problem it addressed — a live third-party quote
was being multiplied by a Supabase share count and added to an IBKR cash balance,
producing a single total built from two different vintages of data, with nothing
on screen saying so. Latency between that displayed price and the price orders
actually filled at had caused real confusion.

But the fallback was wrong, and it took a live incident to see it. On 2026-09-04
the migration was applied after the close, so no reconcile cycle had run yet and
every column was `NULL`. The dashboard reported:

- **Invested Portfolio Value** = $91,788 — exactly the cost basis
- **Unrealized P&L** = **$0.00**

The book was in fact up **$3,019 (+3.3%)**, with all five positions green.

`$0.00` is not a missing value. It reads as a *fact* — a flat book — and it is
produced with the same visual confidence as a real number. That is the same class
of defect the original decision set out to eliminate: a number whose provenance
is invisible. Replacing a misleading price with a misleading zero is not progress.

## Decision

Price dashboard positions **IBKR first, FMP second, cost basis last**, and label
every one of them.

`resolve_position_price()` in `backend/pricing.py` returns
`(display_price, price_source)` where `price_source` is `IBKR`, `FMP` or
`COST_BASIS`. The UI renders the source under every price:

| Source | Label | Colour |
|---|---|---|
| `IBKR` | `IBKR 15:47` | muted |
| `FMP` | `FMP estimate — not broker` | amber |
| `COST_BASIS` | `Cost basis — no quote` | amber |

The Invested Portfolio Value card carries a warning naming the affected tickers,
and the Unrealized P&L card is subtitled `Estimated — N positions priced from
FMP, not IBKR` (or `Understated — … priced at cost basis`).

This mirrors `get_position_price()` in `execution_agent.py`, which has priced
exits IBKR-first-with-FMP-fallback since `decisions/2026-09-04_ibkr-first-live-pricing.md`.
The dashboard was the odd one out.

**FMP is never written into the `portfolio_positions` columns.** The fallback is
applied at render time only. The column comments in
`migrations/add_ibkr_position_values.sql` — *"Never write an FMP price here"* —
remain correct and unchanged.

## Why labelling is what makes this acceptable

The original decision treated *source mixing* as the defect. It is more precise
to say the defect was **unlabelled** source mixing. A user who can see that a
number came from FMP can discount it appropriately; a user looking at an
unmarked number cannot. Two sources are only dangerous when indistinguishable.

The rule that survives intact: no price may be displayed without naming where it
came from. `test_every_source_is_named` in `tests/test_dashboard_pricing.py`
pins it.

## Consequences

**Positive**

- The dashboard shows a real number during the window before the first sync,
  and after any reconcile gap, instead of a confident $0.00.
- Provenance is visible per row rather than inferred from a whole-card warning.
- The dashboard and the execution agent now use the same precedence, so their
  numbers diverge for one reason only — sync recency — rather than two.

**Negative / accepted**

- The FMP quote and the IBKR cash balance still have different vintages when the
  fallback is active. This is now disclosed rather than hidden, which is the
  difference that matters, but the total is still an estimate while any position
  is amber.
- One FMP quote per position per dashboard load. That request was already being
  made for the company name, so there is no new request cost.
- A stale broker mark still outranks a fresher FMP quote. This is deliberate:
  IBKR is what orders fill against, and preferring recency is what caused the
  original fill-vs-decision mismatches.

## Alternatives rejected

- **Leave it at cost basis and wait for the next reconcile.** Correct in the
  narrow sense — the gap closes by itself — but it leaves `$0.00` on screen
  presented as fact, which is the failure mode being fixed.
- **FMP estimate excluded from all totals.** Considered, and it preserves the
  one-source rule for headline numbers. Rejected because an Invested Portfolio
  Value that silently omits positions is a worse lie than one that includes them
  and says which are estimated.
- **Back-fill the columns from FMP.** Rejected outright. It would make a
  never-synced position indistinguishable from a broker-marked one at the data
  layer, permanently, and contradicts the column comments.

## Files

- `backend/pricing.py` — `resolve_position_price()`. A dependency-free module by
  design: `backend/main.py` imports FastAPI at module scope, which CI does not
  install, so a test importing `main` aborts collection. See
  `decisions/2026-09-05_ci-import-hygiene.md`.
- `backend/main.py` — endpoint wired to it.
- `frontend/src/components/DashboardView.jsx` — three-way row label,
  `estimatedPositions` / `costBasisPositions`, card warning and subtitle.
- `frontend/scripts/verify-build.mjs` — fingerprints for the new labels.
- `tests/test_dashboard_pricing.py` — 10 tests over the precedence and labelling.
- `docs/sell_logic.md`, `docs/configuration.md`, `AGENTS.md` — updated.
