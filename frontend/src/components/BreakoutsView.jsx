import React, { useState, useEffect } from 'react';
import useSortableTable from '../hooks/useSortableTable';
import { Activity, AlertCircle, Calendar, Zap, TrendingUp, History, ShieldAlert, Sparkles, TrendingDown } from 'lucide-react';

// ── Shared sub-components ─────────────────────────────────────────────────────

function BreakoutTable({ list }) {
  const { items: sortedList, requestSort, getSortIcon } = useSortableTable(list, 'triggered_at', 'desc');
  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th onClick={() => requestSort('ticker')} style={{ cursor: 'pointer' }}>Ticker{getSortIcon('ticker')}</th>
            <th onClick={() => requestSort('final_score')} style={{ cursor: 'pointer' }}>Conviction Score{getSortIcon('final_score')}</th>
            <th onClick={() => requestSort('triggered_at')} style={{ cursor: 'pointer' }}>Trigger Date{getSortIcon('triggered_at')}</th>
            <th onClick={() => requestSort('close_price')} style={{ cursor: 'pointer' }}>Close Price{getSortIcon('close_price')}</th>
            <th onClick={() => requestSort('volume_surge')} style={{ cursor: 'pointer' }}>Volume Surge{getSortIcon('volume_surge')}</th>
            <th onClick={() => requestSort('sma_50')} style={{ cursor: 'pointer' }}>50-day SMA{getSortIcon('sma_50')}</th>
            <th onClick={() => requestSort('rolling_high_52w')} style={{ cursor: 'pointer' }}>52-week High{getSortIcon('rolling_high_52w')}</th>
            <th onClick={() => requestSort('pivot_distance_pct')} style={{ cursor: 'pointer' }}>Pivot Dist{getSortIcon('pivot_distance_pct')}</th>
          </tr>
        </thead>

        <tbody>
          {sortedList.map((b, index) => {
            const volSurge   = b.volume_surge ? parseFloat(b.volume_surge).toFixed(2) : 'N/A';
            const isHighSurge = b.volume_surge && parseFloat(b.volume_surge) >= 2.0;

            return (
              <tr key={b.ticker + '-' + index}>
                <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                  <span style={{ verticalAlign: 'middle' }}>{b.ticker}</span>
                  {b.retention_period && b.retention_period !== '1d' && (
                    <span style={{
                      display: 'inline-block',
                      marginLeft: '0.4rem',
                      padding: '0.1rem 0.4rem',
                      borderRadius: '4px',
                      fontSize: '0.62rem',
                      fontWeight: 800,
                      color: '#f59e0b',
                      background: 'rgba(245,158,11,0.12)',
                      border: '1px solid rgba(245,158,11,0.35)',
                      letterSpacing: '0.04em',
                      verticalAlign: 'middle',
                    }}>
                      {b.retention_period}
                    </span>
                  )}
                  {b.company_size && (
                    <span style={{
                      display: 'inline-block',
                      marginLeft: '0.4rem',
                      padding: '0.1rem 0.4rem',
                      borderRadius: '4px',
                      fontSize: '0.62rem',
                      fontWeight: 700,
                      color: b.company_size === 'Large' ? '#3b82f6' : b.company_size === 'Mid' ? '#8b5cf6' : '#10b981',
                      background: b.company_size === 'Large' ? 'rgba(59,130,246,0.12)' : b.company_size === 'Mid' ? 'rgba(139,92,246,0.12)' : 'rgba(16,185,129,0.12)',
                      border: `1px solid ${b.company_size === 'Large' ? 'rgba(59,130,246,0.35)' : b.company_size === 'Mid' ? 'rgba(139,92,246,0.35)' : 'rgba(16,185,129,0.35)'}`,
                      textTransform: 'uppercase',
                      verticalAlign: 'middle',
                    }}>
                      {b.company_size}
                    </span>
                  )}
                </td>
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
                          fontFamily: 'var(--font-display)',
                          fontWeight: 800,
                          fontSize: '1rem',
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
                <td>{formatCurrency(b.rolling_high_52w)}</td>
                <td style={{ fontWeight: 600, color: b.pivot_distance_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                  {b.pivot_distance_pct > 0 ? '+' : ''}{b.pivot_distance_pct?.toFixed(2)}%
                </td>
              </tr>
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
  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

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
