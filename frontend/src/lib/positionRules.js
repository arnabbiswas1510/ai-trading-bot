/**
 * positionRules.js — the single source of truth for "what is happening to this position".
 *
 * Mirrors the evaluation order and thresholds documented in `docs/sell_logic.md` and
 * implemented in `execution_agent.py::monitor_portfolio_intraday()`. Pure functions, no
 * React, no fetching — so the compact table cell and the expanded panel can never
 * disagree with each other.
 *
 * ⚠️ If a threshold changes in execution_agent.py, change it here too. The constants
 * below are mirrors, not the source of truth.
 */

// ── Mirrored agent defaults ───────────────────────────────────────────────────
export const RULES_CONFIG = {
  STOP_LOSS_PCT: 0.10,            // base trailing stop floor
  ATR_STOP_MAX_PCT: 0.12,         // cap on the ATR-derived stop
  TRAIL_PROFIT_TIERS: [           // (min unrealised gain %, trail %) — tightening only
    [50.0, 0.050],
    [30.0, 0.060],
    [20.0, 0.065],
  ],
  EARLY_LOSS_STOP_PCT: 0.02,      // kill-switch, days 0–1
  EARLY_LOSS_LAST_DAY: 1,
  EARLY_DOLLAR_STOP_AMOUNT: 500,  // hard dollar cap on unrealised loss, days 0–5
  EARLY_DOLLAR_STOP_MAX_DAY: 5,
  THESIS_STOP_ATR_MULT: 1.0,
  THESIS_STOP_START_DAY: 2,
  THESIS_STOP_LAST_DAY: 5,
  THESIS_STOP_ATR_FALLBACK: 3.0,  // used when entry_atr_pct is missing
  EXIT_MA_WINDOW: 21,
  EXIT_MA_BUFFER_PCT: 0.01,
  EXIT_MA_MIN_DAYS: 7,
  STALE_EXIT_DAYS: 10,            // trading days without a new HWM
  STALE_EXIT_MIN_DAYS_HELD: 7,
  RANK_REPLACE_MIN_DAYS: 7,
  RANK_REPLACE_THRESHOLD: 15,     // margin required when the day-3 verdict was PASS
  RANK_REPLACE_FAIL_THRESHOLD: 5, // lower bar once the breakout has already failed
  MAX_POSITIONS: 5,               // Rank & Replace only runs when the book is full
  POWER_HOLD_GAIN_PCT: 20.0,
  POWER_HOLD_TRIGGER_DAYS: 21,    // calendar
  POWER_HOLD_DURATION_DAYS: 56,   // calendar
  POWER_HOLD_TRAIL_PCT: 0.30,
  ARMED_EXIT_TRAIL_PCT: 0.006,
  ARMED_EXIT_DEADLINE_HOURS: 3.25,
  BREAKOUT_VERDICT_DAY: 3,
};

// ── States ────────────────────────────────────────────────────────────────────
// Ordered by urgency — position in STATE_ORDER doubles as the "most urgent" sort key.
export const STATE = {
  ARMED:      'ARMED',       // an exit is live at the broker right now
  TRIGGERED:  'TRIGGERED',   // condition is met; the agent will act this cycle
  DEGRADED:   'DEGRADED',    // the rule cannot do its job — missing data or disarmed
  WATCH:      'WATCH',       // within striking distance of the trigger
  ACTIVE:     'ACTIVE',      // live and protecting, comfortably clear
  PENDING:    'PENDING',     // window has not opened yet
  SUPPRESSED: 'SUPPRESSED',  // deliberately switched off for this position
  EXPIRED:    'EXPIRED',     // window has closed
  OFF:        'OFF',         // disabled by configuration
};

const STATE_ORDER = [
  STATE.ARMED, STATE.TRIGGERED, STATE.DEGRADED, STATE.WATCH,
  STATE.ACTIVE, STATE.PENDING, STATE.SUPPRESSED, STATE.EXPIRED, STATE.OFF,
];

