import React, { useState, useEffect } from 'react';
import useSortableTable from '../hooks/useSortableTable';
import { Activity, AlertCircle, Calendar, Zap, TrendingUp, History, ShieldAlert, Sparkles, TrendingDown, ChevronDown, ChevronRight, BarChart2, Brain, Droplets, Gauge, Clock } from 'lucide-react';

// Stable sort-key function — must be module-level so reference is identical
// across renders (required for getSortIcon's === comparison to light up the arrow).
// Mirrors the display fallback: final_score (quality + AI bonus) → ai_rating → 0.
const sortByConviction = (t) => t.final_score ?? t.ai_rating ?? 0;

// ── Score detail panel (expandable row) ──────────────────────────────────────

function ScoreBar({ value, max = 100, color }) {
  const pct = Math.min(100, Math.max(0, ((value ?? 0) / max) * 100));
  return (
    <div style={{ height: '5px', background: 'rgba(255,255,255,0.07)', borderRadius: '3px', marginTop: '0.35rem' }}>
      <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: '3px', transition: 'width 0.4s ease' }} />
    </div>
  );
}

function ScoreCard({ label, icon, value, subtitle, color, rgb, max = 100 }) {
  const cardStyle = {
    background: `rgba(${rgb}, 0.06)`,
    border: `1px solid rgba(${rgb}, 0.18)`,
    borderRadius: '10px',
    padding: '0.75rem 1rem',
    minWidth: 0,
  };
  const labelStyle = { fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.35rem' };
  return (
    <div style={cardStyle}>
      <div style={labelStyle}>{icon}{label}</div>
      <div style={{ fontSize: '1.15rem', fontWeight: 800, color, fontFamily: 'var(--font-display)' }}>
        {value ?? <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>—</span>}
        {typeof value === 'number' && <span style={{ fontSize: '0.65rem', fontWeight: 500, color: 'var(--text-muted)', marginLeft: '0.2rem' }}>/100</span>}
      </div>
      <ScoreBar value={value} max={max} color={color} />
      {subtitle && <div style={{ fontSize: '0.71rem', color: 'var(--text-muted)', marginTop: '0.35rem', lineHeight: 1.4 }}>{subtitle}</div>}
    </div>
  );
}

function BreakoutDetailPanel({ b, colSpan }) {
  const fmtVol = (v) => v != null ? new Intl.NumberFormat('en-US', { notation: 'compact' }).format(v) : '—';

  const estDays  = b.est_days_to_target;
  const atrPct   = b.atr_pct;
  const swingLabel = estDays == null ? '—'
    : estDays <= 15  ? '🚀 Fast mover (< 15 days)'
    : estDays <= 30  ? '✅ Swing-compatible (≤ 30 days)'
    : estDays <= 60  ? '⚠️ Slow mover (≤ 60 days)'
    : '❌ Long-term only (> 60 days)';
  const swingColor = estDays == null ? 'var(--text-muted)'
    : estDays <= 15  ? '#10b981'
    : estDays <= 30  ? '#3b82f6'
    : estDays <= 60  ? '#f59e0b'
    : '#f43f5e';

  const techScore = b.technical_score;
  const liqScore  = b.liquidity_score;
  const aiScore   = b.ai_rating;
  const sentScore = b.sentiment_score;
  const rsScore   = b.rs_score;

  const scoreColor = (s) =>
    s == null ? 'var(--text-muted)'
    : s >= 75  ? '#10b981'
    : s >= 55  ? '#3b82f6'
    : s >= 35  ? '#f59e0b'
    : '#f43f5e';

  return (
    <tr>
      <td colSpan={colSpan} style={{ padding: 0 }}>
        <div style={{
          background: 'rgba(255,255,255,0.02)',
          borderTop: '1px solid rgba(255,255,255,0.06)',
          padding: '1rem 1.25rem 1.25rem',
        }}>

          {/* ── Score component grid ─────────────────────────────────── */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>

            <ScoreCard
              label="Technical"
              icon={<BarChart2 size={11} />}
              value={techScore}
              color={scoreColor(techScore)}
              rgb={techScore >= 55 ? '59,130,246' : '245,158,11'}
              subtitle={`Vol surge: ${b.volume_surge?.toFixed(2) ?? '—'}×  ·  Pivot dist: ${b.pivot_distance_pct != null ? (b.pivot_distance_pct > 0 ? '+' : '') + b.pivot_distance_pct.toFixed(2) + '%' : '—'}`}
            />

            <ScoreCard
              label="Liquidity"
              icon={<Droplets size={11} />}
              value={liqScore}
              color={scoreColor(liqScore)}
              rgb={liqScore >= 55 ? '52,211,153' : '245,158,11'}
              subtitle={`Avg 50d vol: ${fmtVol(b.avg_volume_50)}  ·  Price: $${b.close_price?.toFixed(2) ?? '—'}`}
            />

            <ScoreCard
              label="AI Rating"
              icon={<Brain size={11} />}
              value={aiScore}
              color={scoreColor(aiScore)}
              rgb={aiScore >= 55 ? '139,92,246' : '244,63,94'}
              subtitle={b.ai_grade ? `Grade: ${b.ai_grade}` : undefined}
            />

            <ScoreCard
              label="Sentiment"
              icon={<Sparkles size={11} />}
              value={sentScore}
              color={scoreColor(sentScore)}
              rgb={sentScore >= 55 ? '251,191,36' : '248,113,113'}
              subtitle="News headline score"
            />

            <ScoreCard
              label="RS vs SPY"
              icon={<TrendingUp size={11} />}
              value={rsScore}
              color={scoreColor(rsScore)}
              rgb={rsScore >= 50 ? '16,185,129' : '244,63,94'}
              subtitle="12-week relative strength"
            />

          </div>

          {/* ── Swing velocity ───────────────────────────────────────── */}
          <div style={{
            display: 'flex',
            gap: '1.5rem',
            flexWrap: 'wrap',
            alignItems: 'center',
            padding: '0.6rem 0.9rem',
            background: `rgba(${estDays <= 30 ? '59,130,246' : '245,158,11'}, 0.06)`,
            border: `1px solid rgba(${estDays <= 30 ? '59,130,246' : '245,158,11'}, 0.18)`,
            borderRadius: '8px',
            marginBottom: b.score_rationale ? '0.75rem' : 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Gauge size={14} color="var(--text-muted)" />
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700 }}>ATR/day</span>
              <span style={{ fontWeight: 800, fontSize: '0.9rem', color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                {atrPct != null ? `${atrPct}%` : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Clock size={14} color="var(--text-muted)" />
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700 }}>Est. days to +25%</span>
              <span style={{ fontWeight: 800, fontSize: '0.9rem', color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                {estDays != null ? estDays : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: 600, color: swingColor }}>{swingLabel}</span>
            </div>
          </div>

          {/* ── AI Rationale ─────────────────────────────────────────── */}
          {b.score_rationale && (
            <div style={{
              padding: '0.65rem 0.9rem',
              background: 'rgba(139,92,246,0.06)',
              border: '1px solid rgba(139,92,246,0.18)',
              borderRadius: '8px',
              fontSize: '0.82rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
            }}>
              <span style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginRight: '0.5rem' }}>
                🤖 AI Rationale:
              </span>
              {b.score_rationale}
            </div>
          )}

        </div>
      </td>
    </tr>
  );
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function BreakoutTable({ list }) {
  const [expandedRow, setExpandedRow] = useState(null);
  const { items: sortedList, requestSort, getSortIcon } = useSortableTable(list, sortByConviction, 'desc');
  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  const toggleRow = (ticker) => setExpandedRow(prev => prev === ticker ? null : ticker);

  const COL_COUNT = 8;

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th style={{ width: '2rem' }}></th>
            <th onClick={() => requestSort('ticker')} style={{ cursor: 'pointer' }}>Ticker{getSortIcon('ticker')}</th>
            <th onClick={() => requestSort(sortByConviction)} style={{ cursor: 'pointer' }}>Conviction Score{getSortIcon(sortByConviction)}</th>
            <th onClick={() => requestSort('triggered_at')} style={{ cursor: 'pointer' }}>Trigger Date{getSortIcon('triggered_at')}</th>
            <th onClick={() => requestSort('close_price')} style={{ cursor: 'pointer' }}>Close Price{getSortIcon('close_price')}</th>
            <th onClick={() => requestSort('volume_surge')} style={{ cursor: 'pointer' }}>Volume Surge{getSortIcon('volume_surge')}</th>
            <th onClick={() => requestSort('sma_50')} style={{ cursor: 'pointer' }}>50-day SMA{getSortIcon('sma_50')}</th>
            <th onClick={() => requestSort('pivot_distance_pct')} style={{ cursor: 'pointer' }}>Pivot Dist{getSortIcon('pivot_distance_pct')}</th>
          </tr>
        </thead>

        <tbody>
          {sortedList.map((b, index) => {
            const volSurge    = b.volume_surge ? parseFloat(b.volume_surge).toFixed(2) : 'N/A';
            const isHighSurge = b.volume_surge && parseFloat(b.volume_surge) >= 2.0;
            const isExpanded  = expandedRow === b.ticker;

            return (
              <React.Fragment key={b.ticker + '-' + index}>
                <tr
                  onClick={() => toggleRow(b.ticker)}
                  style={{ cursor: 'pointer', background: isExpanded ? 'rgba(139,92,246,0.06)' : undefined, transition: 'background 0.15s' }}
                  title="Click to expand score details"
                >
                  {/* Expand chevron */}
                  <td style={{ color: 'var(--text-muted)', paddingRight: 0 }}>
                    {isExpanded
                      ? <ChevronDown size={14} />
                      : <ChevronRight size={14} />}
                  </td>

                  {/* Ticker + badges */}
                  <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                    <span style={{ verticalAlign: 'middle' }}>{b.ticker}</span>
                    {b.retention_period && b.retention_period !== '1d' && (
                      <span style={{
                        display: 'inline-block', marginLeft: '0.4rem', padding: '0.1rem 0.4rem',
                        borderRadius: '4px', fontSize: '0.62rem', fontWeight: 800,
                        color: '#f59e0b', background: 'rgba(245,158,11,0.12)',
                        border: '1px solid rgba(245,158,11,0.35)', letterSpacing: '0.04em', verticalAlign: 'middle',
                      }}>
                        {b.retention_period}
                      </span>
                    )}
                    {b.company_size && (
                      <span style={{
                        display: 'inline-block', marginLeft: '0.4rem', padding: '0.1rem 0.4rem',
                        borderRadius: '4px', fontSize: '0.62rem', fontWeight: 700,
                        color: b.company_size === 'Large' ? '#3b82f6' : b.company_size === 'Mid' ? '#8b5cf6' : '#10b981',
                        background: b.company_size === 'Large' ? 'rgba(59,130,246,0.12)' : b.company_size === 'Mid' ? 'rgba(139,92,246,0.12)' : 'rgba(16,185,129,0.12)',
                        border: `1px solid ${b.company_size === 'Large' ? 'rgba(59,130,246,0.35)' : b.company_size === 'Mid' ? 'rgba(139,92,246,0.35)' : 'rgba(16,185,129,0.35)'}`,
                        textTransform: 'uppercase', verticalAlign: 'middle',
                      }}>
                        {b.company_size}
                      </span>
                    )}
                  </td>

                  {/* Conviction score */}
                  <td style={{ fontWeight: 600 }}>
                    {(() => {
                      const score = b.final_score ?? b.ai_rating ?? null;
                      const grade = b.ai_grade ?? null;
                      const gradeColor = { A: '#10b981', B: '#3b82f6', C: '#f59e0b', D: '#f43f5e' }[grade] ?? 'var(--text-muted)';
                      const gradeAlpha = { A: '0.15', B: '0.15', C: '0.12', D: '0.12' }[grade] ?? '0.08';
                      if (score === null) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
                      return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                          <span style={{
                            color: score >= 85 ? '#10b981' : score >= 65 ? '#3b82f6' : score >= 45 ? '#f59e0b' : '#f43f5e',
                            fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '1rem',
                          }}>{score}</span>
                          {grade && (
                            <span style={{
                              fontSize: '0.65rem', fontWeight: 800, padding: '0.1rem 0.35rem',
                              borderRadius: '4px', color: gradeColor,
                              background: `rgba(${gradeColor === '#10b981' ? '16,185,129' : gradeColor === '#3b82f6' ? '59,130,246' : gradeColor === '#f59e0b' ? '245,158,11' : '244,63,94'},${gradeAlpha})`,
                              border: `1px solid ${gradeColor}55`, letterSpacing: '0.04em',
                            }}>{grade}</span>
                          )}
                        </span>
                      );
                    })()}
                  </td>

                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      <Calendar size={12} />
                      {b.triggered_at}
                    </div>
                  </td>
                  <td style={{ fontWeight: 500 }}>{formatCurrency(b.close_price)}</td>
                  <td>
                    <span style={{
                      fontWeight: 600,
                      color: isHighSurge ? 'var(--color-up)' : 'var(--text-primary)',
                      background: isHighSurge ? 'rgba(16, 185, 129, 0.1)' : 'transparent',
                      padding: isHighSurge ? '0.15rem 0.4rem' : '0',
                      borderRadius: '4px',
                    }}>
                      {volSurge}x
                    </span>
                  </td>
                  <td>{formatCurrency(b.sma_50)}</td>
                  <td style={{ fontWeight: 600, color: b.pivot_distance_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                    {b.pivot_distance_pct > 0 ? '+' : ''}{b.pivot_distance_pct?.toFixed(2)}%
                  </td>
                </tr>

                {/* Expandable score detail row */}
                {isExpanded && <BreakoutDetailPanel b={b} colSpan={COL_COUNT} />}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RemovedTags({ list, label }) {
  if (!list || list.length === 0) return null;
  return (
    <div style={{ marginTop: '2rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border-light)' }}>
      <h4 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', marginBottom: '1rem', fontSize: '0.9rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </h4>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
        {list.map((ticker) => (
          <div key={ticker} className="deleted-stock-tag" title="Removed from triggers today">
            {ticker}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function BreakoutsView({ breakouts }) {
  const tier1List    = Array.isArray(breakouts) ? breakouts : (breakouts?.breakouts || []);
  const tier1Removed = Array.isArray(breakouts) ? [] : (breakouts?.removed || []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          {/* Execution strategy banner */}
          <div className="market-banner" style={{ borderLeftColor: 'var(--accent-secondary)', background: 'linear-gradient(to right, rgba(139, 92, 246, 0.1), transparent)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <AlertCircle size={20} color="var(--accent-secondary)" />
              <div>
                <span style={{ fontWeight: 500, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Execution Strategy:</span>
                <span style={{ marginLeft: '0.5rem', fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                  The execution agent monitors these triggers automatically. At 9:30 AM EST, it purchases up to a 5-position cap in IBKR if sufficient cash is available. No manual intervention is required.
                </span>
              </div>
            </div>
          </div>

          {/* ── Tier 1: CAN SLIM Daily Breakouts ───────────────────────────────── */}
          <div className="card">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
              <Activity size={20} color="var(--accent-secondary)" />
              Tier 1 — CAN SLIM Breakout Triggers
              <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem', fontWeight: 500, color: 'var(--text-muted)', background: 'rgba(139,92,246,0.12)', padding: '0.15rem 0.55rem', borderRadius: '999px' }}>
                Last 7 Days
              </span>
            </h3>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
              CANSLIM breakouts — fundamentally qualified stocks with volume surge ≥ 1.2×, pivot proximity ≥ 95%.
              Ranked by <strong>Conviction Score</strong> (quality score + AI bonus). A-grade = highest conviction; D-grade tickers are vetoed.
              <span style={{ marginLeft: '0.5rem', color: 'var(--accent-secondary)' }}>Click any row to expand score breakdown.</span>
            </p>

            {tier1List.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
                <Activity size={36} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No Tier 1 breakouts triggered recently</p>
                <p style={{ fontSize: '0.85rem' }}>Breakout triggers scanned daily by GitHub Actions will appear here.</p>
              </div>
            ) : (
              <>
                <BreakoutTable list={tier1List} />
                <RemovedTags list={tier1Removed} label="Daily Breakout Rotations (Removed)" />
              </>
            )}
          </div>


    </div>
  );
}
