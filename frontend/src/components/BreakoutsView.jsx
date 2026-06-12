import React, { useState } from 'react';
import { Activity, AlertCircle, Calendar, ArrowUpRight, DollarSign, Play, ShoppingCart } from 'lucide-react';

export default function BreakoutsView({ breakouts, onBuyStock }) {
  const [selectedStock, setSelectedStock] = useState(null);
  const [sharesToBuy, setSharesToBuy] = useState(10);
  const [buyLoading, setBuyLoading] = useState(false);

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  const handleBuy = async (e) => {
    e.preventDefault();
    if (!selectedStock || sharesToBuy <= 0) return;
    
    setBuyLoading(true);
    try {
      await onBuyStock(selectedStock.ticker, sharesToBuy);
      alert(`Simulated purchase successful for ${sharesToBuy} shares of ${selectedStock.ticker}`);
      setSelectedStock(null);
    } catch (err) {
      alert(`Purchase failed: ${err.message}`);
    } finally {
      setBuyLoading(false);
    }
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
              The local execution agent checks these triggers automatically at 9:30 AM EST market open and purchases up to a 5-position cap if cash is available.
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

        {breakouts.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
            <Activity size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
            <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No breakouts triggered recently</p>
            <p style={{ fontSize: '0.85rem' }}>Breakout triggers scanned daily by GitHub Actions will be displayed here.</p>
          </div>
        ) : (
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
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {breakouts.map((b, index) => {
                  const volSurge = b.volume_surge ? parseFloat(b.volume_surge).toFixed(2) : 'N/A';
                  const isHighSurge = b.volume_surge && parseFloat(b.volume_surge) >= 2.0;
                  
                  return (
                    <tr key={b.ticker + '-' + index}>
                      <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                        {b.ticker}
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
                      <td>
                        <button 
                          className="btn btn-secondary"
                          style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.25rem' }}
                          onClick={() => setSelectedStock({ ticker: b.ticker, price: b.close_price })}
                        >
                          <ShoppingCart size={12} />
                          Buy
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Buy Dialog Drawer Overlay */}
      {selectedStock && (
        <div className="drawer-overlay" onClick={() => setSelectedStock(null)}></div>
      )}

      {/* Buy Dialog Drawer */}
      <div className={`drawer ${selectedStock ? 'open' : ''}`}>
        {selectedStock && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'between', alignItems: 'center', borderBottom: '1px solid var(--border-light)', paddingBottom: '1rem' }}>
              <div>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.8rem', color: 'var(--text-primary)' }}>Buy {selectedStock.ticker}</h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Breakout Candidate Manual Order</p>
              </div>
              <button onClick={() => setSelectedStock(null)} style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', marginLeft: 'auto' }}>
                Close
              </button>
            </div>

            <div style={{ flex: 1 }}>
              <form onSubmit={handleBuy} style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                <div style={{ display: 'flex', justifyContent: 'between', padding: '1rem', background: 'rgba(255, 255, 255, 0.02)', borderRadius: '6px', border: '1px solid var(--border-light)' }}>
                  <div>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Estimated Price</span>
                    <h3 style={{ margin: '0.25rem 0 0 0', fontFamily: 'var(--font-display)' }}>{formatCurrency(selectedStock.price)}</h3>
                  </div>
                  <div style={{ textAlign: 'right', marginLeft: 'auto' }}>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Total Cost</span>
                    <h3 style={{ margin: '0.25rem 0 0 0', color: 'var(--accent-secondary)', fontFamily: 'var(--font-display)' }}>
                      {formatCurrency(selectedStock.price * sharesToBuy)}
                    </h3>
                  </div>
                </div>

                <div className="form-group">
                  <label htmlFor="shares">Shares to Buy</label>
                  <input 
                    type="number" 
                    id="shares"
                    className="form-control"
                    value={sharesToBuy}
                    onChange={(e) => setSharesToBuy(Math.max(1, parseInt(e.target.value) || 0))}
                    required 
                  />
                </div>

                <button 
                  type="submit" 
                  className="btn btn-primary pulse-glow" 
                  disabled={buyLoading}
                  style={{ width: '100%', padding: '0.85rem', marginTop: '1rem', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '0.5rem' }}
                >
                  {buyLoading ? (
                    <>
                      <div className="spinner"></div>
                      <span>Submitting Order...</span>
                    </>
                  ) : (
                    <>
                      <Play size={16} fill="white" />
                      <span>Confirm Purchase Order</span>
                    </>
                  )}
                </button>
              </form>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
