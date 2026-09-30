# Recorded intraday research

The **Backtester** page contains **Recorded intraday research**, separate from
the older daily-bar backtest. It compares the recorded rules with a variant
that removes only the AI D-grade veto. Scores, ranking, other entry gates,
position limits, exits and costs remain in place.

Both strategies begin with the **actual recorded account**, not a user-entered
cash reset: existing positions and their state, prior sales, re-entry
restrictions, cash/equity and broker protection. This does not manufacture
history from before recording began.

## Deploying

1. Apply `migrations/20260930_add_intraday_research.sql` in the Supabase SQL
   Editor. It is safe to re-run; its final query reports each object.
2. Add `INTRADAY_SUPABASE_KEY` to this application's Bitwarden project with a
   server-side Supabase service-role key. The new tables deliberately refuse
   anonymous/publishable-key access. Do not put the value in source, a patch or
   the browser. `.env.template` resolves it through `@bws`; deploy fails rather
   than inventing a missing secret.
3. Configure the same name as a GitHub Actions secret for the weekly backup
   job, which also archives private saved comparisons.
4. Apply the delivered patch and deploy the web and execution images through
   the existing pipeline. The recorder spool lives at
   `/app/logs/intraday_capture.sqlite3`, inside the execution agent's existing
   persistent logs mount. Do not place it on an ephemeral container filesystem.
5. Open **Backtester → Recorded intraday research**. Confirm a recent capture,
   recording health and recorded sessions. Missing migration, permissions,
   price coverage or starting protection is an error, not an empty account.

The migration is **not applied automatically** by deploying the code. Existing
open positions continue to be managed independently of research recording.

## Using the dashboard

Select recorded start/end sessions and run the comparison. No starting-capital
override is offered. The server saves the job, returns its identifier and runs
it in the background; the page polls its status. Closing the page does not
cancel server work. A web restart marks interrupted jobs failed rather than
leaving them appearing successful.

Read the starting-account summary and limitations before interpreting the
comparison. A positive **variant minus baseline** difference means removing
the veto increased final modelled account value in that window. A negative
difference means it reduced it. Account value includes unsold positions, and
costs include commission and adverse execution-price assumptions.

Rejected runs retain their reason. Do not narrow or alter a dataset just to
hide a recording gap or an unsupported trade. The JSON export preserves a
replayable dataset only when the required inputs pass validation.

The first interface tests one change, not every possible strategy. Neither
manual nor automatic jobs can write live parameters or place orders.

## What is recorded

| Data | Purpose |
|---|---|
| Effective configuration and build revision | Prevent a result from silently combining different rules |
| Buy/monitor/EOD inputs and phase observations | Preserve what the bot could know, including uncomputed/failed gates |
| Unfiltered candidate membership and source-labelled prices | Include vetoed/capacity-skipped names and possible alternative holdings |
| Actual positions, prior trades/fills and protective orders | Initialize both variants from the same real account |
| Decisions, fills and protection changes | Audit actual activity without forcing simulated trades to follow it |
| Errors, pending/dropped records and heartbeat | Make incomplete recording visible |

A background worker handles database/network work and retries outside live
decision-making. It never uses the live IB connection from another thread.
Additional candidate sampling uses FMP and retains its source and observation
time; FMP is not renamed “IBKR.” Existing live mark/quote lookups are recorded
without making another blocking broker request just for research.

Producer errors update in-memory health without logging on the trading thread.
Graceful shutdown drains accepted events into the bounded disk spool and counts
capacity failures as dropped records. Pending starting-snapshot enrichment
resumes after restart with the original configuration and observation times.
Forced termination can still lose events that have not reached the spool.

Prices are samples, not a continuous tick history. Five-minute sampling can
miss intervening stop touches. Unknown broker trailing protection cannot be
reconstructed from a position's highest price; an unresolved initial order
state rejects the comparison. Rotation/manual activity and other unsupported
paths also reject it rather than being silently omitted.

Initial protection must use supported GTC (good-until-cancelled) orders and
supported OCA (one-cancels-all) grouping. DAY orders, which expire at the end of
the trading session, are rejected rather than incorrectly carried into tomorrow.
Every recorded monitor cycle needs its preceding buy attempt, including an
explicit outcome when an early gate prevents further evaluation. An opening buy
check alone does not establish complete intraday coverage.

Missing quotes for unrelated retained symbols and identified technical gaps
outside regular trading hours are disclosed as coverage warnings, not treated
as missing trading decisions. Required holding/candidate quotes, regular-session
gaps and unexplained sequence gaps remain blocking. Unsupported financial
activity still rejects the comparison even if it occurred overnight.

## Automatic comparisons and data window

With `INTRADAY_AUTO_COMPARE=true`, the web service checks hourly and saves at
most one automatic attempt per week over up to 30 calendar days ending in the
preceding week. Insufficient data produces a saved rejection, not a strategy
recommendation. Manual requests support up to 93 calendar days by default, with
50,000-row and 64-MiB input limits to protect the web worker. Evaluate longer
retained history as multiple windows; **do not sum their dollar differences as
one continuous portfolio backtest** because each has its own recorded start.

Retain 365 days of raw observations initially. Use the first 4–8 weeks to check
coverage and decision reproduction, three months for exploratory comparisons,
and six to twelve months with varied market conditions for broader evaluation.
About 100 completed positions is a useful planning target, not proof of
statistical reliability. Partial exits do not count as separate positions, and
many observations from one trading date do not constitute many independent
market conditions.

Keep evaluation data that was not used to select parameters. Examine losses
and maximum account decline as well as profit, and whether one or two winners
explain the difference. There is no automated “profitable enough” promotion.
Every live change requires human approval.

## Storage and credentials

`INTRADAY_SAMPLE_SECONDS=300` and `INTRADAY_MAX_SYMBOLS=250` can produce roughly
4.9 million individual quote observations in 252 full trading sessions at the
cap, before accounting for snapshots and audit records. These are observations
inside event payloads, not necessarily individual database rows. Measure actual
database/disk growth; do not assume a free Supabase tier can hold a year.

The collector maintains a rolling raw-data horizon through
`purge_intraday_capture`. Candidate membership follows that horizon so an
expired live trigger can still be valued in an alternative portfolio.
Collector spool limits and unavailable prices remain visible failures.

Raw events are not copied into the indefinitely retained weekly full backup;
saved comparison results are. Export important input datasets before their
retention expires. A result's fingerprint identifies its input but cannot
recover deleted observations.

See [configuration](configuration.md), [backups](backups.md), and
`decisions/2026-09-30_intraday-capture-and-approved-research.md` for why.
