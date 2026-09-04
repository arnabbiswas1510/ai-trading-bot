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
    [5.0, 0.015],
  ],
  // ── The Prove-It Stop ───────────────────────────────────────────────────────
  // Mirrors execution_agent.py. One question decides the phase: has this
  // position ever CLOSED above entry (portfolio_positions.closed_above_entry)?
  PROVE_IT_P1_DAY0_PCT: 0.01,       // unproven, entry day — 1% below entry
  PROVE_IT_P1_LATER_PCT: 0.03,      // unproven, day 1+   — 3% below entry
  PROVE_IT_P1_DAY0_LAST_DAY: 0,
  PROVE_IT_P2_ARM_GAIN_PCT: 0.02,   // peak gain that arms the give-back floor
  PROVE_IT_P2_FLOOR_PCT: -0.01,     // floor sits 1% BELOW entry (negative)
  PROVE_IT_BACKSTOP_SLACK_PCT: 0.01,// resting IBKR order sits this much wider
  STALE_EXIT_DAYS: 10,              // trading days without a new HWM
  STALE_EXIT_MIN_DAYS_HELD: 7,
  RANK_REPLACE_MIN_DAYS: 7,
  RANK_REPLACE_THRESHOLD: 15,     // margin required when the day-3 verdict was PASS
  RANK_REPLACE_FAIL_THRESHOLD: 5, // lower bar once the breakout has already failed
  MAX_POSITIONS: 5,               // Rank & Replace only runs when the book is full
  POWER_HOLD_GAIN_PCT: 10.0,
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
const approxEq = (a, b) => a != null && b != null && Math.abs(a - b) < 1e-6;

/**
 * Has this position ever CLOSED above entry? Mirror of
 * execution_agent.prove_it_is_proven().
 *
 * Fails SAFE, exactly as the agent does: a missing `closed_above_entry` column
 * reads as null, and null must never be reported as "unproven" — that would show
 * the tight Phase 1 band on a position the agent is actually treating as proven.
 */
export function proveItIsProven(pos) {
  const latch = pos.closed_above_entry;
  if (latch === true || latch === false) return latch;
  const buy = num(pos.buy_price) ?? 0;
  if (buy <= 0) return true;
  return (num(pos.highest_unrealized_pct) ?? 0) > 0
      || (num(pos.hwm_price) ?? 0) > buy
      || (num(pos.intraday_high_today) ?? 0) > buy;
}

/** Phase 1 band for a given day, as a positive fraction below entry. */
export function proveItP1ThresholdPct(daysHeld) {
  const C = RULES_CONFIG;
  return daysHeld <= C.PROVE_IT_P1_DAY0_LAST_DAY ? C.PROVE_IT_P1_DAY0_PCT : C.PROVE_IT_P1_LATER_PCT;
}

/**
 * Price this position is protected at right now, and which phase produced it.
 * Mirror of execution_agent.prove_it_stop_level().
 *
 * Returns level === null for a proven position whose peak has not yet reached
 * PROVE_IT_P2_ARM_GAIN_PCT — the give-back floor is not armed, so only the base
 * trailing stop applies.
 */
export function proveItStopLevel(pos, daysHeld) {
  const C = RULES_CONFIG;
  const buy = num(pos.buy_price) ?? 0;
  if (buy <= 0) return { level: null, phase: 'unknown' };
  if (proveItIsProven(pos)) {
    const peakPct = num(pos.highest_unrealized_pct) ?? 0;
    if (peakPct < C.PROVE_IT_P2_ARM_GAIN_PCT * 100) return { level: null, phase: 'phase2-unarmed' };
    return { level: buy * (1 + C.PROVE_IT_P2_FLOOR_PCT), phase: 'phase2' };
  }
  return { level: buy * (1 - proveItP1ThresholdPct(daysHeld)), phase: 'phase1' };
}

