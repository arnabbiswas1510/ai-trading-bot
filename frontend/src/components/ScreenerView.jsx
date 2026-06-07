import React, { useState, useEffect } from 'react';
import { Play, Sparkles, ChevronRight, X, ArrowUpRight, ShoppingCart, HelpCircle } from 'lucide-react';
import StockChart from './StockChart';

export default function ScreenerView({ results, onRunScan, loading, onBuyStock }) {
  const [selectedStock, setSelectedStock] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [chartLoading, setChartLoading] = useState(false);
  const [sharesToBuy, setSharesToBuy] = useState(10);
  const [buyLoading, setBuyLoading] = useState(false);

  // Fetch stock history when selected stock changes
  useEffect(() => {
    if (!selectedStock) {
      setChartData([]);
      return;
    }
    
    setChartLoading(true);
    fetch(`/api/stock-history/${selectedStock.ticker}`)
      .then(res => {
        if (!res.ok) throw new Error("Chart load failed");
        return res.json();
      })
      .then(data => {
        setChartData(data);
        setChartLoading(false);
      })
      .catch(err => {
        console.error(err);
        setChartLoading(false);
      });
  }, [selectedStock]);

  const handleBuy = async (e) => {
    e.preventDefault();
    if (!selectedStock || sharesToBuy <= 0) return;
    
    setBuyLoading(true);
    try {
      await onBuyStock(selectedStock.ticker, sharesToBuy);
      alert(`Simulated purchase successful for ${sharesToBuy} shares of ${selectedStock.ticker}`);
    } catch (err) {
      alert(`Purchase failed: ${err.message}`);
    } finally {
      setBuyLoading(false);
    }
  };

  const getScoreColor = (score) => {
    if (score >= 75) return '#10b981'; // Green
    if (score >= 50) return '#f59e0b'; // Amber
    return '#f43f5e'; // Red
  };

  const checkCriteria = (letter, stock) => {
    // Determine pass/fail based on individual score thresholds
    switch (letter) {
      case 'C': return stock.score_c >= 8.0;
      case 'A': return stock.score_a >= 8.0;
      case 'N': return stock.score_n >= 10.0;
      case 'S': return stock.score_s >= 8.0;
      case 'L': return stock.score_l >= 10.0;
      case 'I': return stock.score_i >= 6.0;
      case 'M': return stock.score_m >= 15.0;
      default: return false;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem', position: 'relative' }}>
      
      {/* Header with Run Trigger */}
      <div className="header-actions">
        <div className="title-area">
          <p>Analyze and score your watchlist using William J. O'Neil's CAN SLIM system</p>
        </div>
        <button 
          className="btn btn-primary pulse-glow" 
          onClick={onRunScan}
          disabled={loading}
          style={{ padding: '0.75rem 1.5rem' }}
        >
          {loading ? (
            <>
              <div className="spinner"></div>
              <span>Scanning Watchlist...</span>
            </>
          ) : (
            <>
              <Play size={16} fill="white" />
              <span>Run CAN SLIM Scan</span>
            </>
          )}
        </button>
      </div>

      {/* Screener Results Table */}
      <div className="card">
        {results.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--text-muted)' }}>
            <Sparkles size={40} strokeWidth={1} style={{ marginBottom: '1.25rem', color: 'var(--accent-primary)' }} />
            <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No scan results found</p>
            <p style={{ fontSize: '0.85rem' }}>Click the "Run CAN SLIM Scan" button to retrieve live data and calculate scores.</p>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th style={{ textAlign: 'center' }}>Total Score</th>
                  <th style={{ textAlign: 'center' }}>CAN SLIM Health</th>
                  <th>Price</th>
                  <th>YoY EPS Growth (C)</th>
                  <th>Ann CAGR (A)</th>
                  <th>RS Rating (L)</th>
                  <th>Inst % (I)</th>
                  <th style={{ width: '40px' }}></th>
                </tr>
              </thead>
              <tbody>
                {results.map((stock) => (
                  <tr 
                    key={stock.ticker} 
                    onClick={() => setSelectedStock(stock)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.05rem' }}>
                      {stock.ticker}
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <span 
                        style={{ 
                          fontFamily: 'var(--font-display)', 
                          fontWeight: 800, 
                          fontSize: '1.1rem',
                          color: getScoreColor(stock.total_score)
                        }}
                      >
                        {stock.total_score}
                      </span>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>/100</span>
                    </td>
                    <td style={{ display: 'flex', justifyContent: 'center', gap: '4px', verticalAlign: 'middle', padding: '1rem 0' }}>
                      {['C', 'A', 'N', 'S', 'L', 'I', 'M'].map(letter => {
                        const passed = checkCriteria(letter, stock);
                        return (
                          <div 
                            key={letter} 
                            className={`score-badge-circle ${passed ? 'pass' : 'fail'}`}
                            title={`${letter}: ${passed ? 'PASS' : 'FAIL'} (${stock['score_' + letter.toLowerCase()]} pts)`}
                          >
                            {letter}
                          </div>
                        );
                      })}
                    </td>
                    <td style={{ fontWeight: 500 }}>
                      ${stock.details.current_price?.toFixed(2) || 'N/A'}
                    </td>
                    <td style={{ color: stock.details.c_growth_yoy >= 25 ? 'var(--color-up)' : 'var(--text-primary)', fontWeight: stock.details.c_growth_yoy >= 25 ? 600 : 400 }}>
                      {stock.details.c_growth_yoy ? `${stock.details.c_growth_yoy}%` : 'N/A'}
                    </td>
                    <td>
                      {stock.details.a_eps_growth_cagr ? `${stock.details.a_eps_growth_cagr}%` : 'N/A'}
                    </td>
                    <td style={{ color: stock.details.l_rs_rating >= 80 ? 'var(--color-up)' : 'var(--text-primary)', fontWeight: stock.details.l_rs_rating >= 80 ? 600 : 400 }}>
                      {stock.details.l_rs_rating || 'N/A'}
                    </td>
                    <td>
                      {stock.details.i_held_percent_inst ? `${stock.details.i_held_percent_inst}%` : 'N/A'}
                    </td>
                    <td>
                      <ChevronRight size={18} color="var(--text-muted)" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Sliding Drawer Overlay */}
      {selectedStock && (
        <div className="drawer-overlay" onClick={() => setSelectedStock(null)}></div>
      )}

      {/* Sliding Drawer */}
      <div className={`drawer ${selectedStock ? 'open' : ''}`}>
        {selectedStock && (
          <>
            <div className="drawer-header">
              <div>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.8rem', display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
                  {selectedStock.ticker}
                  <span style={{ fontSize: '1rem', color: getScoreColor(selectedStock.total_score), fontWeight: 700 }}>
                    Score: {selectedStock.total_score}/100
                  </span>
                </h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                  Last Scanned: {new Date(selectedStock.timestamp).toLocaleTimeString()}
                </p>
              </div>
              <button className="drawer-close" onClick={() => setSelectedStock(null)}>
                <X size={24} />
              </button>
            </div>

            {/* Quick Chart */}
            <div className="card" style={{ padding: '1rem', background: '#0b0f19', marginBottom: '1.5rem', minHeight: 250 }}>
              {chartLoading ? (
                <div style={{ display: 'flex', height: 250, alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '0.75rem' }}>
                  <div className="spinner"></div>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Fetching live charts...</span>
                </div>
              ) : (
                <StockChart data={chartData} ticker={selectedStock.ticker} />
              )}
            </div>

            {/* CAN SLIM Detailed Scorecard */}
            <h3 style={{ marginBottom: '0.75rem', fontSize: '1.1rem' }}>Detailed CAN SLIM Scorecard</h3>
            <div className="scorecard-details" style={{ marginBottom: '2rem' }}>
              {/* C */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('C', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>C</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Current Earnings</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    YoY growth: {selectedStock.details.c_growth_yoy || 0}% | Revenue growth: {selectedStock.details.c_rev_growth_yoy || 0}%
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_c} / 15</span>
              </div>

              {/* A */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('A', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>A</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Annual Growth</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    3-Yr EPS CAGR: {selectedStock.details.a_eps_growth_cagr || 0}% | ROE: {selectedStock.details.a_roe || 0}%
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_a} / 15</span>
              </div>

              {/* N */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('N', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>N</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>New Highs / Catalyst</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    Dist from 52w High: {selectedStock.details.n_pct_from_high || 0}% | Above 50 MA: {selectedStock.details.current_price > selectedStock.details.sma50 ? 'YES' : 'NO'}
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_n} / 15</span>
              </div>

              {/* S */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('S', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>S</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Supply and Demand</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    Accumulation vs Distribution: {selectedStock.details.s_acc_days} / {selectedStock.details.s_dist_days} days
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_s} / 15</span>
              </div>

              {/* L */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('L', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>L</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Leader or Laggard</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    Relative Strength Score: {selectedStock.details.l_rs_rating}
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_l} / 15</span>
              </div>

              {/* I */}
              <div className="scorecard-row">
                <span className="scorecard-letter" style={{ color: checkCriteria('I', selectedStock) ? 'var(--color-up)' : 'var(--color-down)' }}>I</span>
                <div style={{ flex: 1, marginLeft: '1.5rem' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Institutional Sponsorship</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    Holding ownership: {selectedStock.details.i_held_percent_inst || 0}%
                  </div>
                </div>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{selectedStock.score_i} / 10</span>
              </div>
            </div>

            {/* Simulated Trading Action */}
            <div className="card" style={{ padding: '1.25rem', border: '1px solid rgba(139, 92, 246, 0.2)', background: 'rgba(139, 92, 246, 0.03)' }}>
              <h4 style={{ marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <ShoppingCart size={16} color="var(--accent-primary)" />
                Paper Trade: Buy shares
              </h4>
              <form onSubmit={handleBuy} style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                <div style={{ flex: 1 }}>
                  <input 
                    type="number" 
                    className="form-control"
                    value={sharesToBuy}
                    onChange={(e) => setSharesToBuy(Math.max(1, parseInt(e.target.value) || 0))}
                    placeholder="Shares count"
                    min="1"
                    required
                  />
                </div>
                <button type="submit" className="btn btn-primary" style={{ flex: 1.5 }} disabled={buyLoading}>
                  {buyLoading ? <div className="spinner"></div> : <>Buy {selectedStock.ticker} <ArrowUpRight size={16} /></>}
                </button>
              </form>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.5rem', display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
                <HelpCircle size={12} />
                Bought at live market close. Virtual stop-loss (7%) & profit target (25%) will be set automatically.
              </p>
            </div>
          </>
        )}
      </div>

    </div>
  );
}
