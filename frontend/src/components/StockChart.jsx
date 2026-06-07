import React from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Line,
  CartesianGrid,
  ReferenceLine
} from 'recharts';

export default function StockChart({ data, ticker }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ display: 'flex', height: 250, alignItems: 'center', justifyContent: 'center', color: '#6b7280' }}>
        No historical data loaded.
      </div>
    );
  }

  // Format tooltip values
  const formatPrice = (value) => `$${Number(value).toFixed(2)}`;
  const formatVolume = (value) => Number(value).toLocaleString();

  // Custom tooltip component
  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div style={{
          backgroundColor: '#111827',
          border: '1px solid rgba(255, 255, 255, 0.08)',
          borderRadius: 8,
          padding: '10px 14px',
          boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.5)'
        }}>
          <p style={{ margin: 0, fontSize: 12, color: '#9ca3af', fontWeight: 600 }}>{label}</p>
          {payload.map((item, idx) => (
            <p key={idx} style={{ margin: '4px 0 0 0', fontSize: 13, color: item.color || '#fff', fontWeight: 600 }}>
              {item.name}: {item.name.toLowerCase().includes('volume') ? formatVolume(item.value) : formatPrice(item.value)}
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%' }}>
      {/* Price Chart */}
      <div style={{ height: 250, width: '100%' }}>
        <h4 style={{ marginBottom: '0.5rem', color: '#9ca3af', display: 'flex', justifyContent: 'space-between' }}>
          <span>{ticker} Price History</span>
          <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>1-Year Daily</span>
        </h4>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} syncId="stockSync" margin={{ top: 5, right: 5, left: -25, bottom: 5 }}>
            <defs>
              <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.2}/>
                <stop offset="95%" stopColor="#06b6d4" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.03)" />
            <XAxis dataKey="date" hide={true} />
            <YAxis 
              domain={['auto', 'auto']} 
              stroke="#6b7280" 
              fontSize={11} 
              tickFormatter={(v) => `$${v}`}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area 
              type="monotone" 
              dataKey="close" 
              name="Price" 
              stroke="#06b6d4" 
              strokeWidth={2}
              fillOpacity={1} 
              fill="url(#colorPrice)" 
            />
            <Line 
              type="monotone" 
              dataKey="sma50" 
              name="50-day SMA" 
              stroke="#8b5cf6" 
              strokeWidth={1.5} 
              dot={false}
            />
            <Line 
              type="monotone" 
              dataKey="sma200" 
              name="200-day SMA" 
              stroke="#f59e0b" 
              strokeWidth={1.5} 
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Volume Chart */}
      <div style={{ height: 120, width: '100%' }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} syncId="stockSync" margin={{ top: 5, right: 5, left: -25, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.03)" />
            <XAxis dataKey="date" stroke="#6b7280" fontSize={10} />
            <YAxis 
              stroke="#6b7280" 
              fontSize={10} 
              tickFormatter={(v) => {
                if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
                if (v >= 1000) return `${(v / 1000).toFixed(0)}K`;
                return v;
              }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar 
              dataKey="volume" 
              name="Volume" 
              fill="rgba(255, 255, 255, 0.15)"
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
