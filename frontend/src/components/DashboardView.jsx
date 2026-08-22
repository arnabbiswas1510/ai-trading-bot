import React, { useState, useEffect } from 'react';
import useSortableTable from '../hooks/useSortableTable';
import {
  evaluatePositionRules,
  rulesTooltip,
  buildLifecycle,
  STATE,
  STATE_META,
  RULES_CONFIG,
} from '../lib/positionRules';
import { 
  TrendingUp, 
  TrendingDown, 
  DollarSign, 
  Activity, 
  Award,
  Briefcase,
  History,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  Clock,
  TrendingDown as StopIcon,
  Zap,
  Calendar
} from 'lucide-react';

// ── Constants mirrored from execution_agent.py env defaults ──────────────────
// Fallback only — the agent writes the live per-position value into
// portfolio_positions.stop_loss_pct (ATR-scaled, 10% floor / 12% cap).
const STOP_LOSS_PCT  = RULES_CONFIG.STOP_LOSS_PCT;
// Trading days without a new high water mark before the plateau exit fires.
const PLATEAU_DAYS   = RULES_CONFIG.STALE_EXIT_DAYS;

// ── Stable module-level sort-key functions ────────────────────────────────────
// Must be module-level so the === reference stays identical across renders
// (required for getSortIcon to light up the active-sort arrow).
// Mirrors display fallback: entry_final_score → entry_ai_rating → 0
const sortByConvictionPos = (p) => p.entry_final_score ?? p.entry_ai_rating ?? 0;
const sortByMarketValue   = (p) => (p.current_price || p.buy_price) * p.shares;
// Sort by risk urgency — higher = more urgent, so a descending sort puts armed and
// degraded positions at the top. STATE_URGENCY is the reverse of the display order.
const STATE_URGENCY = ['OFF', 'EXPIRED', 'SUPPRESSED', 'PENDING', 'ACTIVE', 'WATCH', 'DEGRADED', 'TRIGGERED', 'ARMED'];
const sortByLifecyclePos = (p) => {
  try { return STATE_URGENCY.indexOf(rulesFor(p).headline.state); }
  catch { return -1; }
};

// ── Helpers ──────────────────────────────────────────────────────────────────
function daysHeld(buyDate) {
  if (!buyDate) return 0;
  const buy = new Date(buyDate);
  const now = new Date();
  return Math.floor((now - buy) / (1000 * 60 * 60 * 24));
}

// ── NYSE trading-day calendar ──────────────────────────────────────────────────
// Returns a Set of holiday date-strings "YYYY-MM-DD" for a given year.
// Computed algorithmically — no external package required.
function _nyseHolidays(year) {
  const holidays = new Set();

  // Shift Sat → Fri, Sun → Mon for observed holiday
  const observed = (d) => {
    const day = d.getDay(); // 0=Sun,6=Sat
    if (day === 6) { d.setDate(d.getDate() - 1); }
    if (day === 0) { d.setDate(d.getDate() + 1); }
    return d;
  };
  const iso = (d) => d.toISOString().slice(0, 10);

  // nth weekday: weekday 1=Mon..5=Fri, n=1,2,...
  const nthWeekday = (y, month, weekday, n) => {
    const d = new Date(y, month - 1, 1);
    let count = 0;
    while (d.getMonth() === month - 1) {
      if (d.getDay() === weekday) { count++; if (count === n) return new Date(d); }
      d.setDate(d.getDate() + 1);
    }
  };
  const lastWeekday = (y, month, weekday) => {
    const d = new Date(y, month, 0); // last day of month
    while (d.getDay() !== weekday) d.setDate(d.getDate() - 1);
    return new Date(d);
  };
  // Easter via Anonymous Gregorian algorithm
  const easter = (y) => {
    const a = y % 19, b = Math.floor(y / 100), c = y % 100;
    const d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25);
    const g = Math.floor((b - f + 1) / 3);
    const h = (19 * a + b - d - g + 15) % 30;
    const i = Math.floor(c / 4), k = c % 4;
    const l = (32 + 2 * e + 2 * i - h - k) % 7;
    const m = Math.floor((a + 11 * h + 22 * l) / 451);
    const month = Math.floor((h + l - 7 * m + 114) / 31);
    const day   = ((h + l - 7 * m + 114) % 31) + 1;
    return new Date(y, month - 1, day);
  };

  // New Year's Day
  holidays.add(iso(observed(new Date(year, 0, 1))));
  // MLK Day — 3rd Monday of January
  holidays.add(iso(nthWeekday(year, 1, 1, 3)));
  // Presidents' Day — 3rd Monday of February
  holidays.add(iso(nthWeekday(year, 2, 1, 3)));
  // Good Friday — 2 days before Easter
  const gf = easter(year); gf.setDate(gf.getDate() - 2);
  holidays.add(iso(gf));
  // Memorial Day — last Monday of May
  holidays.add(iso(lastWeekday(year, 5, 1)));
  // Juneteenth — Jun 19 observed (from 2022)
  if (year >= 2022) holidays.add(iso(observed(new Date(year, 5, 19))));
  // Independence Day — Jul 4 observed
  holidays.add(iso(observed(new Date(year, 6, 4))));
  // Labor Day — 1st Monday of September
  holidays.add(iso(nthWeekday(year, 9, 1, 1)));
  // Thanksgiving — 4th Thursday of November
  holidays.add(iso(nthWeekday(year, 11, 4, 4)));
  // Christmas — Dec 25 observed
  holidays.add(iso(observed(new Date(year, 11, 25))));

  return holidays;
}

// Cache holidays per year to avoid recomputing on every render
const _holidayCache = {};
function _getHolidays(year) {
  if (!_holidayCache[year]) _holidayCache[year] = _nyseHolidays(year);
  return _holidayCache[year];
}

/**
 * Count NYSE trading days in [start, end) — weekends and market holidays excluded.
 * start and end are Date objects or ISO date strings.
 */
