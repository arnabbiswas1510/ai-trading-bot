/**
 * exitDetails.js — single source of truth for interpreting `sell_reason`.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `getCleanExitReason()` was previously duplicated in DashboardView.jsx and
 * TradesView.jsx, and the two copies had drifted apart: the Dashboard copy knew
 * about Thesis Stop, Early Dollar Stop, Kill-switch and the Tier rules, while
 * the Trade History copy still classified every reconciled exit as either
 * "Profit Target (+25%)" or "Stop Loss (-7%)".
 *
 * Both of those labels were fabrications:
 *   - The fixed +25% profit target was REMOVED from the bot
 *     (decisions/2026-07-23_backtester-accuracy-rewrite.md: "No fixed profit
 *     target: live bot removed this"). No trade can exit on a rule that does
 *     not exist.
 *   - "-7%" has not been the stop for some time. The base trail is 8.25-10%
 *     and tightens to 1.5% once a position locks in at +5%
 *     (decisions/2026-08-22_hwm-profit-lock-arm-5pct.md).
 * A trade that closed at -2.77% was being labelled "Stop Loss (-7%)", which is
 * simply a number the UI invented. Everything here is either read out of the
 * stored reason string or derived arithmetically from stored fills; nothing is
 * guessed.
 *
 * WHAT IS AND IS NOT RECORDED
 * ---------------------------
 * `trade_history` stores the exit as a free-text `sell_reason` plus the fill.
 * Agent-initiated exits embed their numbers in that text (the Intraday Loss
 * Minimiser records the intraday high and the current price, Rank & Replace
 * records both scores), so those can be parsed back out.
 *
 * Broker-side exits cannot. When an IBKR GTC TRAIL order fires, the agent only
 * learns about it at the next reconcile and writes the bare string
 * "Trailing stop (IBKR GTC TRAIL order)" — no trail percentage, no high-water
 * mark, no trigger price, and frequently no intraday timestamp. That gap is
 * reported honestly by `unrecordedFields()` rather than papered over.
 */

// ── Who actually pulled the trigger ──────────────────────────────────────────
export const EXECUTOR = {
  BOT: {
    key: 'BOT',
    label: 'Bot (execution agent)',
    blurb: 'The execution agent evaluated a rule and submitted the sell order itself.',
  },
  BROKER: {
    key: 'BROKER',
    label: 'IBKR (resting order)',
    blurb:
      'A GTC order resting at IBKR filled without the agent being involved. ' +
      'The agent discovered the fill afterwards during reconciliation.',
  },
  MANUAL: {
    key: 'MANUAL',
    label: 'Manual / human',
    blurb: 'Closed outside the automated rules — in TWS, or via a force-sell script.',
  },
  UNKNOWN: {
    key: 'UNKNOWN',
    label: 'Unattributed',
    blurb: 'The stored reason does not identify who closed the position.',
  },
};

/**
 * Rule table. Order matters — the first match wins, so specific patterns must
 * precede general ones ("early dollar stop" before "dollar stop", and every
 * named agent rule before the generic "trailing stop" catch-all).
 */
