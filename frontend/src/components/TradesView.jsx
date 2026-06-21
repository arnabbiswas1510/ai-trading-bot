import React, { useState, useEffect } from 'react';
import { History, TrendingUp, TrendingDown, Award, Calendar, AlertCircle, ShieldAlert, Sparkles, Activity } from 'lucide-react';

export default function TradesView({ trades }) {
  const [activeSubView, setActiveSubView] = useState('history'); // 'history' or 'retro'
  const [retroData, setRetroData] = useState([]);
  const [retroLoading, setRetroLoading] = useState(false);

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  // Fetch retro data when retro tab is activated
  useEffect(() => {
    if (activeSubView === 'retro' && retroData.length === 0) {
      setRetroLoading(true);
      fetch('/api/trades/retro')
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

  // Stats from full trade history (history view)
  const totalTrades = trades.length;
  const wins = trades.filter(t => t.profit_loss > 0).length;
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
  const netPnL = trades.reduce((sum, t) => sum + t.profit_loss, 0);

  // Stats from retro data (retro view)
  const totalRetro = retroData.length;
  const capitalSavedExits = retroData.filter(t => t.perf_since_sale <= 0).length;
  const exitAccuracy = totalRetro > 0 ? (capitalSavedExits / totalRetro) * 100 : 0;
  
  const totalSaved = retroData.reduce((sum, t) => {
    return t.opportunity_cost < 0 ? sum + Math.abs(t.opportunity_cost) : sum;
  }, 0);
  
  const totalMissed = retroData.reduce((sum, t) => {
    return t.opportunity_cost > 0 ? sum + t.opportunity_cost : sum;
  }, 0);

  // Group stats by exit reason
  const ruleStats = {};
  retroData.forEach(t => {
    const reason = t.exit_reason || 'Manual Close';
    if (!ruleStats[reason]) {
      ruleStats[reason] = { count: 0, saved: 0, missed: 0, totalPerf: 0 };
    }
    ruleStats[reason].count += 1;
    if (t.perf_since_sale <= 0) {
      ruleStats[reason].saved += 1;
    } else {
      ruleStats[reason].missed += 1;
    }
    ruleStats[reason].totalPerf += t.perf_since_sale;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* Sub-Tab Navigation Bar */}
      <div style={{ display: 'flex', gap: '0.75rem', borderBottom: '1px solid var(--border-light)', paddingBottom: '1rem' }}>
        <button 
          className={`btn ${activeSubView === 'history' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveSubView('history')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <History size={16} />
          <span>Completed Transactions</span>
        </button>
        <button 
          className={`btn ${activeSubView === 'retro' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveSubView('retro')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <Activity size={16} />
          <span>Post-Trade Retro Analysis</span>
        </button>
      </div>

      {activeSubView === 'history' ? (
        <>
          {/* Summary Metrics */}
          <div className="metrics-grid">
            <div className="card metric-card">
              <div className="metric-header">
                <span>Net Realized P&L</span>
                <div className="metric-icon-wrap" style={{ color: netPnL >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                  {netPnL >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                </div>
              </div>
              <div className="metric-value" style={{ color: netPnL >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {formatCurrency(netPnL)}
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Closed trading performance
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Win Rate</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-warn)' }}>
                  <Award size={16} />
                </div>
              </div>
              <div className="metric-value">{winRate.toFixed(1)}%</div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                {wins} wins out of {totalTrades} trades
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Total Closed Trades</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--accent-primary)' }}>
                  <History size={16} />
                </div>
              </div>
              <div className="metric-value">{totalTrades}</div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Positions closed in Supabase
              </span>
            </div>
          </div>

          {/* Main Trade History Table Card */}
          <div className="card">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
              <History size={20} color="var(--accent-secondary)" />
              All Completed Transactions
            </h3>

            {trades.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
                <History size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No trade history found</p>
                <p style={{ fontSize: '0.85rem' }}>Closed positions will be recorded here automatically.</p>
              </div>
            ) : (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Shares</th>
                      <th>Buy Price / Date</th>
                      <th>Sell Price / Date</th>
                      <th>Buy Reason</th>
                      <th>P&L ($)</th>
                      <th>Return (%)</th>
                      <th>Exit Trigger</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((trade) => {
                      const buyDateStr = trade.buy_date ? trade.buy_date.split('T')[0] : 'N/A';
                      const sellDateStr = trade.sell_date ? trade.sell_date.split('T')[0] : 'N/A';
                      
                      return (
                        <tr key={trade.id}>
                          <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                            {trade.ticker}
                          </td>
                          <td>{trade.shares}</td>
                          <td>
                            <div style={{ fontWeight: 500 }}>{formatCurrency(trade.buy_price)}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.25rem', marginTop: '0.15rem' }}>
                              <Calendar size={10} /> {buyDateStr}
                            </div>
                          </td>
                          <td>
                            <div style={{ fontWeight: 500 }}>{formatCurrency(trade.sell_price)}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.25rem', marginTop: '0.15rem' }}>
                              <Calendar size={10} /> {sellDateStr}
                            </div>
                          </td>
                          <td style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={trade.buy_reason}>
                            {trade.buy_reason || 'N/A'}
                          </td>
                          <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {trade.profit_loss >= 0 ? '+' : ''}{formatCurrency(trade.profit_loss)}
                          </td>
                          <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {trade.percent_return.toFixed(2)}%
                          </td>
                          <td>
                            <span className={`badge ${trade.exit_reason === '25% Profit Target' || trade.exit_reason === 'Profit Target' ? 'badge-success' : trade.exit_reason === '7% Stop Loss' || trade.exit_reason === 'Stop Loss' ? 'badge-danger' : 'badge-warning'}`}>
                              {trade.exit_reason}
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
      ) : (
        <>
          {/* Post-Trade Retro Scorecard */}
          <div className="metrics-grid">
            <div className="card metric-card">
              <div className="metric-header">
                <span>Exit Accuracy (Relief Rate)</span>
                <div className="metric-icon-wrap" style={{ color: exitAccuracy >= 50 ? 'var(--color-up)' : 'var(--color-down)' }}>
                  <ShieldAlert size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: exitAccuracy >= 50 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {exitAccuracy.toFixed(1)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Exits where stock fell post-sale
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Total Capital Saved</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-up)' }}>
                  <TrendingUp size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--color-up)' }}>
                {formatCurrency(totalSaved)}
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Losses avoided since exits
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Total Missed Profits</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-down)' }}>
                  <TrendingDown size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--color-down)' }}>
                {formatCurrency(totalMissed)}
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Gains missed by exiting early
              </span>
            </div>
          </div>

          {/* Trigger Audit Matrix Card */}
          {retroData.length > 0 && (
            <div className="card">
              <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
                <Sparkles size={20} color="var(--accent-secondary)" />
                Exit Trigger Rule Audit Matrix
              </h3>
              <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                Educational performance breakdown grouped by your exit rules. High **Relief Rates** validate the trigger. High **Remorse Rates** indicate a trigger may be too tight and whipsawing.
              </p>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Exit Trigger Rule</th>
                      <th>Trades Audited</th>
                      <th>Relief Rate (Saved Losses)</th>
                      <th>Remorse Rate (Sold Too Early)</th>
                      <th>Avg. Performance Post-Exit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(ruleStats).map(([reason, stats]) => {
                      const relief = (stats.saved / stats.count) * 100;
                      const remorse = (stats.missed / stats.count) * 100;
                      const avg = stats.totalPerf / stats.count;
                      return (
                        <tr key={reason}>
                          <td style={{ fontWeight: 600 }}>{reason}</td>
                          <td>{stats.count}</td>
                          <td style={{ color: 'var(--color-up)', fontWeight: 600 }}>{relief.toFixed(0)}%</td>
                          <td style={{ color: 'var(--color-down)', fontWeight: 600 }}>{remorse.toFixed(0)}%</td>
                          <td style={{ color: avg >= 0 ? 'var(--color-down)' : 'var(--color-up)', fontWeight: 600 }}>
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

          {/* Retro Detail Table Card */}
          <div className="card">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
              <Activity size={20} color="var(--accent-primary)" />
              Post-Trade Remorse Ledger
            </h3>

            {retroLoading ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem', width: '32px', height: '32px' }}></div>
                <p style={{ color: 'var(--text-secondary)' }}>Hydrating historical trades with live market prices...</p>
              </div>
            ) : retroData.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
                <History size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No trade history found</p>
                <p style={{ fontSize: '0.85rem' }}>Closed positions are required to calculate post-sale performance.</p>
              </div>
            ) : (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Exit Trigger</th>
                      <th>Exit Price / Date</th>
                      <th>Current Price</th>
                      <th>Perf. Post-Exit</th>
                      <th>Capital Impact</th>
                      <th>Decision Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {retroData.map((trade) => {
                      const sellDateStr = trade.sell_date ? trade.sell_date.split('T')[0] : 'N/A';
                      const isSaved = trade.perf_since_sale <= 0;
                      
                      return (
                        <tr key={trade.id}>
                          <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                            {trade.ticker}
                          </td>
                          <td>
                            <span className="badge" style={{ background: 'rgba(255,255,255,0.05)', color: 'var(--text-secondary)' }}>
                              {trade.exit_reason}
                            </span>
                          </td>
                          <td>
                            <div style={{ fontWeight: 500 }}>{formatCurrency(trade.sell_price)}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.25rem', marginTop: '0.15rem' }}>
                              <Calendar size={10} /> {sellDateStr}
                            </div>
                          </td>
                          <td style={{ fontWeight: 500 }}>{formatCurrency(trade.current_price)}</td>
                          <td style={{ fontWeight: 600, color: isSaved ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {trade.perf_since_sale >= 0 ? '+' : ''}{trade.perf_since_sale.toFixed(2)}%
                          </td>
                          <td style={{ fontWeight: 600, color: isSaved ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {isSaved 
                              ? `Saved ${formatCurrency(Math.abs(trade.opportunity_cost))}`
                              : `Missed ${formatCurrency(trade.opportunity_cost)}`
                            }
                          </td>
                          <td>
                            <span className="badge" style={{
                              backgroundColor: trade.verdict === 'Saved Capital' ? 'var(--color-up-glow)' : trade.verdict === 'Flat' ? 'var(--color-warn-glow)' : 'var(--color-down-glow)',
                              color: trade.verdict === 'Saved Capital' ? 'var(--color-up)' : trade.verdict === 'Flat' ? 'var(--color-warn)' : 'var(--color-down)',
                              border: `1px solid ${trade.verdict === 'Saved Capital' ? 'var(--color-up)' : trade.verdict === 'Flat' ? 'var(--color-warn)' : 'var(--color-down)'}`
                            }}>
                              {trade.verdict}
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
