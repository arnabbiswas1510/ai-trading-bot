import React, { useState, useEffect } from 'react';
import { 
  LayoutDashboard, 
  Search, 
  BarChart2, 
  Settings as SettingsIcon,
  TrendingUp,
  Cpu
} from 'lucide-react';

import DashboardView from './components/DashboardView';
import ScreenerView from './components/ScreenerView';
import BacktesterView from './components/BacktesterView';
import SettingsView from './components/SettingsView';

export default function App() {
  const [currentView, setCurrentView] = useState('dashboard');
  const [marketData, setMarketData] = useState(null);
  const [screenerResults, setScreenerResults] = useState([]);
  const [portfolioData, setPortfolioData] = useState(null);
  const [tradeHistory, setTradeHistory] = useState([]);
  const [settings, setSettings] = useState(null);
  
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [dataLoading, setDataLoading] = useState(true);

  const fetchAllData = async () => {
    try {
      // Fetch market direction
      const marketRes = await fetch('/api/market');
      if (marketRes.ok) {
        const mData = await marketRes.json();
        setMarketData(mData);
      }
      
      // Fetch cached screener results
      const screenerRes = await fetch('/api/screener/results');
      if (screenerRes.ok) {
        const sData = await screenerRes.json();
        setScreenerResults(sData);
      }
      
      // Fetch portfolio summary & positions
      const portfolioRes = await fetch('/api/portfolio');
      if (portfolioRes.ok) {
        const pData = await portfolioRes.json();
        setPortfolioData(pData);
      }

      // Fetch completed trades
      const tradesRes = await fetch('/api/trades');
      if (tradesRes.ok) {
        const tData = await tradesRes.json();
        setTradeHistory(tData);
      }

      // Fetch configurations
      const settingsRes = await fetch('/api/settings');
      if (settingsRes.ok) {
        const setts = await settingsRes.json();
        setSettings(setts);
      }
    } catch (e) {
      console.error("Failed to load initial REST API data: ", e);
    } finally {
      setDataLoading(false);
    }
  };

  useEffect(() => {
    fetchAllData();
  }, []);

  const handleRunScan = async () => {
    setScreenerLoading(true);
    try {
      const res = await fetch('/api/screener/run');
      if (!res.ok) throw new Error("Screener failed");
      const results = await res.json();
      setScreenerResults(results);
      // Refresh portfolio and market status too
      await fetchAllData();
    } catch (err) {
      alert(`Failed to execute scanner: ${err.message}`);
    } finally {
      setScreenerLoading(false);
    }
  };

  const handleBuyStock = async (ticker, shares) => {
    const res = await fetch('/api/portfolio/buy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, shares })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Buy order failed");
    }
    // Refresh portfolio
    await fetchAllData();
  };

  const handleSellPosition = async (ticker) => {
    const reason = prompt("Enter exit reason:", "Manual Profit Taker");
    if (reason === null) return; // cancelled
    
    try {
      const res = await fetch('/api/portfolio/sell', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, reason })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Sell order failed");
      }
      alert(`Position for ${ticker} closed successfully.`);
      await fetchAllData();
    } catch (err) {
      alert(`Sell order failed: ${err.message}`);
    }
  };

  const handleSaveSettings = async (updatedSettings) => {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updatedSettings)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Saving settings failed");
    }
    await fetchAllData();
  };

  const handleResetPortfolio = async () => {
    const res = await fetch('/api/settings/reset', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Reset failed");
    }
    await fetchAllData();
  };

  const renderView = () => {
    if (dataLoading) {
      return (
        <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '1rem', minHeight: '60vh' }}>
          <div className="spinner" style={{ width: '40px', height: '40px', borderTopColor: 'var(--accent-primary)' }}></div>
          <p style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>Initializing Core Analytics Engine...</p>
        </div>
      );
    }

    switch (currentView) {
      case 'dashboard':
        return (
          <DashboardView 
            data={portfolioData} 
            marketData={marketData} 
            trades={tradeHistory}
            onSellPosition={handleSellPosition}
          />
        );
      case 'screener':
        return (
          <ScreenerView 
            results={screenerResults} 
            onRunScan={handleRunScan} 
            loading={screenerLoading}
            onBuyStock={handleBuyStock}
          />
        );
      case 'backtester':
        return <BacktesterView />;
      case 'settings':
        return (
          <SettingsView 
            settings={settings}
            onSaveSettings={handleSaveSettings}
            onResetPortfolio={handleResetPortfolio}
          />
        );
      default:
        return <DashboardView data={portfolioData} marketData={marketData} trades={tradeHistory} onSellPosition={handleSellPosition} />;
    }
  };

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <nav className="sidebar">
        <div className="brand">
          <div className="brand-icon">
            <Cpu size={20} />
          </div>
          <h2>CAN SLIM Bot</h2>
        </div>
        
        <ul className="nav-menu">
          <li>
            <div 
              className={`nav-item ${currentView === 'dashboard' ? 'active' : ''}`}
              onClick={() => setCurrentView('dashboard')}
            >
              <LayoutDashboard />
              <span>Dashboard</span>
            </div>
          </li>
          <li>
            <div 
              className={`nav-item ${currentView === 'screener' ? 'active' : ''}`}
              onClick={() => setCurrentView('screener')}
            >
              <Search />
              <span>Screener</span>
            </div>
          </li>
          <li>
            <div 
              className={`nav-item ${currentView === 'backtester' ? 'active' : ''}`}
              onClick={() => setCurrentView('backtester')}
            >
              <BarChart2 />
              <span>Backtester</span>
            </div>
          </li>
          <li>
            <div 
              className={`nav-item ${currentView === 'settings' ? 'active' : ''}`}
              onClick={() => setCurrentView('settings')}
            >
              <SettingsIcon />
              <span>Settings</span>
            </div>
          </li>
        </ul>

        <div className="sidebar-footer">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
            <TrendingUp size={14} color="var(--accent-secondary)" />
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>O'Neil Growth Engine</span>
          </div>
          <span>v1.0.0 (Simulated Mode)</span>
        </div>
      </nav>

      {/* Main Panel View Area */}
      <main className="main-content">
        <header style={{ marginBottom: '1.5rem' }}>
          <h1 style={{ fontSize: '2.2rem', fontFamily: 'var(--font-display)', textTransform: 'capitalize', letterSpacing: '-0.03em' }}>
            {currentView}
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
            {currentView === 'dashboard' && "Simulated portfolio statistics, index direction, and live position tickers."}
            {currentView === 'screener' && "Live stock ranking, multi-factor scorecard checks, and technical details."}
            {currentView === 'backtester' && "Simulate technical breakout entries and automated exits on historical ranges."}
            {currentView === 'settings' && "Manage trading budgets, risk constraints, and active ticker watchlists."}
          </p>
        </header>

        {renderView()}
      </main>
    </div>
  );
}
