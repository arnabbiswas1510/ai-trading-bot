# Tech Debt & Functional Requirements Tracker

This file is a persistent cross-session tracker for:
- Functional requirements the bot should satisfy
- Known deviations/gaps between desired behavior and current implementation
- Prioritized technical debt items

## How to use this tracker

1. Add/update requirements in **Functional Requirements**.
2. Log observed mismatches in **Requirement Gaps**.
3. Track implementation cleanup in **Tech Debt Backlog**.
4. Update status as work progresses: `Proposed`, `In Progress`, `Blocked`, `Done`.

---

## Functional Requirements

| ID | Requirement | Source | Priority | Status | Notes |
|---|---|---|---|---|---|
| FR-001 | Daily screener supports a two-lane CAN SLIM filter: (A) institutional leaders and (B) high-beta small caps. | User requirement (2026-08-20) | High | Proposed | Should be implemented as explicit lane logic with clear thresholds and ordering. |
| FR-002 | Lane A (institutional leaders): market cap > $1B, price > $15, avg 30d volume > 500k, quarterly EPS YoY > 25%, quarterly revenue YoY > 20%, ROE > 17%, within 5% of 52w high, 50 SMA > 200 SMA, price > 50 SMA, relative volume > 1.5. | User requirement (2026-08-20) | High | Proposed | Include deterministic sort priority. |
| FR-003 | Lane B (high-beta small caps): market cap $300M-$2B, beta > 1.3, price $5-$50, avg 30d volume > 300k, quarterly EPS YoY > 30%, quarterly revenue YoY > 25%, ROE > 15%, within 5% of 52w high, 20 SMA > 50 SMA, price > 50 SMA, relative volume > 2.0. | User requirement (2026-08-20) | High | Proposed | Include deterministic sort priority. |
| FR-004 | Combined screener sorts by relative volume desc, then quarterly EPS growth desc. | User requirement (2026-08-20) | Medium | Proposed | Confirm fallback tiebreakers for stable ordering. |

---

## Requirement Gaps (Current Bot vs Required Filter)

| Gap ID | Area | Required | Current | Impact | Priority | Status |
|---|---|---|---|---|---|---|
| GAP-001 | Filter shape | Single OR-combined two-lane filter | Two-stage pipeline (fundamental watchlist + technical triggers) | Strategy mismatch vs requested behavior | High | Open |
| GAP-002 | Market cap lanes | Lane A > $1B; Lane B $300M-$2B | Single floor > $300M | Over/under-inclusion across both lanes | High | Open |
| GAP-003 | Price lanes | Lane A > $15; Lane B $5-$50 | Single floor > $15 | Misses intended small-cap lane scope | High | Open |
| GAP-004 | Beta gate | Lane B beta > 1.3 | No beta filter | No high-beta isolation | High | Open |
| GAP-005 | EPS metric | Quarterly EPS **YoY** thresholds (25/30) | Quarterly EPS **QoQ** threshold (default 20) | Different growth signal semantics | High | Open |
| GAP-006 | Revenue threshold | YoY 20/25 by lane | TTM revenue growth default 15 | Looser and structurally different gate | High | Open |
| GAP-007 | ROE gate | Lane-specific hard ROE cutoffs | ROE collected but not gated | Quality filter not enforced | High | Open |
| GAP-008 | Trend structure | Lane A: 50>200 + price>50; Lane B: 20>50 + price>50 | Mainly price>50 in technical checks | Missing stage/trend confirmation | High | Open |
| GAP-009 | Relative volume lanes | >1.5 (A), >2.0 (B) | Breakout >=1.5; pre-breakout allows volume contraction | Small-cap lane not represented; additional non-requested mode | Medium | Open |
| GAP-010 | Sorting | Relative volume desc, then quarterly EPS desc | Fundamental sorted by market cap; technical is scan order | Different ranking outcomes | Medium | Open |
| GAP-011 | Universe type | US stocks | Includes preferred/DR/some fund types | Universe broadened beyond requirement | Medium | Open |

---

## Tech Debt Backlog

