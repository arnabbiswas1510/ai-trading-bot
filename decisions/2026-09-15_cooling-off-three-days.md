# Cooling-off is 3 days — measured, not inherited

**Date:** 2026-09-15
**Status:** Accepted — provisional, revisit at ~30 re-entries
**Supersedes in part:** [`2026-08-09_stop-loss-cooloff-single-source.md`](2026-08-09_stop-loss-cooloff-single-source.md)

## Context

`COOLING_OFF_DAYS` blocks re-entry into a ticker for N days after it was sold
(buy gate 2, `execution_agent.py` ~3625; mirrored in `force_buy.py` and
`rotate_positions.py`).

Three things forced this decision:

1. **The shipped default and the live value disagreed.** `config.py`,
   `.env.template`, `docs/buy_logic.md` and `docs/configuration.md` all said
   **7**. Production's `.env` has held **3** since before the 2026-08-09
   centralisation. Every measurement of live performance is therefore a
   measurement of 3, while a clean deployment would have inherited 7.

2. **The value 7 was never measured.** The 2026-08-09 ADR adopted it because it
   was the value `execution_agent.py` already carried, justified by the assertion
   that "re-buying a name two days after it stopped out repeatedly re-entered the
   same failing setup". No trade data was cited.

3. **A concrete counterexample landed.** NTRA was sold 2026-09-10 and re-bought
   2026-09-14 — a 4-day gap — for **+$575.44**. At 7 days the cutoff is 09-07 and
   that buy is blocked; at 3 days the cutoff is 09-11 and it is allowed.

The question raised was whether cooling-off could be **retired entirely**.

## Evidence

### 7 days is measurably wrong

Across all closed trades, the bot has made **9 genuine re-entries** (the TRV
gap-0 pair is excluded: both rows share buy timestamp `2026-07-21T13:30:44` —
one order split across two rows, the known "TRV incident", not a re-entry).

A 7-day rule blocks **7** of the 9:

| Ticker | Gap | Net |
|---|---|---|
| LPG | 4d | **+$882.13** |
| NTRA | 4d | **+$575.44** |
| ECO | 7d | **+$421.83** |
| LPG | 6d | +$155.85 |
| DXCM | 6d | −$36.38 |
| MPC | 5d | −$109.42 |
| PSX | 5d | −$152.78 |
| | | **+$1,736.67** |

4 winners / 3 losers, and the winners are roughly 5× the losers. The premise
behind 7 — that fast re-entries re-enter a failing setup — is not visible in the
realised data.

### 0 days is worse

The 3-day gate has logged **28** `COOLING_OFF` skips in `trigger_decisions`; 26
have forward bars. Replayed against FMP EOD data, the naive read favours
retirement: mean **+2.17%** at 5 days, 20/26 positive, mean max gain +4.90%.

**That gain is unreachable, because the bot does not hold to +5d.** Testing each
blocked re-entry against the bot's own Prove-It Phase 1 band (1% on day 0):

> **16 of 26 (62%) break the day-0 band on the very first session.**

A name sold within 3 days is typically still falling; re-buying it at the 3-day
mark catches a knife, pays spread and commission, and stops out same-day. Mean
max drawdown across the set is −3.12%.

Retiring the gate also re-enables same-session churn. NTRA on 2026-08-31 was sold
at 10:26 and re-bought at **10:32** — six minutes — producing a −$162.84 round
trip and the contaminated `averageCost` basis that
[`2026-09-10_lot-basis-and-broker-aware-cooling-off.md`](2026-09-10_lot-basis-and-broker-aware-cooling-off.md)
was written to fix. The cooling-off gate is the only rule standing between the
bot and that behaviour.

### Confound, stated plainly

`SLOTS_FULL` accounts for **115** of 214 logged skips against `COOLING_OFF`'s
**28**. Capacity, not cooling-off, is the binding constraint on most days — many
blocked re-entries could not have been bought regardless. **+$1,736.67 is an
upper bound on what 7 costs, not realised foregone profit.**

## Decision

Ship **`COOLING_OFF_DAYS = 3`** as the default in `config.py` and
`.env.template`, matching what production has been running and what every live
measurement describes.

Do **not** retire the gate. The evidence that 7 is too wide is not evidence that
0 is correct; the day-0 band test says the opposite.

Production behaviour is unchanged by this commit — the `.env` override already
held 3. What changes is that a fresh deployment, a rebuilt container, or a
cleared `.env` now inherits the measured value instead of the asserted one.

## Consequences

- `force_buy.py` and `rotate_positions.py` inherit 3 through `config.py`; a
  manual rotation now re-enters on the same schedule as the agent.
- `research/rank_policy_bt.py` mirrors production constants by design; its
  hardcoded `COOLING_OFF_DAYS` moves 7 → 3. **Backtest figures published from
  that harness before 2026-09-15 assumed 7 and are not comparable to runs after
  it.**
- `tests/test_max_positions_config.py::test_defaults_match_the_adrs` now asserts
  `3` and cites this ADR.
- Nothing is deleted, so there is no `docs/retired_code.md` entry. The constant,
  the gate and both fill-source lookups all survive unchanged.

## Open questions

1. **The sample is 9 re-entries over roughly one month, entirely inside a BULL
   regime.** Every trading day in the window passes the market-direction gate.
   Whether 3 holds in a corrective tape is untested.
2. **Is 3 optimal, or merely better than 7 and 0?** 2, 4 and 5 were not swept —
   there are not enough re-entries to discriminate between adjacent values.
3. **The `SLOTS_FULL` confound is not modelled.** A proper answer needs the
   counterfactual run through the slot allocator, which the current harness
   cannot do.
4. **The FMP counterfactual assumes entry at the next session's open** and
   simulates only the Phase 1 band, not the full trail/Prove-It exit path.

Registered in `decisions/provisional_decisions.json` for re-measurement at ~30
genuine re-entries.
