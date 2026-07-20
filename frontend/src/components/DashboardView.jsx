import React, { useState, useEffect } from 'react';
import useSortableTable from '../hooks/useSortableTable';
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
const STOP_LOSS_PCT  = 0.07;  // 7% trailing stop
const PLATEAU_DAYS   = 7;    // days without new HWM before plateau exit eligible (lowered from 10)

// ── Stable module-level sort-key functions ────────────────────────────────────
// Must be module-level so the === reference stays identical across renders
// (required for getSortIcon to light up the active-sort arrow).
// Mirrors display fallback: entry_final_score → entry_ai_rating → 0
const sortByConvictionPos = (p) => p.entry_final_score ?? p.entry_ai_rating ?? 0;
const sortByMarketValue   = (p) => (p.current_price || p.buy_price) * p.shares;

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
  if (lower.includes('stale rotation') || lower.includes('plateau rotation')) {
    return 'Plateau Rotation';
  }
  if (lower.includes('force sell') || lower.includes('user request')) {
    return 'Manual Force Sell';
  }
  if (lower.includes('manual close')) {
    return 'Manual Close';
  }
  if (lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
    if (pctReturn >= 24.0) {
      return 'Profit Target (+25%)';
    } else {
      return 'Stop Loss (-7%)';
    }
  }

  return raw;
}

function getDetailedExitTooltip(raw, pctReturn) {
  if (!raw) return `Manual close at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  const lower = raw.toLowerCase();
  
  if (lower.includes('ema-21') || lower.includes('exit ma')) {
    return raw;
  }
  if (lower.includes('stale rotation')) {
    return raw;
  }
  if (lower.includes('force sell') || lower.includes('user request')) {
    return `Manual Force Sell executed at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  }
  if (lower.includes('manual close')) {
    return `Manual Close on IBKR reconciled at ${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}% return`;
  }
  if (lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
    if (pctReturn >= 24.0) {
      return `Profit Target Filled (+25.0% target) with final return of +${pctReturn.toFixed(2)}%`;
    } else {
      return `Trailing Stop Loss Triggered (-7.0% stop) with final return of ${pctReturn.toFixed(2)}%`;
    }
  }
  return `${raw} (${pctReturn >= 0 ? '+' : ''}${pctReturn.toFixed(2)}%)`;
}

// Derive the most urgent status badge for the compact column
function getStatusBadge(pos, days) {
  // Power Hold and Stale Rotation rules removed — only plateau exits are active.
  return null; // Normal — no special badge
}

