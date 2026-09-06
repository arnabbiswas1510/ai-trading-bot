import React, { useState, useEffect } from 'react';
import { 
  LayoutDashboard, 
  Search, 
  BarChart2, 
  Settings as SettingsIcon,
  TrendingUp,
  Cpu,
  History,
  Activity,
  LineChart,
  AlertTriangle
} from 'lucide-react';

import DashboardView from './components/DashboardView';
import ScreenerView from './components/ScreenerView';
import BacktesterView from './components/BacktesterView';
import SettingsView from './components/SettingsView';
import TradesView from './components/TradesView';
import BreakoutsView from './components/BreakoutsView';
import ReturnsView from './components/ReturnsView';

/**
 * Turn a failed API response into a message that says what is actually wrong.
 *
 * 503 is reserved by the backend for "the database could not be reached", which
 * is the case that must never be rendered as an empty portfolio.
 */
async function describeApiFailure(res, what) {
  let detail = '';
  try {
    const body = await res.json();
    detail = body?.detail || '';
  } catch {
    /* non-JSON error body — the status alone still tells us enough */
  }
  if (res.status === 503) {
    return `The backend could not reach its database, so ${what} could not be loaded. `
      + `Your positions and trades are NOT lost — they simply could not be read. `
      + `Do not treat any figure on this screen as your real account state.`
      + (detail ? ` (${detail})` : '');
  }
  return `Failed to load ${what} (HTTP ${res.status}).${detail ? ` ${detail}` : ''}`;
}

