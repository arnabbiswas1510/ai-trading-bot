# Decision: Scope the volume surge gate to confirmed breakouts only

## Problem

`MIN_VOL_SURGE_GATE` (added 2026-08-18 in response to the FROG/APH post-mortem)
was applied to every trigger type in `run_market_open_buys()`:

```python
trigger_vol_surge = float(trigger.get("volume_surge") or 0)
if trigger_vol_surge < MIN_VOL_SURGE_GATE:      # 0.75
    continue
```

That is wrong, because `daily_triggers.volume_surge` is an **overloaded column
carrying two different metrics with opposite polarity**:

| Trigger type | What `volume_surge` holds | Screener gate | Good direction |
|---|---|---|---|
| `BREAKOUT` | today's volume / 50-day avg | `>= VOLUME_SURGE_MIN` (1.50) | **higher** |
| `PRE_BREAKOUT` | 3-day avg volume / 50-day avg (**contraction**) | `< PRE_BREAKOUT_VOL_MAX` (1.00) | **lower** |
| `PRE_BREAKOUT_RELAXED` | same contraction ratio | `< 1.10` | **lower** |

`technical_screener.py:417` stores the contraction ratio in the same field:

```python
"volume_surge": float(round(vol_contraction_ratio, 2)),  # contraction ratio stored here
```

Volume drying up while a stock coils beneath its pivot is the *constructive*
CAN SLIM / VCP setup — supply exhausting before the move. Applying a **minimum**
to that number inverts the selection: it rejects the tightest coils and admits
the loosest.

The gate was therefore harmful where it applied and useless where it was aimed:
confirmed breakouts already require `>= 1.50` at the screener, so a 0.75 floor
can never bind on them. **It was dead code for `BREAKOUT` and an inverted filter
for `PRE_BREAKOUT`.**

### Observed damage — 2026-08-19

Every trigger that day was a pre-breakout. Ranked by `final_score`:

| Ticker | Contraction | Final score | Outcome under the buggy gate |
|---|---|---|---|
| FRO | 0.69 | **80** | 🚫 rejected — *tightest coil, highest score* |
| PTGX | 0.66 | 73 | 🚫 rejected |
| RS | 0.66 | 67 | 🚫 rejected |
| **LPG** | **0.77** | **65** | ✅ **bought** — loosest coil, lowest score |

The bot bought the worst of the four candidates *because* of the gate. LPG was
entered at $49.88 (436 sh, $21,747.68) and was ~-0.9% by late morning.

The original FROG/APH analysis that motivated the gate made the same misreading:
both were `PRE_BREAKOUT` rows, so their quoted "0.64x" and "0.86x" volume figures
were **contraction ratios, not failed surges**. APH's 0.64 was a *tight coil*.
The reasoning behind the gate's threshold was therefore built on a
misinterpretation of the column.

## Decision

Apply `MIN_VOL_SURGE_GATE` **only when `trigger_type == "BREAKOUT"`**.

Pre-breakout looseness stays governed by the screener's own contraction gates
(`PRE_BREAKOUT_VOL_MAX`, `RELAXED_PRE_BREAKOUT_VOL_MAX`), which already express
the correct polarity and run at screening time on full daily bars.

The AI prompt had the identical defect and is corrected in the same change:

- `_format_trigger_block()` emitted a bare `VolSurge={volume_surge}x` for every
  row, so the model saw a tight coil as a failed breakout. It now emits
  `SetupType` plus either `VolSurge=…(higher = stronger confirmation)` or
  `VolContraction=…(lower = tighter coil, <1.0 required)`.
- The `MANDATORY QUALITY PENALTIES` block scoped its -25/-10 volume penalties to
  `SetupType=BREAKOUT`, and gained a pre-breakout rule penalising *loose* coils
  (`VolContraction > 0.85x`, -10) instead of tight ones.

## Consequences

- Tight pre-breakout coils are eligible again; the 2026-08-19 ranking would have
  bought FRO (score 80) rather than LPG (65).
- `MIN_VOL_SURGE_GATE` retains its intended meaning for confirmed breakouts,
  where it remains a redundant-but-harmless backstop beneath the screener's 1.50.
- AI ratings on pre-breakouts should rise, since the model is no longer
  penalising the defining feature of the setup. The D-veto floor of 50 is
  unchanged, so the veto path still works.
- **This does not vindicate the original FROG/APH entries.** Those had real
  defects — negative ROE on FROG, a "Sell" analyst consensus and 1.23B float on
  APH — which the other penalty rules address. Volume was simply the wrong
  charge to bring.

## Guard

`tests/test_buy_gates.py::TestVolumeGateRespectsTriggerType` pins both
polarities: confirmed breakouts are still blocked below 0.75x and bought above
it, while coils at 0.40/0.66/0.95 are admitted for both pre-breakout types.
Three of these fail against the pre-fix code.