/** Which rung of the profit ratchet the position currently sits on. */
export function trailLadderRung(gainPct, powerHold, effectivePct = null) {
  if (powerHold) return { pct: RULES_CONFIG.POWER_HOLD_TRAIL_PCT, label: 'Power Hold (ladder bypassed)', kind: 'power_hold' };
  for (const [threshold, pct] of RULES_CONFIG.TRAIL_PROFIT_TIERS) {
    if (pct != null && approxEq(effectivePct, pct)) {
      return {
        pct,
        kind: 'profit_lock',
        armedThreshold: threshold,
        label: `HWM profit lock active (armed at +${threshold}% gain)`,
      };
    }
  }
  for (const [threshold, pct] of RULES_CONFIG.TRAIL_PROFIT_TIERS) {
    if (gainPct >= threshold) return { pct, kind: 'eligible', armedThreshold: threshold, label: `Eligible at +${threshold}% gain` };
  }
  return { pct: null, label: 'Base (ATR-scaled)', kind: 'base' };
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
 * @param {number} [equity]            total account equity. Retained for signature compatibility;
 *                                     no live rule derives a threshold from it any more.
 * @returns {{phase: object, rules: array, headline: object}}
 */
export function evaluatePositionRules(pos, daysHeld, daysSinceHwm, calendarDaysHeld, openPositions, equity = null) {  // eslint-disable-line no-unused-vars
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
      detail: `When the Prove-It Stop fires it places a ${(C.ARMED_EXIT_TRAIL_PCT * 100).toFixed(2)}% `
            + `trailing stop rather than market-selling into the low tick, with a ${C.ARMED_EXIT_DEADLINE_HOURS}h deadline.`,
    });
  }

  // ── 2. Trailing stop (broker-side, always live) ──────────────────────────────
  {
    const effPct = num(pos.stop_loss_pct) ?? C.STOP_LOSS_PCT;
    const level = hwm * (1 - effPct);
    const away = pctAway(price, level);
    const rung = trailLadderRung(gainPct, powerHold, effPct);
    const state = price <= level ? STATE.TRIGGERED
                : (away != null && away <= 2) ? STATE.WATCH
                : STATE.ACTIVE;
    const headline = rung.kind === 'profit_lock'
      ? `HWM lock active · ${(effPct * 100).toFixed(1)}% from peak${away != null ? ` · ${away.toFixed(1)}% above` : ''}`
      : `${(effPct * 100).toFixed(1)}% from peak${away != null ? ` · ${away.toFixed(1)}% above` : ''}`;
    rules.push({
      id: 'trailing_stop', tier: 'STOP', name: 'Trailing Stop (IBKR GTC)',
      state,
      headline,
      level, levelLabel: 'Stop at',
      distancePct: away,
      window: 'Always',
      detail: `Trails ${(effPct * 100).toFixed(2)}% below the running peak of $${hwm.toFixed(2)}. `
            + `Ratchet rung: ${rung.label}. This is the only exit that survives the bot being offline.`
            + (powerHold ? '\nPower Hold has widened it to 30% so a genuine leader can complete its move.' : ''),
    });
  }

  // ── 3. The Prove-It Stop ────────────────────────────────────────────────────
  // Replaces the Early Loss Kill-switch, Early Dollar Stop and Thesis Stop with
  // one rule and one question: has this position ever CLOSED above entry?
  {
    const { level, phase } = proveItStopLevel(pos, daysHeld);
    const latch    = pos.closed_above_entry;
    const proven   = phase === 'phase2' || phase === 'phase2-unarmed';
    const bandPct  = proveItP1ThresholdPct(daysHeld) * 100;
    const armGain  = C.PROVE_IT_P2_ARM_GAIN_PCT * 100;
    const floorPct = C.PROVE_IT_P2_FLOOR_PCT * 100;
    const away     = level != null ? pctAway(price, level) : null;

    const base = proven
      ? `This position CLOSED above its $${buy.toFixed(2)} entry, so it earned patience. `
        + `Phase 2 anchors to the peak: once the gain has reached +${armGain.toFixed(1)}% the floor arms at `
        + `${floorPct.toFixed(1)}% of entry and a trade that went green is not allowed to become a real loss. `
      : `This position has never CLOSED above its $${buy.toFixed(2)} entry, so the breakout is unproven. `
        + `Phase 1 anchors to entry: ${(C.PROVE_IT_P1_DAY0_PCT * 100).toFixed(1)}% below it on the entry day, `
        + `${(C.PROVE_IT_P1_LATER_PCT * 100).toFixed(1)}% from day 1 onward. `;
    const mechanism = 'Fires arm_exit() (0.6% tight trail) rather than a market sell, so a bounce can still be '
      + `captured. A GTC order also rests ${(C.PROVE_IT_BACKSTOP_SLACK_PCT * 100).toFixed(1)}% wider at the broker `
      + 'so an overnight gap is still capped when the agent is offline.';

    // The NBIX failure mode. Without the `closed_above_entry` column the rule
    // fails safe to "proven", so a single intraday poke above entry promotes a
    // position that has never actually CLOSED green — and Phase 1, the tight
    // entry-anchored band that is the whole point of the rule, silently stops
    // applying. That must be visible, not inferred.
    const latchMissing = latch !== true && latch !== false;
    const degraded = latchMissing && proven;

    let state, headline, detail;
    if (degraded && !powerHold) {
      state = STATE.DEGRADED;
      headline = 'DISARMED by an intraday poke above entry';
      detail = 'The `closed_above_entry` column is missing, so the phase is being guessed from '
        + 'intraday highs. This position never CLOSED above its $' + buy.toFixed(2) + ' entry, but a '
        + 'poke above it has promoted it to Phase 2 — the tight Phase 1 band is not protecting it. '
        + 'Apply migrations/add_closed_above_entry.sql.\n' + base + mechanism;
    } else if (powerHold) {
      state = STATE.SUPPRESSED;
      headline = 'Suppressed by Power Hold';
      detail = base + 'Power Hold has widened the trail deliberately; a leader that far ahead is nowhere near this level.';
    } else if (phase === 'phase2-unarmed') {
      state = STATE.PENDING;
      headline = `Floor arms at +${armGain.toFixed(1)}% peak · peak is +${peakPct.toFixed(2)}%`;
      detail = base + 'Below the arming gain a floor would sit inside ordinary noise, so only the base trailing '
        + 'stop applies for now.\n' + mechanism;
    } else if (level != null && price <= level) {
      state = STATE.TRIGGERED;
      headline = proven ? 'Gave back to the floor — exit arming' : 'Threshold breached — exit arming';
      detail = base + mechanism;
    } else if (away != null && away <= 1.0) {
      state = STATE.WATCH;
      headline = `${away.toFixed(2)}% above the ${proven ? 'give-back floor' : 'Phase 1 band'}`;
      detail = base + mechanism;
    } else {
      state = STATE.ACTIVE;
      headline = proven
        ? `Phase 2 · floor locked${away != null ? ` · ${away.toFixed(1)}% of room` : ''}`
        : `Phase 1 · ${bandPct.toFixed(1)}% band${away != null ? ` · ${away.toFixed(1)}% of room` : ''}`;
      detail = base + mechanism;
    }

    rules.push({
      id: 'prove_it', tier: 'T1', name: `Prove-It Stop (${proven ? 'Phase 2 — proven' : 'Phase 1 — unproven'})`,
      state, headline, detail,
      level: level ?? null,
      levelLabel: level != null ? (proven ? 'Floor at' : 'Fires at') : null,
      distancePct: away,
      window: 'Always',
      latch: latch === true ? 'Closed above entry'
           : latch === false ? 'Never closed above entry'
           : 'Column missing — using intraday fallback',
    });
  }


  // ── 4. Day-3 breakout verdict ───────────────────────────────────────────────
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

  // ── 5. Rank & Replace (day 7+, absorbs the retired Plateau Exit) ─────────────
  {
    const mt = num(pos.momentum_health_score);
    const top = num(pos.top_trigger_score);
    const staleDays = num(daysSinceHwm) ?? 0;
    const isStale = daysHeld >= C.STALE_EXIT_MIN_DAYS_HELD && staleDays >= C.STALE_EXIT_DAYS;
    const verdictMargin = pos.breakout_verdict === 'FAIL' ? C.RANK_REPLACE_FAIL_THRESHOLD : C.RANK_REPLACE_THRESHOLD;
    // Staleness discounts the bar rather than selling to cash on its own.
    const margin = isStale ? Math.min(verdictMargin, C.RANK_REPLACE_FAIL_THRESHOLD) : verdictMargin;
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
    if (isStale && state !== STATE.SUPPRESSED && state !== STATE.PENDING) {
      headline += ` · stale ${staleDays}d`;
    }
    rules.push({
      id: 'rank_replace', tier: 'T3', name: 'Rank & Replace',
      state, headline,
      window: `Day ${C.RANK_REPLACE_MIN_DAYS}+`,
      progress: daysHeld >= C.STALE_EXIT_MIN_DAYS_HELD ? { value: staleDays, max: C.STALE_EXIT_DAYS } : undefined,
      detail: 'Runs once daily at EOD, and only when all '
            + `${C.MAX_POSITIONS} slots are full and fresh triggers exist — with a free slot the agent buys the `
            + 'trigger instead of rotating. Compares this holding\'s momentum health Mₜ (0.40 × live RS + '
            + '0.35 × volume ratio + 0.25 × sentiment) against the best available trigger. Required margin: '
            + `${C.RANK_REPLACE_THRESHOLD} pts after a PASS verdict, ${C.RANK_REPLACE_FAIL_THRESHOLD} pts after a FAIL.`
            + `\nA position with no new high in ${C.STALE_EXIT_DAYS} trading days is stale, which also drops the bar `
            + `to ${C.RANK_REPLACE_FAIL_THRESHOLD} pts. Staleness no longer sells to cash on its own — with the `
            + 'Prove-It floor in place, holding dead money is nearly free, so the position is only released when '
            + 'somewhere better to put the money actually exists.'
            + (isStale ? `\n⏳ Stale now: ${staleDays} trading days without a new high — rotation bar discounted to ${margin} pts.` : '')
            + (mt != null ? `\nMₜ now ${mt.toFixed(1)}${top != null ? ` vs best trigger ${top.toFixed(1)} (EOD snapshot)` : ''}.` : ''),
    });
  }

  // ── 6. Power Hold (O'Neil's 8-week rule) ────────────────────────────────────
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
  // The phase is now the Prove-It phase: everything hinges on whether this
  // position has ever CLOSED above entry.
  const proven = proveItIsProven(pos);
  let phase;
  if (pos.exit_armed) {
    phase = { key: 'EXITING', label: 'Exiting', color: '#f43f5e', note: 'An armed exit is live at the broker.' };
  } else if (powerHold) {
    phase = { key: 'POWER_HOLD', label: `D${daysHeld} · Power Hold`, color: '#8b5cf6', note: 'Discretionary exits suppressed; a 30% disaster stop is the only backstop.' };
  } else if (!proven) {
    const band = (proveItP1ThresholdPct(daysHeld) * 100).toFixed(1);
    phase = {
      key: 'PROVE_IT_P1', label: `D${daysHeld} · Unproven`, color: '#3b82f6',
      note: `Never closed above entry — a ${band}% reverse from entry arms an exit.`,
    };
  } else if (peakPct < C.PROVE_IT_P2_ARM_GAIN_PCT * 100) {
    phase = {
      key: 'PROVE_IT_P2_UNARMED', label: `D${daysHeld} · Proven`, color: '#0ea5e9',
      note: `Closed above entry, but the peak has not reached +${(C.PROVE_IT_P2_ARM_GAIN_PCT * 100).toFixed(1)}% `
          + 'so the give-back floor has not armed — the trailing stop is the protection.',
    };
  } else if (daysHeld >= C.RANK_REPLACE_MIN_DAYS) {
    phase = {
      key: 'ROTATION', label: `D${daysHeld} · Rotation window`, color: '#a78bfa',
      note: `Give-back floor locked at ${(C.PROVE_IT_P2_FLOOR_PCT * 100).toFixed(1)}% of entry; Rank & Replace is live.`,
    };
  } else {
    phase = {
      key: 'PROVE_IT_P2', label: `D${daysHeld} · Floor locked`, color: '#10b981',
      note: `Went green and held it — the floor at ${(C.PROVE_IT_P2_FLOOR_PCT * 100).toFixed(1)}% of entry stops this becoming a real loss.`,
    };
  }

  // Most urgent rule drives the row-level colour.
  const headline = [...rules].sort(
    (a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)
  )[0];

  return { phase, rules, headline };
}

