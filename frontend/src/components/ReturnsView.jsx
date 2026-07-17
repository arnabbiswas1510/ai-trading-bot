import React, { useState, useEffect, useMemo } from 'react';
import { 
  TrendingUp, 
  ArrowDownCircle, 
  ArrowUpCircle, 
  DollarSign, 
  Percent, 
  Activity,
  History,
  BarChart2,
  ShieldAlert
} from 'lucide-react';
import { 
  AreaChart, 
  Area, 
  LineChart,
  Line,
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  Legend,
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
  const [activeBenchmark, setActiveBenchmark] = useState('BOT');

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
    // Starting capital = first day's total portfolio value (already includes initial deposit).
    // Cash flows from account_balances are already baked into ibkr_total_value on that date,
    // so we must NOT add the Day-1 cash flow again — that causes the $200K double-count.
    let investedCapital = dailyBalances[sortedDates[0]]['ibkr_total_value'] || 0;
    const startingCapital = investedCapital;

    let previousValue = startingCapital;
    let netDeposits = 0;

    const chartData = [];

    sortedDates.forEach((date, i) => {
      const todayTotal = dailyBalances[date]['ibkr_total_value'] || previousValue;
      const flowToday = flowsByDate[date] || 0;

      netDeposits += flowToday;
      // Only adjust investedCapital for flows AFTER Day 0.
      // The Day-0 flow is already reflected in startingCapital (ibkr_total_value),
      // so adding it here would double-count the initial deposit.
      if (i > 0) {
        investedCapital += flowToday;
      }

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
      endVal   = endPoint.totalValue;
      filteredChartData.forEach(d => rangeDeposits += d.flow);
      const startMultiplier = (startPoint.twr / 100) + 1;
      const endMultiplier   = (endPoint.twr   / 100) + 1;
      rangeTwr    = startMultiplier > 0 ? ((endMultiplier / startMultiplier) - 1) * 100 : 0;
      rangeSimple = (startVal + rangeDeposits) > 0 ? ((endVal - startVal - rangeDeposits) / (startVal + rangeDeposits)) * 100 : 0;
    }

    const periodRoi = returnType === 'TWR' ? rangeTwr : rangeSimple;
    const realizedPnL = trades ? trades.reduce((sum, t) => sum + parseFloat(t.profit_loss || 0), 0) : 0;

    // ── Risk metrics from portfolio daily values ─────────────────────────────
    const dailyReturns = [];
    for (let i = 1; i < filteredChartData.length; i++) {
      const prev = filteredChartData[i - 1].totalValue;
      const curr = filteredChartData[i].totalValue;
      if (prev > 0) dailyReturns.push((curr - prev) / prev);
    }
    let botVolatility = null;
    // Require at least 20 data points for meaningful annualized volatility.
    // With fewer points one outlier day dominates the std-dev completely.
    if (dailyReturns.length >= 20) {
      const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
      const variance = dailyReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
      botVolatility = Math.sqrt(variance) * Math.sqrt(252) * 100;
    }

    let botMaxDrawdown = null;
    if (filteredChartData.length > 1) {
      let peak = filteredChartData[0].totalValue;
      let maxDD = 0;
      for (const d of filteredChartData) {
        if (d.totalValue > peak) peak = d.totalValue;
        const dd = (d.totalValue - peak) / peak;
        if (dd < maxDD) maxDD = dd;
      }
      botMaxDrawdown = maxDD * 100;
    }

    let botAnnReturn = null;
    let botReturnIsAnnualized = false;
    if (filteredChartData.length > 1) {
      const t0 = new Date(filteredChartData[0].date);
      const t1 = new Date(filteredChartData[filteredChartData.length - 1].date);
      const nDays = Math.max((t1 - t0) / (1000 * 60 * 60 * 24), 1);
      if (nDays >= 180) {
        // Enough history to annualize meaningfully
        botAnnReturn = (Math.pow(1 + periodRoi / 100, 365 / nDays) - 1) * 100;
        botReturnIsAnnualized = true;
      } else {
        // Too short to annualize — show the raw period return instead
        botAnnReturn = periodRoi;
        botReturnIsAnnualized = false;
      }
    }

    // ── Bot normalized series for the benchmark chart (start = 100) ──────────
    const botNormalized = filteredChartData.length > 0
      ? filteredChartData.map(d => ({
          date: d.date,
          BOT: parseFloat(((d.totalValue / filteredChartData[0].totalValue) * 100).toFixed(4))
        }))
      : [];

    return {
      chartData: filteredChartData,
      botNormalized,
      effectiveFromDate: filteredChartData.length > 0 ? filteredChartData[0].date : null,
      effectiveToDate:   filteredChartData.length > 0 ? filteredChartData[filteredChartData.length - 1].date : null,
      kpis: {
        startingCapital: startVal,
        currentValue:    endVal,
        netDeposits:     rangeDeposits,
        roi:             periodRoi,
        annReturn:       botAnnReturn,
        isAnnualized:    botReturnIsAnnualized,
        volatility:      botVolatility,
        maxDrawdown:     botMaxDrawdown,
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

      {/* KPI Strip — 6 cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '1rem' }}>
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
          title="Realized P&L" 
          value={`${kpis?.realizedPnL >= 0 ? '+' : ''}$${(kpis?.realizedPnL || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}`}
          valueColor={kpis?.realizedPnL >= 0 ? 'var(--success-color)' : 'var(--danger-color)'}
          icon={<Activity size={20} />}
        />
        <KpiCard 
          title={
            kpis?.isAnnualized
              ? (returnType === 'TWR' ? 'Ann. Return (TWR)' : 'Ann. Return (Simple)')
              : (returnType === 'TWR' ? 'Period Return (TWR)' : 'Period Return (Simple)')
          }
          value={kpis?.annReturn != null ? `${kpis.annReturn >= 0 ? '+' : ''}${kpis.annReturn.toFixed(2)}%` : '—'}
          valueColor={kpis?.annReturn >= 0 ? 'var(--success-color)' : 'var(--danger-color)'}
          icon={<Percent size={20} />}
        />
        <KpiCard 
          title="Volatility (Ann.)"
          value={kpis?.volatility != null ? `${kpis.volatility.toFixed(2)}%` : '— (need 20+ days)'}
          icon={<BarChart2 size={20} />}
        />
        <KpiCard 
          title="Max Drawdown"
          value={kpis?.maxDrawdown != null ? `${kpis.maxDrawdown.toFixed(2)}%` : '—'}
          valueColor={kpis?.maxDrawdown != null && kpis.maxDrawdown < -5 ? 'var(--danger-color)' : kpis?.maxDrawdown != null && kpis.maxDrawdown < -2 ? '#f59e0b' : 'var(--success-color)'}
          icon={<ShieldAlert size={20} />}
        />
      </div>

      {/* Benchmark Analyzer */}
      <BenchmarkAnalyzer
        benchmarks={benchmarks}
        benchmarksLoading={benchmarksLoading}
        botNormalized={processedData.botNormalized}
        botStats={{
          ann_return:   kpis?.annReturn,
          is_annualized: kpis?.isAnnualized,
          volatility:   kpis?.volatility,
          max_drawdown: kpis?.maxDrawdown,
          return:       kpis?.roi
        }}
        activeBenchmark={activeBenchmark}
        setActiveBenchmark={setActiveBenchmark}
        effectiveFromDate={processedData.effectiveFromDate}
        effectiveToDate={processedData.effectiveToDate}
      />

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

// ── Colour palette shared by chart + table ──────────────────────────────────
const BENCH_COLORS = {
  BOT: '#a855f7',
  SPY: '#ef4444',
  QQQ: '#3b82f6',
  IWM: '#10b981',
  RSP: '#f59e0b',
};

function KpiCard({ title, value, valueColor = 'var(--text-primary)', icon }) {
  return (
    <div className="dashboard-card" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', color: 'var(--text-secondary)' }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{title}</span>
        {icon}
      </div>
      <div style={{ fontSize: '1.75rem', fontWeight: 700, color: valueColor }}>{value}</div>
    </div>
  );
}

// ── Benchmark Analyzer ───────────────────────────────────────────────────────
function BenchmarkAnalyzer({ benchmarks, benchmarksLoading, botNormalized, botStats, activeBenchmark, setActiveBenchmark, effectiveFromDate, effectiveToDate }) {

  // Build combined chart data: merge bot + all benchmark daily_normalized by date
  const combinedChart = useMemo(() => {
    const byDate = {};
    (botNormalized || []).forEach(({ date, BOT }) => {
      byDate[date] = { date, BOT };
    });
    (benchmarks || []).forEach(b => {
      (b.daily_normalized || []).forEach(({ date, value }) => {
        if (!byDate[date]) byDate[date] = { date };
        byDate[date][b.symbol] = value;
      });
    });
    return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date));
  }, [botNormalized, benchmarks]);

  // Build table rows: Bot first, then each benchmark
  const tableRows = useMemo(() => {
    const rows = [];
    if (botStats) {
      rows.push({
        symbol: 'BOT', name: 'This Bot',
        ann_return:   botStats.ann_return,
        volatility:   botStats.volatility,
        max_drawdown: botStats.max_drawdown,
        return:       botStats.return,
        error: null
      });
    }
    (benchmarks || []).forEach(b => rows.push(b));
    return rows;
  }, [botStats, benchmarks]);

  const activeRow = tableRows.find(r => r.symbol === activeBenchmark) || tableRows[0];

  const fmtPct = (v, signed = true) => {
    if (v == null) return '—';
    return `${signed && v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  };

  const BenchmarkTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    return (
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', padding: '0.75rem 1rem', borderRadius: '8px', fontSize: '0.82rem' }}>
        <p style={{ margin: '0 0 0.4rem 0', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</p>
        {payload.map(p => (
          <div key={p.dataKey} style={{ display: 'flex', justifyContent: 'space-between', gap: '1.5rem', color: p.color }}>
            <span>{p.name}</span>
            <span style={{ fontWeight: 600 }}>{p.value != null ? p.value.toFixed(2) : '—'}</span>
          </div>
        ))}
      </div>
    );
  };

  const seriesKeys = ['BOT', ...((benchmarks || []).map(b => b.symbol))];

  return (
    <div className="dashboard-card" style={{ padding: '1.5rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <TrendingUp size={20} className="text-accent" />
          Benchmark Analyzer
        </h3>
        {effectiveFromDate && effectiveToDate && (
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.04)', padding: '0.25rem 0.65rem', borderRadius: '20px', border: '1px solid var(--border-color)' }}>
            {effectiveFromDate} → {effectiveToDate}
          </span>
        )}
      </div>

      {benchmarksLoading ? (
        <div className="flex-center" style={{ height: '260px' }}><div className="spinner" /></div>
      ) : benchmarks.length === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', textAlign: 'center', padding: '3rem' }}>
          Benchmark data unavailable — ensure FMP API key is configured in Settings.
        </div>
      ) : (
        <>
          {/* Normalized price chart */}
          <p style={{ margin: '0 0 0.75rem 0', fontSize: '0.78rem', color: 'var(--text-muted)', fontWeight: 500 }}>Normalized Price ($) ↑</p>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={combinedChart} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
              <XAxis
                dataKey="date"
                stroke="var(--text-muted)"
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                tickFormatter={v => {
                  const d = new Date(v);
                  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                }}
                interval="preserveStartEnd"
              />
              <YAxis
                stroke="var(--text-muted)"
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                domain={['auto', 'auto']}
                tickFormatter={v => `$${v.toFixed(0)}`}
              />
              <ReferenceLine y={100} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
              <Tooltip content={<BenchmarkTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: '0.8rem', paddingTop: '0.5rem' }}
                formatter={name => <span style={{ color: BENCH_COLORS[name] || '#aaa' }}>{name}</span>}
              />
              {seriesKeys.map(key => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={key}
                  stroke={BENCH_COLORS[key] || '#aaa'}
                  strokeWidth={key === 'BOT' ? 2.5 : 1.5}
                  dot={false}
                  connectNulls
                  strokeOpacity={activeBenchmark === key ? 1 : 0.45}
                  strokeWidth={activeBenchmark === key ? 3 : (key === 'BOT' ? 2.5 : 1.5)}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          {/* Table */}
          <div style={{ marginTop: '1.5rem', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.88rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                  {['Ticker',
                    botStats?.is_annualized === false ? 'Return (Period)' : 'Ann. Return',
                    'Volatility', 'Max Drawdown'].map(h => (
                    <th key={h} style={{ padding: '0.6rem 1rem', textAlign: h === 'Ticker' ? 'left' : 'right', color: 'var(--text-secondary)', fontWeight: 600, fontSize: '0.82rem', textTransform: 'uppercase', letterSpacing: '0.4px' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableRows.map(row => {
                  const isActive = row.symbol === activeBenchmark;
                  const hasData  = row.ann_return != null;
                  return (
                    <tr
                      key={row.symbol}
                      onClick={() => setActiveBenchmark(row.symbol)}
                      style={{
                        background: isActive ? `${BENCH_COLORS[row.symbol] || 'rgba(99,102,241,0.25)'}22` : 'transparent',
                        borderLeft: isActive ? `3px solid ${BENCH_COLORS[row.symbol] || 'var(--accent-primary)'}` : '3px solid transparent',
                        cursor: 'pointer',
                        transition: 'background 0.15s',
                        borderBottom: '1px solid rgba(255,255,255,0.04)'
                      }}
                    >
                      <td style={{ padding: '0.7rem 1rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <div style={{ width: 9, height: 9, borderRadius: '50%', background: BENCH_COLORS[row.symbol] || '#aaa', flexShrink: 0 }} />
                          <span style={{ fontWeight: 700 }}>{row.symbol}</span>
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{row.name}</span>
                        </div>
                      </td>
                      <td style={{ padding: '0.7rem 1rem', textAlign: 'right', fontWeight: 600, color: hasData && row.ann_return >= 0 ? 'var(--success-color)' : 'var(--danger-color)' }}>
                        {fmtPct(row.ann_return)}
                      </td>
                      <td style={{ padding: '0.7rem 1rem', textAlign: 'right', fontWeight: 500, color: 'var(--text-primary)' }}>
                        {fmtPct(row.volatility, false)}
                      </td>
                      <td style={{ padding: '0.7rem 1rem', textAlign: 'right', fontWeight: 500, color: row.max_drawdown != null && row.max_drawdown < -10 ? 'var(--danger-color)' : row.max_drawdown != null && row.max_drawdown < -5 ? '#f59e0b' : 'var(--text-primary)' }}>
                        {fmtPct(row.max_drawdown)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Summary stats for active row */}
          {activeRow && (
            <div style={{
              marginTop: '1.5rem',
              padding: '1rem 1.5rem',
              background: 'rgba(255,255,255,0.03)',
              borderRadius: '10px',
              border: '1px solid var(--border-color)',
              display: 'flex',
              gap: '0',
              alignItems: 'center',
            }}>
              {[
                { label: 'ANNUALIZED RETURN', value: fmtPct(activeRow.ann_return) },
                { label: 'VOLATILITY',        value: fmtPct(activeRow.volatility, false) },
                { label: 'MAX DRAWDOWN',      value: fmtPct(activeRow.max_drawdown) },
              ].map((stat, i, arr) => (
                <React.Fragment key={stat.label}>
                  <div style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.8px', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.3rem' }}>{stat.label}</div>
                    <div style={{ fontSize: '1.45rem', fontWeight: 700, color: BENCH_COLORS[activeRow.symbol] || 'var(--accent-primary)' }}>{stat.value}</div>
                  </div>
                  {i < arr.length - 1 && <div style={{ width: '1px', height: '40px', background: 'var(--border-color)', flexShrink: 0 }} />}
                </React.Fragment>
              ))}
            </div>
          )}

          {/* Active series label */}
          <div style={{ marginTop: '1rem', fontSize: '0.8rem', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between' }}>
            <span>Active Series: <strong style={{ color: BENCH_COLORS[activeBenchmark] || 'var(--accent-primary)' }}>{activeBenchmark}</strong></span>
            <span style={{ color: 'var(--text-muted)' }}>Click a row to highlight its series</span>
          </div>
        </>
      )}
    </div>
  );
}

