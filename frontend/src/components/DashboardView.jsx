import React from 'react';
import { 
  TrendingUp, 
  TrendingDown, 
  DollarSign, 
  Activity, 
  Award, 
  AlertTriangle,
  Briefcase,
  History,
  Info
} from 'lucide-react';

export default function DashboardView({ data, marketData, trades, onSellPosition }) {
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

  // Determine market status color and class
  const getMarketClass = () => {
    if (!marketData) return '';
    if (marketData.status === 'Market in Correction') return 'correction';
    if (marketData.status === 'Uptrend Under Pressure') return 'pressure';
    return '';
  };

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

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
        {/* Net Portfolio Value */}
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

        {/* Cash Balance */}
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

        {/* Unrealized P&L */}
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

        {/* Win Rate */}
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

      {/* Main Grid: Active Positions */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <Briefcase size={20} color="var(--accent-primary)" />
          Open Positions
        </h3>
        
        {positions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
            <Briefcase size={40} strokeWidth={1} style={{ marginBottom: '1rem', color: 'var(--text-muted)' }} />
            <p style={{ fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>No open positions</p>
            <p style={{ fontSize: '0.85rem' }}>Run a CAN SLIM Scan and purchase breakouts to populate your portfolio.</p>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Shares</th>
                  <th>Buy Price</th>
                  <th>Current Price</th>
                  <th>Market Value</th>
                  <th>Return</th>
                  <th>Stop Loss</th>
                  <th>Profit Target</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id}>
                    <td style={{ fontWeight: 700, fontFamily: 'var(--font-display)' }}>{pos.ticker}</td>
                    <td>{pos.shares}</td>
                    <td>{formatCurrency(pos.buy_price)}</td>
                    <td>{formatCurrency(pos.current_price)}</td>
                    <td>{formatCurrency(pos.value)}</td>
                    <td style={{ fontWeight: 600, color: pos.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                      {pos.pnl >= 0 ? '+' : ''}{pos.pnl_pct.toFixed(2)}% ({formatCurrency(pos.pnl)})
                    </td>
                    <td style={{ color: 'var(--color-down)', fontSize: '0.85rem' }}>{formatCurrency(pos.stop_loss)}</td>
                    <td style={{ color: 'var(--color-up)', fontSize: '0.85rem' }}>{formatCurrency(pos.profit_target)}</td>
                    <td>
                      <button 
                        className="btn btn-secondary" 
                        style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                        onClick={() => onSellPosition(pos.ticker)}
                      >
                        Sell
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Grid Section: Closed Trades History */}
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
                      {trade.sell_date.split(' ')[0]}
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
