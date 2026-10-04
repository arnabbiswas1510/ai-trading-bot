# Once-daily "unfilled slots" operator summary

- **Date:** 2026-09-28
- **Status:** Accepted; backup exclusion superseded by [2026-10-04](2026-10-04_backup-vault-and-private-exit-shadow.md)

> **2026-10-04:** The daily-notification behavior remains in force. The backup
> exclusion recorded below is no longer current: the operator explicitly
> includes `daily_notifications` in the required 27-table weekly inventory
> alongside shadow and research reporting evidence.

## Context

The book routinely runs below its five-slot capacity — often only one or two
positions are open. That is frequently *correct* (a bearish market gate, no
qualifying breakouts, cooling-off), but from the operator's phone it is
indistinguishable from the bot being wedged. The only way to tell *why* slots
sat empty was to SSH into the production host and read the agent log, which is
exactly the friction the Supabase logging effort was meant to remove.

Every per-trigger skip is already recorded in `trigger_decisions` with a stable
`reason_code` (`SCORE_FLOOR`, `AI_VETO`, `EXTENDED_ABOVE_PIVOT`, …). But the
*top-level* stand-downs that block the whole cycle before any trigger is
evaluated — market bearish, margin loan active, schema degraded, no triggers
produced — write no per-trigger row at all, and those are the single most likely
reasons a mostly-empty book stays empty. Nothing surfaced any of this proactively.

## Decision

`run_market_open_buys()` now sends **one** Telegram summary per **ET day**
whenever the portfolio has at least one idle slot, explaining why the idle slots
were not filled. It is a no-op when the book is full.

The reason it reports is:

- the **single top-level cause** when the cycle stood down early (market
  bearish, margin loan, schema degraded, or no triggers produced); or
- an **aggregated per-reason breakdown** of the day's `trigger_decisions` when
  the market was open and candidates were evaluated but none cleared the gates
  (e.g. "2 below the quality-score floor (XYZ, QRS); 1 extended too far above
  the pivot (TUV)").

### Once-per-day, restart-safe dedup

The buy check runs every 15 minutes (~26×/day), so the summary must fire at most
once daily and must keep that promise across container restarts. An in-memory
flag would resend after every deploy or crash-loop. Dedup is therefore persisted
in a new generic `daily_notifications` table keyed by `(report_type,
report_date)` — one row means "already sent today". The probe **fails safe**: if
the table is missing or the query errors, the summary is *suppressed*, never
spammed. The day is only marked done when Telegram actually accepted the message
(`_send()` returned true), so a transient delivery failure retries next cycle.

> **Addendum 2026-09-29 — latch on ANY delivery, not ALL.** The line above
> ("`_send()` returned true") was a latent bug. `_send()` returns true only when
> **every** configured `TELEGRAM_CHAT_IDS` recipient is delivered to; with a
> second, misconfigured recipient that always fails, `_send()` returned false on
> every cycle even though the operator's working chat received the summary. The
> day therefore never latched and the summary re-fired every 15 minutes to the
> working chat — observed live on 2026-09-29 (the `daily_notifications` latch row
> for the day was absent while the message kept arriving). The dedup now latches
> when the message reached **at least one** recipient
> (`notify_unfilled_slots` returns `any_delivered` via the new
> `TelegramNotifier._send_multi`). A **total** failure (no recipient) still
> returns false and retries next cycle, preserving transient-failure recovery.
> The partial-delivery fault is not hidden — it is still recorded by
> `_record_failure` and surfaced through the delivery-alarm path.

`daily_notifications` is deliberately **excluded** from `supabase_backup.py` —
it is regenerable operational state, and coupling it to the weekly backup would
reintroduce the exact PGRST205 failure `exit_shadow_log` caused on 2026-09-27.

## Lifecycle-notification audit (same change)

Prompted by the same request, every trade lifecycle transition was audited for
Telegram coverage. **No gaps were found.** Buy fill, buy failure, each
`sell_state` transition (Unproven→Proven→Floor→Profit-locked), Power-Hold arm,
exit arm, Prove-It stop, Day-3 verdict fail, partial scale-out, full close,
broker-side/manual close, and Rank & Replace swaps all emit a dedicated message;
every exit funnels through `execute_sell`/`execute_scale_out`, both of which
notify. The only intentional silence is the *initial* `sell_state` observation
(the buy was already announced). No code change was required.

## Consequences

- One extra Supabase table (`daily_notifications`) and one migration
  (`20260928_add_daily_notifications.sql`, idempotent).
- One new notifier method (`notify_unfilled_slots`) and one reporter
  (`buying.maybe_report_unfilled_slots`) called at every stand-down `return` and
  once at end-of-cycle.
- The message reports the **first** stand-down reason of the day. If the market
  is bearish at the open and turns bullish later, only the bearish message goes
  out that day. This is deliberate: the request was explicitly "once each day,
  don't spam me", and the first stand-down is representative of why the day
  started empty.
- No new environment variable. The feature is always on; it only fires when
  slots are actually idle.

See `docs/buy_logic.md` and `docs/configuration.md`.