| Debt ID | Debt Item | Why it matters | Suggested Resolution | Priority | Status |
|---|---|---|---|---|---|
| TD-001 | Screener criteria split across separate modules with mixed semantics (`tv_api_screener.py` vs `technical_screener.py`) | Hard to reason about exact screening contract and lane-specific behavior | Introduce a canonical screening spec with lane-aware gates and shared validation helpers | High | Open |
| TD-002 | Hard-coded thresholds mixed with env-tunable thresholds | Inconsistent operability and change safety | Centralize thresholds in one config schema with explicit per-lane parameters | High | Open |
| TD-003 | Metric naming ambiguity (e.g., quarterly EPS QoQ vs requested YoY) | Easy to misconfigure and misinterpret signals | Normalize metric naming and add assertions/tests for field semantics | High | Open |
| TD-004 | Output ranking logic differs by stage | Non-deterministic candidate prioritization vs requirement | Implement unified ranking strategy at final candidate assembly | Medium | Open |

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-08-20 | Created tracker with initial functional requirements, gap analysis, and tech debt backlog. | Copilot CLI |
| 2026-08-20 | Winner-side HWM profit lock shipped (arm at +6%, 1.5% give-back). See `decisions/2026-08-20_hwm-profit-lock-first-leg.md`. | Copilot CLI |
| 2026-08-22 | Winner-side HWM profit lock retuned to +5% arm (1.5% give-back unchanged) after 5-minute replay sensitivity analysis. See `decisions/2026-08-22_hwm-profit-lock-arm-5pct.md`. | Copilot CLI |
| 2026-08-20 | Loser-side early-exit tightened: kill-switch 2%→1%, window days 0–1 → entry day only. See `decisions/2026-08-20_early-loss-day0-tightening.md`. | Copilot CLI |
| 2026-08-20 | Dashboard **Position Journey** panel added — current phase, prior events, and next scheduled/price-triggered step per position. | Copilot CLI |
| 2026-08-21 | Weekly Supabase backup added — full snapshots to Parquet on the prod server, queryable with DuckDB. Closes the single point of failure on `trade_history`, which every review below replays. See `decisions/2026-08-21_weekly-supabase-backup.md` and `docs/backups.md`. | Copilot CLI |
| 2026-08-22 | Closed-loop learning wired end to end: `breakout_learnings` rows are now written on the reconcile path (not just `execute_sell()`), and `ai_evaluator.py` computes a deterministic trade-history penalty into `adjusted_score`. See `decisions/2026-08-22_closed-loop-learning-from-trade-history.md`. | Copilot CLI |
| 2026-08-22 | CANSLIM "M" gate rewritten: SPY+QQQ, 1% buffer above SMA-200, non-falling SMA-200 on at least one index, uniformly fail-closed. Removes three fail-**open** paths that authorised buying on any FMP error. First real unit tests for `is_market_bullish()`. See `decisions/2026-08-22_market-direction-gate-spy-qqq.md`. | Copilot CLI |
| 2026-08-22 | iOS sidebar/nav touch fix — the left "CAN Slim Bot" tab could freeze on iOS Safari. Non-behavioural UI fix. | Copilot CLI |

---

## Follow-ups Raised by Recent Changes

