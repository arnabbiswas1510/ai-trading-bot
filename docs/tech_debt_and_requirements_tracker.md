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
| 2026-08-20 | Loser-side early-exit tightened: kill-switch 2%→1%, window days 0–1 → entry day only. See `decisions/2026-08-20_early-loss-day0-tightening.md`. | Copilot CLI |
| 2026-08-20 | Dashboard **Position Journey** panel added — current phase, prior events, and next scheduled/price-triggered step per position. | Copilot CLI |

---

## Follow-ups Raised by Recent Changes

| ID | Item | Why it matters | Trigger to revisit | Status |
|---|---|---|---|---|
| FU-001 | Re-run the winner-side HWM give-back grid search | The +6% / 1.5% lock was selected on 9 winners; the tighter +3% arm scored higher but regressed one trade | Once ~30 closed trades exist | Open |
| FU-002 | Re-run the loser-side day-0 replay | The day-0-only window rests largely on one winner (CPAY) for its measured day-1 damage | Once ~30 closed trades exist | Open |
| FU-003 | Day 1 is now covered only by the Early Dollar Stop | Removing the percentage kill-switch from day 1 leaves a wider gap before the Thesis Stop opens on day 2 | If day-1 losers begin exiting materially worse | Open |