function tradingDaysBetween(start, end) {
  const s = typeof start === 'string' ? new Date(start) : new Date(start);
  const e = typeof end   === 'string' ? new Date(end)   : new Date(end);
  // Normalise to midnight to avoid DST issues
  s.setHours(0, 0, 0, 0);
  e.setHours(0, 0, 0, 0);
  if (e <= s) return 0;

  let count = 0;
  const cur = new Date(s);
  while (cur < e) {
    const dow = cur.getDay(); // 0=Sun,6=Sat
    if (dow !== 0 && dow !== 6) {
      const isoStr = cur.toISOString().slice(0, 10);
      const holidays = _getHolidays(cur.getFullYear());
      if (!holidays.has(isoStr)) count++;
    }
    cur.setDate(cur.getDate() + 1);
  }
  return count;
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function activeProfitLockTier(pos) {
  const effPct = typeof pos?.stop_loss_pct === 'number' ? pos.stop_loss_pct : RULES_CONFIG.STOP_LOSS_PCT;
  if (pos?.power_hold || pos?.is_power_hold) return null;
  return RULES_CONFIG.TRAIL_PROFIT_TIERS.find(([, pct]) => pct != null && Math.abs(effPct - pct) < 1e-6) || null;
}

function getCleanExitReason(raw, pctReturn) {
  if (!raw) return 'Manual Close';
  const lower = raw.toLowerCase();

  if (lower.includes('rank & replace') || lower.includes('rank and replace')) {
    return 'Rank & Replace';
  }
  if (lower.includes('time-stop') || (lower.includes('mandatory') && lower.includes('time'))) {
    return 'Day 7 Time-Stop';
  }
  if (lower.includes('break-even') || lower.includes('hwm break')) {
    return 'Break-Even Stop';
  }
  if (lower.includes('floor break') || lower.includes('floor_break') || lower.includes('consolidation floor')) {
    return 'Floor Break';
  }
  if (lower.includes('tier 3') || lower.includes('hard time-stop')) {
    return 'Tier 3 Time-Stop';
  }
  if (lower.includes('tier 2') || lower.includes('score upgrade')) {
    return 'Tier 2 Score Upgrade';
  }
  if (lower.includes('tier 1') || lower.includes('rs decay')) {
    return 'Tier 1 RS Decay';
  }
  if (lower.includes('ema-21') || lower.includes('exit ma') || lower.includes('moving average')) {
    return 'EMA-21 Exit';
  }
  if (lower.includes('stale rotation') || lower.includes('plateau rotation') || lower.includes('plateau exit')) {
    return 'Plateau Exit';
  }
  if (lower.includes('early dollar stop') || lower.includes('dollar stop')) {
    return 'Early Dollar Stop';
  }
  if (lower.includes('thesis stop')) {
    return 'Thesis Stop';
  }
  if (lower.includes('early loss kill-switch') || lower.includes('kill-switch') || lower.includes('kill_switch')) {
    return 'Early Loss Kill-switch';
  }
  if (lower.includes('intraday loss minimiser') || lower.includes('intraday minimiser')) {
    return 'Intraday Minimiser';
  }
  if (lower.includes('force sell') || lower.includes('user request')) {
    return 'Manual Force Sell';
  }
  if (lower.includes('manual close')) {
    return 'Manual Close';
  }
  // GTC trailing stop fired at IBKR — the raw reason from the agent is
  // "Trailing stop (IBKR GTC TRAIL order)" — catch it cleanly here.
  if (lower.includes('trailing stop') || lower.includes('trail order')) {
    return pctReturn >= 24.0 ? 'Profit Target (+25%)' : 'Trailing Stop';
  }
  // Legacy: reconciliation note written by older agent versions
  if (lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
    return pctReturn >= 24.0 ? 'Profit Target (+25%)' : 'Trailing Stop';
  }

  return raw;
}

function getDetailedExitTooltip(raw, pctReturn) {
  if (!raw) return `Manual close at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  const lower = raw.toLowerCase();
  
  if (lower.includes('ema-21') || lower.includes('exit ma')) {
    return raw;
  }
  if (lower.includes('stale rotation') || lower.includes('plateau')) {
    return raw;
  }
  if (lower.includes('thesis stop')) {
    return raw;
  }
  if (lower.includes('early dollar stop') || lower.includes('dollar stop')) {
    return raw;
  }
  if (lower.includes('early loss kill-switch') || lower.includes('kill-switch')) {
    return raw;
  }
  if (lower.includes('intraday loss minimiser') || lower.includes('intraday minimiser')) {
    return raw;
  }
  if (lower.includes('force sell') || lower.includes('user request')) {
    return `Manual Force Sell executed at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  }
  if (lower.includes('manual close')) {
    return `Manual Close on IBKR reconciled at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  }
  // GTC trailing stop — return the raw reason (includes the stop %) plus the final return.
  if (lower.includes('trailing stop') || lower.includes('trail order')
      || lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
    if (pctReturn >= 24.0) {
      return `Profit Target Filled (+25.0% target) with final return of +${pctReturn.toFixed(2)}%`;
    } else {
      return `${raw}\nFinal return: ${pctReturn.toFixed(2)}%`;
    }
  }
  return `${raw} (${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}%)`;
}

// Derive the most urgent status badge for the compact column
function getStatusBadge(pos, days) {
  // Power Hold and Stale Rotation rules removed — only plateau exits are active.
  return null; // Normal — no special badge
}

// ── Risk-rule lifecycle ───────────────────────────────────────────────────────
/**
 * Evaluate every sell rule for a position using agent-equivalent day counts.
 *
 * The agent recomputes days_held live each cycle (trading_days_between), so the
 * DB column goes stale between cycles. We recompute here for the same reason —
 * showing a stale day count would mis-state which window a position is in.
 */
function rulesFor(pos, openPositions, equity) {
  const now = new Date();
  const tdHeld = tradingDaysBetween(pos.buy_date, now);
  const sinceHwm = pos.days_since_hwm != null
    ? pos.days_since_hwm
    : tradingDaysBetween(pos.hwm_date || pos.buy_date, now);
  return evaluatePositionRules(pos, tdHeld, sinceHwm, daysHeld(pos.buy_date), openPositions, equity);
}

/** Compact lifecycle cell: phase pill + one dot per rule, with a full tooltip. */
function LifecycleCell({ pos, openPositions, equity }) {
  const evald = rulesFor(pos, openPositions, equity);
  const { phase, rules } = evald;
  const now = new Date();
  const sinceHwm = pos.days_since_hwm != null
    ? pos.days_since_hwm
    : tradingDaysBetween(pos.hwm_date || pos.buy_date, now);
  const lifecycle = buildLifecycle(pos, evald, tradingDaysBetween(pos.buy_date, now), sinceHwm);
  const upNext = lifecycle.next[0];
  const rec = pos.rotation_recommendation;
  const recLabel = rec === 'TIER_1' ? 'T1'
                 : rec === 'TIER_2' ? 'T2'
                 : rec === 'PROGRESS_DEFICIT' ? 'PD'
                 : rec === 'FLOOR_BREAK' ? 'FB'
                 : rec === 'RS_DECAY' ? 'RS'
                 : rec === 'HARD_STOP' ? 'HS'
                 : rec === 'PARAM_DRIFT' ? 'PD'
                 : null;

  // Only the states that mean "something needs your attention" get a visible dot.
  // Pending / expired / suppressed rules are noise in a 1-line cell — they are
  // still in the tooltip and in full in the expanded panel.
  const notable = rules.filter(r =>
    r.state === STATE.ARMED || r.state === STATE.TRIGGERED ||
    r.state === STATE.DEGRADED || r.state === STATE.WATCH);

  return (
    <td title={rulesTooltip(pos.ticker, phase, rules, lifecycle)} style={{ whiteSpace: 'nowrap' }}>
      <span style={{
        display: 'inline-block', fontSize: '0.63rem', fontWeight: 800,
        padding: '0.12rem 0.4rem', borderRadius: '4px',
        color: phase.color, background: `${phase.color}1f`,
        border: `1px solid ${phase.color}55`, letterSpacing: '0.03em',
      }}>{phase.label}</span>

      {recLabel && (
        <span style={{
          marginLeft: '0.3rem', fontSize: '0.58rem', fontWeight: 800,
          padding: '0.1rem 0.28rem', borderRadius: '3px',
          background: 'rgba(244,63,94,0.14)', color: '#f43f5e',
          border: '1px solid rgba(244,63,94,0.4)',
        }}>{recLabel}</span>
      )}

      <div style={{ display: 'flex', gap: '0.25rem', marginTop: '0.28rem', alignItems: 'center', minHeight: '0.75rem' }}>
        {notable.length === 0 ? (
          <span style={{ fontSize: '0.62rem', color: '#10b981', fontWeight: 600 }}>
            ✓ all rules nominal
          </span>
        ) : notable.map(r => {
          const m = STATE_META[r.state];
          return (
            <span key={r.id} style={{
              fontSize: '0.58rem', fontWeight: 800, letterSpacing: '0.02em',
              padding: '0.08rem 0.3rem', borderRadius: '3px',
              color: m.color, background: `${m.color}1f`, border: `1px solid ${m.color}55`,
            }}>{r.tier} {m.label}</span>
          );
        })}
      </div>

      {upNext && (
        <div
          title={`${upNext.when} — ${upNext.label}\n${upNext.detail || ''}`}
          style={{
            marginTop: '0.22rem', fontSize: '0.6rem', lineHeight: 1.3,
            color: 'var(--text-muted)', maxWidth: '15rem',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}
        >
          <span style={{ opacity: 0.75 }}>next ▸ </span>
          <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{upNext.label}</span>
        </div>
      )}
    </td>
  );
}

// ── Position Journey — past / present / next in one glance ───────────────────
/**
 * Answers the three questions an operator has when looking at a holding:
 *   1. Which phase is it in right now?      → the day track
 *   2. What happens to it next?             → the "Next" column
 *   3. What has already happened to it?     → the "So far" column
 *
 * All content comes from buildLifecycle() in positionRules.js, so it can never
 * contradict the Risk Rule Ladder below it.
 */
function PositionJourney({ pos, openPositions, equity }) {
  const now = new Date();
  const tdHeld = tradingDaysBetween(pos.buy_date, now);
  const sinceHwm = pos.days_since_hwm != null
    ? pos.days_since_hwm
    : tradingDaysBetween(pos.hwm_date || pos.buy_date, now);
  const evald = rulesFor(pos, openPositions, equity);
  const { past, track, next, phase, offTrack } = buildLifecycle(pos, evald, tdHeld, sinceHwm);

  const toneColor = { good: '#10b981', bad: '#f43f5e', neutral: 'var(--text-secondary)' };

  const segColor = (s) => s.status === 'current' ? phase.color
                        : s.status === 'done' ? '#475569'
                        : 'rgba(255,255,255,0.14)';

  const colHead = {
    fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.08em',
    textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.45rem',
  };

  const item = (e) => (
    <div key={e.key} style={{
      display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '0.45rem',
      padding: '0.35rem 0', alignItems: 'start',
    }}>
      <span style={{ fontSize: '0.75rem', lineHeight: 1.35 }}>{e.icon}</span>
      <div>
        <div style={{ fontSize: '0.75rem', fontWeight: 700, color: toneColor[e.tone] || 'var(--text-secondary)', lineHeight: 1.35 }}>
          {e.label}
          {e.when && (
            <span style={{ marginLeft: '0.35rem', fontSize: '0.63rem', fontWeight: 600, color: 'var(--text-muted)' }}>
              · {e.when}
            </span>
          )}
        </div>
        {e.detail && (
          <div style={{ fontSize: '0.67rem', color: 'var(--text-muted)', lineHeight: 1.45, marginTop: '0.1rem' }}>
            {e.detail}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div style={{
      gridColumn: '1 / -1',
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '10px',
      padding: '0.85rem 1rem',
      marginBottom: '0.5rem',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.7rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          🧭 Position Journey
        </span>
        <span style={{
          fontSize: '0.68rem', fontWeight: 800, padding: '0.12rem 0.45rem', borderRadius: '5px',
          color: phase.color, background: `${phase.color}1f`, border: `1px solid ${phase.color}55`,
        }}>{phase.label}</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{phase.note}</span>
      </div>

      {/* ── The day track: where it is in its life ─────────────────────────── */}
      <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.3rem' }}>
        {track.map((s) => (
          <div key={s.key} title={`${s.label} (${s.short}) — ${s.owns}`} style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              height: '5px', borderRadius: '3px', background: segColor(s),
              boxShadow: s.status === 'current' ? `0 0 0 1px ${phase.color}88` : 'none',
            }} />
            <div style={{
              marginTop: '0.25rem', fontSize: '0.6rem', lineHeight: 1.25,
              fontWeight: s.status === 'current' ? 800 : 600,
              color: s.status === 'current' ? phase.color
                   : s.status === 'done' ? 'var(--text-muted)' : 'rgba(148,163,184,0.55)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {s.short} {s.label}
            </div>
          </div>
        ))}
      </div>
      {offTrack && (
        <div style={{ fontSize: '0.65rem', color: phase.color, fontWeight: 700, marginBottom: '0.3rem' }}>
          ▲ {phase.label} overrides the normal day track.
        </div>
      )}

      {/* ── Past and future, side by side ──────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
        gap: '0.9rem', marginTop: '0.75rem',
      }}>
        <div>
          <div style={colHead}>⏮ What has happened</div>
          {past.length ? past.map(item)
            : <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Nothing yet — just entered.</div>}
        </div>
        <div style={{ borderLeft: '1px solid rgba(255,255,255,0.07)', paddingLeft: '0.9rem' }}>
          <div style={colHead}>⏭ What happens next</div>
          {next.length ? next.map(item)
            : <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Nothing scheduled — the trailing stop is the only live exit.</div>}
        </div>
      </div>
    </div>
  );
}

/** Full-width risk ladder shown inside the expanded row. */
function RiskLadder({ pos, formatCurrency, openPositions, equity }) {
  const { phase, rules } = rulesFor(pos, openPositions, equity);
  const [legendOpen, setLegendOpen] = useState(false);
  const price = pos.current_price || pos.buy_price;

  const rowStyle = (color) => ({
    display: 'grid',
    gridTemplateColumns: 'auto minmax(150px, 1.1fr) minmax(200px, 2fr) auto',
    gap: '0.6rem',
    alignItems: 'baseline',
    padding: '0.45rem 0.6rem',
    borderLeft: `3px solid ${color}`,
    background: `${color}0d`,
    borderRadius: '0 7px 7px 0',
    marginBottom: '0.3rem',
  });

  return (
    <div data-risk-ladder="1" style={{
      gridColumn: '1 / -1',
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '10px',
      padding: '0.85rem 1rem',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.7rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          🛡️ Risk Rule Ladder
        </span>
        <span style={{
          fontSize: '0.68rem', fontWeight: 800, padding: '0.12rem 0.45rem', borderRadius: '5px',
          color: phase.color, background: `${phase.color}1f`, border: `1px solid ${phase.color}55`,
        }}>{phase.label}</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{phase.note}</span>
        <button
          onClick={(e) => { e.stopPropagation(); setLegendOpen(o => !o); }}
          style={{
            marginLeft: 'auto', fontSize: '0.68rem', fontWeight: 600, cursor: 'pointer',
            padding: '0.15rem 0.5rem', borderRadius: '5px',
            background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)',
            border: '1px solid rgba(255,255,255,0.12)',
          }}
        >{legendOpen ? 'Hide legend' : 'What do the colours mean?'}</button>
      </div>

      {legendOpen && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0.3rem 0.9rem',
          marginBottom: '0.7rem', padding: '0.55rem 0.7rem',
          background: 'rgba(255,255,255,0.03)', borderRadius: '8px',
          border: '1px solid rgba(255,255,255,0.07)',
        }}>
          {Object.entries(STATE_META).map(([key, m]) => (
            <div key={key} style={{ fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.45 }}>
              <span style={{ color: m.color, fontWeight: 800 }}>● {m.label}</span> — {m.meaning}
            </div>
          ))}
        </div>
      )}

      {rules.map(r => {
        const m = STATE_META[r.state];
        const dim = r.state === STATE.EXPIRED || r.state === STATE.OFF;
        return (
          <div key={r.id} style={{ ...rowStyle(m.color), opacity: dim ? 0.55 : 1 }}>
            {/* Tier chip */}
            <span style={{
              fontSize: '0.6rem', fontWeight: 800, letterSpacing: '0.04em',
              padding: '0.1rem 0.32rem', borderRadius: '3px', whiteSpace: 'nowrap',
              color: m.color, background: `${m.color}22`, border: `1px solid ${m.color}55`,
            }}>{r.tier}</span>

            {/* Name + window */}
            <div>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-primary)' }}>{r.name}</div>
              {r.window && <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{r.window}</div>}
            </div>

            {/* State + headline + explanation */}
            <div>
              <div style={{ fontSize: '0.74rem', fontWeight: 700, color: m.color }}>
                {m.label} · <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{r.headline}</span>
              </div>
              {r.latch && (
                <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '0.1rem' }}>
                  Follow-through latch: <b style={{ color: 'var(--text-secondary)' }}>{r.latch}</b>
                </div>
              )}
              {r.progress && (
                <div style={{ height: '3px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', margin: '0.3rem 0 0.1rem', maxWidth: '220px' }}>
                  <div style={{
                    height: '100%', borderRadius: '2px', background: m.color,
                    width: `${Math.min(100, Math.max(0, (r.progress.value / r.progress.max) * 100))}%`,
                  }} />
                </div>
              )}
              <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.45, marginTop: '0.2rem', whiteSpace: 'pre-wrap' }}>
                {r.detail}
              </div>
            </div>

            {/* Trigger level */}
            <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
              {r.level != null ? (
                <>
                  <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {r.levelLabel || 'Level'}
                  </div>
                  <div style={{ fontSize: '0.86rem', fontWeight: 800, color: m.color, fontFamily: 'var(--font-display)' }}>
                    {formatCurrency(r.level)}
                  </div>
                  {r.distancePct != null && (
                    <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)' }}>
                      {r.distancePct >= 0 ? '+' : ''}{r.distancePct.toFixed(2)}% from {formatCurrency(price)}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>—</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Position Intelligence Panel (expandable) ─────────────────────────────────
function ExitConditionsPanel({ pos, formatCurrency, openPositions, equity }) {
  const days = daysHeld(pos.buy_date);
  const hwmPrice  = pos.hwm_price || pos.buy_price;  // hwm_price: running peak IBKR tracks
  const stopLossPct = pos.stop_loss_pct || STOP_LOSS_PCT;
  const profitLockTier = activeProfitLockTier(pos);
  // Trail stop floor = hwm_price × (1 - trail%)  — matches IBKR's own calculation.
  // IBKR trails from the running peak (hwm_price), not the current price.
  const trailStop = parseFloat((hwmPrice * (1 - stopLossPct)).toFixed(2));

  const panelStyle = {
    background: 'rgba(255,255,255,0.02)',
    borderTop: '1px solid rgba(255,255,255,0.06)',
    padding: '1rem 1.25rem 1.25rem',
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: '0.875rem',
  };

  const cardStyle = (accentColor) => ({
    background: `rgba(${accentColor}, 0.06)`,
    border: `1px solid rgba(${accentColor}, 0.18)`,
    borderRadius: '10px',
    padding: '0.75rem 1rem',
  });

  const labelStyle = { fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.35rem' };
  const valueStyle = (color) => ({ fontSize: '0.92rem', fontWeight: 700, color: color || 'var(--text-primary)', marginBottom: '0.2rem' });
  const noteStyle = { fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4 };

  // Helper: color scale for 0-100 scores
  const scoreColor = (v) => v >= 80 ? '#10b981' : v >= 60 ? '#3b82f6' : v >= 40 ? '#f59e0b' : '#f43f5e';
  const gradeColors = { A: '#10b981', B: '#3b82f6', C: '#f59e0b', D: '#f43f5e' };

  const hasConviction = pos.entry_final_score != null
    || pos.entry_technical_score != null
    || pos.entry_score_rationale;

  return (
    <tr>
      <td colSpan={12} style={{ padding: 0 }}>
        <div style={panelStyle}>

          {/* ── Position Journey — phase, history and what comes next ──────── */}
          <PositionJourney pos={pos} openPositions={openPositions} equity={equity} />

          {/* ── Risk Rule Ladder — every applicable tier and its live state ─── */}
          <RiskLadder pos={pos} formatCurrency={formatCurrency} openPositions={openPositions} equity={equity} />

          {/* ── Holding Info ─────────────────────────── */}
          <div style={cardStyle('147,197,253')}>
            <div style={labelStyle}>📅 Position</div>
            <div style={valueStyle('var(--text-primary)')}>{days} days held</div>
            <div style={noteStyle}>
              Bought {formatDate(pos.buy_date)}<br />
              Source: CANSLIM Breakout<br />
              Entry: {formatCurrency(pos.buy_price)} · HWM: {formatCurrency(hwmPrice)}
              {pos.hwm_date ? ` (${formatDate(pos.hwm_date)})` : ''}
            </div>
            <div style={{
              marginTop: '0.55rem',
              padding: '0.4rem 0.55rem',
              background: profitLockTier ? 'rgba(56,189,248,0.08)' : 'rgba(255,255,255,0.04)',
              border: profitLockTier ? '1px solid rgba(56,189,248,0.28)' : '1px solid rgba(255,255,255,0.08)',
              borderRadius: '7px',
            }}>
              <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.14rem' }}>
                Profit Lock
              </div>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: profitLockTier ? '#38bdf8' : 'var(--text-secondary)' }}>
                {profitLockTier
                  ? `ACTIVE — ${formatCurrency(hwmPrice * (1 - profitLockTier[1]))} stop (${(profitLockTier[1] * 100).toFixed(1)}% below HWM)`
                  : `Standby — activates after +${RULES_CONFIG.TRAIL_PROFIT_TIERS[0][0]}% gain`}
              </div>
            </div>
            {/* Live dollar P&L — most visible loss signal */}
            {pos.current_price != null && pos.buy_price != null && pos.shares != null && (() => {
              const dollarPnl = pos.shares * (pos.current_price - pos.buy_price);
              const pctPnl = ((pos.current_price / pos.buy_price) - 1) * 100;
              const isLoss = dollarPnl < 0;
              const color = isLoss ? '#f43f5e' : '#10b981';
              return (
                <div style={{
                  marginTop: '0.5rem', padding: '0.35rem 0.5rem',
                  background: isLoss ? 'rgba(244,63,94,0.08)' : 'rgba(16,185,129,0.08)',
                  border: `1px solid ${isLoss ? 'rgba(244,63,94,0.25)' : 'rgba(16,185,129,0.25)'}`,
                  borderRadius: '6px',
                }}>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: '0.1rem' }}>Unrealized P&L</div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span style={{ fontSize: '1rem', fontWeight: 800, color, fontFamily: 'var(--font-display)' }}>
                      {dollarPnl >= 0 ? '+' : ''}{formatCurrency(dollarPnl)}
                    </span>
                    <span style={{ fontSize: '0.8rem', fontWeight: 700, color }}>
                      {pctPnl >= 0 ? '+' : ''}{pctPnl.toFixed(2)}%
                    </span>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* ── Trail Stop card removed — superseded by the Risk Rule Ladder above,
                 which shows the live trail level alongside every other rule. ── */}

          {/* ── Live Momentum & Drift ───────────────────────────── */}
          {(() => {
            // Live signals that feed Rank & Replace and the rotation recommendations.
            // The rule-by-rule lifecycle lives in the Risk Rule Ladder above; this card
            // is only the underlying evidence.
            const hasCushion = pos.highest_unrealized_pct >= 5.0;

            // Best available trigger score (informational context only)
            const entryScore = pos.entry_final_score;
            const topScore   = pos.top_trigger_score;
            const scoreGap   = (entryScore != null && topScore != null) ? (topScore - entryScore) : null;
            const scoreGapColor = scoreGap != null
              ? (scoreGap >= 20 ? '#f59e0b' : scoreGap >= 10 ? '#34d399' : 'var(--text-muted)')
              : 'var(--text-muted)';

            // Recommendation banner
            const rec = pos.rotation_recommendation;
            const tierColors = {
              PARAM_DRIFT:       { bg: 'rgba(244,63,94,0.12)',   border: 'rgba(244,63,94,0.4)',   text: '#f43f5e', label: '⚠️ Breakout Parameters Failed — Rotation Recommended',       hasApprove: true  },
              HARD_STOP:         { bg: 'rgba(244,63,94,0.18)',   border: 'rgba(244,63,94,0.5)',   text: '#f43f5e', label: '🛑 Hard Stop — Day 7 Auto-Sell Pending',                      hasApprove: true  },
              RS_DECAY:          { bg: 'rgba(244,63,94,0.12)',   border: 'rgba(244,63,94,0.4)',   text: '#f43f5e', label: '⚠️ RS Decay — Rotation Recommended',                          hasApprove: true  },
              TIER_1:            { bg: 'rgba(244,63,94,0.12)',   border: 'rgba(244,63,94,0.4)',   text: '#f43f5e', label: '⚠️ RS Decay — Rotation Recommended',                          hasApprove: true  },
              TIER_2:            { bg: 'rgba(245,158,11,0.12)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b', label: '📈 Score Upgrade — Rotation Recommended',                     hasApprove: true  },
              PROGRESS_DEFICIT:  { bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.38)', text: '#fbbf24', label: '📉 Progress Deficit — Position Behind Pace',                  hasApprove: false },
              FLOOR_BREAK:       { bg: 'rgba(239,68,68,0.10)',   border: 'rgba(239,68,68,0.35)',  text: '#ef4444', label: '🚫 Floor Break — Consolidation Support Violated on Volume',     hasApprove: false },
            };
            const tierInfo = rec ? tierColors[rec] : null;

            // RS Score tracking: entry vs live (used in the RS decay indicator below)
            const entryRS = pos.entry_rs_score ?? null;
            const liveRS  = pos.live_rs_score ?? pos.rs_score ?? null;
            // rsDecay > 0 means RS has weakened since entry (entry was higher than live)
            const rsDecay = (entryRS != null && liveRS != null) ? entryRS - liveRS : null;
            const rsDecayColor = rsDecay == null
              ? 'var(--text-muted)'
              : rsDecay >= 15 ? '#f43f5e'   // significant decay — red
              : rsDecay > 0  ? '#f59e0b'    // mild decay — amber
              : '#10b981';                   // same or improved — green

            const handleApprove = async () => {
              if (!window.confirm(`Approve rotation of ${pos.ticker}? This will execute a live sell order.`)) return;
              try {
                const r = await fetch(`/api/portfolio/${pos.ticker}/approve-rotation`, { method: 'POST' });
                const body = await r.json();
                if (!r.ok) { alert(`Rotation failed: ${body.detail}`); return; }
                alert(`✅ ${pos.ticker} rotated successfully.`);
                window.location.reload();
              } catch (e) { alert(`Network error: ${e.message}`); }
            };

            const handleDismiss = async () => {
              try {
                await fetch(`/api/portfolio/${pos.ticker}/dismiss-rotation`, { method: 'POST' });
                window.location.reload();
              } catch (e) { alert(`Network error: ${e.message}`); }
            };

            return (
              <div style={cardStyle(hasCushion ? '16,185,129' : '245,158,11')}>
                <div style={labelStyle}>📊 Live Momentum & Drift</div>

                {/* Peak cushion */}
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Peak Cushion:</span>
                  <span style={{ fontWeight: 700, color: hasCushion ? '#10b981' : 'var(--text-secondary)' }}>
                    {pos.highest_unrealized_pct ? `+${pos.highest_unrealized_pct.toFixed(2)}%` : '0.00%'}
                  </span>
                </div>

                {/* Volume Distribution Status */}
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', marginTop: '0.2rem' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Volume Distribution:</span>
                  <span style={{ fontWeight: 700, color: pos.volume_distribution_flag ? '#f43f5e' : '#10b981' }}>
                    {pos.volume_distribution_flag ? '⚠️ Detected (High Vol Down)' : '✅ Normal'}
                  </span>
                </div>

                {/* Custom ATR Trail Stop % */}
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', marginTop: '0.2rem' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Custom ATR Stop:</span>
                  <span style={{ fontWeight: 700, color: 'var(--text-secondary)' }}>
                    {pos.stop_loss_pct ? `${(pos.stop_loss_pct * 100).toFixed(2)}%` : `${(STOP_LOSS_PCT * 100).toFixed(2)}%`}
                  </span>
                </div>

                {/* RS Score (entry→live, informational) */}
                {entryRS != null && (
                  <div style={{ marginTop: '0.6rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                    <div style={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>RS Score (entry→live)</div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 700, color: rsDecayColor }}>
                      {liveRS != null ? `${entryRS} → ${liveRS}` : `${entryRS} (live pending)`}
                      {rsDecay != null && rsDecay > 0 && <span style={{ marginLeft: '0.4rem', fontSize: '0.72rem' }}>(−{rsDecay} pts)</span>}
                    </div>
                    {rsDecay != null && rsDecay >= 15 && <div style={{ fontSize: '0.7rem', color: '#f59e0b', marginTop: '0.15rem' }}>RS has decayed — included in parameter drift analysis.</div>}
                  </div>
                )}

                {/* Breakout Parameter Drift Analysis */}
                {pos.analysis_reason && (
                  <div style={{
                    marginTop: '0.6rem', padding: '0.5rem 0.65rem',
                    background: 'rgba(244,63,94,0.08)', border: '1px solid rgba(244,63,94,0.25)',
                    borderRadius: '8px',
                  }}>
                    <div style={{ fontSize: '0.7rem', fontWeight: 700, color: '#f43f5e', marginBottom: '0.3rem' }}>
                      ⚠️ Breakout Analysis
                      {pos.analysis_ai_grade && <span style={{ marginLeft: '0.5rem', color: '#f59e0b' }}>AI: {pos.analysis_ai_grade}</span>}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                      {pos.analysis_reason}
                    </div>
                    {/* Param drift detail table */}
                    {pos.param_drift && (() => {
                      let drift;
                      try { drift = typeof pos.param_drift === 'string' ? JSON.parse(pos.param_drift) : pos.param_drift; }
                      catch { drift = null; }
                      if (!drift) return null;
                      const rows = Object.entries(drift)
                        .filter(([,v]) => v.entry != null && v.current != null)
                        .map(([param, v]) => ({ param, ...v }));
                      if (!rows.length) return null;
                      return (
                        <table style={{ width: '100%', fontSize: '0.68rem', marginTop: '0.4rem', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ color: 'var(--text-muted)' }}>
                              <th style={{ textAlign: 'left', paddingRight: '0.5rem' }}>Param</th>
                              <th style={{ textAlign: 'right' }}>Entry</th>
                              <th style={{ textAlign: 'right' }}>Now</th>
                              <th style={{ textAlign: 'right' }}>Drift</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map(r => (
                              <tr key={r.param} style={{ color: r.failed ? '#f43f5e' : 'var(--text-secondary)' }}>
                                <td style={{ paddingRight: '0.5rem' }}>{r.param.replace('_', ' ')}</td>
                                <td style={{ textAlign: 'right' }}>{typeof r.entry === 'number' ? r.entry.toFixed(1) : r.entry}</td>
                                <td style={{ textAlign: 'right' }}>{typeof r.current === 'number' ? r.current.toFixed(1) : r.current}</td>
                                <td style={{ textAlign: 'right' }}>{r.drift != null ? (r.drift > 0 ? '+' : '') + (typeof r.drift === 'number' ? r.drift.toFixed(1) : r.drift) : '—'}{r.failed ? ' ⚠️' : ''}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      );
                    })()}
                  </div>
                )}

                {/* Momentum Health Score Mₜ */}
                {pos.momentum_health_score != null && (() => {
                  const mt = pos.momentum_health_score;
                  const mtColor = mt >= 70 ? '#10b981' : mt >= 50 ? '#3b82f6' : mt >= 35 ? '#f59e0b' : '#f43f5e';
                  return (
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', marginTop: '0.2rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Live Mₜ Score:</span>
                      <span style={{ fontWeight: 700, color: mtColor }}>
                        {mt.toFixed(1)} / 100
                        {pos.live_sentiment_score != null && (
                          <span style={{ marginLeft: '0.35rem', fontWeight: 400, color: 'var(--text-muted)' }}>
                            (sent: {pos.live_sentiment_score})
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })()}

                {/* Recommendation banner */}
                {tierInfo && (() => {
                  // Progress Deficit gets a special amber info card, no Approve button
                  if (rec === 'PROGRESS_DEFICIT') {
                    const daysHeld    = pos.days_held != null ? pos.days_held : 0;
                    const estDays     = pos.entry_est_days_target;
                    const buyPx       = pos.buy_price || 0;
                    const currPx      = pos.current_price || buyPx;
                    const actualPct   = buyPx > 0 ? ((currPx / buyPx) - 1) * 100 : 0;
                    const expectedPct = estDays > 0 ? (25.0 * daysHeld / estDays) : null;
                    const deficit     = expectedPct != null ? expectedPct - actualPct : null;
                    const atrPct      = pos.entry_atr_pct;
                    const remPct      = 25.0 - actualPct;
                    const daysToTarget = atrPct > 0 ? Math.ceil(remPct / atrPct) : null;
                    return (
                      <div style={{
                        marginTop: '0.75rem', padding: '0.6rem 0.75rem',
                        background: tierInfo.bg, border: `1px solid ${tierInfo.border}`,
                        borderRadius: '8px',
                      }}>
                        <div style={{ fontWeight: 700, fontSize: '0.8rem', color: tierInfo.text, marginBottom: '0.4rem' }}>
                          {tierInfo.label}
                        </div>
                        {expectedPct != null && (
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.55, marginBottom: '0.45rem' }}>
                            Expected at day {daysHeld}:
                            <strong style={{ color: tierInfo.text, marginLeft: '0.25rem' }}>+{expectedPct.toFixed(1)}%</strong>
                            {' '}toward +25% goal — actual:
                            <strong style={{ color: actualPct >= 0 ? '#10b981' : '#f43f5e', marginLeft: '0.25rem' }}>
                              {actualPct >= 0 ? '+' : ''}{actualPct.toFixed(1)}%
                            </strong>
                            {deficit != null && (
                              <span style={{ marginLeft: '0.3rem', color: '#f87171' }}>
                                ({deficit.toFixed(1)} pts behind pace)
                              </span>
                            )}
                          </div>
                        )}
                        {daysToTarget != null && (
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.45rem' }}>
                            At ATR {atrPct.toFixed(2)}%/day → est.
                            <strong style={{ color: 'var(--text-secondary)', marginLeft: '0.2rem' }}>
                              {daysToTarget}d
                            </strong>{' '}more to reach +25%.
                          </div>
                        )}
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                          No auto-sell. Review and approve a rotation manually if desired.
                        </div>
                        <button
                          id={`dismiss-rotation-${pos.ticker}`}
                          onClick={(e) => { e.stopPropagation(); handleDismiss(); }}
                          style={{
                            padding: '0.3rem 0.75rem', fontSize: '0.75rem', fontWeight: 600,
                            background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)',
                            border: '1px solid rgba(255,255,255,0.12)',
                            borderRadius: '6px', cursor: 'pointer',
                          }}
                        >Dismiss</button>
                      </div>
                    );
                  }

                  // Floor Break: red-orange info card, dismiss only
                  if (rec === 'FLOOR_BREAK') {
                    return (
                      <div style={{
                        marginTop: '0.75rem', padding: '0.6rem 0.75rem',
                        background: tierInfo.bg, border: `1px solid ${tierInfo.border}`,
                        borderRadius: '8px',
                      }}>
                        <div style={{ fontWeight: 700, fontSize: '0.8rem', color: tierInfo.text, marginBottom: '0.4rem' }}>
                          {tierInfo.label}
                        </div>
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.55, marginBottom: '0.45rem' }}>
                          Price closed below the 7-day trading range floor on above-average volume.
                          This signals that the consolidation base has failed and institutions
                          may be exiting. The trailing stop is still active via IBKR.
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                          No auto-sell. If the EMA-21 also fails, an automatic exit will fire.
                          Dismiss if you believe this was a one-day shake-out.
                        </div>
                        <button
                          id={`dismiss-rotation-${pos.ticker}`}
                          onClick={(e) => { e.stopPropagation(); handleDismiss(); }}
                          style={{
                            padding: '0.3rem 0.75rem', fontSize: '0.75rem', fontWeight: 600,
                            background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)',
                            border: '1px solid rgba(255,255,255,0.12)',
                            borderRadius: '6px', cursor: 'pointer',
                          }}
                        >Dismiss</button>
                      </div>
                    );
                  }

                  // All other rotation recommendations: Approve + Dismiss
                  return (
                    <div data-plateau-health-card="tier3" style={{
                      marginTop: '0.75rem', padding: '0.6rem 0.75rem',
                      background: tierInfo.bg, border: `1px solid ${tierInfo.border}`,
                      borderRadius: '8px',
                    }}>
                      <div style={{ fontWeight: 700, fontSize: '0.8rem', color: tierInfo.text, marginBottom: '0.35rem' }}>
                        {tierInfo.label}
                      </div>
                      <div style={{ ...noteStyle, marginBottom: '0.5rem' }}>
                        Advisory only. Rank &amp; Replace can act on this automatically from day{' '}
                        {RULES_CONFIG.RANK_REPLACE_MIN_DAYS} if the margin is met — see the Risk Rule Ladder above.
                      </div>
                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button
                          id={`approve-rotation-${pos.ticker}`}
                          onClick={(e) => { e.stopPropagation(); handleApprove(); }}
                          style={{
                            padding: '0.3rem 0.75rem', fontSize: '0.75rem', fontWeight: 700,
                            background: tierInfo.text, color: '#fff', border: 'none',
                            borderRadius: '6px', cursor: 'pointer',
                          }}
                        >Approve Rotation</button>
                        <button
                          id={`dismiss-rotation-${pos.ticker}`}
                          onClick={(e) => { e.stopPropagation(); handleDismiss(); }}
                          style={{
                            padding: '0.3rem 0.75rem', fontSize: '0.75rem', fontWeight: 600,
                            background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)',
                            border: '1px solid rgba(255,255,255,0.12)',
                            borderRadius: '6px', cursor: 'pointer',
                          }}
                        >Dismiss</button>
                      </div>
                    </div>
                  );
                })()}
              </div>
            );
          })()}

          {/* ── Entry Conviction Scorecard ────────────────────────────────────
               Copied from daily_triggers at buy time — all entry_* fields.      */}
          {hasConviction && (() => {
            const scores = [
              { label: 'Technical',  value: pos.entry_technical_score  ?? pos.entry_quality_score },
              { label: 'Liquidity',  value: pos.entry_liquidity_score },
              { label: 'RS Rating',  value: pos.entry_rs_score },
              { label: 'Sentiment',  value: pos.entry_sentiment_score },
              { label: 'AI Rating',  value: pos.entry_ai_rating },
            ].filter(s => s.value != null);

            const grade = pos.entry_ai_grade;
            const gradeColor = gradeColors[grade] ?? 'var(--text-muted)';

            return (
              <div style={{ ...cardStyle('167,139,250'), gridColumn: '1 / -1' }}>
                {/* Header row: label + final score + AI grade badge */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.75rem' }}>
                  <div style={labelStyle}>🎯 Entry Conviction</div>
                  {pos.entry_final_score != null && (
                    <span style={{ fontSize: '0.78rem', fontWeight: 800, color: scoreColor(pos.entry_final_score), fontFamily: 'var(--font-display)' }}>
                      {pos.entry_final_score}
                    </span>
                  )}
                  {grade && (
                    <span style={{
                      fontSize: '0.62rem', fontWeight: 800, padding: '0.1rem 0.35rem',
                      borderRadius: '4px', color: gradeColor,
                      background: `${gradeColor}22`, border: `1px solid ${gradeColor}55`,
                      letterSpacing: '0.04em',
                    }}>{grade}</span>
                  )}
                  {pos.entry_atr_pct != null && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
                      ATR&nbsp;<b style={{ color: 'var(--text-secondary)' }}>{pos.entry_atr_pct.toFixed(2)}%</b>
                    </span>
                  )}
                  {pos.entry_est_days_target != null && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                      Est.&nbsp;+25%&nbsp;<b style={{ color: 'var(--text-secondary)' }}>~{pos.entry_est_days_target}d</b>
                    </span>
                  )}
                </div>

                {/* 5-component mini score gauges */}
                {scores.length > 0 && (
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: `repeat(${scores.length}, 1fr)`,
                    gap: '0.75rem',
                    marginBottom: pos.entry_score_rationale ? '0.75rem' : 0,
                  }}>
                    {scores.map(({ label, value }) => {
                      const col = scoreColor(value);
                      return (
                        <div key={label} style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.3rem' }}>
                            {label}
                          </div>
                          {/* Progress bar */}
                          <div style={{ height: '3px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', margin: '0 auto 0.3rem', maxWidth: '80px' }}>
                            <div style={{ height: '100%', width: `${value}%`, background: col, borderRadius: '2px' }} />
                          </div>
                          <div style={{ fontSize: '1rem', fontWeight: 800, color: col, fontFamily: 'var(--font-display)' }}>
                            {value}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* AI Narrative */}
                {pos.entry_score_rationale && (
                  <div style={{
                    fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.5,
                    fontStyle: 'italic',
                    borderTop: '1px solid rgba(255,255,255,0.06)',
                    paddingTop: '0.6rem',
                    marginTop: '0.1rem',
                  }}>
                    {pos.entry_score_rationale}
                  </div>
                )}

                {/* ── Entry quality flags — surfaces the gate violations at buy time ── */}
                {(() => {
                  const flags = [];
                  const volSurge = pos.entry_volume_surge;
                  const pivotDist = pos.entry_pivot_distance_pct;
                  if (volSurge != null && volSurge < 0.75) {
                    flags.push({ icon: '📉', color: '#f43f5e', text: `Vol surge ${volSurge.toFixed(2)}× — below 0.75× gate (new gate now blocks this)` });
                  } else if (volSurge != null && volSurge < 1.0) {
                    flags.push({ icon: '⚠️', color: '#f59e0b', text: `Vol surge ${volSurge.toFixed(2)}× — below average (weak confirmation)` });
                  }
                  if (pivotDist != null && pivotDist < -5) {
                    flags.push({ icon: '📍', color: '#f43f5e', text: `${Math.abs(pivotDist).toFixed(1)}% below 52W pivot at entry — new gate now blocks PRE_BREAKOUT entries >5% below pivot` });
                  } else if (pivotDist != null && pivotDist < -3) {
                    flags.push({ icon: '📍', color: '#f59e0b', text: `${Math.abs(pivotDist).toFixed(1)}% below 52W pivot at entry` });
                  }
                  if (!flags.length) return null;
                  return (
                    <div style={{ marginTop: '0.55rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                      {flags.map((f, i) => (
                        <div key={i} style={{
                          fontSize: '0.68rem', padding: '0.3rem 0.5rem', borderRadius: '5px',
                          background: `${f.color}12`, border: `1px solid ${f.color}35`, color: f.color,
                          fontWeight: 600,
                        }}>
                          {f.icon} {f.text}
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            );
          })()}

        </div>
      </td>
    </tr>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────
export default function DashboardView({ data, marketData, trades }) {
  const [expandedRow, setExpandedRow] = useState(null);
  const [buildVersion, setBuildVersion] = useState(null);

  useEffect(() => {
    fetch('/api/version')
      .then(r => r.json())
      .then(v => setBuildVersion(v))
      .catch(() => {});
  }, []);

  const summary = data?.summary || {
    initial_balance: 100000.0,
    cash_balance: 100000.0,
    portfolio_value: 100000.0,
    invested_value: 0.0,
    unrealized_pnl: 0.0,
    total_pnl: 0.0,
    total_pnl_pct: 0.0,
    win_rate: 0.0,
    total_trades: 0
  };
  

  const positions = data?.positions || [];
  const investedValue = positions.reduce((sum, pos) => sum + (pos.current_price || pos.buy_price) * pos.shares, 0);
  const recentTrades = trades?.slice(0, 5) || [];

  const { items: sortedPositions, requestSort: requestSortPos, getSortIcon: getSortIconPos } = useSortableTable(positions, 'ticker', 'asc');
  const { items: sortedTrades, requestSort: requestSortTrades, getSortIcon: getSortIconTrades } = useSortableTable(recentTrades, 'sell_date', 'desc');


  const getMarketClass = () => {
    if (!marketData) return '';
    if (marketData.status === 'Market in Correction') return 'correction';
    if (marketData.status === 'Uptrend Under Pressure') return 'pressure';
    return '';
  };

  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  const toggleRow = (ticker) =>
    setExpandedRow(prev => prev === ticker ? null : ticker);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>

      {/* ── Company / Product Name Banner ────────────────────────────────────── */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0.85rem 1.25rem',
        background: 'linear-gradient(135deg, rgba(99,102,241,0.12) 0%, rgba(139,92,246,0.08) 50%, rgba(59,130,246,0.06) 100%)',
        border: '1px solid rgba(99,102,241,0.25)',
        borderRadius: '14px',
        backdropFilter: 'blur(8px)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{
            width: '36px', height: '36px', borderRadius: '10px',
            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 0 12px rgba(99,102,241,0.4)',
            fontSize: '1.1rem',
          }}>📈</div>
          <div>
            <div style={{
              fontSize: '1.05rem', fontWeight: 800,
              fontFamily: 'var(--font-display)',
              background: 'linear-gradient(90deg, #a5b4fc, #c4b5fd)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
              letterSpacing: '-0.01em',
            }}>O'Neil Growth Engine</div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
              CANSLIM-based AI Execution System · Interactive Brokers (U12941651)
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{
            width: '7px', height: '7px', borderRadius: '50%',
            background: '#10b981',
            boxShadow: '0 0 6px rgba(16,185,129,0.7)',
            animation: 'pulse 2s ease-in-out infinite',
          }} />
          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#10b981', letterSpacing: '0.04em' }}>LIVE</span>
        </div>
      </div>

      
      {/* Market Direction Alert Banner */}
      {marketData && (
        <div className={`market-banner ${getMarketClass()}`}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Activity size={20} color={marketData.status === 'Market in Correction' ? '#f43f5e' : marketData.status === 'Uptrend Under Pressure' ? '#f59e0b' : '#10b981'} />
            <div>
              <span style={{ fontWeight: 500, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Market Direction (M):</span>
              <strong style={{ marginLeft: '0.5rem', fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                {marketData.status}
              </strong>
              {/* The descriptive status above answers "how does the market look?".
                  This answers "will the bot actually buy?" — a different question
                  with a different rule. Showing only the first misled the operator
                  whenever the two disagreed. */}
              {marketData.execution_gate && (
                <div
                  data-market-gate
                  title={marketData.execution_gate.reason}
                  style={{
                    marginTop: '0.25rem', fontSize: '0.78rem',
                    color: marketData.execution_gate.bullish ? '#10b981' : '#f43f5e'
                  }}
                >
                  Buy Gate:{' '}
                  <strong>
                    {marketData.execution_gate.bullish ? 'OPEN — new buys allowed' : 'CLOSED — standing down'}
                  </strong>
                  <span style={{ color: 'var(--text-muted)' }}> · {marketData.execution_gate.reason}</span>
                </div>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '1.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            {Object.entries(marketData.indices || {}).map(([name, idx]) => (
              <span key={name}>
                {name}: <strong>{formatCurrency(idx.price)}</strong> (50d SMA: {formatCurrency(idx.sma50)})
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Metrics Row */}
      <div className="metrics-grid">
        <div className="card metric-card">
          <div className="metric-header">
            <span>Invested Portfolio Value</span>
            <div className="metric-icon-wrap" style={{ color: 'var(--accent-primary)' }}>
              <Briefcase size={16} />
            </div>
          </div>
          <div className="metric-value">
            {formatCurrency(summary.invested_value ?? investedValue)}
          </div>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
            {summary.portfolio_value > 0 
              ? `${(((summary.invested_value ?? investedValue) / summary.portfolio_value) * 100).toFixed(1)}% of total portfolio (${formatCurrency(summary.portfolio_value)})`
              : `0.0% of total portfolio (${formatCurrency(0)})`}
          </span>
        </div>

        <div className="card metric-card">
          <div className="metric-header">
            <span>Cash Balance</span>
            <div className="metric-icon-wrap" style={{ color: 'var(--accent-secondary)' }}>
              <DollarSign size={16} />
            </div>
          </div>
          <div className="metric-value">{formatCurrency(summary.cash_balance)}</div>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
            IBKR Settled Cash
          </span>
          {/* ── Margin Rationale Note ───────────────────────────────────────────
               Explains why settled cash ≠ Net Liquidation Value on a margin
               account. Cash here = ibkr_cash_balance (Settled Cash in IBKR UI);
               NLV = Cash + Market Value of Positions - Margin Loan.
               Margin loan is implicit: positions bought on margin reduce visible
               cash but IBKR only applies it at T+1/T+2 settlement.           */}
          <div style={{
            marginTop: '0.65rem',
            padding: '0.55rem 0.7rem',
            background: 'rgba(245,158,11,0.07)',
            border: '1px solid rgba(245,158,11,0.22)',
            borderRadius: '8px',
            fontSize: '0.7rem',
            lineHeight: 1.55,
            color: 'var(--text-muted)',
          }}>
            <div style={{ fontWeight: 700, color: '#f59e0b', marginBottom: '0.2rem', fontSize: '0.68rem', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              ⚠️ Margin Account Note
            </div>
            Settled Cash ≠ Net Liquidation Value (NLV). On a Reg-T margin account,
            IBKR's <em>Settled Cash</em> reflects your cash component after margin borrowing.
            Stocks bought on margin reduce visible cash; the full NLV is:
            <div style={{ marginTop: '0.35rem', fontFamily: 'var(--font-display)', fontSize: '0.68rem', color: 'var(--text-secondary)', fontWeight: 600 }}>
              NLV = Cash + Position Market Value − Margin Loan
            </div>
            Cash is reconciled by IBKR at T+1/T+2 settlement — not intraday.
          </div>
        </div>

        <div className="card metric-card">
          <div className="metric-header">
            <span>Unrealized profit</span>
            <div className="metric-icon-wrap" style={{ color: summary.unrealized_pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
              <TrendingUp size={16} />
            </div>
          </div>
          <div className="metric-value" style={{ color: summary.unrealized_pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
            {formatCurrency(summary.unrealized_pnl)}
          </div>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
            Open positions growth
          </span>
        </div>

        <div className="card metric-card">
          <div className="metric-header">
            <span>Win Rate</span>
            <div className="metric-icon-wrap" style={{ color: 'var(--color-warn)' }}>
              <Award size={16} />
            </div>
          </div>
          <div className="metric-value">{summary.win_rate.toFixed(1)}%</div>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
            Across {summary.total_trades} completed trades
          </span>
        </div>
      </div>

      {/* Open Positions */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <Briefcase size={20} color="var(--accent-primary)" />
          Open Positions
          <span style={{ fontSize: '0.72rem', fontWeight: 400, color: 'var(--text-muted)', marginLeft: '0.5rem' }}>
            Click a row to see exit conditions
          </span>
        </h3>
        
        {positions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
            <Briefcase size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
            <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No open positions</p>
            <p style={{ fontSize: '0.85rem' }}>The execution engine will automatically open positions when breakout triggers are detected.</p>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th style={{ width: '1.5rem' }}></th>{/* chevron */}
                  <th onClick={() => requestSortPos('ticker')} style={{ cursor: 'pointer' }}>Ticker{getSortIconPos('ticker')}</th>
                  <th onClick={() => requestSortPos(sortByConvictionPos)} style={{ cursor: 'pointer' }}>Conviction{getSortIconPos(sortByConvictionPos)}</th>
                  <th onClick={() => requestSortPos('shares')} style={{ cursor: 'pointer' }}>Shares{getSortIconPos('shares')}</th>
                  <th onClick={() => requestSortPos('buy_price')} style={{ cursor: 'pointer' }}>Buy Price{getSortIconPos('buy_price')}</th>
                  <th onClick={() => requestSortPos('current_price')} style={{ cursor: 'pointer' }}>Current Price{getSortIconPos('current_price')}</th>
                  <th onClick={() => requestSortPos('hwm_price')} style={{ cursor: 'pointer' }} title="Highest price reached since entry">High Water Mark{getSortIconPos('hwm_price')}</th>
                  <th onClick={() => requestSortPos(sortByMarketValue)} style={{ cursor: 'pointer' }}>Market Value{getSortIconPos(sortByMarketValue)}</th>
                  <th onClick={() => requestSortPos('trail_stop')} style={{ cursor: 'pointer' }}>Trail Stop{getSortIconPos('trail_stop')}</th>
                  <th onClick={() => requestSortPos(sortByLifecyclePos)} style={{ cursor: 'pointer' }} title="Which risk rules are live, armed or degraded for this position">Lifecycle / Tiers{getSortIconPos(sortByLifecyclePos)}</th>
                  <th onClick={() => requestSortPos('pnl')} style={{ cursor: 'pointer' }}>Profit/Loss ($){getSortIconPos('pnl')}</th>
                  <th onClick={() => requestSortPos('buy_date')} style={{ cursor: 'pointer' }}>Buy Date{getSortIconPos('buy_date')}</th>

                </tr>
              </thead>
              <tbody>
                {sortedPositions.map((pos) => {
                  const days = daysHeld(pos.buy_date);
                  const isOpen = expandedRow === pos.ticker;
                  const hwmPrice  = pos.hwm_price || pos.buy_price;
                  const stopLossPct = pos.stop_loss_pct || STOP_LOSS_PCT;
                  const trailStop = parseFloat((hwmPrice * (1 - stopLossPct)).toFixed(2));
                  const profitLockTier = activeProfitLockTier(pos);


                  return (
                    <React.Fragment key={pos.ticker}>
                      <tr
                        onClick={() => toggleRow(pos.ticker)}
                        style={{ cursor: 'pointer', userSelect: 'none' }}
                        className={isOpen ? 'row-expanded' : ''}
                      >
                        {/* Chevron */}
                        <td style={{ color: 'var(--text-muted)', paddingRight: 0 }}>
                          {isOpen
                            ? <ChevronDown size={14} />
                            : <ChevronRight size={14} />}
                        </td>
                        <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {pos.ticker}
                          {pos.company_name && pos.company_name !== pos.ticker && (
                            <div style={{
                              fontSize: '0.67rem',
                              fontWeight: 500,
                              fontFamily: 'inherit',
                              color: 'var(--text-muted)',
                              marginTop: '0.1rem',
                              letterSpacing: '0.01em',
                              maxWidth: '9rem',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}>
                              {pos.company_name}
                            </div>
                          )}
                        </td>
                        {/* Conviction Score at entry */}
                        <td>
                          {(() => {
                            const score = pos.entry_final_score ?? null;
                            const grade = pos.entry_ai_grade ?? null;
                            if (score === null && grade === null) return <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>—</span>;
                            const gradeColor = { A: '#10b981', B: '#3b82f6', C: '#f59e0b', D: '#f43f5e' }[grade] ?? 'var(--text-muted)';
                            const scoreColor = score >= 85 ? '#10b981' : score >= 65 ? '#3b82f6' : score >= 45 ? '#f59e0b' : '#f43f5e';
                            return (
                              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                                {score !== null && (
                                  <span style={{ fontWeight: 800, fontSize: '0.9rem', fontFamily: 'var(--font-display)', color: scoreColor }}>
                                    {score}
                                  </span>
                                )}
                                {grade && (
                                  <span style={{
                                    fontSize: '0.62rem', fontWeight: 800, padding: '0.1rem 0.3rem',
                                    borderRadius: '4px', color: gradeColor,
                                    background: `${gradeColor}22`,
                                    border: `1px solid ${gradeColor}55`,
                                    letterSpacing: '0.04em',
                                  }}>{grade}</span>
                                )}
                              </span>
                            );
                          })()}
                        </td>
                        <td>{pos.shares}</td>
                        <td>{formatCurrency(pos.buy_price)}</td>
                        <td style={{ color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {formatCurrency(pos.current_price)}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <div style={{ fontWeight: 600, color: '#93c5fd' }}>
                            {formatCurrency(hwmPrice)}
                          </div>
                          <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.1rem' }}>
                            {pos.hwm_date ? formatDate(pos.hwm_date) : 'Since entry'}
                          </div>
                        </td>
                        <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {formatCurrency((pos.current_price || pos.buy_price) * pos.shares)}
                        </td>
                        {/* Trail Stop: red, from high_water_mark */}
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <div style={{ color: 'var(--color-down)', fontWeight: 600, fontSize: '0.85rem' }}>
                            {formatCurrency(trailStop)}
                          </div>
                          <div style={{
                            fontSize: '0.64rem',
                            marginTop: '0.12rem',
                            color: profitLockTier ? '#38bdf8' : 'var(--text-muted)',
                            fontWeight: profitLockTier ? 700 : 500,
                          }}>
                            {profitLockTier
                              ? `HWM lock active · ${(profitLockTier[1] * 100).toFixed(1)}%`
                              : 'Base / ATR trail'}
                          </div>
                        </td>
                        {/* Lifecycle / risk tiers */}
                        <LifecycleCell pos={pos} openPositions={positions.length} equity={summary.portfolio_value} />
                        <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {pos.pnl >= 0 ? '+' : ''}{formatCurrency(pos.pnl)}
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                          {formatDate(pos.buy_date)}
                        </td>

                      </tr>
                      {isOpen && (
                        <ExitConditionsPanel pos={pos} formatCurrency={formatCurrency} openPositions={positions.length} equity={summary.portfolio_value} />
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Closed Trades History */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <History size={20} color="var(--accent-secondary)" />
          Recent Simulated Trades
        </h3>

        {recentTrades.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem 1rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            No trades executed yet.
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th onClick={() => requestSortTrades('ticker')} style={{ cursor: 'pointer' }}>Ticker{getSortIconTrades('ticker')}</th>
                  <th onClick={() => requestSortTrades('shares')} style={{ cursor: 'pointer' }}>Shares{getSortIconTrades('shares')}</th>
                  <th onClick={() => requestSortTrades('buy_price')} style={{ cursor: 'pointer' }}>Buy Price{getSortIconTrades('buy_price')}</th>
                  <th onClick={() => requestSortTrades('sell_price')} style={{ cursor: 'pointer' }}>Sell Price{getSortIconTrades('sell_price')}</th>
                  <th onClick={() => requestSortTrades('buy_date')} style={{ cursor: 'pointer' }}>Buy Date{getSortIconTrades('buy_date')}</th>
                  <th onClick={() => requestSortTrades('sell_date')} style={{ cursor: 'pointer' }}>Sell Date{getSortIconTrades('sell_date')}</th>
                  <th onClick={() => requestSortTrades('profit_loss')} style={{ cursor: 'pointer' }}>P&L ($){getSortIconTrades('profit_loss')}</th>
                  <th onClick={() => requestSortTrades('percent_return')} style={{ cursor: 'pointer' }}>Return (%){getSortIconTrades('percent_return')}</th>
                  <th onClick={() => requestSortTrades('exit_reason')} style={{ cursor: 'pointer' }}>Exit Reason{getSortIconTrades('exit_reason')}</th>
                </tr>
              </thead>
              <tbody>
                {sortedTrades.map((trade) => (
                  <tr key={trade.id}>
                    <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)' }}>{trade.ticker}</td>
                    <td>{trade.shares}</td>
                    <td>{formatCurrency(trade.buy_price)}</td>
                    <td>{formatCurrency(trade.sell_price)}</td>
                    <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      {formatDate(trade.buy_date)}
                    </td>
                    <td 
                      style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', cursor: 'help' }}
                      title={trade.buy_date && trade.sell_date ? `Held For: ${Math.floor((new Date(trade.sell_date) - new Date(trade.buy_date)) / (1000 * 60 * 60 * 24))} Days` : ''}
                    >
                      {formatDate(trade.sell_date)}
                    </td>
                    <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                      {trade.profit_loss >= 0 ? '+' : ''}{formatCurrency(trade.profit_loss)}
                    </td>
                    <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                      {trade.percent_return.toFixed(2)}%
                    </td>
                    <td>
                      {(() => {
                        const cleanExitReason = getCleanExitReason(trade.exit_reason, trade.percent_return);
                        const detailedExitTooltip = getDetailedExitTooltip(trade.exit_reason, trade.percent_return);
                        return (
                          <span 
                            className={`badge ${cleanExitReason.includes('Profit Target') ? 'badge-success' : cleanExitReason.includes('Stop Loss') ? 'badge-danger' : 'badge-warning'}`}
                            title={detailedExitTooltip}
                          >
                            {cleanExitReason}
                          </span>
                        );
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {/* ── Deploy Version Badge ──────────────────────────────────────
           Fetches /api/version for git SHA + build time.
           Amber 'stale?' when build args were not injected (local/manual builds). */}
      {buildVersion && (() => {
        const sha     = buildVersion.git_commit;
        const ts      = buildVersion.build_time;
        const isKnown = sha && sha !== 'unknown';
        const shortSha = isKnown ? sha.slice(0, 7) : '???????';
        const buildDate = ts && ts !== 'unknown'
          ? new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : 'unknown';
        return (
          <a
            href="/api/version"
            target="_blank"
            rel="noreferrer"
            title={`Deployed commit: ${sha}\nBuilt: ${ts}`}
            style={{
              position: 'fixed', bottom: '1rem', right: '1.25rem',
              display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
              padding: '0.25rem 0.6rem',
              background: isKnown ? 'rgba(16,185,129,0.1)' : 'rgba(245,158,11,0.12)',
              border: `1px solid ${isKnown ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.4)'}`,
              borderRadius: '999px',
              fontSize: '0.65rem', fontWeight: 700, fontFamily: 'var(--font-display)',
              color: isKnown ? '#10b981' : '#f59e0b',
              textDecoration: 'none', letterSpacing: '0.04em',
              opacity: 0.75,
              zIndex: 100,
            }}
          >
            {isKnown ? '🟢' : '🟡'} {shortSha} · {buildDate}{!isKnown && ' — stale?'}
          </a>
        );
      })()}

    </div>
  );
}