| ID | Item | Why it matters | Trigger to revisit | Status |
|---|---|---|---|---|
| FU-001 | Re-run the winner-side HWM give-back grid search | The shipped threshold has moved to +5% / 1.5% on a 17-trade sample; still needs confirmation on a larger sample | Once ~30 closed trades exist | Open |
| FU-002 | Re-run the loser-side day-0 replay | The day-0-only window rests largely on one winner (CPAY) for its measured day-1 damage | Once ~30 closed trades exist | Open |
| FU-003 | Day 1 is now covered only by the Early Dollar Stop | Removing the percentage kill-switch from day 1 leaves a wider gap before the Thesis Stop opens on day 2 | If day-1 losers begin exiting materially worse | Open |
| FU-004 | **Early Dollar Stop sizing** | Shipped as a flat $500, which the full-stack replay showed was net harmful: all-in net −$272 versus +$3,027 at ~$1,500 or with the rule removed. Every loser it caught was already caught a day earlier by the day-0 kill-switch, leaving only winner damage (CPAY −$1,873, DXCM −$1,367). **Resolved 2026-08-20** by making the cap slot-derived — `(equity / EFFECTIVE_POSITION_SLOTS) × 0.06` ≈ $1,500 — rather than removing the rule, since the peak-anchored base trail cannot cover a position that never rose. **Still open:** whether 6% is right. The sample cannot distinguish it from any larger value. | First scheduled review, 2026-09-20 | Partially resolved |
| FU-005 | Thesis Stop never fired in the 17-trade replay | `THESIS_STOP_ATR_MULT=1.0` over days 2–5 is effectively untested against real trade history | Once enough trades reach day 2+ underwater | Open |
| FU-006 | Only ever compare whole stacks, never single rules | The 2026-08-20 single-rule figures overstated the kill-switch because they ignored the dollar stop running alongside it. `headline_configs()` now carries explicit full-stack rows; every future review must quote those, not the isolation rows | Every scheduled review | Done — rows added 2026-08-20 |
| FU-007 | **`EFFECTIVE_POSITION_SLOTS` must become `MAX_POSITIONS`** | `MAX_POSITIONS=5` is set but inert: the open positions were each sized as a quarter of capital, so no cash remains for a fifth slot and sizing will not converge on 5 until the portfolio is liquidated and rebuilt. `EFFECTIVE_POSITION_SLOTS=4` keeps the Early Dollar Stop's slot arithmetic honest meanwhile. Leaving it hard-coded after the reset would make the stop 20% looser than intended. Remove it from `execution_agent.py`, `.env.template` and `frontend/src/lib/positionRules.js`. | When the portfolio is reset at 5 slots | Open |
| FU-008 | Re-derive the winner-side HWM give-back grid on the fixed harness | The harness now has a 5-minute replay sensitivity run that favoured +5% over +6%, but the sample is still small and needs re-validation on a larger closed-trade set. | First scheduled review, 2026-09-20 | Partially resolved |
| FU-009 | **Backup job reads on the publishable Supabase key** | `supabase_backup.py` reads every table using `SUPABASE_KEY`, a publishable key that today can read everything because `migrations/enable_rls_all_tables.sql` defines its policies as `FOR ALL USING (true)`. It works, but broad read access to live trading data on a publishable key is wider than a backup job needs. Consider a dedicated read-only role. | Before any wider sharing of the key, or at the next RLS change | Open |
| FU-010 | **Backtester market filter no longer matches live** | `backend/backtester.py` gates on SPY `close > EMA-21`, while the live gate is now SPY+QQQ >1% above SMA-200 with a slope test and fail-closed semantics. Backtests therefore model a materially more permissive market filter than the bot applies, inflating trade counts. `decisions/2026-07-23_backtester-accuracy-rewrite.md` carries an erratum. | Before quoting any backtest trade count as representative of live | Open |
| FU-011 | **M-gate parameters tuned on index data, not this bot's trades** | The 1% buffer, 20-session slope and SPY+QQQ pair come from a 4,940-session index grid. The trade-history replay could not discriminate at all: all 21 closed trades sit inside one six-week window where every candidate config returns BULL. The parameters are therefore unvalidated against the strategy they gate. Also note the measured mean-return edge is negative outside 2008 — the rule is justified as drawdown insurance only. | Once the closed-trade sample spans more than one market regime; fold into the scheduled reviews | Open |
| FU-012 | **Two independent "M" implementations remain** | `execution_agent.is_market_bullish()` (SPY/QQQ, authoritative) and `backend/screener.get_market_direction()` (^GSPC/^IXIC, dashboard) compute market direction separately. As of 2026-08-22 the dashboard also returns an `execution_gate` field using the agent's rule and env vars, so it can no longer *contradict* the agent, but the descriptive `status`/`score` still derive from a different rule on different symbols. Consider collapsing to one. | If the two are ever observed disagreeing in a way that confuses operations | Open |
| FU-010 | **Restore from backup has never been rehearsed** | The archive is verified on write and on arrival, but no restore drill has been run, so the recovery path is documented rather than proven. A backup that has never been restored is an assumption, not a guarantee. Rehearse into a scratch Supabase project and confirm row counts and types survive the round trip. | Before relying on it in an actual incident | Open |

---

## Scheduled Reviews

Exit parameters are re-validated against real trade history on a fixed schedule —
see the **Exit-Parameter Review** section of `AGENTS.md` for dates, the command
(`research/exit_rule_replay.py`) and what each review must answer.

| Due | Status |
|---|---|
| 2026-09-20 | ☐ not run |
| 2026-10-20 | ☐ not run |
| 2026-11-20 | ☐ not run |
| 2026-12-20 | ☐ not run |
| 2027-01-20 | ☐ not run |
| 2027-02-20 | ☐ not run |
