import React, { useState, useEffect, useMemo } from 'react';
import { 
  TrendingUp, 
  ArrowDownCircle, 
  ArrowUpCircle, 
  DollarSign, 
  Percent, 
  Calendar,
  Activity,
  History
} from 'lucide-react';
import { 
  AreaChart, 
  Area, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  ReferenceLine
} from 'recharts';

export default function ReturnsView({ trades }) {
  const [cashFlows, setCashFlows] = useState([]);
  const [balances, setBalances] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Date range filters
  const [dateRange, setDateRange] = useState('YTD'); // '1M', '3M', 'YTD', 'ALL'

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [cfRes, balRes] = await Promise.all([
          fetch('/api/cash_flows'),
          fetch('/api/account_balances')
        ]);
        
        if (cfRes.ok) setCashFlows(await cfRes.json());
        if (balRes.ok) setBalances(await balRes.json());
      } catch (e) {
        console.error("Failed to load performance data:", e);
      } finally {
        setLoading(false);
      }
    };
    
    fetchData();
  }, []);

  // Process data for KPIs and Chart
  const processedData = useMemo(() => {
    if (!balances || balances.length === 0) return { chartData: [], kpis: null };

    // 1. Group balances by date (new schema: date is PK, values are columns)
    const dailyBalances = {};
    balances.forEach(b => {
      dailyBalances[b.date] = {
        ibkr_cash_balance: parseFloat(b.ibkr_cash_balance || 0),
        ibkr_positions_value: parseFloat(b.ibkr_positions_value || 0),
        ibkr_total_value: parseFloat(b.ibkr_total_value || 0)
      };
    });

    // 2. Sort dates chronologically
    const sortedDates = Object.keys(dailyBalances).sort();
    
    if (sortedDates.length === 0) return { chartData: [], kpis: null };

    // 3. Map cash flows by date
    const flowsByDate = {};
    cashFlows.forEach(cf => {
      if (!flowsByDate[cf.date]) flowsByDate[cf.date] = 0;
      flowsByDate[cf.date] += parseFloat(cf.amount);
    });

    let cumulativeTwrMultiplier = 1.0;
    let investedCapital = dailyBalances[sortedDates[0]]['ibkr_total_value'] || 0; // Starting capital
    const startingCapital = investedCapital;
    
    let previousValue = startingCapital;
    let netDeposits = 0;

    const chartData = [];

    sortedDates.forEach((date, i) => {
      const todayTotal = dailyBalances[date]['ibkr_total_value'] || previousValue;
      const flowToday = flowsByDate[date] || 0;
      
      netDeposits += flowToday;
      investedCapital += flowToday;

      // Calculate Daily Return for TWR
      // R_t = (Value_today - Flow_today) / Value_yesterday - 1
      let dailyReturn = 0;
      if (previousValue > 0) {
        dailyReturn = (todayTotal - flowToday) / previousValue - 1;
      }

      cumulativeTwrMultiplier *= (1 + dailyReturn);

      chartData.push({
        date,
        totalValue: todayTotal,
        investedCapital: investedCapital,
        twr: (cumulativeTwrMultiplier - 1) * 100,
        flow: flowToday
      });

      previousValue = todayTotal;
    });

    // Filter by dateRange
    let filteredChartData = chartData;
    if (dateRange !== 'ALL' && chartData.length > 0) {
      const latestDate = new Date(chartData[chartData.length - 1].date);
      let cutoffDate = new Date();
      
      if (dateRange === '1M') cutoffDate.setMonth(latestDate.getMonth() - 1);
      else if (dateRange === '3M') cutoffDate.setMonth(latestDate.getMonth() - 3);
      else if (dateRange === 'YTD') cutoffDate = new Date(latestDate.getFullYear(), 0, 1);
      
      const cutoffStr = cutoffDate.toISOString().split('T')[0];
      filteredChartData = chartData.filter(d => d.date >= cutoffStr);
    }

    // Recompute KPIs for the filtered range
    let startVal = 0, endVal = 0, rangeDeposits = 0, rangeTwr = 0;
    if (filteredChartData.length > 0) {
      const startPoint = filteredChartData[0];
      const endPoint = filteredChartData[filteredChartData.length - 1];
      
      startVal = startPoint.totalValue;
      endVal = endPoint.totalValue;
      
      // We need to sum flows strictly within the filtered dates
      filteredChartData.forEach(d => rangeDeposits += d.flow);

      // Rebase TWR for the range
      const startMultiplier = (startPoint.twr / 100) + 1;
      const endMultiplier = (endPoint.twr / 100) + 1;
      rangeTwr = ((endMultiplier / startMultiplier) - 1) * 100;
    }

    const realizedPnL = trades ? trades.reduce((sum, t) => sum + parseFloat(t.profit_loss || 0), 0) : 0;

    return {
      chartData: filteredChartData,
      kpis: {
        startingCapital: startVal,
        currentValue: endVal,
        netDeposits: rangeDeposits,
        twr: rangeTwr,
        realizedPnL
      }
    };
  }, [balances, cashFlows, dateRange, trades]);

  if (loading) {
    return (
      <div className="flex-center" style={{ minHeight: '60vh' }}>
        <div className="spinner"></div>
      </div>
    );
  }

  const { chartData, kpis } = processedData;

  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          padding: '1rem',
          borderRadius: '8px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
        }}>
          <p style={{ margin: '0 0 0.5rem 0', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</p>
          {payload.map((p, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
              <span style={{ color: p.color }}>{p.name}:</span>
              <span style={{ fontWeight: 600 }}>
                {p.name === 'TWR' ? `${p.value.toFixed(2)}%` : `$${p.value.toLocaleString(undefined, {minimumFractionDigits: 2})}`}
              </span>
            </div>
          ))}
          {payload[0] && payload[0].payload.flow !== 0 && (
            <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid var(--border-color)' }}>
              <span style={{ color: payload[0].payload.flow > 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                {payload[0].payload.flow > 0 ? 'Deposit' : 'Withdrawal'}: ${Math.abs(payload[0].payload.flow).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      );
    }
    return null;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      
      {/* Date Controls */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
        {['1M', '3M', 'YTD', 'ALL'].map(range => (
          <button 
            key={range}
            onClick={() => setDateRange(range)}
            style={{
              padding: '0.4rem 1rem',
              borderRadius: '20px',
              border: `1px solid ${dateRange === range ? 'var(--accent-primary)' : 'var(--border-color)'}`,
              background: dateRange === range ? 'rgba(99, 102, 241, 0.1)' : 'transparent',
              color: dateRange === range ? 'var(--accent-primary)' : 'var(--text-secondary)',
              cursor: 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s'
            }}
          >
            {range}
          </button>
        ))}
      </div>

      {/* KPI Strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
        <KpiCard 
          title="Starting Capital" 
          value={`$${(kpis?.startingCapital || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}`}
          icon={<DollarSign size={20} />}
        />
        <KpiCard 
          title="Net Deposits" 
          value={`$${(kpis?.netDeposits || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}`}
          valueColor={kpis?.netDeposits > 0 ? 'var(--success-color)' : 'var(--text-primary)'}
          icon={kpis?.netDeposits >= 0 ? <ArrowUpCircle size={20} /> : <ArrowDownCircle size={20} />}
        />
        <KpiCard 
          title="Realized Trading P&L" 
          value={`${kpis?.realizedPnL >= 0 ? '+' : ''}$${(kpis?.realizedPnL || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}`}
          valueColor={kpis?.realizedPnL >= 0 ? 'var(--success-color)' : 'var(--danger-color)'}
          icon={<Activity size={20} />}
        />
        <KpiCard 
          title="True ROI (TWR %)" 
          value={`${kpis?.twr >= 0 ? '+' : ''}${(kpis?.twr || 0).toFixed(2)}%`}
          valueColor={kpis?.twr >= 0 ? 'var(--success-color)' : 'var(--danger-color)'}
          icon={<Percent size={20} />}
        />
      </div>

      {/* Equity Curve Chart */}
      <div className="dashboard-card" style={{ padding: '1.5rem', minHeight: '400px' }}>
        <h3 style={{ margin: '0 0 1.5rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <TrendingUp size={20} className="text-accent" />
          Portfolio Value vs Invested Capital
        </h3>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={350}>
            <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--accent-primary)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--accent-primary)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
              <XAxis 
                dataKey="date" 
                stroke="var(--text-secondary)" 
                tick={{fill: 'var(--text-secondary)'}}
                tickFormatter={(val) => new Date(val).toLocaleDateString(undefined, {month: 'short', day: 'numeric'})}
              />
              <YAxis 
                stroke="var(--text-secondary)" 
                tick={{fill: 'var(--text-secondary)'}}
                tickFormatter={(val) => `$${(val/1000).toFixed(0)}k`}
                domain={['auto', 'auto']}
              />
              <Tooltip content={<CustomTooltip />} />
              <Area 
                type="stepAfter" 
                dataKey="investedCapital" 
                name="Invested Capital" 
                stroke="var(--text-secondary)" 
                strokeDasharray="5 5" 
                fillOpacity={0} 
                strokeWidth={2}
              />
              <Area 
                type="monotone" 
                dataKey="totalValue" 
                name="Total Value" 
                stroke="var(--accent-primary)" 
                fillOpacity={1} 
                fill="url(#colorTotal)" 
                strokeWidth={3}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex-center" style={{ height: '350px', color: 'var(--text-secondary)' }}>
            Not enough historical data to chart returns.
          </div>
        )}
      </div>

      {/* Audit Ledgers */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
        
        {/* Trade History Ledger */}
        <div className="dashboard-card" style={{ padding: '1.5rem' }}>
          <h3 style={{ margin: '0 0 1rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <History size={18} className="text-accent" />
            Closed Trades
          </h3>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th style={{ textAlign: 'right' }}>PnL</th>
                  <th style={{ textAlign: 'right' }}>Return</th>
                </tr>
              </thead>
              <tbody>
                {trades && trades.length > 0 ? trades.slice(0, 10).map((t, i) => (
                  <tr key={i}>
                    <td>{new Date(t.sell_date).toLocaleDateString()}</td>
                    <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                    <td style={{ textAlign: 'right', color: t.profit_loss >= 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                      {t.profit_loss >= 0 ? '+' : ''}${parseFloat(t.profit_loss).toFixed(2)}
                    </td>
                    <td style={{ textAlign: 'right', color: t.percent_return >= 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                      {t.percent_return >= 0 ? '+' : ''}{parseFloat(t.percent_return).toFixed(2)}%
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan="4" style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>No trades recorded.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Cash Flow Ledger */}
        <div className="dashboard-card" style={{ padding: '1.5rem' }}>
          <h3 style={{ margin: '0 0 1rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <DollarSign size={18} className="text-accent" />
            Cash Flow Ledger
          </h3>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {cashFlows && cashFlows.length > 0 ? cashFlows.map((cf, i) => (
                  <tr key={i}>
                    <td>{new Date(cf.date).toLocaleDateString()}</td>
                    <td>{cf.description || 'Deposit/Withdrawal'}</td>
                    <td style={{ textAlign: 'right', color: cf.amount > 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                      {cf.amount > 0 ? '+' : ''}${parseFloat(cf.amount).toLocaleString(undefined, {minimumFractionDigits: 2})}
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan="3" style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>No external cash flows detected.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}

function KpiCard({ title, value, valueColor = 'var(--text-primary)', icon }) {
  return (
    <div className="dashboard-card" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', color: 'var(--text-secondary)' }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{title}</span>
        {icon}
      </div>
      <div style={{ fontSize: '1.8rem', fontWeight: 700, color: valueColor }}>
        {value}
      </div>
    </div>
  );
}