const RULES = [
  {
    match: (s) => s.includes('prove-it stop') && s.includes('phase 2'),
    label: 'Prove-It Stop · Phase 2',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'The position had closed above entry and reached the arming gain, then gave the move back. '
        + 'The give-back floor closed it just below entry so a trade that went green did not become a real loss.',
  },
  {
    match: (s) => s.includes('prove-it stop'),
    label: 'Prove-It Stop · Phase 1',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'The breakout never closed above its entry price and then reversed through the Phase 1 band below entry, '
        + 'so it was cut before the loss could widen.',
  },
  {
    match: (s) => s.includes('rank & replace') || s.includes('rank and replace'),
    label: 'Rank & Replace',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'A fresh breakout outscored this holding after day 7, so capital was rotated into it.',
  },
  {
    match: (s) => s.includes('intraday loss minimiser') || s.includes('intraday minimiser'),
    label: 'Intraday Loss Minimiser',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'The position was flat or failing by day 3, so the agent sold into a small pullback from the intraday high rather than holding for the trailing stop. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('early loss kill-switch') || s.includes('kill-switch') || s.includes('kill_switch'),
    label: 'Early Loss Kill-switch',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'Down more than the day-0 tolerance on the entry day itself — cut immediately rather than given room. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('early dollar stop'),
    label: 'Early Dollar Stop',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'The open loss breached the slot-derived dollar cap, which acts as a backstop for a position that never rose enough for the peak-anchored trail to help. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('thesis stop'),
    label: 'Thesis Stop',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'The breakout thesis was invalidated on an ATR-scaled basis during days 2-5. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('time-stop') || (s.includes('mandatory') && s.includes('time')),
    label: 'Time-Stop',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Held its maximum allotted time without earning an extension.',
  },
  {
    match: (s) => s.includes('break-even') || s.includes('hwm break'),
    label: 'Break-Even Stop',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Retreated to the entry price after showing a gain; closed flat rather than allowed to turn into a loss.',
  },
  {
    match: (s) => s.includes('floor break') || s.includes('floor_break') || s.includes('consolidation floor'),
    label: 'Floor Break',
    executor: EXECUTOR.BOT,
    tone: 'bad',
    what: 'Lost the floor of its post-breakout consolidation range.',
  },
  {
    match: (s) => s.includes('tier 3') || s.includes('hard time-stop'),
    label: 'Tier 3 Time-Stop',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Plateau rotation, tier 3 — hard time limit reached.',
  },
  {
    match: (s) => s.includes('tier 2') || s.includes('score upgrade'),
    label: 'Tier 2 Score Upgrade',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Plateau rotation, tier 2 — a materially better-scoring candidate was available.',
  },
  {
    match: (s) => s.includes('tier 1') || s.includes('rs decay'),
    label: 'Tier 1 RS Decay',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Plateau rotation, tier 1 — relative strength decayed while the position went nowhere.',
  },
  {
    match: (s) => s.includes('ema-21') || s.includes('exit ma') || s.includes('moving average'),
    label: 'EMA-21 Exit',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Closed below the EMA-21 buffer at end of day, after the breakout consolidation window had passed. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('stale rotation') || s.includes('plateau rotation') || s.includes('plateau exit'),
    label: 'Plateau Exit',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Went sideways long enough that the capital was better deployed elsewhere. (Retired rule — kept so historical trades still label correctly; see docs/retired_code.md.)',
  },
  {
    match: (s) => s.includes('partial_sell') || s.includes('partial sell'),
    label: 'Partial Sell',
    executor: EXECUTOR.BOT,
    tone: 'neutral',
    what: 'Part of the position was sold to correct share count or cash usage — not a rule-driven exit.',
  },
  {
    match: (s) => s.includes('force sell') || s.includes('user request') || s.includes('manual_rotation') || s.includes('manual rotation'),
    label: 'Manual Force Sell',
    executor: EXECUTOR.MANUAL,
    tone: 'neutral',
    what: 'Closed on demand, bypassing the rule set.',
  },
  {
    match: (s) => s.includes('manual close'),
    label: 'Manual Close in IBKR',
    executor: EXECUTOR.MANUAL,
    tone: 'neutral',
    what: 'Closed directly in IBKR. The agent found the position gone at reconciliation and back-filled the record.',
  },
  {
    // Must stay near the end: several agent rules also mention a trailing stop
    // in passing, and they should classify as the specific rule, not as this.
    match: (s) => s.includes('trailing stop') || s.includes('trail order') || s.includes('trail triggered'),
    label: 'Trailing Stop',
    executor: EXECUTOR.BROKER,
    tone: 'bad',
    what: 'The GTC trailing stop resting at IBKR was hit and filled by the broker. The agent was not consulted; it reconciled the fill afterwards.',
  },
  {
    match: (s) => s.includes('order filled') || s.includes('reconciled'),
    label: 'Reconciled Fill',
    executor: EXECUTOR.BROKER,
    tone: 'neutral',
    what: 'A fill was detected at IBKR that the agent did not initiate in this session.',
  },
];

const num = (v) => {
  const n = typeof v === 'string' ? parseFloat(v) : v;
  return Number.isFinite(n) ? n : null;
};

/** Pull any `$123.45` amounts out of the reason text, in order of appearance. */
function extractDollarValues(raw) {
  const out = [];
  const re = /\$\s?(-?[\d,]+(?:\.\d+)?)/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    const v = parseFloat(m[1].replace(/,/g, ''));
    if (Number.isFinite(v)) out.push(v);
  }
  return out;
}

/**
 * Structured facts embedded in the reason string by the agent that wrote it.
 * Returns [{label, value}]. Empty when the reason carries no numbers, which is
 * the normal case for broker-side exits.
 */
