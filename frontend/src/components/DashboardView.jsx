import React, { useState } from 'react';
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
const PLATEAU_DAYS   = 10;   // days without new HWM before plateau exit eligible

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
  
  if (lower.includes('ema-21') || lower.includes('exit ma')) {
    return 'EMA-21 Exit';
  }
  if (lower.includes('stale rotation')) {
    return 'Stale Rotation';
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
  const trailStop = parseFloat(((pos.high_water_mark || pos.buy_price) * (1 - STOP_LOSS_PCT)).toFixed(2));

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
              Entry: {formatCurrency(pos.buy_price)} · High: {formatCurrency(pos.high_water_mark || pos.buy_price)}
            </div>
          </div>

          {/* ── Trail Stop ───────────────────────────── */}
          <div style={cardStyle('248,113,113')}>
            <div style={labelStyle}>🔴 Trail Stop (IBKR GTC)</div>
            <div style={valueStyle('#f87171')}>{formatCurrency(trailStop)}</div>
            <div style={noteStyle}>
              7% below high of {formatCurrency(pos.high_water_mark || pos.buy_price)}<br />
              Managed by IBKR — fires automatically.<br />
              EMA-21 exit also active at end of each trading day.
            </div>
          </div>

          {/* ── Plateau Risk ─────────────────────────── */}
          {(() => {
            const hwmDate = pos.hwm_date || pos.buy_date;
            const daysSinceHWM = tradingDaysBetween(hwmDate, new Date());
            const pct = Math.min(daysSinceHWM / PLATEAU_DAYS, 1.0);
            const isPlateauing = daysSinceHWM >= PLATEAU_DAYS;
            const color = isPlateauing ? '#f43f5e' : pct >= 0.7 ? '#f59e0b' : '#10b981';
            return (
              <div style={cardStyle(isPlateauing ? '244,63,94' : pct >= 0.7 ? '245,158,11' : '52,211,153')}>
                <div style={labelStyle}>⏱️ Plateau Exit</div>
                <div style={valueStyle(color)}>
                  {daysSinceHWM} / {PLATEAU_DAYS} trading days
                  {isPlateauing && <span style={{ marginLeft: '0.4rem', fontSize: '0.72rem' }}>⚠️ ELIGIBLE</span>}
                </div>
                <div style={{ height: '4px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', margin: '0.4rem 0' }}>
                  <div style={{ height: '100%', width: `${pct * 100}%`, background: color, borderRadius: '2px', transition: 'width 0.3s' }} />
                </div>
                <div style={noteStyle}>
                  HWM last set: {pos.hwm_date ? new Date(pos.hwm_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'at entry'}<br />
                  Exit fires at {PLATEAU_DAYS} trading days with &lt;3% gain.<br />
                  {isPlateauing ? '🚨 Plateau rotation eligible today.' : `${PLATEAU_DAYS - daysSinceHWM} trading days remaining.`}
                </div>
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
            Available to trade
          </span>
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
                  const trailStop = parseFloat(((pos.high_water_mark || pos.buy_price) * (1 - STOP_LOSS_PCT)).toFixed(2));

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
                        <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>{pos.ticker}</td>
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
                        {/* Plateau Days: trading days since HWM (weekends + holidays excluded) */}
                        {(() => {
                          const hwmDate = pos.hwm_date || pos.buy_date;
                          const daysSinceHWM = tradingDaysBetween(hwmDate, new Date());
                          const pct = Math.min(daysSinceHWM / PLATEAU_DAYS, 1.0);
                          const isPlateauing = daysSinceHWM >= PLATEAU_DAYS;
                          const color = isPlateauing ? 'var(--color-down)' : pct >= 0.7 ? '#f59e0b' : 'var(--color-up)';
                          return (
                            <td>
                              <span style={{ fontWeight: 700, fontSize: '0.85rem', color }}>
                                {daysSinceHWM}d
                              </span>
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
      
    </div>
  );
}
