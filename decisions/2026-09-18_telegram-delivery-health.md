# Telegram delivery health: make a dead alert channel observable

**Date:** 2026-09-18
**Status:** Accepted

## Context

On 2026-09-18 the bot opened five positions (TWLO, INCY, TEN, STNG, DHT) between
09:31 and 10:02 ET and closed a sixth (SMTC, +$140.48) on a broker trailing stop
at 09:54. **Not one of those six events produced a Telegram alert.** The operator
discovered this by noticing an absence, hours later.

Nothing was broken in the trading path. `buy_source=daily_triggers` on all five
positions confirms they ran through `run_market_open_buys`, which calls
`notify_buy`; the signature matches the call exactly, and the buy loop `break`s
on any exception, yet four buys ran back-to-back. So `notify_buy` was called,
returned cleanly, and reached `_send()`.

The 06:00 health check (`scripts/restart_6am.sh` → `docker exec execution-agent
python3 /app/restart_and_health_check.py`) **did** deliver, from inside the very
same container. So at 06:00 that container had a valid token, correct chat IDs
and working connectivity to api.telegram.org. Misconfiguration and a revoked
token are both ruled out as standing conditions.

What made this expensive was not the fault itself but that `_send()` could not
report one:

```python
if not self._is_configured(): return          # silent — no output at all
if r.status_code != 200: print(...)           # stdout only, never raises
except Exception as e:  print(...)            # stdout only, never raises
```

Three distinct failure modes, three different fixes, and no counter, no marker
and no durable record distinguishing any of them from "nothing happened today".
For a live-money bot whose *only* alerting channel is Telegram, the channel had
no health signal of its own.

## Decision

Keep the guarantee that notification failures never raise or affect trading —
that is correct and must not change. Add observability around it instead.

1. **`_send()` returns `bool`** and maintains `consecutive_failures`,
   `sends_attempted`, `sends_delivered`, `last_error`, `last_success_at`.
2. **Every failure path emits `[TELEGRAM-FAIL]` on stderr**, including the
   previously-silent unconfigured case, which now names the specific missing
   variable. One grep answers "is the alert channel alive?":
   `docker logs execution-agent 2>&1 | grep TELEGRAM-FAIL`
3. **Escalation at `DELIVERY_ALARM_AFTER = 3`.** Telegram drops the occasional
   request; three consecutive failures is an outage, and the alarm line states
   the operational consequence — trades are still executing, unannounced.
4. **`verify_delivery()` startup self-test** via `getMe`, run in `main_loop()`
   *before* the IB connect retry loop, followed by a boot message.
5. **Persisted health**: `account_balances.telegram_consecutive_failures` and
   `telegram_last_success`, refreshed every reconcile cycle (~15 min).

### Why stderr, and why a marker string

The agent tees stdout to a log file. A dead alert channel should still surface
in `docker logs` even when stdout is captured elsewhere. The fixed marker makes
it greppable without knowing the message format — the previous strings
("Telegram API Error", "Telegram Network Error") were three different phrasings
for one question.

### Why `getMe` rather than sending a test message

It has no side effects, and it **separates a revoked token (401) from an
unreachable API**. Those need completely different fixes. Not being able to tell
them apart is what made this incident slow to diagnose — and the token is
committed to a public repository, which makes revocation a live hypothesis that
must be cheap to confirm or dismiss.

### Why partial delivery counts as failure

There are two configured recipients. If one is silently dropping alerts, that is
a real fault; rounding it up to success would hide exactly the kind of
half-broken state that is hardest to notice.

### Why a startup self-test cannot be fatal

A bot that trades without alerts is bad. A bot that refuses to guard open
positions is worse. The self-test logs loudly and continues.

### Why the persisted columns are advisory, not critical

Registered in `schema_guard.ADVISORY_COLUMNS`. A missing column degrades
operator visibility, never a risk rule, so it must not block buys. They are
written as a **separate best-effort update**, never folded into the balance
upsert — the same pattern as `hard_stop_price`, so a lagging migration cannot
fail the balance sync that the dashboard and exit sizing depend on.

## Consequences

- A dead alert channel is now visible three ways: greppable logs, an escalating
  alarm, and a queryable column that does not require container access.
- The outage is *detectable*, not *prevented*. Nothing here restores delivery;
  it removes the blindness. An independent watchdog (an external service
  checking `telegram_last_success` staleness) would be the next step if this
  recurs.
- `_send()` returning `bool` is a behaviour change for the five call sites that
  invoke it directly (`arm_exit`, smart-OCA placement, trail ratchet, hard-stop
  ratchet, scale-out). None inspect the return value, so all are unaffected.
- Two existing tests asserting the old stdout strings were re-pointed rather
  than deleted; they encoded the real intent ("must not be swallowed") and now
  assert the stronger contract.
- 14 new tests. The core regression — unconfigured send being silent — was
  verified non-vacuous by restoring the bare `return` and confirming failure.

## What this does NOT establish

**The root cause of the 2026-09-18 outage is still unknown.** This change makes
the *next* occurrence diagnosable; it does not explain this one. The outstanding
diagnostic is `docker logs execution-agent 2>&1 | grep -iE "telegram (api
error|network error|timeout)"`, which will name the HTTP code or network error.
Until that is read, treat the cause as open.

That diagnostic requires access to the production host, which is exactly what
was unavailable when the outage was noticed. `decisions/2026-09-18_supabase-log-shipping.md`
closes that second gap by shipping `[TELEGRAM-FAIL]` lines (among others) to
Supabase, so a future alert-channel failure is readable without reaching the
host. It does not help retroactively here.

## Audit performed alongside

Every lifecycle event was checked for notification coverage. **No gaps were
found** — buys, buy failures, regime transitions (DB-latched via
`maybe_notify_sell_state`), Prove-It arming, exit arming, OCA placement, trail
and hard-stop ratchets, scale-outs, bot-initiated sells (`notify_sell`) and
broker-side closes (`notify_manual_close`) are all covered. Five sites call the
private `_send()` instead of a named method and therefore omit the timestamp
footer; that is a consistency issue, not a coverage gap, and is left alone.

## See also

- `docs/configuration.md` — `TELEGRAM_*` variables and the health columns
- `migrations/20260918_add_telegram_health.sql`
- `tests/test_telegram_notifier.py` — the regression suite