export function extractReasonFacts(raw) {
  if (!raw) return [];
  const facts = [];
  const lower = raw.toLowerCase();

  if (lower.includes('intraday loss minimiser')) {
    const dollars = extractDollarValues(raw);
    if (dollars.length >= 1) facts.push({ label: 'Intraday high', value: dollars[0], kind: 'money' });
    if (dollars.length >= 2) facts.push({ label: 'Price when sold', value: dollars[1], kind: 'money' });
    if (dollars.length >= 2 && dollars[0] > 0) {
      facts.push({
        label: 'Pullback from high',
        value: ((dollars[1] - dollars[0]) / dollars[0]) * 100,
        kind: 'percent',
      });
    }
    const day = raw.match(/day\s+(\d+)/i);
    if (day) facts.push({ label: 'Day of hold', value: parseInt(day[1], 10), kind: 'int' });
    const verdict = raw.match(/verdict\s+([A-Z]+)/);
    if (verdict) facts.push({ label: 'Day-3 verdict', value: verdict[1], kind: 'text' });
  }

  if (lower.includes('rank & replace') || lower.includes('rank and replace')) {
    const replacement = raw.match(/breakout\s+([A-Z.]{1,6})/);
    if (replacement) facts.push({ label: 'Rotated into', value: replacement[1], kind: 'text' });
    const newScore = raw.match(/new trigger:\s*([\d.]+)/i);
    if (newScore) facts.push({ label: 'New trigger score', value: parseFloat(newScore[1]), kind: 'number' });
    const heldScore = raw.match(/held\s*M[ₜt]?\s*=\s*([\d.]+)/i);
    if (heldScore) facts.push({ label: 'Held momentum score', value: parseFloat(heldScore[1]), kind: 'number' });
    const day = raw.match(/day\s+(\d+)\+/i);
    if (day) facts.push({ label: 'Eligible from day', value: parseInt(day[1], 10), kind: 'int' });
  }

  if (lower.includes('vol surge')) {
    const vs = raw.match(/vol surge\s*([\d.]+)x/i);
    if (vs) facts.push({ label: 'Entry volume surge', value: `${vs[1]}x`, kind: 'text' });
  }

  // Risk-state context appended by the agent at reconcile time. Before this was
  // recorded, a broker trailing-stop exit was a bare label with no numbers at
  // all — see _exit_context_suffix() in execution_agent.py. Trades closed before
  // that change simply have none of these, which is why every extraction here is
  // individually optional.
  const trail = raw.match(/trail\s+([\d.]+)%/i);
  if (trail) facts.push({ label: 'Trail in force', value: parseFloat(trail[1]), kind: 'percent' });

  const hwm = raw.match(/HWM\s+\$([\d,]+(?:\.\d+)?)/i);
  if (hwm) {
    facts.push({ label: 'High-water mark', value: parseFloat(hwm[1].replace(/,/g, '')), kind: 'money' });
    const hwmDate = raw.match(/HWM\s+\$[\d,.]+\s+set\s+([\d-]+)/i);
    if (hwmDate) facts.push({ label: 'Peak set on', value: hwmDate[1], kind: 'text' });
  }

  const trigger = raw.match(/implied trigger\s+\$([\d,]+(?:\.\d+)?)/i);
  if (trigger) {
    facts.push({
      label: 'Implied trigger',
      value: parseFloat(trigger[1].replace(/,/g, '')),
      kind: 'money',
    });
  }

  const dayOf = raw.match(/day\s+(\d+)\s+of hold/i);
  if (dayOf) facts.push({ label: 'Day of hold', value: parseInt(dayOf[1], 10), kind: 'int' });

  const peak = raw.match(/peak\s+([+-][\d.]+)%/i);
  if (peak) facts.push({ label: 'Peak unrealised', value: parseFloat(peak[1]), kind: 'percent' });

  const armed = raw.match(/armed at\s+\$([\d,]+(?:\.\d+)?)/i);
  if (armed) {
    facts.push({
      label: 'Exit armed at',
      value: parseFloat(armed[1].replace(/,/g, '')),
      kind: 'money',
    });
  }

  if (/power hold active/i.test(raw)) {
    facts.push({ label: 'Power hold', value: 'Active', kind: 'text' });
  }

  return facts;
}

/**
 * Fields a viewer might reasonably expect that are genuinely absent from the
 * record. Naming them is the point: a blank space reads as "nothing happened",
 * whereas an explicit "not recorded" tells the operator the data was never
 * captured and that the number cannot be recovered from this screen.
 */
