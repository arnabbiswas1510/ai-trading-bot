import React, { useState } from 'react';
import { Play, TrendingUp, BarChart2, Calendar, ShieldAlert, Award } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

export default function BacktesterView() {
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [capital, setCapital] = useState(100000);
  const [stopLoss, setStopLoss] = useState(7.0);
  const [profitTarget, setProfitTarget] = useState(25.0);
  const [maxPositions, setMaxPositions] = useState(5);
  
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleRunBacktest = async (e) => {
    e.preventDefault();
    setLoading(true);
    setResults(null);
    
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          initial_capital: parseFloat(capital),
          stop_loss_pct: parseFloat(stopLoss),
          profit_target_pct: parseFloat(profitTarget),
          max_positions: parseInt(maxPositions)
        })
      });
      
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to run backtest");
      }
      
      const data = await res.json();
      setResults(data);
    } catch (err) {
      alert(`Error running backtest: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* Parameters Form Card */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <BarChart2 size={20} color="var(--accent-primary)" />
          Setup Backtest Parameters
        </h3>
        <form onSubmit={handleRunBacktest}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1.25rem', marginBottom: '1.5rem' }}>
            <div className="form-group">
              <label>Start Date</label>
              <input 
                type="date" 
                className="form-control" 
                value={startDate} 
                onChange={(e) => setStartDate(e.target.value)} 
                required 
              />
            </div>
            <div className="form-group">
              <label>End Date</label>
              <input 
                type="date" 
                className="form-control" 
                value={endDate} 
                onChange={(e) => setEndDate(e.target.value)} 
                required 
              />
            </div>
            <div className="form-group">
              <label>Initial Capital ($)</label>
              <input 
                type="number" 
                className="form-control" 
                value={capital} 
                onChange={(e) => setCapital(Math.max(1000, parseInt(e.target.value) || 0))} 
                required 
              />
            </div>
            <div className="form-group">
              <label>Max Open Positions</label>
              <input 
                type="number" 
                className="form-control" 
                value={maxPositions} 
                onChange={(e) => setMaxPositions(Math.max(1, parseInt(e.target.value) || 0))} 
                required 
              />
            </div>
            <div className="form-group">
              <label>Stop Loss (%)</label>
              <input 
                type="number" 
                step="0.5"
                className="form-control" 
                value={stopLoss} 
                onChange={(e) => setStopLoss(Math.max(0.5, parseFloat(e.target.value) || 0))} 
                required 
              />
            </div>
            <div className="form-group">
              <label>Profit Target (%)</label>
              <input 
                type="number" 
                step="0.5"
                className="form-control" 
                value={profitTarget} 
                onChange={(e) => setProfitTarget(Math.max(1, parseFloat(e.target.value) || 0))} 
                required 
              />
            </div>
          </div>
          <button type="submit" className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
            {loading ? (
              <>
                <div className="spinner"></div>
                <span>Running Historical Simulation...</span>
              </>
            ) : (
              <>
                <Play size={16} fill="white" />
                <span>Run Historical Backtest</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Results Section */}
      {results && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          
          {/* Performance Widgets */}
          <div className="metrics-grid">
            <div className="card metric-card">
              <div className="metric-header">
                <span>Final Equity Value</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--accent-secondary)' }}>
                  <TrendingUp size={16} />
                </div>
              </div>
              <div className="metric-value">{formatCurrency(results.summary.final_equity)}</div>
              <div className={`metric-change ${results.summary.total_return_pct >= 0 ? 'up' : 'down'}`}>
                <span>Return: {results.summary.total_return_pct.toFixed(2)}%</span>
              </div>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>S&P 500 Buy & Hold</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--text-secondary)' }}>
                  <Calendar size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--text-secondary)' }}>
                {results.summary.sp500_return_pct.toFixed(2)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Benchmark comparison
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Max Drawdown</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-down)' }}>
                  <ShieldAlert size={16} />
                </div>
              </div>
              <div className="metric-value" style={{ color: 'var(--color-down)' }}>
                {results.summary.max_drawdown.toFixed(2)}%
              </div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                Peak-to-trough risk
              </span>
            </div>

            <div className="card metric-card">
              <div className="metric-header">
                <span>Win Rate / Trades</span>
                <div className="metric-icon-wrap" style={{ color: 'var(--color-warn)' }}>
                  <Award size={16} />
                </div>
              </div>
              <div className="metric-value">{results.summary.win_rate.toFixed(1)}%</div>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                {results.summary.winning_trades} wins / {results.summary.total_trades} trades
              </span>
            </div>
          </div>

          {/* Equity Curve Chart */}
          <div className="card" style={{ height: 350 }}>
            <h3 style={{ marginBottom: '1.25rem' }}>Equity Curve Growth</h3>
            <ResponsiveContainer width="100%" height="90%">
              <AreaChart data={results.equity_curve} margin={{ top: 10, right: 10, left: -10, bottom: 5 }}>
                <defs>
                  <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--accent-primary)" stopOpacity={0.25}/>
                    <stop offset="95%" stopColor="var(--accent-primary)" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.03)" />
                <XAxis dataKey="date" stroke="#6b7280" fontSize={11} />
                <YAxis 
                  stroke="#6b7280" 
                  fontSize={11}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} 
                />
                <Tooltip 
                  formatter={(value) => [formatCurrency(value), 'Equity']}
                  contentStyle={{ backgroundColor: '#111827', border: '1px solid rgba(255, 255, 255, 0.08)', borderRadius: 8 }}
                />
                <Area 
                  type="monotone" 
                  dataKey="equity" 
                  stroke="var(--accent-primary)" 
                  strokeWidth={2}
                  fillOpacity={1} 
                  fill="url(#colorEquity)" 
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Executed Trades Log */}
          <div className="card">
            <h3 style={{ marginBottom: '1.25rem' }}>Simulation Trade Log</h3>
            {results.trades.length === 0 ? (
              <div style={{ padding: '2rem 1rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                No breakouts were triggered during this time window. Try extending the date range or expanding your watchlist.
              </div>
            ) : (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Shares</th>
                      <th>Buy Price</th>
                      <th>Buy Date</th>
                      <th>Sell Price</th>
                      <th>Sell Date</th>
                      <th>PnL ($)</th>
                      <th>Return (%)</th>
                      <th>Exit Trigger</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.trades.map((trade, idx) => (
                      <tr key={idx}>
                        <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)' }}>{trade.ticker}</td>
                        <td>{trade.shares}</td>
                        <td>{formatCurrency(trade.buy_price)}</td>
                        <td>{trade.buy_date}</td>
                        <td>{formatCurrency(trade.sell_price)}</td>
                        <td>{trade.sell_date}</td>
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
      )}

    </div>
  );
}