export default function App() {
  const [currentView, setCurrentView] = useState('dashboard');
  const [marketData, setMarketData] = useState(null);
  const [screenerResults, setScreenerResults] = useState([]);
  const [portfolioData, setPortfolioData] = useState(null);
  const [tradeHistory, setTradeHistory] = useState([]);
  const [breakouts, setBreakouts] = useState([]);

  const [settings, setSettings] = useState(null);
  
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [dataLoading, setDataLoading] = useState(true);
  // Non-null when the backend could not reach its database. Kept separate from
  // `dataLoading` because the failure mode this guards against is not a slow
  // load — it is the dashboard cheerfully rendering $100,000 / 0 positions when
  // Supabase is unreachable, which is indistinguishable from a liquidated
  // account. See decisions/2026-09-06_fail-loudly-on-unreachable-database.md.
  const [dataError, setDataError] = useState(null);

  const fetchAllData = async () => {
    try {
      setDataError(null);
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
      } else {
        throw new Error(await describeApiFailure(portfolioRes, 'portfolio'));
      }

      // Fetch completed trades
      const tradesRes = await fetch('/api/trades');
      if (tradesRes.ok) {
        const tData = await tradesRes.json();
        setTradeHistory(tData);
      } else {
        throw new Error(await describeApiFailure(tradesRes, 'trade history'));
      }

      // Fetch breakouts
      const breakoutsRes = await fetch('/api/breakouts');
      if (breakoutsRes.ok) {
        const bData = await breakoutsRes.json();
        setBreakouts(bData);
      }


      // Fetch configurations
      const settingsRes = await fetch('/api/settings');
      if (settingsRes.ok) {
        const setts = await settingsRes.json();
        setSettings(setts);
      }
    } catch (e) {
      console.error("Failed to load initial REST API data: ", e);
      setDataError(e.message || String(e));
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

    if (dataError) {
      // Deliberately replaces the whole view rather than sitting above it. A
      // banner over a $100,000 / 0-positions dashboard is still a dashboard
      // showing $100,000; the numbers are the danger, so they must not render.
      return (
        <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', minHeight: '60vh', padding: '2rem' }}>
          <div style={{ maxWidth: '640px', border: '1px solid var(--danger, #ef4444)', borderRadius: '12px', padding: '2rem', background: 'rgba(239, 68, 68, 0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', color: 'var(--danger, #ef4444)' }}>
              <AlertTriangle size={28} />
              <h2 style={{ margin: 0, fontSize: '1.25rem' }}>Portfolio data unavailable</h2>
            </div>
            <p style={{ color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 1.25rem' }}>{dataError}</p>
            <button
              type="button"
              className="btn-primary"
              onClick={() => { setDataLoading(true); fetchAllData(); }}
            >
              Retry
            </button>
          </div>
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
          />
        );
      case 'screener':
        return (
          <ScreenerView 
            results={screenerResults} 
            onRunScan={handleRunScan} 
            loading={screenerLoading}
          />
        );
      case 'backtester':
        return <BacktesterView />;
      case 'history':
        return <TradesView trades={tradeHistory} />;
      case 'breakouts':
        return <BreakoutsView breakouts={breakouts} />;
      case 'performance':
        return <ReturnsView trades={tradeHistory} />;
      case 'settings':
        return (
          <SettingsView 
            settings={settings}
            onSaveSettings={handleSaveSettings}
            onResetPortfolio={handleResetPortfolio}
          />
        );
      default:
        return <DashboardView data={portfolioData} marketData={marketData} trades={tradeHistory} />;
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
            <button
              type="button"
              className={`nav-item ${currentView === 'dashboard' ? 'active' : ''}`}
              onClick={() => setCurrentView('dashboard')}
              aria-current={currentView === 'dashboard' ? 'page' : undefined}
            >
              <LayoutDashboard />
              <span>Dashboard</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'screener' ? 'active' : ''}`}
              onClick={() => setCurrentView('screener')}
              aria-current={currentView === 'screener' ? 'page' : undefined}
            >
              <Search />
              <span>Screener</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'breakouts' ? 'active' : ''}`}
              onClick={() => setCurrentView('breakouts')}
              aria-current={currentView === 'breakouts' ? 'page' : undefined}
            >
              <Activity />
              <span>Breakouts</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'history' ? 'active' : ''}`}
              onClick={() => setCurrentView('history')}
              aria-current={currentView === 'history' ? 'page' : undefined}
            >
              <History />
              <span>Trade History</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'performance' ? 'active' : ''}`}
              onClick={() => setCurrentView('performance')}
              aria-current={currentView === 'performance' ? 'page' : undefined}
            >
              <LineChart />
              <span>Performance</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'backtester' ? 'active' : ''}`}
              onClick={() => setCurrentView('backtester')}
              aria-current={currentView === 'backtester' ? 'page' : undefined}
            >
              <BarChart2 />
              <span>Backtester</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${currentView === 'settings' ? 'active' : ''}`}
              onClick={() => setCurrentView('settings')}
              aria-current={currentView === 'settings' ? 'page' : undefined}
            >
              <SettingsIcon />
              <span>Settings</span>
            </button>
          </li>
        </ul>

        <div className="sidebar-footer">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
            <TrendingUp size={14} color="var(--accent-secondary)" />
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>O'Neil Growth Engine</span>
          </div>
          <span>v1.0.0 (Execution Engine Mode)</span>
        </div>
      </nav>

      {/* Main Panel View Area */}
      <main className="main-content">
        <header style={{ marginBottom: '1.5rem' }}>
          <h1 style={{ fontSize: '2.2rem', fontFamily: 'var(--font-display)', textTransform: 'capitalize', letterSpacing: '-0.03em' }}>
            {currentView}
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
            {currentView === 'dashboard' && "Live portfolio monitoring — positions opened and closed automatically by the execution engine."}
            {currentView === 'screener' && "Live stock ranking, multi-factor scorecard checks, and technical details."}
            {currentView === 'breakouts' && "Technical breakout alerts and daily triggers monitored by the execution agent."}
            {currentView === 'backtester' && "Simulate technical breakout entries and automated exits on historical ranges."}
            {currentView === 'history' && "Comprehensive history of all completed buying and selling transactions."}
            {currentView === 'performance' && "Time-Weighted Returns, cash flows, and exact portfolio growth."}
            {currentView === 'settings' && "Manage trading budgets, risk constraints, and active ticker watchlists."}
          </p>
        </header>

        {renderView()}
      </main>
    </div>
  );
}