export function unrecordedFields(trade, classification) {
  const missing = [];
  if (classification.executor.key === 'BROKER') {
    // Only report these as absent when they really are. Exits reconciled after
    // the agent started capturing risk state carry them in the reason string,
    // so checking the parsed facts keeps this honest in both directions rather
    // than permanently accusing every broker exit of being undocumented.
    const present = new Set(extractReasonFacts(trade.exit_reason).map((f) => f.label));
    if (!present.has('Trail in force')) {
      missing.push('Trail percentage in force when the order filled');
    }
    if (!present.has('High-water mark')) {
      missing.push('High-water mark the trail was anchored to');
    }
    if (!present.has('Implied trigger')) {
      missing.push('Stop trigger price');
    }
  }
  if (!trade.sell_date) {
    missing.push('Exit timestamp (no fill found in the TWS session)');
  } else if (isMidnightStamp(trade.sell_date)) {
    missing.push('Exact fill time (only the date was recorded)');
  }
  if ((trade.exit_reason || '').includes('PRICE_UNCERTAIN')) {
    missing.push('Verified fill price — the stored price is an FMP placeholder');
  }
  return missing;
}

/** True when a timestamp is exactly midnight, i.e. a date with no real time. */
export function isMidnightStamp(value) {
  if (!value) return false;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return false;
  return d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
}

/**
 * Whole days held, or null when it genuinely cannot be determined.
 *
 * Broker-side exits store `sell_date` as a date-only midnight stamp, so a
 * position bought at 13:45 and stopped out the same afternoon has a "sell"
 * timestamp 13 hours BEFORE its buy timestamp (FROG and OII both look like
 * this). Subtracting raw timestamps yields a negative duration for what was
 * really a same-day exit. When the stamp carries no time, compare calendar
 * dates instead — that is the full precision the record actually has.
 */
export function holdDays(trade) {
  if (!trade.buy_date || !trade.sell_date) return null;
  const a = new Date(trade.buy_date);
  const b = new Date(trade.sell_date);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;

  if (isMidnightStamp(trade.sell_date)) {
    const dayA = Date.UTC(a.getUTCFullYear(), a.getUTCMonth(), a.getUTCDate());
    const dayB = Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate());
    const days = Math.round((dayB - dayA) / 86400000);
    return days >= 0 ? days : null;
  }

  const days = Math.floor((b - a) / 86400000);
  return days >= 0 ? days : null;
}

/**
 * Classify a stored exit reason.
 * Returns { label, executor, tone, what, raw, priceUncertain }.
 */
export function classifyExit(rawReason) {
  const raw = (rawReason || '').trim();
  if (!raw) {
    return {
      label: 'Unrecorded',
      executor: EXECUTOR.UNKNOWN,
      tone: 'neutral',
      what: 'No exit reason was stored against this trade.',
      raw: '',
      priceUncertain: false,
    };
  }
  const lower = raw.toLowerCase();
  const priceUncertain = raw.includes('PRICE_UNCERTAIN');

  for (const rule of RULES) {
    if (rule.match(lower)) {
      return {
        label: rule.label,
        executor: rule.executor,
        tone: rule.tone,
        what: rule.what,
        raw,
        priceUncertain,
      };
    }
  }
  return {
    label: raw.length > 42 ? `${raw.slice(0, 42)}…` : raw,
    executor: EXECUTOR.UNKNOWN,
    tone: 'neutral',
    what: 'This reason does not match any known exit rule; the stored text is shown verbatim.',
    raw,
    priceUncertain,
  };
}

/** Arithmetic derived purely from the stored fills — never estimated. */
export function deriveTradeMath(trade) {
  const shares = num(trade.shares);
  const buy = num(trade.buy_price);
  const sell = num(trade.sell_price);
  const costBasis = shares !== null && buy !== null ? shares * buy : null;
  const proceeds = shares !== null && sell !== null ? shares * sell : null;
  return {
    shares,
    buyPrice: buy,
    sellPrice: sell,
    costBasis,
    proceeds,
    pnl: num(trade.profit_loss),
    returnPct: num(trade.percent_return),
    perShare: buy !== null && sell !== null ? sell - buy : null,
    days: holdDays(trade),
  };
}

/** Badge class matching the existing `.badge-*` conventions. */
export function toneToBadgeClass(tone, pnl) {
  if (typeof pnl === 'number') {
    if (pnl > 0) return 'badge-success';
    if (pnl < 0) return 'badge-danger';
  }
  if (tone === 'bad') return 'badge-danger';
  if (tone === 'good') return 'badge-success';
  return 'badge-warning';
}