// ── Position Intelligence Panel (expandable) ─────────────────────────────────
function ExitConditionsPanel({ pos, formatCurrency }) {
  const days = daysHeld(pos.buy_date);
  const hwmPrice  = pos.hwm_price || pos.buy_price;  // hwm_price: highest price seen since buy
  const trailStop = parseFloat((hwmPrice * (1 - STOP_LOSS_PCT)).toFixed(2));

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
      <td colSpan={11} style={{ padding: 0 }}>
        <div style={panelStyle}>

          {/* ── Holding Info ─────────────────────────── */}
          <div style={cardStyle('147,197,253')}>
            <div style={labelStyle}>📅 Position</div>
            <div style={valueStyle('var(--text-primary)')}>{days} days held</div>
            <div style={noteStyle}>
              Bought {formatDate(pos.buy_date)}<br />
              Source: CANSLIM Breakout<br />
              Entry: {formatCurrency(pos.buy_price)} · High: {formatCurrency(hwmPrice)}
            </div>
          </div>

          {/* ── Trail Stop ───────────────────────────── */}
          <div style={cardStyle('248,113,113')}>
            <div style={labelStyle}>🔴 Trail Stop (IBKR GTC)</div>
            <div style={valueStyle('#f87171')}>{formatCurrency(trailStop)}</div>
            <div style={noteStyle}>
              7% below HWM price of {formatCurrency(hwmPrice)}<br />
              Managed by IBKR — fires automatically.<br />
              EMA-21 exit also active at end of each trading day.
            </div>
          </div>

          {/* ── Time-Stop & Rotation Health ───────────────────────────── */}
          {(() => {
            const daysHeld = pos.days_held != null ? pos.days_held : 0;
            const pct = Math.min(daysHeld / 7, 1.0);
            
            // Determine exit rule status
            let timeStopStatus = "Relying on trailing stop";
            let statusColor = "#10b981"; // green
            if (daysHeld <= 2) {
              timeStopStatus = "🛡️ Days 1-2: Room to breathe (Trailing stop active)";
              statusColor = "#10b981";
            } else if (daysHeld <= 6) {
              timeStopStatus = "🔄 Days 3-6: Rank & Replace eligible (Drift check active)";
              statusColor = "#f59e0b"; // orange
            } else {
              timeStopStatus = "⏱️ Day 7+: Mandatory Time-Stop if gain < 2.0% | EMA-21 active";
              statusColor = pos.unrealized_gain_pct < 2.0 ? "#f43f5e" : "#10b981";
            }

            // Break-even status
            const hasCushion = pos.highest_unrealized_pct >= 5.0;
            const breakEvenColor = hasCushion ? "#10b981" : "var(--text-muted)";

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
                <div style={labelStyle}>⏱️ Rotation & Time-Stop Health</div>

                {/* Progress bar */}
                <div style={valueStyle(statusColor)}>
                  {daysHeld} / 7 trading days held
                </div>
                <div style={{ height: '4px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', margin: '0.4rem 0' }}>
                  <div style={{ height: '100%', width: `${pct * 100}%`, background: statusColor, borderRadius: '2px', transition: 'width 0.3s' }} />
                </div>
                <div style={{ ...noteStyle, color: statusColor, fontWeight: 600, marginBottom: '0.6rem' }}>
                  {timeStopStatus}
                </div>

                {/* Profit Cushion / Break-Even status */}
                <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '0.5rem', marginTop: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Peak Cushion:</span>
                    <span style={{ fontWeight: 700, color: pos.highest_unrealized_pct >= 5.0 ? '#10b981' : 'var(--text-secondary)' }}>
                      {pos.highest_unrealized_pct ? `+${pos.highest_unrealized_pct.toFixed(2)}%` : '0.00%'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', marginTop: '0.2rem' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Break-Even Stop:</span>
                    <span style={{ fontWeight: 700, color: breakEvenColor }}>
                      {hasCushion ? '🔒 Active (Locked)' : '⏳ Inactive (Need +5.0% peak)'}
                    </span>
                  </div>
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
                    {pos.stop_loss_pct ? `${(pos.stop_loss_pct * 100).toFixed(2)}%` : '7.00%'}
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
                    <div style={{
                      marginTop: '0.75rem', padding: '0.6rem 0.75rem',
                      background: tierInfo.bg, border: `1px solid ${tierInfo.border}`,
                      borderRadius: '8px',
                    }}>
                      <div style={{ fontWeight: 700, fontSize: '0.8rem', color: tierInfo.text, marginBottom: '0.35rem' }}>
                        {tierInfo.label}
                      </div>
                      <div style={{ ...noteStyle, marginBottom: '0.5rem' }}>
                        Hard auto-sell fires at day {PLATEAU_DAYS} if no action taken.
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
                  <th onClick={() => requestSortPos(sortByMarketValue)} style={{ cursor: 'pointer' }}>Market Value{getSortIconPos(sortByMarketValue)}</th>
                  <th onClick={() => requestSortPos('trail_stop')} style={{ cursor: 'pointer' }}>Trail Stop{getSortIconPos('trail_stop')}</th>
                  <th onClick={() => requestSortPos('hwm_date')} style={{ cursor: 'pointer' }}>Plateau Days{getSortIconPos('hwm_date')}</th>
                  <th onClick={() => requestSortPos('pnl')} style={{ cursor: 'pointer' }}>Profit/Loss ($){getSortIconPos('pnl')}</th>
                  <th onClick={() => requestSortPos('buy_date')} style={{ cursor: 'pointer' }}>Buy Date{getSortIconPos('buy_date')}</th>

                </tr>
              </thead>
              <tbody>
                {sortedPositions.map((pos) => {
                  const days = daysHeld(pos.buy_date);
                  const isOpen = expandedRow === pos.ticker;
                  const hwmPrice  = pos.hwm_price || pos.buy_price;
                  const trailStop = parseFloat((hwmPrice * (1 - STOP_LOSS_PCT)).toFixed(2));

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
                        <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {formatCurrency((pos.current_price || pos.buy_price) * pos.shares)}
                        </td>
                        {/* Trail Stop: red, from high_water_mark */}
                        <td style={{ color: 'var(--color-down)', fontWeight: 600, fontSize: '0.85rem' }}>
                          {formatCurrency(trailStop)}
                        </td>
                        {/* Plateau Days + rotation badge */}
                        {(() => {
                          const serverDays = pos.days_since_hwm;
                          const hwmDate    = pos.hwm_date || pos.buy_date;
                          const daysSinceHWM = serverDays != null
                            ? serverDays
                            : tradingDaysBetween(hwmDate, new Date());
                          const pct = Math.min(daysSinceHWM / PLATEAU_DAYS, 1.0);
                          const isPlateauing = daysSinceHWM >= PLATEAU_DAYS;
                          const color = isPlateauing ? 'var(--color-down)' : pct >= 0.7 ? '#f59e0b' : 'var(--color-up)';
                          const rec = pos.rotation_recommendation;
                          const recLabel = rec === 'TIER_1'           ? 'T1'
                                         : rec === 'TIER_2'           ? 'T2'
                                         : rec === 'PROGRESS_DEFICIT' ? 'PD'
                                         : rec === 'FLOOR_BREAK'      ? 'FB'
                                         : null;
                          const recColor = rec === 'TIER_1'           ? '#f43f5e'
                                         : rec === 'TIER_2'           ? '#f59e0b'
                                         : rec === 'PROGRESS_DEFICIT' ? '#fbbf24'
                                         : rec === 'FLOOR_BREAK'      ? '#ef4444'
                                         : '#f59e0b';
                          return (
                            <td>
                              <span style={{ fontWeight: 700, fontSize: '0.85rem', color }}>
                                {daysSinceHWM}d
                              </span>
                              {recLabel && (
                                <span style={{
                                  marginLeft: '0.35rem', fontSize: '0.6rem', fontWeight: 800,
                                  padding: '0.1rem 0.3rem', borderRadius: '3px',
                                  background: `${recColor}22`, color: recColor,
                                  border: `1px solid ${recColor}55`,
                                  verticalAlign: 'middle',
                                  letterSpacing: '0.04em',
                                }}>{recLabel}</span>
                              )}
                            </td>
                          );
                        })()}
                        <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {pos.pnl >= 0 ? '+' : ''}{formatCurrency(pos.pnl)}
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                          {formatDate(pos.buy_date)}
                        </td>

                      </tr>
                      {isOpen && (
                        <ExitConditionsPanel pos={pos} formatCurrency={formatCurrency} />
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
