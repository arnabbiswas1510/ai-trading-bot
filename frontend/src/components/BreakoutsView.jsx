import React, { useState, useEffect } from 'react';
import { Activity, AlertCircle, Calendar, Zap, TrendingUp, History, ShieldAlert, Sparkles, TrendingDown } from 'lucide-react';

// ── Shared sub-components ─────────────────────────────────────────────────────

function BreakoutTable({ list }) {
  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Trigger Date</th>
            <th>Close Price</th>
            <th>Volume Surge</th>
            <th>50-day SMA</th>
            <th>52-week High</th>
            <th>Pivot Dist</th>
          </tr>
        </thead>
        <tbody>
          {list.map((b, index) => {
            const volSurge   = b.volume_surge ? parseFloat(b.volume_surge).toFixed(2) : 'N/A';
            const isHighSurge = b.volume_surge && parseFloat(b.volume_surge) >= 2.0;

            return (
              <tr key={b.ticker + '-' + index}>
                <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                  <span style={{ verticalAlign: 'middle' }}>{b.ticker}</span>
                  {b.change_status === 'NEW' && (
                    <span className="badge-new-pulse">+ NEW</span>
                  )}
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

export default function BreakoutsView({ breakouts, momentum }) {
  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  const tier1List    = Array.isArray(breakouts) ? breakouts : (breakouts?.breakouts || []);
  const tier1Removed = Array.isArray(breakouts) ? [] : (breakouts?.removed || []);
  const tier2List    = Array.isArray(momentum)  ? momentum  : (momentum?.breakouts  || []);
  const tier2Removed = Array.isArray(momentum)  ? [] : (momentum?.removed  || []);

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
              Primary breakouts — strict CAN SLIM fundamentals (EPS ≥ 18%, ≥ 5 inst. holders) with standard technical thresholds (volume surge ≥ 1.4×, pivot proximity ≥ 98%). Highest-conviction signals.
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

          {/* ── Tier 2: Momentum Breakout Triggers ─────────────────────────────── */}
          <div className="card" style={{ borderColor: 'rgba(251, 191, 36, 0.2)', background: 'linear-gradient(135deg, rgba(251,191,36,0.03) 0%, transparent 60%)' }}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
              <Zap size={20} color="#f59e0b" />
              Tier 2 — Momentum Breakout Triggers
              <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem', fontWeight: 500, color: '#92400e', background: 'rgba(251,191,36,0.15)', padding: '0.15rem 0.55rem', borderRadius: '999px' }}>
                Relaxed Thresholds
              </span>
            </h3>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
              Secondary breakouts — only activated when Tier 1 fills fewer than the max position slots. Uses relaxed fundamentals (EPS ≥ 10%, ≥ 3 inst. holders) and relaxed technicals (volume surge ≥ 1.2×, pivot proximity ≥ 95%). Lower-conviction gap-fill signals.
            </p>

            {tier2List.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
                <TrendingUp size={36} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No Tier 2 momentum triggers</p>
                <p style={{ fontSize: '0.85rem' }}>Momentum screener only runs when Tier 1 fills fewer than the max position cap.</p>
              </div>
            ) : (
              <>
                <BreakoutTable list={tier2List} />
                <RemovedTags list={tier2Removed} label="Momentum Trigger Rotations (Removed)" />
              </>
            )}
          </div>


    </div>
  );
}
