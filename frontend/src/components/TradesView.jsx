import React, { useState, useEffect } from 'react';
import useSortableTable from '../hooks/useSortableTable';
import { History, TrendingUp, TrendingDown, Award, Calendar, AlertCircle, ShieldAlert, Sparkles, Activity } from 'lucide-react';

export default function TradesView({ trades }) {
  const { items: sortedTrades, requestSort, getSortIcon } = useSortableTable(trades, 'sell_date', 'desc');
  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  const formatDateTime = (dateStr) => {
    if (!dateStr) return 'N/A';
    try {
      const date = new Date(dateStr);
      if (isNaN(date.getTime())) return dateStr;
      
      const yyyy = date.getFullYear();
      const mm = String(date.getMonth() + 1).padStart(2, '0');
      const dd = String(date.getDate()).padStart(2, '0');
      
      const hh = String(date.getHours()).padStart(2, '0');
      const min = String(date.getMinutes()).padStart(2, '0');
      
      return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
    } catch (e) {
      return dateStr;
    }
  };

  const getCleanExitReason = (raw, pctReturn) => {
    if (!raw) return 'Manual Close';
    const lower = raw.toLowerCase();
    
    if (lower.includes('ema-21') || lower.includes('exit ma')) {
      return 'EMA-21 Exit';
    }
    if (lower.includes('force sell') || lower.includes('user request')) {
      return 'Manual Force Sell';
    }
    if (lower.includes('manual close')) {
      return 'Manual Close';
    }
    if (lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
      if (pctReturn >= 24.0) {
        return 'Profit Target';
      } else {
        return 'Trailing Stop Loss';
      }
    }
    
    return raw;
  };

  // Stats from full trade history (history view)
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
                      <th onClick={() => requestSort('ticker')} style={{ cursor: 'pointer' }}>Ticker{getSortIcon('ticker')}</th>
                      <th onClick={() => requestSort('shares')} style={{ cursor: 'pointer' }}>Shares{getSortIcon('shares')}</th>
                      <th onClick={() => requestSort('buy_price')} style={{ cursor: 'pointer' }}>Buy Price / Date{getSortIcon('buy_price')}</th>
                      <th onClick={() => requestSort('sell_price')} style={{ cursor: 'pointer' }}>Sell Price / Date{getSortIcon('sell_price')}</th>
                      <th onClick={() => requestSort('profit_loss')} style={{ cursor: 'pointer' }}>P&L ($){getSortIcon('profit_loss')}</th>
                      <th onClick={() => requestSort('percent_return')} style={{ cursor: 'pointer' }}>Return (%){getSortIcon('percent_return')}</th>
                      <th onClick={() => requestSort('exit_reason')} style={{ cursor: 'pointer' }}>Exit Reason{getSortIcon('exit_reason')}</th>
                    </tr>
                  </thead>

                  <tbody>
                    {sortedTrades.map((trade) => {
                      const buyDateStr = formatDateTime(trade.buy_date);
                      const sellDateStr = formatDateTime(trade.sell_date);
                      const cleanExitReason = getCleanExitReason(trade.exit_reason, trade.percent_return);
                      
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
                          <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {trade.profit_loss >= 0 ? '+' : ''}{formatCurrency(trade.profit_loss)}
                          </td>
                          <td style={{ fontWeight: 600, color: trade.profit_loss >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                            {trade.percent_return.toFixed(2)}%
                          </td>
                          <td>
                            <span 
                              className={`badge ${cleanExitReason === 'Profit Target' ? 'badge-success' : cleanExitReason === 'Trailing Stop Loss' ? 'badge-danger' : 'badge-warning'}`}
                              title={trade.exit_reason}
                            >
                              {cleanExitReason}
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
