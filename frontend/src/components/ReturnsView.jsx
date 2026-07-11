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
  const [dateRange, setDateRange] = useState('YTD'); // '1M', '3M', 'YTD', 'ALL', 'CUSTOM'
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [appliedFromDate, setAppliedFromDate] = useState('');
  const [appliedToDate, setAppliedToDate] = useState('');
  const [returnType, setReturnType] = useState('TWR'); // 'TWR' vs 'SIMPLE'
  const [benchmarks, setBenchmarks] = useState([]);
  const [benchmarksLoading, setBenchmarksLoading] = useState(false);

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

    // 1. Group balances by date
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

      // Calculate Daily Return for TWR (excl. cash flows)
      let dailyReturn = 0;
      if (previousValue > 0) {
        dailyReturn = (todayTotal - flowToday) / previousValue - 1;
      }

      cumulativeTwrMultiplier *= (1 + dailyReturn);

      // Simple Return (with cash deposits)
      const simpleReturn = investedCapital > 0 ? ((todayTotal / investedCapital) - 1) * 100 : 0;
      const twrReturn = (cumulativeTwrMultiplier - 1) * 100;

      chartData.push({
        date,
        totalValue: todayTotal,
        investedCapital: investedCapital,
        twr: twrReturn,
        simpleReturn: simpleReturn,
        flow: flowToday
      });

      previousValue = todayTotal;
    });

    // Filter by dateRange or date pickers
    let filteredChartData = chartData;
    if (appliedFromDate || appliedToDate) {
      filteredChartData = chartData.filter(d => {
        if (appliedFromDate && d.date < appliedFromDate) return false;
        if (appliedToDate && d.date > appliedToDate) return false;
        return true;
      });
    } else if (dateRange !== 'ALL' && chartData.length > 0) {
      const latestDate = new Date(chartData[chartData.length - 1].date);
      let cutoffDate = new Date();
      
      if (dateRange === '1M') cutoffDate.setMonth(latestDate.getMonth() - 1);
      else if (dateRange === '3M') cutoffDate.setMonth(latestDate.getMonth() - 3);
      else if (dateRange === 'YTD') cutoffDate = new Date(latestDate.getFullYear(), 0, 1);
      
      const cutoffStr = cutoffDate.toISOString().split('T')[0];
      filteredChartData = chartData.filter(d => d.date >= cutoffStr);
    }

    // Recompute KPIs for the filtered range
    let startVal = 0, endVal = 0, rangeDeposits = 0, rangeTwr = 0, rangeSimple = 0;
    if (filteredChartData.length > 0) {
      const startPoint = filteredChartData[0];
      const endPoint = filteredChartData[filteredChartData.length - 1];
      
      startVal = startPoint.totalValue;
      endVal = endPoint.totalValue;
      
      filteredChartData.forEach(d => rangeDeposits += d.flow);

      // Rebase TWR for the range
      const startMultiplier = (startPoint.twr / 100) + 1;
      const endMultiplier = (endPoint.twr / 100) + 1;
      rangeTwr = startMultiplier > 0 ? ((endMultiplier / startMultiplier) - 1) * 100 : 0;

      // Rebase Simple Return for the range (includes cash deposits)
      rangeSimple = (startVal + rangeDeposits) > 0 ? ((endVal - startVal - rangeDeposits) / (startVal + rangeDeposits)) * 100 : 0;
    }

    const realizedPnL = trades ? trades.reduce((sum, t) => sum + parseFloat(t.profit_loss || 0), 0) : 0;

    return {
      chartData: filteredChartData,
      effectiveFromDate: filteredChartData.length > 0 ? filteredChartData[0].date : null,
      effectiveToDate: filteredChartData.length > 0 ? filteredChartData[filteredChartData.length - 1].date : null,
      kpis: {
        startingCapital: startVal,
        currentValue: endVal,
        netDeposits: rangeDeposits,
        roi: returnType === 'TWR' ? rangeTwr : rangeSimple,
        realizedPnL
      }
    };
  }, [balances, cashFlows, dateRange, appliedFromDate, appliedToDate, returnType, trades]);

  // Fetch benchmark returns whenever the effective date range changes
  useEffect(() => {
    if (!processedData.effectiveFromDate || !processedData.effectiveToDate) return;
    setBenchmarksLoading(true);
    const params = new URLSearchParams({
      from_date: processedData.effectiveFromDate,
      to_date:   processedData.effectiveToDate,
    });
    fetch(`/api/benchmark_returns?${params}`)
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(data => setBenchmarks(data.benchmarks || []))
      .catch(err => { console.error('Benchmark fetch failed:', err); setBenchmarks([]); })
      .finally(() => setBenchmarksLoading(false));
  }, [processedData.effectiveFromDate, processedData.effectiveToDate]);

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
      const dataPoint = payload[0].payload;
      const displayReturn = returnType === 'TWR' ? dataPoint.twr : dataPoint.simpleReturn;
      const returnLabel = returnType === 'TWR' ? 'TWR' : 'Simple ROI';

      return (
        <div style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          padding: '1rem',
          borderRadius: '8px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
        }}>
          <p style={{ margin: '0 0 0.5rem 0', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</p>
          
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
            <span style={{ color: 'var(--accent-primary)' }}>Total Value:</span>
            <span style={{ fontWeight: 600 }}>
              ${dataPoint.totalValue.toLocaleString(undefined, {minimumFractionDigits: 2})}
            </span>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Invested Capital:</span>
            <span style={{ fontWeight: 600 }}>
              ${dataPoint.investedCapital.toLocaleString(undefined, {minimumFractionDigits: 2})}
            </span>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
            <span style={{ color: '#10b981' }}>{returnLabel}:</span>
            <span style={{ fontWeight: 600, color: displayReturn >= 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
              {displayReturn >= 0 ? '+' : ''}{displayReturn.toFixed(2)}%
            </span>
          </div>

          {dataPoint.flow !== 0 && (
            <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid var(--border-color)' }}>
              <span style={{ color: dataPoint.flow > 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                {dataPoint.flow > 0 ? 'Deposit' : 'Withdrawal'}: ${Math.abs(dataPoint.flow).toLocaleString()}
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
      
      {/* Controls Bar */}
      <div style={{ 
        display: 'flex', 
        flexDirection: 'column',
        gap: '1rem',
        background: 'rgba(255, 255, 255, 0.01)',
        padding: '1.25rem',
        borderRadius: '12px',
        border: '1px solid var(--border-color)',
        marginBottom: '1rem'
      }}>
        {/* Row 1: Return Calculation Toggle & Quick Ranges */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '1rem'
        }}>
          {/* Toggle Switch */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', fontWeight: 500 }}>Return Calculation:</span>
            <div style={{
              display: 'flex',
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid var(--border-color)',
              borderRadius: '20px',
              padding: '2px'
            }}>
              <button
                onClick={() => setReturnType('TWR')}
                style={{
                  padding: '0.35rem 0.85rem',
                  borderRadius: '18px',
                  border: 'none',
                  background: returnType === 'TWR' ? 'var(--accent-primary)' : 'transparent',
                  color: returnType === 'TWR' ? '#ffffff' : 'var(--text-secondary)',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.2s'
                }}
              >
                Time-Weighted (TWR)
              </button>
              <button
                onClick={() => setReturnType('SIMPLE')}
                style={{
                  padding: '0.35rem 0.85rem',
                  borderRadius: '18px',
                  border: 'none',
                  background: returnType === 'SIMPLE' ? 'var(--accent-primary)' : 'transparent',
                  color: returnType === 'SIMPLE' ? '#ffffff' : 'var(--text-secondary)',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.2s'
                }}
              >
                Simple ROI
              </button>
            </div>
          </div>

          {/* Quick Ranges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', fontWeight: 500, marginRight: '0.25rem' }}>Quick Period:</span>
            {['1M', '3M', 'YTD', 'ALL'].map(range => (
              <button 
                key={range}
                onClick={() => {
                  setDateRange(range);
                  setFromDate('');
                  setToDate('');
                  setAppliedFromDate('');
                  setAppliedToDate('');
                }}
                style={{
                  padding: '0.4rem 1.0rem',
                  borderRadius: '20px',
                  border: `1px solid ${dateRange === range ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                  background: dateRange === range ? 'rgba(99, 102, 241, 0.1)' : 'transparent',
                  color: dateRange === range ? 'var(--accent-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  transition: 'all 0.2s'
                }}
              >
                {range}
              </button>
            ))}
          </div>
        </div>

        {/* Separator line */}
        <div style={{ height: '1px', background: 'var(--border-color)', width: '100%' }}></div>

        {/* Row 2: Custom Date Filter with Apply Button */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-start',
          flexWrap: 'wrap',
          gap: '1rem'
        }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', fontWeight: 500 }}>Custom Date Range:</span>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>From:</span>
              <input 
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                style={{
                  padding: '0.35rem 0.5rem',
                  borderRadius: '6px',
                  border: '1px solid var(--border-color)',
                  background: 'rgba(255, 255, 255, 0.02)',
                  color: 'var(--text-primary)',
                  fontSize: '0.82rem',
                  outline: 'none',
                  colorScheme: 'dark'
                }}
              />
            </div>
            
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>To:</span>
              <input 
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                style={{
                  padding: '0.35rem 0.5rem',
                  borderRadius: '6px',
                  border: '1px solid var(--border-color)',
                  background: 'rgba(255, 255, 255, 0.02)',
                  color: 'var(--text-primary)',
                  fontSize: '0.82rem',
                  outline: 'none',
                  colorScheme: 'dark'
                }}
              />
            </div>

            <button
              onClick={() => {
                setAppliedFromDate(fromDate);
                setAppliedToDate(toDate);
                setDateRange('CUSTOM');
              }}
              style={{
                padding: '0.35rem 1rem',
                borderRadius: '6px',
                border: 'none',
                background: 'var(--accent-primary)',
                color: '#ffffff',
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
            >
              Apply Filter
            </button>

            {(fromDate || toDate || appliedFromDate || appliedToDate) && (
              <button
                onClick={() => {
                  setFromDate('');
                  setToDate('');
                  setAppliedFromDate('');
                  setAppliedToDate('');
                  setDateRange('ALL');
                }}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--color-down)',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 600
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>
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
          title={returnType === 'TWR' ? "True ROI (TWR %)" : "Simple ROI (%)"}
          value={`${kpis?.roi >= 0 ? '+' : ''}${(kpis?.roi || 0).toFixed(2)}%`}
          valueColor={kpis?.roi >= 0 ? 'var(--success-color)' : 'var(--danger-color)'}
          icon={<Percent size={20} />}
        />
      </div>

      {/* Benchmark Comparison */}
      <div className="dashboard-card" style={{ padding: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <TrendingUp size={20} className="text-accent" />
            Benchmark Comparison
          </h3>
          {processedData.effectiveFromDate && processedData.effectiveToDate && (
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.04)', padding: '0.25rem 0.65rem', borderRadius: '20px', border: '1px solid var(--border-color)' }}>
              {processedData.effectiveFromDate} → {processedData.effectiveToDate}
            </span>
          )}
        </div>
        {benchmarksLoading ? (
          <div className="flex-center" style={{ height: '80px' }}><div className="spinner" /></div>
        ) : benchmarks.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', textAlign: 'center', padding: '1rem' }}>
            Benchmark data unavailable — ensure FMP API key is configured in Settings.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
            {benchmarks.map(b => (
              <BenchmarkCard
                key={b.symbol}
                benchmark={b}
                botReturn={kpis?.roi ?? null}
              />
            ))}
          </div>
        )}
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

function BenchmarkCard({ benchmark, botReturn }) {
  const indexReturn = benchmark.return;
  const hasData     = indexReturn !== null && indexReturn !== undefined;
  const alpha       = hasData && botReturn !== null ? botReturn - indexReturn : null;
  const beating     = alpha !== null && alpha > 0;
  const losing      = alpha !== null && alpha < 0;

  const COLORS = {
    SPY: '#6366f1',
    QQQ: '#f59e0b',
    IWM: '#10b981',
  };
  const accentColor = COLORS[benchmark.symbol] || 'var(--accent-primary)';

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: `1px solid ${beating ? 'rgba(16,185,129,0.3)' : losing ? 'rgba(239,68,68,0.25)' : 'var(--border-color)'}`,
      borderRadius: '12px',
      padding: '1.1rem 1.25rem',
      display: 'flex',
      flexDirection: 'column',
      gap: '0.6rem',
      transition: 'border-color 0.2s',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: accentColor, flexShrink: 0 }} />
          <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-primary)' }}>
            {benchmark.symbol}
          </span>
        </div>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{benchmark.name}</span>
      </div>

      {/* Index return */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>Index return</span>
        {hasData ? (
          <span style={{
            fontSize: '1.35rem', fontWeight: 700,
            color: indexReturn >= 0 ? 'var(--success-color)' : 'var(--danger-color)'
          }}>
            {indexReturn >= 0 ? '+' : ''}{indexReturn.toFixed(2)}%
          </span>
        ) : (
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            {benchmark.error || 'N/A'}
          </span>
        )}
      </div>

      {/* Bot return */}
      {botReturn !== null && (
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>Bot return</span>
          <span style={{
            fontSize: '1.05rem', fontWeight: 600,
            color: botReturn >= 0 ? 'var(--success-color)' : 'var(--danger-color)'
          }}>
            {botReturn >= 0 ? '+' : ''}{botReturn.toFixed(2)}%
          </span>
        </div>
      )}

      {/* Alpha */}
      {alpha !== null && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          paddingTop: '0.5rem', borderTop: '1px solid var(--border-color)',
        }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>Alpha</span>
          <span style={{
            fontWeight: 700,
            fontSize: '0.95rem',
            color: beating ? 'var(--success-color)' : losing ? 'var(--danger-color)' : 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', gap: '0.3rem',
          }}>
            {beating ? '▲' : losing ? '▼' : '—'}
            {alpha >= 0 ? '+' : ''}{alpha.toFixed(2)}%
          </span>
        </div>
      )}
    </div>
  );
}