/** Plain-text summary used as the native tooltip on the compact cell. */
export function rulesTooltip(ticker, phase, rules, lifecycle = null) {
  const lines = [`${ticker} — ${phase.label}`, phase.note, ''];
  for (const r of rules) {
    const m = STATE_META[r.state];
    lines.push(`${m.icon} ${m.label.padEnd(10)} ${r.name} — ${r.headline}`);
  }
  if (lifecycle) {
    if (lifecycle.past?.length) {
      lines.push('', 'SO FAR');
      for (const e of lifecycle.past) {
        lines.push(`  ${e.icon} ${e.label}${e.when ? ` (${e.when})` : ''}`);
      }
    }
    if (lifecycle.next?.length) {
      lines.push('', 'NEXT');
      for (const e of lifecycle.next) {
        lines.push(`  ${e.icon} ${e.when} — ${e.label}`);
      }
    }
  }
  lines.push('', 'Click the row for the full risk ladder.');
  return lines.join('\n');
}

// ── Lifecycle: what has happened, where we are, what happens next ─────────────

/**
 * The fixed day-indexed track every position walks. Derived from the evaluation
 * order in `docs/sell_logic.md` — each entry is the set of rules that owns the
 * position during that day range.
 */
export const LIFECYCLE_TRACK = [
  {
    key: 'PROVE_IT_P1', label: 'Unproven', short: 'P1', order: 0,
    owns: 'Prove-It Phase 1 — a fixed band below entry',
  },
  {
    key: 'PROVE_IT_P2_UNARMED', label: 'Proven', short: 'P2', order: 1,
    owns: 'Closed above entry; give-back floor not yet armed',
  },
  {
    key: 'PROVE_IT_P2', label: 'Floor locked', short: 'P2+', order: 2,
    owns: 'Prove-It Phase 2 — give-back floor at ' + (RULES_CONFIG.PROVE_IT_P2_FLOOR_PCT * 100).toFixed(1) + '% of entry',
  },
  {
    key: 'ROTATION', label: 'Rotation', short: `D${RULES_CONFIG.RANK_REPLACE_MIN_DAYS}+`, order: 3,
    owns: 'Floor locked + Rank & Replace',
  },
];

