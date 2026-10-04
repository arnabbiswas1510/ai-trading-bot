# Complete the calibration input pipeline and freeze exploratory operator limits

Date: 2026-10-04
Status: Accepted; market-session evidence remains required
Scope: Research inputs, CI dependencies and explicit operator research settings

## Context

Restoring observer snapshots alone did not restore hypothetical trading.
`build_seed()` attempted to parse every USD account-value tag as a number,
including textual ledger/settlement fields. The separate shadow quote reader
also continued using the FMP bulk endpoint rejected by this subscription.

The latest reproduced Daily Screener failure, revision `5248208c`, stopped at
pytest collection because FastAPI was absent. This establishes that failure's
cause, not the cause of every historical failed run. API tests now exercise real
FastAPI modules; the old root-only dependency constraint no longer fits.

The operator wants suggestions for higher modeled profit and explicitly accepts
bounded additional risk. No usable proposal existed when these limits were
chosen, so they are preferences fixed before evaluation, not fitted thresholds.

## Decisions

- Parse only the required numeric NetLiquidation account tag, with strict
  account/currency/model and ambiguity checks. Preserve raw account evidence.
- Share `quote_transport.py` between recorders and shadow inputs. Only bulk HTTP
  402 enables individual quotes; retain timestamps/provenance and bounded work.
  The shadow reader rejects partial or invalid frames. A stale Sunday quote is
  not usable Monday evidence and is never relabelled as fresh.
- Install `requirements-test.txt` for the Daily Screener's complete Python 3.10
  test gate. Keep runtime dependency separation; do not skip API coverage.
  Python 3.10 timeout assertions use `asyncio.TimeoutError`; market-gate research
  date bounds use New York time while retaining calendar-date annualization.
- Provision the operator's Bitwarden-managed token only into the private
  production environment. Ordinary deployments preserve it; deliberate
  template regeneration must preserve its valid dotenv quoting and value.
- Save an explicit production cloud override: five training sessions, ten
  future evaluation sessions and sixteen candidates. Require ten completed
  positions in each strategy, ten distinct evaluation sessions, $1,000
  after-cost improvement, no more than +1 percentage point maximum drawdown,
  and no more than $100 additional worst completed-position loss. Require three
  positive ticker contributors and at most 50% of total positive improvement
  from one ticker, before subtracting negative contributions.

The policy was saved as revision 1 on 2026-10-04 before any proposal existed.
Global five/five defaults are unchanged. These choices are exploratory; ten
positions does not establish statistical confidence. No candidate is promoted
automatically, and real buying remains OFF.

## Evidence and limits

An unmodified corrected seed reader succeeded against real production sources
with zero positions/orders and positive account equity. A separate quote probe
used bulk then individual transport and retained the genuine Friday timestamp.
Neither probe persisted a hypothetical run or manufactured market evidence.

A prospective recommendation needs at least fifteen complete sessions plus the
required completed positions. A partial initial session does not count. Missing
data extends the interval, and no qualifying candidate is a valid outcome.
The provisional register schedules a date-driven review of this policy without
requiring real trading to resume.

Backup credentials, private exit logging and the 27-table inventory are covered
by [the companion decision](2026-10-04_backup-vault-and-private-exit-shadow.md).
See `docs/configuration.md`, `docs/interactive_calibration.md`,
`docs/intraday_research.md`, `docs/backups.md` and `README.md` for current behavior.
