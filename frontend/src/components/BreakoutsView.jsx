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
  const [activeSubView, setActiveSubView] = useState('triggers'); // 'triggers' or 'retro'
  const [retroData, setRetroData] = useState([]);
  const [retroLoading, setRetroLoading] = useState(false);

  const formatCurrency = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

  useEffect(() => {
    if (activeSubView === 'retro' && retroData.length === 0) {
      setRetroLoading(true);
      fetch('/api/breakouts/retro')
        .then(res => {
          if (!res.ok) throw new Error("Failed to fetch retro data");
          return res.json();
        })
        .then(data => {
          setRetroData(data);
          setRetroLoading(false);
        })
        .catch(err => {
          console.error(err);
          setRetroLoading(false);
        });
    }
  }, [activeSubView, retroData.length]);

  const tier1List    = Array.isArray(breakouts) ? breakouts : (breakouts?.breakouts || []);
  const tier1Removed = Array.isArray(breakouts) ? [] : (breakouts?.removed || []);
  const tier2List    = Array.isArray(momentum)  ? momentum  : (momentum?.breakouts  || []);
  const tier2Removed = Array.isArray(momentum)  ? [] : (momentum?.removed  || []);

  // Stats for Retro
  const totalRetro = retroData.length;
  const avoidedFakeouts = retroData.filter(t => t.perf_since_trigger <= 0).length;
  const fakeoutAvoidanceRate = totalRetro > 0 ? (avoidedFakeouts / totalRetro) * 100 : 0;
  
  const topMissedWinner = totalRetro > 0 
    ? Math.max(...retroData.map(t => t.perf_since_trigger)) 
    : 0;

  const avgMissedReturn = totalRetro > 0
    ? retroData.reduce((sum, t) => sum + t.perf_since_trigger, 0) / totalRetro
    : 0;

  // Group stats by trigger type
  const typeStats = {};
  retroData.forEach(t => {
    const type = t.type || 'Primary CANSLIM';
    if (!typeStats[type]) {
      typeStats[type] = { count: 0, saved: 0, missed: 0, totalPerf: 0 };
    }
    typeStats[type].count += 1;
    if (t.perf_since_trigger <= 0) {
      typeStats[type].saved += 1;
    } else {
      typeStats[type].missed += 1;
    }
    typeStats[type].totalPerf += t.perf_since_trigger;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>

      {/* Sub-Tab Navigation Bar */}
      <div style={{ display: 'flex', gap: '0.75rem', borderBottom: '1px solid var(--border-light)', paddingBottom: '1rem' }}>
        <button 
          className={`btn ${activeSubView === 'triggers' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveSubView('triggers')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <Zap size={16} />
          <span>Active Breakout Triggers</span>
        </button>
        <button 
          className={`btn ${activeSubView === 'retro' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveSubView('retro')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <History size={16} />
          <span>Missed Entry Retro</span>
        </button>
      </div>

      {activeSubView === 'triggers' ? (
        <>
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
        </>
      ) : (
        <>
          {/* Missed Breakout Scorecard */}
          <div className="metrics-grid">
            <div className="card metric-card">
              <div className="metric-header">
                <span>Fakeout Avoidance Rate</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-up)' }}>
                  <ShieldAlert size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--color-up)' }}>
                {fakeoutAvoidanceRate.toFixed(1)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Missed breakouts that failed
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Top Missed Run</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-warn)' }}>
                  <TrendingUp size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--color-warn)' }}>
                +{topMissedWinner.toFixed(1)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Best performance among missed
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Avg. Return Post-Trigger</span>
                <div className="metric-icon-wrap" style={{ color: avgMissedReturn >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                  <Activity size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: avgMissedReturn >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {avgMissedReturn >= 0 ? '+' : ''}{avgMissedReturn.toFixed(1)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Mean performance of skipped stocks
              </span>
            </div>
          </div>

          {/* Trigger Performance Diagnostics */}
          {retroData.length > 0 && (
            <div className="card">
              <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
                <Sparkles size={20} color="var(--accent-secondary)" />
                Screener Accuracy Diagnostics
              </h3>
              <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                Analysis comparing the accuracy of **Primary CANSLIM** breakouts against **Momentum Relaxed** breakouts. Relieved / Avoided signals represent failed breakouts, while Missed Winners represent missed gains.
              </p>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Screener Tier Type</th>
                      <th>Signals Audited</th>
                      <th>Avoidance Rate (Failed Breakouts)</th>
                      <th>Missed Rate (Genuine Runs)</th>
                      <th>Avg. Post-Trigger Performance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(typeStats).map(([type, stats]) => {
                      const relief = (stats.saved / stats.count) * 100;
                      const remorse = (stats.missed / stats.count) * 100;
                      const avg = stats.totalPerf / stats.count;
                      return (
                        <tr key={type}>
                          <td style={{ fontWeight: 600 }}>{type}</td>
                          <td>{stats.count}</td>
                          <td style={{ color: 'var(--color-up)', fontWeight: 600 }}>{relief.toFixed(0)}%</td>
                          <td style={{ color: 'var(--color-down)', fontWeight: 600 }}>{remorse.toFixed(0)}%</td>
                          <td style={{ color: avg >= 0 ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 600 }}>
                            {avg >= 0 ? '+' : ''}{avg.toFixed(2)}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Missed Entry Table */}
          <div className="card">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
              <Zap size={20} color="var(--accent-primary)" />
              Missed Breakout Opportunities (Past 30 Days)
            </h3>

            {retroLoading ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem', width: '32px', height: '32px' }}></div>
                <p style={{ color: 'var(--text-secondary)' }}>Comparing skipped breakout signals with live market quotes...</p>
              </div>
            ) : retroData.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
                <Activity size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No skipped triggers found</p>
                <p style={{ fontSize: '0.85rem' }}>All breakout signals generated in the past 30 days were purchased successfully!</p>
              </div>
            ) : (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Screener Tier</th>
                      <th>Trigger Date</th>
                      <th>Pivot Price</th>
                      <th>Current Price</th>
                      <th>Perf. Since Pivot</th>
                      <th>Audit Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {retroData.map((trig, idx) => {
                      const isAvoided = trig.perf_since_trigger <= 0;
                      return (
                        <tr key={trig.ticker + '-' + trig.triggered_at + '-' + idx}>
                          <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                            {trig.ticker}
                          </td>
                          <td>
                            <span className="badge" style={{ background: 'rgba(255,255,255,0.05)', color: 'var(--text-secondary)' }}>
                              {trig.type}
                            </span>
                          </td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                              <Calendar size={12} />
                              {trig.triggered_at}
                            </div>
                          </td>
                          <td style={{ fontWeight: 500 }}>{formatCurrency(trig.close_price)}</td>
                          <td style={{ fontWeight: 500 }}>{formatCurrency(trig.current_price)}</td>
                          <td style={{ fontWeight: 600, color: isAvoided ? 'var(--color-down)' : 'var(--color-up)' }}>
                            {trig.perf_since_trigger >= 0 ? '+' : ''}{trig.perf_since_trigger.toFixed(2)}%
                          </td>
                          <td>
                            <span className="badge" style={{
                              backgroundColor: trig.verdict === 'Fakeout Avoided' ? 'var(--color-up-glow)' : trig.verdict === 'Flat' ? 'var(--color-warn-glow)' : 'var(--color-down-glow)',
                              color: trig.verdict === 'Fakeout Avoided' ? 'var(--color-up)' : trig.verdict === 'Flat' ? 'var(--color-warn)' : 'var(--color-down)',
                              border: `1px solid ${trig.verdict === 'Fakeout Avoided' ? 'var(--color-up)' : trig.verdict === 'Flat' ? 'var(--color-warn)' : 'var(--color-down)'}`
                            }}>
                              {trig.verdict}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

    </div>
  );
}