const fmtDate = (d) => {
  if (!d) return null;
  const dt = d instanceof Date ? d : new Date(d);
  if (isNaN(dt.getTime())) return null;
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};
const fmtTime = (d) => {
  if (!d) return null;
  const dt = d instanceof Date ? d : new Date(d);
  if (isNaN(dt.getTime())) return null;
  return dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
};

/**
 * Build the three things an operator needs to read off a position at a glance:
 *
 *   past[]    what has already happened to it, oldest first
 *   track[]   the day-indexed phase track with done / current / upcoming status
 *   next[]    what happens next — both the next scheduled milestone and the
 *             nearest price level that would act on it
 *
 * Pure. Everything is derived from the position row plus the already-evaluated
 * rules, so it can never disagree with the Risk Rule Ladder.
 *
 * @param {object} pos      a row from /api/portfolio positions[]
 * @param {object} evald    the return value of evaluatePositionRules()
 * @param {number} daysHeld NYSE trading days since entry
 * @param {number} daysSinceHwm trading days since the last new high
 */
export function buildLifecycle(pos, evald, daysHeld, daysSinceHwm) {
  const C = RULES_CONFIG;
  const { rules, phase } = evald;

  const buy = num(pos.buy_price) ?? 0;
  const price = num(pos.current_price) ?? buy;
  const hwm = num(pos.hwm_price) ?? buy;
  const gainPct = buy > 0 ? (price / buy - 1) * 100 : 0;
  const peakPct = num(pos.highest_unrealized_pct) ?? 0;
  const powerHold = !!(pos.power_hold ?? pos.is_power_hold);
  const effPct = num(pos.stop_loss_pct) ?? C.STOP_LOSS_PCT;
  const rung = trailLadderRung(gainPct, powerHold, effPct);

  // ── What has already happened ───────────────────────────────────────────────
  const past = [];

  past.push({
    key: 'entry',
    icon: '🟢',
    label: `Bought ${pos.shares ?? '?'} sh @ $${buy.toFixed(2)}`,
    when: fmtDate(pos.buy_date),
    detail: pos.buy_reason || 'CANSLIM breakout entry',
    tone: 'neutral',
  });

  if (pos.closed_above_entry === true) {
    past.push({
      key: 'follow_through',
      icon: '✅',
      label: 'Closed above entry — follow-through confirmed',
      detail: 'Promoted to Prove-It Phase 2: the breakout proved itself on a daily close, so the stop now \nanchors to the peak instead of to entry.',
      tone: 'good',
    });
  } else if (pos.closed_above_entry === false && daysHeld >= 1) {
    past.push({
      key: 'no_follow_through',
      icon: '⚠️',
      label: 'Has never closed above entry',
      detail: `Prove-It Phase 1 stays live while this is true — a ${(C.PROVE_IT_P1_LATER_PCT * 100).toFixed(1)}% \nreverse from entry arms an exit.`,
      tone: 'bad',
    });
  }

  if (pos.breakout_verdict) {
    const passed = String(pos.breakout_verdict).toUpperCase() === 'PASS';
    past.push({
      key: 'verdict',
      icon: passed ? '✅' : '❌',
      label: `Day-${C.BREAKOUT_VERDICT_DAY} verdict: ${String(pos.breakout_verdict).toUpperCase()}`,
      when: `D${C.BREAKOUT_VERDICT_DAY}`,
      detail: passed
        ? `Confirmed breakout. Rank & Replace needs a ${C.RANK_REPLACE_THRESHOLD}-point better candidate to rotate it out.`
        : `Failed confirmation. Rank & Replace only needs a ${C.RANK_REPLACE_FAIL_THRESHOLD}-point better candidate to rotate it out.`,
      tone: passed ? 'good' : 'bad',
    });
  }

  if (hwm > buy) {
    const givenBack = hwm > 0 ? (hwm - price) / hwm * 100 : 0;
    past.push({
      key: 'hwm',
      icon: '📈',
      label: `Peaked at $${hwm.toFixed(2)} (+${peakPct.toFixed(1)}%)`,
      when: fmtDate(pos.hwm_date) || (daysSinceHwm != null ? `${daysSinceHwm}d ago` : null),
      detail: givenBack > 0.05
        ? `Currently ${givenBack.toFixed(1)}% below that peak.`
        : 'Trading at its high-water mark.',
      tone: givenBack > 3 ? 'bad' : 'good',
    });
  }

  if (rung.kind === 'profit_lock') {
    past.push({
      key: 'profit_lock',
      icon: '🔒',
      label: `Profit lock armed at +${rung.armedThreshold}%`,
      detail: `The trailing stop has tightened to ${(rung.pct * 100).toFixed(1)}% from the peak, `
            + `locking in at $${(hwm * (1 - rung.pct)).toFixed(2)}.`,
      tone: 'good',
    });
  }

  if (powerHold) {
    past.push({
      key: 'power_hold',
      icon: '💪',
      label: 'Power Hold engaged',
      when: pos.power_hold_expiry ? `until ${fmtDate(pos.power_hold_expiry)}` : null,
      detail: `Gained +${C.POWER_HOLD_GAIN_PCT}% inside ${C.POWER_HOLD_TRIGGER_DAYS} days. Discretionary exits are `
            + `suppressed and the stop is widened to ${(C.POWER_HOLD_TRAIL_PCT * 100).toFixed(0)}%.`,
      tone: 'good',
    });
  }

  if (pos.exit_armed) {
    past.push({
      key: 'armed',
      icon: '🔴',
      label: 'Exit armed at the broker',
      when: fmtTime(pos.exit_armed_at),
      detail: pos.exit_armed_reason || 'A loss rule fired and placed a tight trailing exit.',
      tone: 'bad',
    });
  }

  // ── Where it is now ─────────────────────────────────────────────────────────
  // The track is ordered by proof, not by calendar day: a position advances only
  // by closing above entry and then by reaching the arming gain. A position can
  // therefore sit in the first segment indefinitely, which is the point.
  const currentOrder = LIFECYCLE_TRACK.find((s) => s.key === phase.key)?.order;
  const track = LIFECYCLE_TRACK.map((seg) => {
    let status;
    if (seg.key === phase.key) status = 'current';
    else if (currentOrder == null) status = 'upcoming';
    else status = seg.order < currentOrder ? 'done' : 'upcoming';
    return { ...seg, status };
  });
  const offTrack = !LIFECYCLE_TRACK.some((s) => s.key === phase.key);

  // ── What happens next ───────────────────────────────────────────────────────
  const next = [];

  if (pos.exit_armed) {
    const armedAt = pos.exit_armed_at ? new Date(pos.exit_armed_at) : null;
    const deadline = armedAt ? new Date(armedAt.getTime() + C.ARMED_EXIT_DEADLINE_HOURS * 3.6e6) : null;
    const hoursLeft = deadline ? (deadline.getTime() - Date.now()) / 3.6e6 : null;
    next.push({
      key: 'armed_resolution',
      icon: '🔴',
      when: 'Now',
      label: `Selling on a ${(C.ARMED_EXIT_TRAIL_PCT * 100).toFixed(2)}% trail`,
      detail: deadline
        ? (hoursLeft > 0
            ? `If it does not fill, the agent market-sells at ${fmtTime(deadline)} (${hoursLeft.toFixed(1)}h away).`
            : 'The deadline has passed — a forced market sell happens on the next cycle.')
        : 'A forced market sell follows if the trail does not fill.',
      tone: 'bad',
    });
  } else {
    // Nearest price level that would actually act on the position.
    const live = rules.filter(
      (r) => r.level != null && r.distancePct != null
        && [STATE.TRIGGERED, STATE.WATCH, STATE.ACTIVE].includes(r.state)
    ).sort((a, b) => a.distancePct - b.distancePct);

    if (live.length) {
      const n = live[0];
      next.push({
        key: 'nearest_trigger',
        icon: n.state === STATE.TRIGGERED ? '🔴' : n.state === STATE.WATCH ? '🟡' : '🎯',
        when: n.state === STATE.TRIGGERED ? 'This cycle' : 'On a move down',
        label: n.state === STATE.TRIGGERED
          ? `${n.name} has triggered — acting this cycle`
          : `${n.name} fires at $${n.level.toFixed(2)}`,
        detail: n.state === STATE.TRIGGERED
          ? n.headline
          : `${n.distancePct.toFixed(1)}% below the current $${price.toFixed(2)}. `
            + (n.id === 'trailing_stop'
                ? 'This is a resting broker order — it fills even if the agent is offline.'
                : 'The agent checks this every 15 minutes and arms a tight trailing exit.'),
        tone: n.state === STATE.ACTIVE ? 'neutral' : 'bad',
      });
    }

    // Next advance along the proof track. This is not a calendar event any more —
    // it is a price event, so it is stated as a price rather than a session count.
    if (!powerHold && buy > 0) {
      if (!proveItIsProven(pos)) {
        next.push({
          key: 'next_phase',
          icon: '🎯',
          when: `On a daily close above $${buy.toFixed(2)}`,
          label: 'Promotion to Prove-It Phase 2',
          detail: 'The stop stops anchoring to entry and starts anchoring to the peak. '
                + 'Only a CLOSE counts — an intraday poke above entry does not promote it.',
          tone: 'good',
        });
      } else if (peakPct < C.PROVE_IT_P2_ARM_GAIN_PCT * 100) {
        const armPrice = buy * (1 + C.PROVE_IT_P2_ARM_GAIN_PCT);
        next.push({
          key: 'next_phase',
          icon: '🔒',
          when: `At $${armPrice.toFixed(2)} (+${(C.PROVE_IT_P2_ARM_GAIN_PCT * 100).toFixed(1)}% peak)`,
          label: 'Give-back floor arms',
          detail: `A floor locks in at $${(buy * (1 + C.PROVE_IT_P2_FLOOR_PCT)).toFixed(2)}, so a trade that `
                + 'went green cannot turn into a real loss.',
          tone: 'good',
        });
      }
    }

    // The day-3 verdict is a scheduled, one-off event worth calling out.
    if (!pos.breakout_verdict && daysHeld < C.BREAKOUT_VERDICT_DAY) {
      const inDays = C.BREAKOUT_VERDICT_DAY - daysHeld;
      next.push({
        key: 'verdict_due',
        icon: '⚖️',
        when: `In ${inDays} session${inDays === 1 ? '' : 's'} (D${C.BREAKOUT_VERDICT_DAY})`,
        label: 'Day-3 breakout verdict is recorded',
        detail: 'A FAIL verdict lowers the bar for Rank & Replace to rotate this position out.',
        tone: 'neutral',
      });
    }

    // Profit lock is the next thing that happens on the way UP.
    if (rung.kind !== 'profit_lock' && !powerHold && C.TRAIL_PROFIT_TIERS.length) {
      const [threshold, lockPct] = C.TRAIL_PROFIT_TIERS[0];
      if (gainPct < threshold) {
        next.push({
          key: 'profit_lock_pending',
          icon: '🔒',
          when: `At +${threshold}% (${(threshold - gainPct).toFixed(1)}% away)`,
          label: 'HWM profit lock arms',
          detail: `The trailing stop tightens to ${(lockPct * 100).toFixed(1)}% from the peak so gains are not given back.`,
          tone: 'good',
        });
      }
    }

    // Staleness no longer sells to cash — it discounts the Rank & Replace bar.
    if (daysHeld >= C.STALE_EXIT_MIN_DAYS_HELD && daysSinceHwm != null && !powerHold) {
      const left = C.STALE_EXIT_DAYS - daysSinceHwm;
      if (left > 0 && left <= 5) {
        next.push({
          key: 'stale_due',
          icon: '⏳',
          when: `In ${left} session${left === 1 ? '' : 's'} without a new high`,
          label: 'Counts as stale — rotation bar is discounted',
          detail: `${daysSinceHwm} of ${C.STALE_EXIT_DAYS} stale sessions used. Staleness on its own no longer `
                + `sells: the bar for Rank & Replace simply drops to ${C.RANK_REPLACE_FAIL_THRESHOLD} pts, so the `
                + 'position is released only when there is somewhere better to put the money.',
          tone: 'neutral',
        });
      }
    }
  }

  return { past, track, next, phase, offTrack };
}