export const STATE_META = {
  [STATE.ARMED]:      { color: '#f43f5e', label: 'ARMED',      icon: '🔴', meaning: 'A sell order is live at the broker right now.' },
  [STATE.TRIGGERED]:  { color: '#ef4444', label: 'TRIGGERED',  icon: '🔴', meaning: 'The condition is met — the agent acts on the next cycle.' },
  [STATE.DEGRADED]:   { color: '#f97316', label: 'DEGRADED',   icon: '🟠', meaning: 'The rule is present but cannot protect this position.' },
  [STATE.WATCH]:      { color: '#f59e0b', label: 'WATCH',      icon: '🟡', meaning: 'Within striking distance of its trigger.' },
  [STATE.ACTIVE]:     { color: '#10b981', label: 'ACTIVE',     icon: '🟢', meaning: 'Live and protecting, comfortably clear of its trigger.' },
  [STATE.PENDING]:    { color: '#64748b', label: 'PENDING',    icon: '⚪', meaning: 'Its window has not opened yet.' },
  [STATE.SUPPRESSED]: { color: '#3b82f6', label: 'SUPPRESSED', icon: '🔵', meaning: 'Deliberately switched off for this position.' },
  [STATE.EXPIRED]:    { color: '#475569', label: 'EXPIRED',    icon: '⚫', meaning: 'Its window has closed — it can no longer fire.' },
  [STATE.OFF]:        { color: '#475569', label: 'OFF',        icon: '⚫', meaning: 'Disabled by configuration.' },
};

export const stateColor = (s) => (STATE_META[s] || STATE_META[STATE.PENDING]).color;

// ── Small helpers ─────────────────────────────────────────────────────────────
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const pctAway = (price, level) => (level > 0 ? (price / level - 1) * 100 : null);

/** Which rung of the profit ratchet the position currently sits on. */
export function trailLadderRung(gainPct, powerHold) {
  if (powerHold) return { pct: RULES_CONFIG.POWER_HOLD_TRAIL_PCT, label: 'Power Hold (ladder bypassed)' };
  for (const [threshold, pct] of RULES_CONFIG.TRAIL_PROFIT_TIERS) {
    if (gainPct >= threshold) return { pct, label: `≥ +${threshold}% gain` };
  }
  return { pct: null, label: 'Base (ATR-scaled)' };
}

/**
 * Evaluate every risk rule for one position.
 *
 * @param {object} pos                 a row from /api/portfolio positions[]
 * @param {number} daysHeld            NYSE trading days since entry (agent-equivalent)
 * @param {number} daysSinceHwm        NYSE trading days since the last new high
 * @param {number} calendarDaysHeld    calendar days since entry (Power Hold uses these)
 * @param {number} [openPositions]     how many slots are currently filled — Rank & Replace
 *                                     only runs with a full book. Omit to skip the gate.
 * @returns {{phase: object, rules: array, headline: object}}
 */
