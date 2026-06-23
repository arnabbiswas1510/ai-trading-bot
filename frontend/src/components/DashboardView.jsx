import React, { useState } from 'react';
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
  Target,
  TrendingDown as StopIcon,
  Zap,
  Calendar
} from 'lucide-react';

// ── Constants mirrored from execution_agent.py env defaults ──────────────────
const STALE_HOLD_DAYS     = 15;   // days before sideways eligible for rotation
const STALE_HOLD_MAX_GAIN = 0.03; // <3% gain = "sideways"
const POWER_HOLD_GAIN     = 0.20; // 20%+ in <21 days triggers power hold
const POWER_HOLD_DAYS_LIM = 21;   // window to qualify
const STOP_LOSS_PCT       = 0.07; // 7% trailing stop

// ── Helpers ──────────────────────────────────────────────────────────────────
function daysHeld(buyDate) {
  if (!buyDate) return 0;
  const buy = new Date(buyDate);
  const now = new Date();
  return Math.floor((now - buy) / (1000 * 60 * 60 * 24));
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// Derive the most urgent status badge for the compact column
function getStatusBadge(pos, days) {
  const gain = (pos.current_price / pos.buy_price) - 1.0;

  if (pos.is_power_hold) {
    const expiry = pos.power_hold_expiry ? new Date(pos.power_hold_expiry) : null;
    const daysLeft = expiry ? Math.ceil((expiry - new Date()) / (1000 * 60 * 60 * 24)) : null;
    return { label: daysLeft != null ? `PH · ${daysLeft}d left` : 'Power Hold', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', icon: '🛡️' };
  }

  // Stale risk: held >= stale threshold AND gain < max
  if (days >= STALE_HOLD_DAYS && gain < STALE_HOLD_MAX_GAIN) {
    return { label: 'Stale · Eligible', color: '#f43f5e', bg: 'rgba(244,63,94,0.10)', icon: '⚠️' };
  }

  // Approaching stale (within 3 days)
  const daysToStale = STALE_HOLD_DAYS - days;
  if (daysToStale <= 3 && daysToStale > 0 && gain < STALE_HOLD_MAX_GAIN) {
    return { label: `Stale in ${daysToStale}d`, color: '#f59e0b', bg: 'rgba(245,158,11,0.10)', icon: '⏳' };
  }

  // Power hold eligible window (still watching)
  if (!pos.is_power_hold && days <= POWER_HOLD_DAYS_LIM && gain >= 0.10) {
    return { label: 'PH Watch', color: '#a78bfa', bg: 'rgba(167,139,250,0.12)', icon: '👁' };
  }

  return null; // Normal — no badge
}

// ── Position Intelligence Panel (expandable) ─────────────────────────────────
function ExitConditionsPanel({ pos, formatCurrency }) {
  const days = daysHeld(pos.buy_date);
  const gain = (pos.current_price / pos.buy_price) - 1.0;
  const gainPct = (gain * 100).toFixed(1);
  const trailStop = parseFloat(((pos.high_water_mark || pos.buy_price) * (1 - STOP_LOSS_PCT)).toFixed(2));
  const isPH = pos.is_power_hold;
  const phExpiry = pos.power_hold_expiry ? new Date(pos.power_hold_expiry) : null;
  const phDaysLeft = phExpiry ? Math.ceil((phExpiry - new Date()) / (1000 * 60 * 60 * 24)) : null;

  const daysToStale = STALE_HOLD_DAYS - days;
  const isStaleEligible = days >= STALE_HOLD_DAYS && gain < STALE_HOLD_MAX_GAIN;
  const phQualifyGain = POWER_HOLD_GAIN * 100;
  const phDaysRemain = POWER_HOLD_DAYS_LIM - days;

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
      <td colSpan={9} style={{ padding: 0 }}>
        <div style={panelStyle}>

          {/* ── Holding Info ─────────────────────────── */}
          <div style={cardStyle('147,197,253')}>
            <div style={labelStyle}>📅 Position</div>
            <div style={valueStyle('var(--text-primary)')}>{days} days held</div>
            <div style={noteStyle}>
              Bought {formatDate(pos.buy_date)}<br />
              Source: {pos.buy_source === 'momentum_triggers' ? 'Momentum' : 'CANSLIM Breakout'}<br />
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
              {isPH ? '⚠️ Profit target suspended during Power Hold.' : `Profit target also active at ${formatCurrency(pos.profit_target)}.`}
            </div>
          </div>

          {/* ── Profit Target ────────────────────────── */}
          <div style={cardStyle('52,211,153')}>
            <div style={labelStyle}>🟢 Profit Target (IBKR GTC)</div>
            <div style={valueStyle(isPH ? 'var(--text-muted)' : '#34d399')}>
              {isPH ? <span style={{ textDecoration: 'line-through', opacity: 0.4 }}>{formatCurrency(pos.profit_target)}</span> : formatCurrency(pos.profit_target)}
            </div>
            <div style={noteStyle}>
              +25% from entry price.<br />
              {isPH
                ? '🛡️ Suspended — Power Hold active. Only trail stop applies.'
                : 'OCA pair with trail stop — whichever fires first wins.'}
            </div>
          </div>

          {/* ── Power Hold ───────────────────────────── */}
          <div style={cardStyle(isPH ? '245,158,11' : '167,139,250')}>
            <div style={labelStyle}>🛡️ Power Hold Rule</div>
            {isPH ? (
              <>
                <div style={valueStyle('#f59e0b')}>
                  ACTIVE · {phDaysLeft != null ? `${phDaysLeft} days left` : `expires ${formatDate(pos.power_hold_expiry)}`}
                </div>
                <div style={noteStyle}>
                  Holds until {formatDate(pos.power_hold_expiry)}.<br />
                  Profit target re-activates on expiry.<br />
                  Only trailing stop applies during hold.
                </div>
              </>
            ) : days <= POWER_HOLD_DAYS_LIM ? (
              <>
                <div style={valueStyle('#a78bfa')}>Watching · {phDaysRemain}d window</div>
                <div style={noteStyle}>
                  Triggers if +{phQualifyGain}% within {POWER_HOLD_DAYS_LIM} days of buy.<br />
                  Currently {gainPct}% — need +{(POWER_HOLD_GAIN * 100 - parseFloat(gainPct)).toFixed(1)}% more.<br />
                  Would lock in 8-week hold, suspend profit target.
                </div>
              </>
            ) : (
              <>
                <div style={valueStyle('var(--text-muted)')}>Window Closed</div>
                <div style={noteStyle}>
                  +{phQualifyGain}% surge required within {POWER_HOLD_DAYS_LIM} days of buy.<br />
                  Day {days} — qualification window has passed.<br />
                  Standard trail stop + profit target remain active.
                </div>
              </>
            )}
          </div>

          {/* ── Stale Rotation ───────────────────────── */}
          <div style={cardStyle(isStaleEligible ? '244,63,94' : '148,163,184')}>
            <div style={labelStyle}>⏳ Stale Rotation</div>
            {isPH ? (
              <>
                <div style={valueStyle('#f59e0b')}>Exempt</div>
                <div style={noteStyle}>Power Hold positions cannot be rotated.</div>
              </>
            ) : isStaleEligible ? (
              <>
                <div style={valueStyle('#f43f5e')}>Eligible now</div>
                <div style={noteStyle}>
                  Held {days}d with only {gainPct}% gain (threshold: &lt;{STALE_HOLD_MAX_GAIN * 100}%).<br />
                  Will rotate if portfolio is full AND a stronger breakout fires today.<br />
                  Worst performer among stale candidates is sold first.
                </div>
              </>
            ) : (
              <>
                <div style={valueStyle('var(--text-secondary)')}>
                  {daysToStale > 0 ? `In ${daysToStale} days` : 'Eligible'} {gain >= STALE_HOLD_MAX_GAIN ? `· (protected by +${gainPct}% gain)` : ''}
                </div>
                <div style={noteStyle}>
                  Eligible after {STALE_HOLD_DAYS}d if gain &lt;{STALE_HOLD_MAX_GAIN * 100}%.<br />
                  Currently day {days}, gain {gainPct}%.<br />
                  Requires portfolio full + fresh trigger today.
                </div>
              </>
            )}
          </div>

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
    unrealized_pnl: 0.0,
    total_pnl: 0.0,
    total_pnl_pct: 0.0,
    win_rate: 0.0,
    total_trades: 0
  };
  
  const positions = data?.positions || [];
  const recentTrades = trades?.slice(0, 5) || [];

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
            <span>Portfolio Value</span>
            <div className="metric-icon-wrap" style={{ color: 'var(--accent-primary)' }}>
              <Briefcase size={16} />
            </div>
          </div>
          <div className="metric-value">{formatCurrency(summary.portfolio_value)}</div>
          <div className={`metric-change ${summary.total_pnl >= 0 ? 'up' : 'down'}`}>
            {summary.total_pnl >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            <span>{summary.total_pnl_pct.toFixed(2)}% ({formatCurrency(summary.total_pnl)})</span>
          </div>
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
                  <th>Ticker</th>
                  <th>Shares</th>
                  <th>Buy Price</th>
                  <th>Current Price</th>
                  <th>Trail Stop</th>
                  <th>Profit Target</th>
                  <th>Profit/Loss ($)</th>
                  <th>Buy Date</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const days = daysHeld(pos.buy_date);
                  const badge = getStatusBadge(pos, days);
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
                        <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)' }}>{pos.ticker}</td>
                        <td>{pos.shares}</td>
                        <td>{formatCurrency(pos.buy_price)}</td>
                        <td>{formatCurrency(pos.current_price)}</td>
                        {/* Trail Stop: red, from high_water_mark */}
                        <td style={{ color: 'var(--color-down)', fontWeight: 600, fontSize: '0.85rem' }}>
                          {formatCurrency(trailStop)}
                        </td>
                        {/* Profit Target: strikethrough if Power Hold active */}
                        <td style={{
                          color: pos.is_power_hold ? 'var(--text-muted)' : 'var(--color-up)',
                          fontWeight: 600,
                          fontSize: '0.85rem',
                          textDecoration: pos.is_power_hold ? 'line-through' : 'none',
                          opacity: pos.is_power_hold ? 0.45 : 1
                        }}>
                          {formatCurrency(pos.profit_target)}
                        </td>
                        <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {pos.pnl >= 0 ? '+' : ''}{formatCurrency(pos.pnl)}
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                          {formatDate(pos.buy_date)}
                        </td>
                        {/* Status badge */}
                        <td>
                          {badge ? (
                            <span style={{
                              display: 'inline-block',
                              padding: '0.2rem 0.55rem',
                              borderRadius: '20px',
                              fontSize: '0.7rem',
                              fontWeight: 700,
                              color: badge.color,
                              background: badge.bg,
                              border: `1px solid ${badge.color}44`,
                              whiteSpace: 'nowrap',
                            }}>
                              {badge.icon} {badge.label}
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Normal</span>
                          )}
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
                  <th>Ticker</th>
                  <th>Shares</th>
                  <th>Buy Price</th>
                  <th>Sell Price</th>
                  <th>Buy Date</th>
                  <th>Sell Date</th>
                  <th>P&L ($)</th>
                  <th>Return (%)</th>
                  <th>Exit Reason</th>
                </tr>
              </thead>
              <tbody>
                {recentTrades.map((trade) => (
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
                      <span className={`badge ${trade.exit_reason === 'Profit Target' ? 'badge-success' : trade.exit_reason === 'Stop Loss' ? 'badge-danger' : 'badge-warning'}`}>
                        {trade.exit_reason}
                      </span>
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
