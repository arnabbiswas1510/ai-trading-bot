import React from 'react';
import { History, TrendingUp, TrendingDown, Award, Calendar } from 'lucide-react';

export default function TradesView({ trades }) {
  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  // Calculate stats from full trade history
  const totalTrades = trades.length;
  const wins = trades.filter(t => t.profit_loss > 0).length;
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
  const netPnL = trades.reduce((sum, t) => sum + t.profit_loss, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
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

    </div>
  );
}
