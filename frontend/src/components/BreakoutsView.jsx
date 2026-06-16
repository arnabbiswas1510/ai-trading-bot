import React from 'react';
import { Activity, AlertCircle, Calendar } from 'lucide-react';

export default function BreakoutsView({ breakouts }) {
  const list = Array.isArray(breakouts) ? breakouts : (breakouts?.breakouts || []);
  const removedList = Array.isArray(breakouts) ? [] : (breakouts?.removed || []);

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>

      {/* Informative Alert Banner */}
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

      {/* Main Breakouts Table Card */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <Activity size={20} color="var(--accent-secondary)" />
          Recent Daily Breakout Triggers (Last 7 Days)
        </h3>

        {list.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
            <Activity size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
            <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No breakouts triggered recently</p>
            <p style={{ fontSize: '0.85rem' }}>Breakout triggers scanned daily by GitHub Actions will be displayed here.</p>
          </div>
        ) : (
          <>
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
                    const volSurge = b.volume_surge ? parseFloat(b.volume_surge).toFixed(2) : 'N/A';
                    const isHighSurge = b.volume_surge && parseFloat(b.volume_surge) >= 2.0;

                    return (
                      <tr key={b.ticker + '-' + index}>
                        <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                          <span style={{ verticalAlign: 'middle' }}>{b.ticker}</span>
                          {b.change_status === "NEW" && (
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
                          <span
                            style={{
                              fontWeight: 600,
                              color: isHighSurge ? 'var(--color-up)' : 'var(--text-primary)',
                              background: isHighSurge ? 'rgba(16, 185, 129, 0.1)' : 'transparent',
                              padding: isHighSurge ? '0.15rem 0.4rem' : '0',
                              borderRadius: '4px'
                            }}
                          >
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

            {/* Daily Breakout Rotations (Removed Candidates) */}
            {removedList.length > 0 && (
              <div style={{ marginTop: '2rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border-light)' }}>
                <h4 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', marginBottom: '1rem', fontSize: '0.9rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Daily Breakout Rotations (Removed Candidates)
                </h4>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
                  {removedList.map((ticker) => (
                    <div key={ticker} className="deleted-stock-tag" title="Removed from breakouts today">
                      {ticker}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

    </div>
  );
}
