import React, { useState, useEffect } from 'react';
import { Settings, Save, RefreshCw, Trash2 } from 'lucide-react';

export default function SettingsView({ settings, onSaveSettings, onResetPortfolio }) {
  const [watchlist, setWatchlist] = useState('');
  const [stopLoss, setStopLoss] = useState(7.0);
  const [profitTarget, setProfitTarget] = useState(25.0);
  const [initialBalance, setInitialBalance] = useState(100000.0);
  const [saving, setSaving] = useState(false);

  // Sync settings when loaded
  useEffect(() => {
    if (settings) {
      setWatchlist(settings.watchlist || '');
      setStopLoss(settings.stop_loss_pct ?? 7.0);
      setProfitTarget(settings.profit_target_pct ?? 25.0);
      setInitialBalance(settings.initial_balance ?? 100000.0);
    }
  }, [settings]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSaveSettings({
        watchlist,
        stop_loss_pct: parseFloat(stopLoss),
        profit_target_pct: parseFloat(profitTarget),
        initial_balance: parseFloat(initialBalance)
      });
      alert('Settings updated successfully');
    } catch (err) {
      alert(`Failed to save settings: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (confirm('Are you sure you want to reset your paper trading portfolio and clear all trade logs? This cannot be undone.')) {
      try {
        await onResetPortfolio();
        alert('Portfolio reset successfully');
      } catch (err) {
        alert(`Failed to reset: ${err.message}`);
      }
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* Settings Form Card */}
      <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.5rem' }}>
          <Settings size={20} color="var(--accent-primary)" />
          Bot & Strategy Configuration
        </h3>
        
        <form onSubmit={handleSubmit}>
          
          {/* Watchlist Input */}
          <div className="form-group" style={{ marginBottom: '1.5rem' }}>
            <label>Scanned Watchlist (Comma-separated Tickers)</label>
            <textarea 
              className="form-control" 
              style={{ minHeight: '100px', resize: 'vertical', fontFamily: 'var(--font-sans)', lineHeight: '1.5' }}
              value={watchlist}
              onChange={(e) => setWatchlist(e.target.value)}
              placeholder="e.g. AAPL, MSFT, NVDA, TSLA, AMZN"
              required
            />
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
              These symbols will be evaluated during the CAN SLIM scan and used for technical breakout triggers.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.5rem', marginBottom: '2rem' }}>
            {/* Stop Loss */}
            <div className="form-group">
              <label>Default Stop Loss (%)</label>
              <input 
                type="number" 
                step="0.1"
                className="form-control"
                value={stopLoss}
                onChange={(e) => setStopLoss(Math.max(0.1, parseFloat(e.target.value) || 0))}
                required
              />
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                O'Neil strongly recommends cutting losses at 7.0% - 8.0%.
              </p>
            </div>

            {/* Profit Target */}
            <div className="form-group">
              <label>Default Profit Target (%)</label>
              <input 
                type="number" 
                step="0.5"
                className="form-control"
                value={profitTarget}
                onChange={(e) => setProfitTarget(Math.max(1.0, parseFloat(e.target.value) || 0))}
                required
              />
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                Standard target is 20.0% - 25.0%.
              </p>
            </div>

            {/* Initial Capital */}
            <div className="form-group">
              <label>Initial Paper Trading Balance ($)</label>
              <input 
                type="number" 
                className="form-control"
                value={initialBalance}
                onChange={(e) => setInitialBalance(Math.max(100, parseFloat(e.target.value) || 0))}
                required
              />
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                Starting cash for simulation.
              </p>
            </div>
          </div>

          <button type="submit" className="btn btn-primary" style={{ width: '100%' }} disabled={saving}>
            {saving ? (
              <>
                <div className="spinner"></div>
                <span>Saving Config...</span>
              </>
            ) : (
              <>
                <Save size={16} />
                <span>Save Settings & Update Bot</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Danger Zone Card */}
      <div className="card" style={{ border: '1px solid rgba(244, 63, 94, 0.2)', background: 'rgba(244, 63, 94, 0.02)' }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', color: 'var(--color-down)' }}>
          <Trash2 size={20} />
          Danger Zone
        </h3>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '1.25rem' }}>
          Resetting the bot will instantly sell all active positions at their purchase prices, clear the simulated cash/equity ledger, and permanently delete the transaction history log.
        </p>
        <button className="btn btn-danger" onClick={handleReset}>
          <RefreshCw size={16} />
          Reset Portfolio & Trade Logs
        </button>
      </div>

    </div>
  );
}
