# Earnings blackout, dead news feed fix, and AI news-disqualifier veto

- **Date:** 2026-09-28
- **Status:** Accepted
- **Scope:** `ai_evaluator.py` (cloud screener), `sentiment.py` (held-position
  EOD sentiment), `buying.py` (local execution), `config.py`, `trigger_audit.py`,
  `migrations/20260928_add_next_earnings_date_to_daily_triggers.sql`

## Context

Three related problems were found while checking whether the AI evaluator adds
value at entry.

### 1. The AI's news feed had been silently dead

`ai_evaluator.fetch_news_headlines` called FMP's legacy `/api/v3/stock_news`
endpoint. On the current FMP plan that endpoint now returns **HTTP 403
Forbidden** for every ticker (verified live 2026-09-28; `/api/v4/stock_news` and
`/api/v3/earning_calendar` 403 as well). The function caught the failure and
returned `[]`, so the model was handed **"No recent news" for every candidate**
and sentiment defaulted to 50. Stored evidence confirmed the blindness:
`daily_triggers.sentiment_score` was pinned at the default 50 on 5 of 6 recent
rows and every `score_rationale` was 100% technical with zero news references.
The supported replacement, `/stable/news/stock?symbols=`, returns 200 with the
same `title` field.

### 2. The AI was told imminent earnings were a BONUS

The scoring prompt contained:

> Near-term catalyst (earnings, product launch) within 2-3 weeks: boost 10 pts

That is backwards for this bot. A fresh position sits under the Prove-It stop's
tight floor (−1% on day 0, −3% day 1+), so an earnings gap is far more likely to
stop the position out at a loss than to help it. Rewarding proximity to earnings
actively steered capital into the highest-gap-risk names.

### 3. There was no hard guard against a broken story

Even with news restored, the model was only softly instructed to "reduce rating
accordingly" for bad news. A strong technical setup could override a
dilution/going-concern/SEC-probe headline.

## Decision

**(a) Fix the news endpoint.** `fetch_news_headlines` now calls
`/stable/news/stock?symbols=`. This restores the news the AI was designed to use.
The **same dead endpoint** was independently in use by
`sentiment.fetch_held_position_sentiment`, the EOD sell-side sentiment score for
open positions (it feeds the held-position health score in `indicators.py`); it
had been pinned at the neutral 50 fallback for the same reason and is fixed to the
same `/stable/news/stock` endpoint in this change.

**(b) Flip the earnings framing and add hard news disqualifiers.** The "+10 for
near earnings" line is removed and replaced with an explicit instruction that
imminent earnings are a RISK, not a bonus. A new **MANDATORY NEWS DISQUALIFIERS**
block forces `rating ≤ 45` (→ grade D → the existing AI_VETO gate) when the
headlines clearly report equity dilution/offering, going-concern/default,
SEC/DOJ probe or fraud, a guidance cut, material adverse litigation/regulatory
ruling, or a delisting notice — and requires the event be NAMED in the rationale.
Disqualifiers apply only to real, ticker-specific reporting; "No recent news"
yields no adjustment.

**(c) Add a deterministic earnings blackout at buy time.** `ai_evaluator.py`
fetches each trigger's next earnings date from FMP `/stable/earnings?symbol=`
(the bulk `/stable/earnings-calendar` ignores the symbol filter) and writes it to
`daily_triggers.next_earnings_date`. `buying.py` reads it and **defers** any buy
whose next report is within `EARNINGS_BLACKOUT_TRADING_DAYS` (default **3**) NYSE
trading days.

Key properties of the blackout:

- **Deferral, not veto.** The breakout re-triggers and can be bought once the
  report clears. It is recorded as `EARNINGS_IMMINENT` in the trigger audit.
- **Fails OPEN.** A missing, unparseable, or past `next_earnings_date` returns
  `None` and allows the buy — a per-name data gap must never take the bot
  offline. (Contrast the market-direction gate, which fails CLOSED because it
  guards a systemic risk.)
- **Deterministic and model-independent.** The date comparison runs in the local
  execution path, so the protection holds even if the AI or its news feed breaks
  again — which is exactly how #1 went unnoticed.
- **Trading-day distance**, via `trading_days_between`, so a weekend/holiday does
  not make an imminent report look further away than it is.

Why 3 trading days: it covers the report plus the settle, without idling capital
for a week. It is a first guess on essentially no in-sample earnings-adjacent
losses, so it is registered as provisional (`earnings-blackout-window`).

## Consequences

- The AI once again sees real headlines and can act on them; the hard
  disqualifiers give a broken story a floor the technicals cannot override.
- Capital is no longer opened into a near-certain earnings gap under a tight
  stop; those breakouts are picked up after the event instead.
- One extra FMP call per rated ticker in the screener (small set, cloud-side).
  No new API failure mode in the live execution path — that reads a stored date.

## Alternatives considered

- **Veto instead of defer.** Rejected: the setup is often still valid after the
  report; a permanent veto would throw away good post-earnings continuation.
- **Let the AI handle earnings avoidance via the prompt.** Rejected as the sole
  mechanism: #1 proves a model-only guard fails silently. The prompt change and
  the deterministic gate are complementary — the gate is the guarantee.

## Retired

The "+10 pts for near-term earnings" prompt rule is removed; logged in
`docs/retired_code.md`.
