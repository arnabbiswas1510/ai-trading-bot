# Fail loudly on Telegram when the agent cannot reach IBKR

**Date:** 2026-09-07
**Status:** Accepted

## Context

On 2026-09-07 the dashboard showed every open position priced from FMP with the
label "FMP estimate — not broker." Investigation on the prod box (`192.168.1.2`)
found the real fault: **IB Gateway was stuck in a login loop all session**
(`connection error No Internet connection`, TOTP entered, `breaking out of
maintenance cycle because login is needed again`, repeat). The API port 4000 was
listening and accepted TCP, but a manual IB API handshake got no response — an
unauthenticated gateway answers the socket and nothing else. The execution agent
therefore never connected (25+ consecutive `TimeoutError`s), so it never wrote
IBKR marks onto `portfolio_positions`. `resolve_position_price()` requires both a
price and a sync timestamp to attribute a mark to IBKR, so every row fell through
to the FMP fallback. The FMP label was a *symptom*; the disease was that the
whole risk loop — trailing stops, Prove-It Stop, EMA-21 / plateau exits, 15-min
monitoring, market-open buys — was offline for a full session on five open
positions.

The alerting code did fire. The absent "autoheal watching" log lines at attempts
6, 12, 18, 24 mark exactly where `notify_exception` was called, and two of those
timestamps (2:57pm, 3:57pm ET) matched Telegram messages the operator received.
So this was **not** a delivery failure.

It was a *legibility* failure. The message read:

```
⚠️ TRADING BOT EXCEPTION
📍 Location: main_loop() — IB Gateway still unreachable after 6 attempts...
❌ Error:     TimeoutError:
```

The operator described these as *"Telegram timeout errors"* — i.e. read them as a
Telegram/network hiccup, not as "IBKR is down and your stops are not running."
The alert shared the generic `TRADING BOT EXCEPTION` headline used for every
swallowed exception, never stated the operational consequence, and — via the
1-hour `EXCEPTION_COOLDOWN` keyed on context+error-type — arrived at most once an
hour as the same forgettable line. The failure was effectively silent despite
messages being delivered.

This is the same class of bug as
`decisions/2026-09-06_fail-loudly-on-unreachable-database.md`: a real,
dangerous condition rendered as something indistinguishable from routine noise.

## Decision

**A broker disconnection gets its own loud, consequence-stating alert, separate
from generic exceptions.**

1. `telegram_notifier.py` adds `notify_ibkr_disconnected(...)`. It:
   - leads with `🚨 IBKR DISCONNECTED — RISK MANAGEMENT OFFLINE`, which cannot be
     read as a Telegram hiccup;
   - enumerates what is NOT running (trailing/Prove-It stops, EMA-21/plateau
     exits, 15-min monitoring, market-open buys);
   - states how many open positions are unmonitored (`None` → "positions are
     UNMONITORED", never a false "0");
   - flags whether the market is currently open (an open-hours outage means exits
     are actively not firing);
   - uses a **fixed** cache key (`ibkr_disconnected`) so it is never deduped away
     by an unrelated exception, and reminds every `DISCONNECT_REMINDER_SECONDS`
     (30 min) for the duration of the outage instead of the 1-hour generic
     cooldown.
2. `execution_agent.py` routes **both** IBKR-connection failure sites — the
   initial connect loop and the mid-session reconnect failsafe — to this method
   instead of `notify_exception`. The `AUTOHEAL_ALERT_AFTER = 6` (~18 min)
   suppression window is unchanged: autoheal still gets its chance before the
   alert fires. Two cheap, best-effort helpers (`_is_rth_now()`,
   `_count_open_positions()`) supply the market-open flag and position count;
   both fail safe (a Supabase failure yields `None`, not `0`, and never raises).

`notify_exception` is retained for the generic loop-body exception path; only the
two connection-failure sites were re-routed.

## Consequences

- A stuck gateway now produces an alert an operator cannot mistake for noise,
  naming the exact risk (unmonitored positions, offline exits) and repeating
  every 30 minutes until resolved.
- The alert does not depend on IBKR being reachable, and tolerates Supabase also
  being down (position count degrades to "UNMONITORED").
- No behavioural change to trading logic, autoheal timing, or the FMP dashboard
  fallback — this is purely about making an existing failure legible.

## Not addressed here

Why the gateway itself got stuck in the login loop (IBKR-side auth/TOTP or a
gateway-image issue) is an operational incident, not a code decision. The
immediate remedy is `docker restart ib-gateway`; see
`docs/ibkr_totp_setup.md`.
