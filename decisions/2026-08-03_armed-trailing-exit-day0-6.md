# Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals

## Problem

For the first week of a position (Day 0-6), two independent signals could
trigger an immediate market sell:

- **Early Loss Kill-switch (Day 0-1):** hard stop if price drops
  `EARLY_LOSS_STOP_PCT` below entry.
- **Intraday Loss Minimiser (Day 2-6):** sell on a pullback from today's
  intraday high, provided that high was near/above entry.

Both called `execute_sell()` the instant the condition was met. Because the
agent only polls every 15 minutes, the price observed at trigger time is
frequently a local trough rather than the best available exit — the position
would sell at (or near) the worst recent print instead of capturing any
rebound that might occur seconds or minutes later.

Simply holding longer to "wait for a better price" was rejected: an
unbounded wait increases exposure to further downside on the very positions
already flagged as underperforming, which is the opposite of what these
signals are meant to prevent.

## Decision

Introduce an **Armed Trailing Exit**: when either Day 0-6 signal fires, the
agent no longer sells at market immediately. Instead it "arms" the exit via
`arm_exit()`:

1. Cancels any existing sell order and places a tight IBKR native GTC
   trailing stop (`ARMED_EXIT_TRAIL_PCT = 0.6%`) on the position. IBKR tracks
   this tick-by-tick (not limited to the 15-minute poll cadence), so it rides
   any bounce upward and fires on the first meaningful reversal.
2. Records `exit_armed`, `exit_armed_at`, `exit_armed_reason`, and
   `exit_armed_price` on the `portfolio_positions` row.
3. On every subsequent monitoring cycle, `monitor_portfolio_intraday()`
   checks the elapsed time since arming against a hard deadline
   (`ARMED_EXIT_DEADLINE_HOURS = 3.25`, ~half a trading day). If the trail
   hasn't already closed the position by then, a market sell is forced
   immediately — the position is never held past this bound waiting for a
   better exit.
4. If the tight trail fires on its own before the deadline, the existing
   `reconcile_with_ibkr()` Case 1 logic (position gone from IBKR, still in
   Supabase) detects it and archives to `trade_history` as usual — no new
   reconciliation path was needed.

This applies only to the two Day 0-6 triggers. Day 7+ mechanisms (EMA-21
exit, Rank & Replace, the Intraday Minimiser's FAIL-verdict hard fallback)
are unchanged and continue to sell immediately at market, since they operate
under a different risk profile once the breakout-consolidation protection
period ends.

## Why these specific numbers

- **0.6% trail:** CAN SLIM breakout names commonly show 0.3-0.6% of normal
  intraday noise/spread. A trail tighter than ~0.5% would frequently fire on
  pure noise immediately after arming, defeating the purpose. 0.6% is tight
  enough to meaningfully cap giveback relative to the normal 7% position
  stop, while tolerant enough of ordinary chop to actually let a rebound
  register before reversing.
- **Half-day deadline:** long enough to give a genuine intraday bounce room
  to develop, short enough that a stalled/no-bounce position is force-closed
  well before end of day rather than carried into a new session.

## Files changed

- `execution_agent.py`: added `ARMED_EXIT_TRAIL_PCT`, `ARMED_EXIT_DEADLINE_HOURS`
  constants; added `arm_exit()`; wired Early Loss Kill-switch and Intraday
  Loss Minimiser (Day <7 branch only) to call `arm_exit()` instead of
  `execute_sell()`; added the Armed Exit deadline check in
  `monitor_portfolio_intraday()`.
- `migrations/add_armed_exit_columns.sql`: new `exit_armed`, `exit_armed_at`,
  `exit_armed_reason`, `exit_armed_price` columns on `portfolio_positions`.
- `tests/test_breakout_verdict.py`: updated Day 0-6 trigger tests to assert
  `arm_exit()` is called instead of `execute_sell()`; added
  `TestArmedExitDeadline` covering forced-sell-after-deadline and
  left-open-before-deadline behavior.
