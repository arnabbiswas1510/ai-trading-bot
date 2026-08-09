"""
config.py — single source of truth for cross-module trading parameters.

Every value here is read from the environment, so changing behaviour is a
`.env` edit plus a container restart. No code change, no redeploy of logic.

WHY THIS FILE EXISTS
--------------------
MAX_POSITIONS used to be declared independently in four places with two
different defaults. ADR 2026-08-04_power-hold-trail-and-five-slots.md moved the
portfolio from 4 slots to 5, but only execution_agent.py was updated. The
screener kept its own default of 4 and uses that value as the target for its
candidate waterfall (`Target = MAX_POSITIONS so the waterfall always aims to
fill the portfolio`), so it stopped relaxing filters at 4 candidates while the
agent had 5 slots to fill — the fifth slot was structurally starved.

A duplicated constant is not a constant; it is four constants that happen to
agree until someone changes one. Import from here instead of re-reading the
environment locally.

NOTE ON CONTAINER LAYOUT
------------------------
`backend/` is built into its own image (Dockerfile) that does NOT contain this
file, so backend modules cannot import it. They read the same environment
variable with the same default instead — the .env remains the single operational
switch even though the import cannot be shared.
"""
import os

# ── Portfolio capacity ───────────────────────────────────────────────────────
# Concurrent stock positions. 5 per ADR 2026-08-04: the CAGR/drawdown gaps
# versus 4 slots are inside the noise floor, but the reduction in outlier
# dependence is not (top-10 trades fall from 109% -> 92% of total P/L on the
# growth universe, 98% -> 74% on the broad one). At 4 slots on growth, removing
# the ten best trades makes the strategy lose money outright.
#
# To change slot count: set MAX_POSITIONS in .env and restart. Nothing else.
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 5))

# ── Risk ─────────────────────────────────────────────────────────────────────
# Base trailing stop, measured from the position's PEAK, not from entry. Widened
# 7% -> 10% on 2026-08-04: 7% was inside the normal daily range of the higher-ATR
# names the screener surfaces, so it was stopping out working positions on noise.
# The live per-position stop is max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT,
# 2.5 x entry ATR%)), so this acts as the floor of that band.
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 0.10))

# Days a ticker is ineligible for re-entry after being sold. Widened 3 -> 7:
# re-buying a name two days after it stopped out repeatedly re-entered the same
# failing setup.
COOLING_OFF_DAYS = int(os.getenv("COOLING_OFF_DAYS", 7))
