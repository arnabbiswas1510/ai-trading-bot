# Trade lifecycle — how winners and losers are treated

> These diagrams are [Mermaid](https://mermaid.js.org/). They render as pictures
> on GitHub, in VS Code (with a Mermaid extension) and in any Mermaid-aware
> Markdown previewer. They will **not** render in a plain terminal or in some IDE
> chat panes — open this file on GitHub to see the flowcharts.

Every value below is read from the live Python, not prose:
`config.py`, `exit_rules.py`, `execution_agent.py`, `cooling_off.py`. When those
change, update this page (see the Doc Sync Rule in `AGENTS.md`).

## Shipped thresholds at a glance

| Rule | Constant | Value |
|---|---|---|
| Concurrent position slots | `MAX_POSITIONS` | 5 |
| Cooling-off window (loss exits) | `COOLING_OFF_DAYS` | 3 days |
| Phase 1 band — day 0 | `PROVE_IT_P1_DAY0_PCT` | entry − 1.0% |
| Phase 1 band — day 1+ | `PROVE_IT_P1_LATER_PCT` | entry − 3.0% |
| Phase 2 arm (give-back floor engages) | `PROVE_IT_P2_ARM_GAIN_PCT` | peak ≥ +2.0% |
| Phase 2 give-back floor | `PROVE_IT_P2_FLOOR_PCT` | entry − 1.0% |
| Partial scale-out trigger / fraction | `SCALE_OUT_TRIGGER_PCT` / `SCALE_OUT_FRACTION` | +4.0% peak / sell 33% |
| Profit-lock trail | `TRAIL_PROFIT_TIERS` | peak ≥ +5% → 1.5% trail from HWM |
| Power-hold gain / trigger / duration | `POWER_HOLD_GAIN_PCT` / `_TRIGGER_DAYS` / `_DURATION_DAYS` | +10% by day 21 → hold 56 days |
| Base trailing stop | `STOP_LOSS_PCT` | 10% |
| Disaster floor (last-resort) | `MAX_LOSS_PCT` | 7% |
| Armed-exit trail / deadline | `ARMED_EXIT_TRAIL_PCT` / `_DEADLINE_HOURS` | 0.6% / 3.25h |

The single question that splits every loss-cutting decision is **"has this
position ever _closed_ above the price we paid?"** — unproven positions get the
tight Phase-1 band; proven positions get the −1% give-back floor and patience.
See `decisions/2026-09-04_prove-it-stop.md`.

---

## 1. Master lifecycle — entry to exit

```mermaid
flowchart TD
    A["Daily scan writes<br/>daily_triggers (Supabase)"] --> B{"Portfolio full?<br/>held ≥ MAX_POSITIONS = 5"}
    B -- yes --> BX["Skip: no free slot"]
    B -- no --> C{"Cooling-off block?<br/>(reason-aware)"}
    C -- "(A) sold TODAY (any P&L)" --> CX["Block re-entry"]
    C -- "(B) LOSS exit within 3d" --> CX
    C -- "profit exit >1d ago / clean" --> D{"Buy gates pass?<br/>breakout + market direction<br/>+ score + drift tol"}
    D -- no --> DX["Skip"]
    D -- yes --> E["Size = available_cash / remaining_slots<br/>capped at equity / 5"]
    E --> F["BUY at market"]
    F --> G["Place OCA protective bracket at broker:<br/>• TRAIL leg (profit-lock)<br/>• static STP leg @ hard_stop_price()"]
    G --> H["Position OPEN<br/>closed_above_entry = false (unproven)"]
    H --> I(["Enter 15-min monitor loop<br/>(see Diagram 2)"])
    I --> J{"Exit fires"}
    J --> K["execute_sell() → trade_history<br/>→ starts cooling-off clock"]

    classDef block fill:#ffd6d6,stroke:#c0392b,color:#000;
    classDef go fill:#d6f5d6,stroke:#27ae60,color:#000;
    class BX,CX,DX block;
    class F,G,H go;
```

---

## 2. The exit ladder — evaluated in THIS order, every 15 minutes

Orange = a loser being held inside its band. Red = a sell being triggered.
Green = a winner being protected or left to run.

```mermaid
flowchart TD
    S(["Monitor cycle: one position"]) --> O{"Smart-OCA<br/>managed exit active?"}
    O -- yes --> OC["Suspend automated rules<br/>(OCA legs govern) → next position"]
    O -- no --> AD{"Already exit_armed<br/>AND ≥ 3.25h ago?"}
    AD -- yes --> ADS["Armed-exit deadline:<br/>force execute_sell() now"]:::sell
    AD -- no --> HW["Update HWM + peak gain %"]
    HW --> PH{"POWER-HOLD?<br/>peak ≥ +10% by day 21"}
    PH -- yes --> PHY["Hold up to 56 days.<br/>Suppress discretionary exits,<br/>WIDEN trail. → skip to ratchet"]:::win
    PH -- no --> PROVEN{"Proven?<br/>ever CLOSED above entry"}

    PROVEN -- "NO — unproven (LOSER path)" --> P1{"price ≤ Phase-1 band?<br/>day0 = entry−1%<br/>day1+ = entry−3%"}
    P1 -- yes --> ARM["arm_exit(): tight 0.6% IBKR trail<br/>+ 3.25h deadline → cuts the loser"]:::sell
    P1 -- no --> HOLD1["Hold (still inside band).<br/>Broker STP rests AT the band<br/>→ IBKR fills on a gap-open"]:::loss

    PROVEN -- "YES — proven (WINNER path)" --> P2{"peak ≥ +2%?<br/>(give-back floor armed?)"}
    P2 -- "no (proven, unarmed)" --> HOLD2["Hold on base trailing stop only"]:::win
    P2 -- yes --> P2F{"price ≤ floor?<br/>floor = entry −1%<br/>'green never becomes a loss'"}
    P2F -- yes --> ARM
    P2F -- no --> SC{"First time peak ≥ +4%?"}
    SC -- yes --> SCY["SCALE-OUT: sell 33% now,<br/>let 67% ride (once)"]:::win
    SC -- no --> TR{"peak ≥ +5%?"}
    TR -- yes --> TRY["Tighten trail to 1.5% from HWM<br/>→ re-price broker OCA bracket"]:::win
    TR -- no --> RATCHET["Hard-stop RATCHET:<br/>re-price resting STP to hard_stop_price()<br/>(ratchet-up-only once proven)"]
    SCY --> RATCHET
    TRY --> RATCHET
    PHY --> RATCHET
    HOLD2 --> RATCHET
    RATCHET --> HEAL["Self-heal: ensure BOTH broker legs exist"]
    HEAL --> RR{"Day 7+ AND a stronger<br/>trigger exists (stale-discounted)?"}
    RR -- yes --> RRY["RANK & REPLACE: swap<br/>stalled holding for the leader"]:::sell
    RR -- no --> NEXT(["→ next position"])

    classDef sell fill:#ffd6d6,stroke:#c0392b,color:#000;
    classDef loss fill:#ffe9d6,stroke:#e67e22,color:#000;
    classDef win fill:#d6f5d6,stroke:#27ae60,color:#000;
```

---

## 3. Why sells rest on the broker, not in Python — the arm/OCA sequence

The bot never relies on its own 15-minute poll to be the only thing standing
between a position and a loss. An OCA (one-cancels-other) pair rests at IBKR from
the moment of entry, so a gap-down or an outage is still caught while the agent is
dark. This is what closed the ECO/TNK overnight-gap hole on 2026-09-22 — see
`decisions/2026-09-26_phase1-broker-primary-stop.md`.

```mermaid
sequenceDiagram
    participant M as Monitor (15-min poll)
    participant B as IBKR (broker, always-on)
    participant DB as Supabase

    Note over B: At entry, an OCA pair is ALWAYS resting:<br/>TRAIL leg + static STP @ hard_stop_price()
    Note over B: Phase-1 STP rests AT the band →<br/>catches overnight gaps (ECO/TNK fix)

    M->>M: Prove-It level touched (loser or give-back)
    M->>B: arm_exit(): cancel legs,<br/>place tight 0.6% TRAIL
    Note over B: rides any bounce up, sells on reversal
    alt Trail fills before deadline
        B-->>M: filled → best price on the bounce
    else 3.25h deadline passes
        M->>B: force market sell
    end
    M->>DB: execute_sell() writes trade_history
    DB->>DB: starts reason-aware cooling-off
```

---

## The four scenarios, in one line each

- **Loser (never proves):** capped tight — cut at **−1% day 0**, **−3% day 1+**;
  the resting broker STP at the band catches gaps even while the bot is dark.
- **Winner that fades back:** the **−1% give-back floor** once proven ("a green
  trade never becomes a loss"), plus **33% booked at +4%** that a later fade
  cannot erase.
- **Winner that runs:** **+5% → 1.5% trail** from the peak; **+10% by day 21 →
  power-hold for 8 weeks**, discretionary exits off and the trail widened to let
  it run.
- **Winner that stalls:** **Rank & Replace** swaps the dead-money slot for a
  stronger breakout from day 7 onward.

## Related pages

- `docs/buy_logic.md` — entry gates, trigger ranking, slot allocation, cooling-off
- `docs/sell_logic.md` — the full exit-rule specification with rationale
- `docs/technical_triggers.md` — how a breakout is detected and scored
- `docs/configuration.md` — every constant above as an env var