export function evaluatePositionRules(pos, daysHeld, daysSinceHwm, calendarDaysHeld, openPositions) {
  const C = RULES_CONFIG;
  const buy = num(pos.buy_price) ?? 0;
  const price = num(pos.current_price) ?? buy;
  const hwm = num(pos.hwm_price) ?? buy;
  const gainPct = buy > 0 ? (price / buy - 1) * 100 : 0;
  const peakPct = num(pos.highest_unrealized_pct) ?? 0;
  const powerHold = !!(pos.power_hold ?? pos.is_power_hold);
  const rules = [];

  // ── 1. Armed Exit ───────────────────────────────────────────────────────────
  // Not a rule that decides anything — it is the mechanism the loss rules call.
  // Listed first because when it is live it pre-empts everything below it.
  if (pos.exit_armed) {
    const armedAt = pos.exit_armed_at ? new Date(pos.exit_armed_at) : null;
    const hoursArmed = armedAt ? (Date.now() - armedAt.getTime()) / 3.6e6 : null;
    const hoursLeft = hoursArmed != null ? C.ARMED_EXIT_DEADLINE_HOURS - hoursArmed : null;
    rules.push({
      id: 'armed_exit', tier: 'EXIT', name: 'Armed Exit',
      state: STATE.ARMED,
      headline: hoursLeft != null && hoursLeft > 0
        ? `Selling — ${hoursLeft.toFixed(1)}h until forced market sell`
        : 'Selling — deadline reached, forced market sell',
      level: num(pos.exit_armed_price),
      levelLabel: pos.exit_armed_price ? 'Armed at' : null,
      detail: `${(C.ARMED_EXIT_TRAIL_PCT * 100).toFixed(2)}% trailing stop placed at IBKR to follow any bounce. `
            + `If it is not filled within ${C.ARMED_EXIT_DEADLINE_HOURS}h the agent market-sells.`
            + (pos.exit_armed_reason ? `\nReason: ${pos.exit_armed_reason}` : ''),
    });
  } else {
    rules.push({
      id: 'armed_exit', tier: 'EXIT', name: 'Armed Exit',
      state: STATE.PENDING,
      headline: 'Standby — no loss rule has fired',
      detail: `When the Kill-switch or Thesis Stop fires it places a ${(C.ARMED_EXIT_TRAIL_PCT * 100).toFixed(2)}% `
            + `trailing stop rather than market-selling into the low tick, with a ${C.ARMED_EXIT_DEADLINE_HOURS}h deadline.`,
    });
  }

  // ── 2. Trailing stop (broker-side, always live) ──────────────────────────────
  {
    const effPct = num(pos.stop_loss_pct) ?? C.STOP_LOSS_PCT;
    const level = hwm * (1 - effPct);
    const away = pctAway(price, level);
    const rung = trailLadderRung(gainPct, powerHold);
    const state = price <= level ? STATE.TRIGGERED
                : (away != null && away <= 2) ? STATE.WATCH
                : STATE.ACTIVE;
    rules.push({
      id: 'trailing_stop', tier: 'STOP', name: 'Trailing Stop (IBKR GTC)',
      state,
      headline: `${(effPct * 100).toFixed(1)}% from peak${away != null ? ` · ${away.toFixed(1)}% above` : ''}`,
      level, levelLabel: 'Stop at',
      distancePct: away,
      window: 'Always',
      detail: `Trails ${(effPct * 100).toFixed(2)}% below the running peak of $${hwm.toFixed(2)}. `
            + `Ratchet rung: ${rung.label}. This is the only exit that survives the bot being offline.`
            + (powerHold ? '\nPower Hold has widened it to 30% so a genuine leader can complete its move.' : ''),
    });
  }

  // ── 3. Early Loss Kill-switch (days 0–1) ─────────────────────────────────────
  {
    const level = buy * (1 - C.EARLY_LOSS_STOP_PCT);
    const away = pctAway(price, level);
    let state, headline;
    if (daysHeld > C.EARLY_LOSS_LAST_DAY) {
      state = STATE.EXPIRED;
      headline = `Window closed after day ${C.EARLY_LOSS_LAST_DAY}`;
    } else if (price <= level) {
      state = STATE.TRIGGERED;
      headline = 'Threshold breached — exit arming';
    } else if (away != null && away <= 1.0) {
      state = STATE.WATCH;
      headline = `${away.toFixed(2)}% above the trigger`;
    } else {
      state = STATE.ACTIVE;
      headline = `Armed through day ${C.EARLY_LOSS_LAST_DAY} · ${away.toFixed(1)}% of room`;
    }
    const done = state === STATE.EXPIRED;
    rules.push({
      id: 'kill_switch', tier: 'T1', name: 'Early Loss Kill-switch',
      state, headline,
      level: done ? null : level,
      levelLabel: 'Fires at',
      distancePct: done ? null : away,
      window: `Days 0–${C.EARLY_LOSS_LAST_DAY}`,
      detail: `Arms an exit if price falls ${(C.EARLY_LOSS_STOP_PCT * 100).toFixed(0)}% below entry in the first two `
            + 'sessions. A breakout that reverses that fast has already falsified its premise, and the 10–12% '
            + 'trailing stop is far too wide to be useful this early.',
    });
  }

  // ── 3b. Early Dollar Stop (days 0–EARLY_DOLLAR_STOP_MAX_DAY) ─────────────────
  // Flat dollar cap on unrealised loss during the first 5 trading days.
  // Complements the % kill-switch (days 0-1) and ATR-based thesis stop (days 2-5).
  {
    const dollarCap  = C.EARLY_DOLLAR_STOP_AMOUNT;
    const maxDay     = C.EARLY_DOLLAR_STOP_MAX_DAY;
    const shares     = num(pos.shares) ?? 0;
    const posSize    = shares > 0 ? shares * buy : null;
    const dollarLoss = shares > 0 ? shares * (price - buy) : null; // negative when losing
    const triggerPx  = (shares > 0 && dollarCap > 0) ? buy - dollarCap / shares : null;
    const away       = triggerPx != null ? pctAway(price, triggerPx) : null;
    const usedDollar = dollarLoss != null ? Math.max(0, -dollarLoss) : 0;
    const capEquivPct = (posSize != null && posSize > 0) ? (dollarCap / posSize * 100) : null;

    let state, headline, detail;
    const base = `Arms an exit if the unrealised dollar loss reaches $${dollarCap.toFixed(0)} at any point during days 0–${maxDay}. `
               + (capEquivPct != null ? `On this $${posSize != null ? (posSize/1000).toFixed(1) : '?'}K position that is ≈ ${capEquivPct.toFixed(2)}%. ` : '')
               + 'Fires arm_exit() (0.6% tight trail) rather than a market sell, so any bounce can be captured.';

    if (dollarCap <= 0) {
      state   = STATE.OFF;
      headline = 'Disabled (EARLY_DOLLAR_STOP_AMOUNT = 0)';
      detail   = base;
    } else if (daysHeld > maxDay) {
      state   = STATE.EXPIRED;
      headline = `Window closed after day ${maxDay}`;
      detail   = base;
    } else if (dollarLoss != null && dollarLoss <= -dollarCap) {
      state   = STATE.TRIGGERED;
      headline = `Loss $${usedDollar.toFixed(0)} ≥ $${dollarCap.toFixed(0)} cap — exit arming`;
      detail   = base;
    } else if (away != null && away <= 1.0) {
      state   = STATE.WATCH;
      headline = `$${usedDollar.toFixed(0)} / $${dollarCap.toFixed(0)} · ${away.toFixed(2)}% above trigger`;
      detail   = base;
    } else {
      state   = STATE.ACTIVE;
      headline = `$${usedDollar.toFixed(0)} / $${dollarCap.toFixed(0)} used`
               + (away != null ? ` · ${away.toFixed(1)}% of room` : '');
      detail   = base + ' This is the primary early-exit for any position losing more than the daily floor before confirming itself.';
    }

    const done = state === STATE.EXPIRED || state === STATE.OFF;
    rules.push({
      id: 'dollar_stop', tier: 'T1', name: 'Early Dollar Stop',
      state, headline, detail,
      level: done ? null : triggerPx,
      levelLabel: 'Fires at',
      distancePct: done ? null : away,
      window: `Days 0–${maxDay}`,
      progress: (!done && dollarCap > 0) ? { value: usedDollar, max: dollarCap } : undefined,
    });
  }

  // ── 4. Thesis Stop (days 2–5) — and its follow-through latch ────────────────
  {
    const atr = num(pos.entry_atr_pct) ?? C.THESIS_STOP_ATR_FALLBACK;
    const thresholdPct = C.THESIS_STOP_ATR_MULT * atr;
    const level = buy * (1 - thresholdPct / 100);
    const away = pctAway(price, level);
    const latch = pos.closed_above_entry;           // true / false / null(=column absent)
    const pokedAboveEntry = peakPct > 0 || hwm > buy || (num(pos.intraday_high_today) ?? 0) > buy;

    const base = `Fires only while the position has never CLOSED above its $${buy.toFixed(2)} entry, and price is `
               + `at least 1×ATR (${thresholdPct.toFixed(2)}%) below it. `;
    let state, headline, detail;

    if (daysHeld > C.THESIS_STOP_LAST_DAY) {
      // Past the window the latch is irrelevant — the rule cannot fire either way,
      // so reporting DEGRADED here would be alarming without being actionable.
      state = STATE.EXPIRED;
      headline = `Window closed after day ${C.THESIS_STOP_LAST_DAY}`;
      detail = base + 'Beyond day 5 the EMA-21, plateau and rotation rules take over.';
    } else if (latch === true) {
      state = STATE.SUPPRESSED;
      headline = 'Exempt — closed above entry (followed through)';
      detail = base + 'This position established follow-through on a daily close, so it is permanently exempt by design.';
    } else if (latch === null || latch === undefined) {
      // The `closed_above_entry` column is missing, so the agent is running the weaker
      // intraday fallback. This is the NBIX/DELL failure mode — surface it loudly.
      state = STATE.DEGRADED;
      headline = pokedAboveEntry ? 'DISARMED by an intraday poke above entry' : 'Running on the intraday fallback';
      detail = base
        + '\n⚠️ The closed_above_entry column is missing from Supabase, so the agent is falling back to intraday '
        + 'evidence (peak / HWM / today\'s high above entry). '
        + (pokedAboveEntry
            ? `This position printed above entry intraday (peak +${peakPct.toFixed(2)}%, HWM $${hwm.toFixed(2)}) `
              + 'without ever closing there, so the fallback has exempted it and the Thesis Stop CANNOT fire. '
              + 'Only the wide trailing stop is protecting it — this is exactly how NBIX ran from −2.9% to −11.5%.'
            : 'Apply migrations/2026-08-13_apply_missing_migrations.sql to restore the close-based latch.');
    } else if (daysHeld < C.THESIS_STOP_START_DAY) {
      state = STATE.PENDING;
      headline = `Arms on day ${C.THESIS_STOP_START_DAY} (${C.THESIS_STOP_START_DAY - daysHeld} session away)`;
      detail = base + 'Days 0–1 are covered by the Kill-switch instead.';
    } else if (price <= level) {
      state = STATE.TRIGGERED;
      headline = 'Threshold breached — exit arming';
      detail = base + 'Price is below the 1×ATR threshold and the position never closed above entry.';
    } else if (away != null && away <= thresholdPct / 3) {
      state = STATE.WATCH;
      headline = `${away.toFixed(2)}% above the trigger`;
      detail = base;
    } else {
      state = STATE.ACTIVE;
      headline = `Live · ${away.toFixed(1)}% of room · 1×ATR = ${thresholdPct.toFixed(2)}%`;
      detail = base + 'This is the primary loss-cutting rule for a breakout that never follows through.';
    }

    const done = state === STATE.EXPIRED || state === STATE.SUPPRESSED;
    rules.push({
      id: 'thesis_stop', tier: 'T2', name: 'Thesis Stop',
      state, headline, detail,
      level: done ? null : level,
      levelLabel: 'Fires at',
      distancePct: done ? null : away,
      window: `Days ${C.THESIS_STOP_START_DAY}–${C.THESIS_STOP_LAST_DAY}`,
      latch: latch === true ? 'Closed above entry'
           : latch === false ? 'Never closed above entry'
           : 'Unknown — column missing',
    });
  }

  // ── 5. Day-3 breakout verdict ───────────────────────────────────────────────
  {
    const v = pos.breakout_verdict;
    let state, headline;
    if (v === 'PASS')      { state = STATE.ACTIVE;   headline = 'PASS — closed ≥ +1% on healthy volume'; }
    else if (v === 'FAIL') { state = STATE.WATCH;    headline = `FAIL — rotation bar drops to ${C.RANK_REPLACE_FAIL_THRESHOLD} pts`; }
    else if (daysHeld < C.BREAKOUT_VERDICT_DAY) {
      state = STATE.PENDING; headline = `Recorded at day ${C.BREAKOUT_VERDICT_DAY} EOD`;
    } else { state = STATE.DEGRADED; headline = 'Not recorded — day-3 EOD was missed'; }
    rules.push({
      id: 'breakout_verdict', tier: 'D3', name: 'Day-3 Breakout Verdict',
      state, headline,
      window: `Day ${C.BREAKOUT_VERDICT_DAY} EOD`,
      detail: 'Never sells anything by itself. PASS requires a close ≥ +1% above entry with day-3 volume ≥ 75% of '
            + `the 20-day average. A FAIL lowers the Rank & Replace margin from ${C.RANK_REPLACE_THRESHOLD} to `
            + `${C.RANK_REPLACE_FAIL_THRESHOLD} points — a breakout that already failed to confirm has forfeited `
            + 'the benefit of the doubt.',
    });
  }

  // ── 6. EMA-21 support breach (day 7+) ───────────────────────────────────────
  {
    let state, headline;
    if (powerHold) { state = STATE.SUPPRESSED; headline = 'Suppressed by Power Hold'; }
    else if (daysHeld < C.EXIT_MA_MIN_DAYS) {
      state = STATE.PENDING;
      headline = `Arms on day ${C.EXIT_MA_MIN_DAYS} (${C.EXIT_MA_MIN_DAYS - daysHeld} sessions away)`;
    } else { state = STATE.ACTIVE; headline = 'Live — checked at 15:45–16:00 ET'; }
    rules.push({
      id: 'ema21', tier: 'T3', name: `EMA-${C.EXIT_MA_WINDOW} Support Breach`,
      state, headline,
      window: `Day ${C.EXIT_MA_MIN_DAYS}+`,
      detail: `Market-sells when the close is below EMA-${C.EXIT_MA_WINDOW} × ${(1 - C.EXIT_MA_BUFFER_PCT).toFixed(2)} `
            + `(a ${(C.EXIT_MA_BUFFER_PCT * 100).toFixed(0)}% buffer). Evaluated only in the closing window so an `
            + 'intraday wick cannot trigger a sale the close would not have justified. Suppressed before day '
            + `${C.EXIT_MA_MIN_DAYS} so normal post-breakout consolidation is not read as failure.`,
    });
  }

  // ── 7. Plateau exit (day 7+, capital velocity) ──────────────────────────────
  {
    const d = num(daysSinceHwm) ?? 0;
    let state, headline;
    if (powerHold) { state = STATE.SUPPRESSED; headline = 'Suppressed by Power Hold'; }
    else if (daysHeld < C.STALE_EXIT_MIN_DAYS_HELD) {
      state = STATE.PENDING;
      headline = `Arms on day ${C.STALE_EXIT_MIN_DAYS_HELD} · ${d}/${C.STALE_EXIT_DAYS} stale days so far`;
    } else if (d >= C.STALE_EXIT_DAYS) {
      state = STATE.TRIGGERED; headline = `${d}/${C.STALE_EXIT_DAYS} stale days — sells at EOD`;
    } else if (d >= C.STALE_EXIT_DAYS - 3) {
      state = STATE.WATCH; headline = `${d}/${C.STALE_EXIT_DAYS} days without a new high`;
    } else {
      state = STATE.ACTIVE; headline = `${d}/${C.STALE_EXIT_DAYS} days without a new high`;
    }
    rules.push({
      id: 'plateau', tier: 'T3', name: 'Plateau Exit',
      state, headline,
      progress: { value: d, max: C.STALE_EXIT_DAYS },
      window: `Day ${C.STALE_EXIT_MIN_DAYS_HELD}+`,
      detail: `Sells after ${C.STALE_EXIT_DAYS} trading days without a new high water mark. This is a capital `
            + 'velocity rule, not a risk rule — the position may sit comfortably above its stop and its EMA, but '
            + 'with a hard cap on slots, dead money costs the return of the best trigger it is blocking.',
    });
  }

  // ── 8. Rank & Replace (day 7+) ──────────────────────────────────────────────
  {
    const mt = num(pos.momentum_health_score);
    const top = num(pos.top_trigger_score);
    const margin = pos.breakout_verdict === 'FAIL' ? C.RANK_REPLACE_FAIL_THRESHOLD : C.RANK_REPLACE_THRESHOLD;
    const gap = (mt != null && top != null) ? top - mt : null;
    // The agent gates the whole rule on `len(positions) >= MAX_POSITIONS`. With a
    // free slot it buys the trigger instead of rotating, so showing a score gap as
    // TRIGGERED while the book is short would be a false alarm.
    const bookFull = openPositions == null || openPositions >= C.MAX_POSITIONS;
    let state, headline;
    if (powerHold) { state = STATE.SUPPRESSED; headline = 'Suppressed by Power Hold'; }
    else if (daysHeld < C.RANK_REPLACE_MIN_DAYS) { state = STATE.PENDING; headline = `Arms on day ${C.RANK_REPLACE_MIN_DAYS}`; }
    else if (!bookFull) {
      state = STATE.SUPPRESSED;
      headline = `Idle — book not full (${openPositions}/${C.MAX_POSITIONS} slots)`;
    }
    else if (gap == null) { state = STATE.PENDING; headline = 'Awaiting a live Mₜ score'; }
    else if (gap > margin) { state = STATE.TRIGGERED; headline = `Best trigger leads by ${gap.toFixed(1)} > ${margin} pts`; }
    else if (gap > margin - 5) { state = STATE.WATCH; headline = `Best trigger leads by ${gap.toFixed(1)} pts (bar ${margin})`; }
    else {
      state = STATE.ACTIVE;
      headline = gap >= 0
        ? `Best trigger leads by only ${gap.toFixed(1)} pts (bar ${margin})`
        : `Outranks the best trigger by ${(-gap).toFixed(1)} pts`;
    }
    rules.push({
      id: 'rank_replace', tier: 'T3', name: 'Rank & Replace',
      state, headline,
      window: `Day ${C.RANK_REPLACE_MIN_DAYS}+`,
      detail: 'Runs once daily at EOD, and only when all '
            + `${C.MAX_POSITIONS} slots are full and fresh triggers exist — with a free slot the agent buys the `
            + 'trigger instead of rotating. Compares this holding\'s momentum health Mₜ (0.40 × live RS + '
            + '0.35 × volume ratio + 0.25 × sentiment) against the best available trigger. Required margin: '
            + `${C.RANK_REPLACE_THRESHOLD} pts after a PASS verdict, ${C.RANK_REPLACE_FAIL_THRESHOLD} pts after a FAIL.`
            + (mt != null ? `\nMₜ now ${mt.toFixed(1)}${top != null ? ` vs best trigger ${top.toFixed(1)} (EOD snapshot)` : ''}.` : ''),
    });
  }

  // ── 9. Power Hold (O'Neil's 8-week rule) ────────────────────────────────────
  {
    const cd = num(calendarDaysHeld) ?? 0;
    const base = `Arms when a position gains ≥ ${C.POWER_HOLD_GAIN_PCT}% within ${C.POWER_HOLD_TRIGGER_DAYS} calendar `
               + `days, then persists for ${C.POWER_HOLD_DURATION_DAYS}. While active it widens the trailing stop to `
               + `${(C.POWER_HOLD_TRAIL_PCT * 100).toFixed(0)}% and suppresses every discretionary exit, so a genuine `
               + 'market leader can complete its move.';
    let state, headline, detail;
    if (powerHold) {
      state = STATE.ACTIVE;
      headline = pos.power_hold_expiry
        ? `Active until ${new Date(pos.power_hold_expiry).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
        : 'Active';
      detail = base + '\nThe base trailing stop remains live as the disaster backstop.';
    } else if (cd > C.POWER_HOLD_TRIGGER_DAYS) {
      state = STATE.EXPIRED;
      headline = `Not reached — peak was +${peakPct.toFixed(1)}%`;
      detail = base;
    } else {
      state = STATE.PENDING;
      headline = `Peak +${peakPct.toFixed(1)}% of +${C.POWER_HOLD_GAIN_PCT}% · ${C.POWER_HOLD_TRIGGER_DAYS - cd} days left`;
      detail = base;
    }
    rules.push({
      id: 'power_hold', tier: 'PH', name: 'Power Hold (8-week rule)',
      state, headline, detail,
      progress: { value: Math.max(peakPct, 0), max: C.POWER_HOLD_GAIN_PCT },
      window: `${C.POWER_HOLD_TRIGGER_DAYS} calendar days`,
    });
  }

  // ── Phase (the compact headline pill) ───────────────────────────────────────
  let phase;
  if (pos.exit_armed) {
    phase = { key: 'EXITING', label: 'Exiting', color: '#f43f5e', note: 'An armed exit is live at the broker.' };
  } else if (powerHold) {
    phase = { key: 'POWER_HOLD', label: `D${daysHeld} · Power Hold`, color: '#8b5cf6', note: 'Discretionary exits suppressed; a 30% disaster stop is the only backstop.' };
  } else if (daysHeld <= C.EARLY_LOSS_LAST_DAY) {
    phase = { key: 'KILL_SWITCH', label: `D${daysHeld} · Kill-switch`, color: '#3b82f6', note: `Days 0–1: a 2% reverse arms an exit. $${C.EARLY_DOLLAR_STOP_AMOUNT} dollar cap also active.` };
  } else if (daysHeld <= C.THESIS_STOP_LAST_DAY) {
    phase = { key: 'THESIS', label: `D${daysHeld} · Thesis window`, color: '#0ea5e9', note: `Days 2–5: Thesis Stop is the primary loss-cutter. $${C.EARLY_DOLLAR_STOP_AMOUNT} dollar cap still active.` };
  } else if (daysHeld < C.EXIT_MA_MIN_DAYS) {
    phase = { key: 'TRANSITION', label: `D${daysHeld} · Transition`, color: '#64748b', note: 'Day 6: the loss rules have expired and the rotation rules have not yet armed — trailing stop only.' };
  } else {
    phase = { key: 'ROTATION', label: `D${daysHeld} · Rotation window`, color: '#a78bfa', note: 'Day 7+: EMA-21, plateau and Rank & Replace are all live.' };
  }

  // Most urgent rule drives the row-level colour.
  const headline = [...rules].sort(
    (a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)
  )[0];

  return { phase, rules, headline };
}

/** Plain-text summary used as the native tooltip on the compact cell. */
export function rulesTooltip(ticker, phase, rules) {
  const lines = [`${ticker} — ${phase.label}`, phase.note, ''];
  for (const r of rules) {
    const m = STATE_META[r.state];
    lines.push(`${m.icon} ${m.label.padEnd(10)} ${r.name} — ${r.headline}`);
  }
  lines.push('', 'Click the row for the full risk ladder.');
  return lines.join('\n');
}
