import React, { useEffect, useRef, useState } from 'react';
import { describeTradingControl } from '../lib/tradingControl';

async function readResponse(response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Trading control failed (HTTP ${response.status}).`);
  return body;
}

export default function TradingControl() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const requestVersion = useRef(0);

  useEffect(() => {
    let mounted = true;
    let controller;
    const refresh = async () => {
      if (controller) controller.abort();
      controller = new AbortController();
      const current = ++requestVersion.current;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch('/api/trading-control', {
          cache: 'no-store', signal: controller.signal,
        });
        const body = await readResponse(response);
        if (mounted && current === requestVersion.current) {
          setStatus(body);
          setRefreshError('');
        }
      } catch (err) {
        if (mounted && current === requestVersion.current) {
          setStatus(null);
          setRefreshError(`Status could not be refreshed: ${err.message}`);
        }
      } finally {
        clearTimeout(timeout);
      }
    };
    refresh();
    const poll = setInterval(refresh, 10000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted = false;
      clearInterval(poll);
      clearInterval(clock);
      if (controller) controller.abort();
    };
  }, []);

  const update = async (enabled) => {
    if (enabled && !window.confirm(
      'Enable NEW REAL buys with real money? Normal strategy and safety gates still apply.'
    )) return;
    ++requestVersion.current;
    setBusy(true);
    setError('');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/trading-control', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ live_entries_enabled: enabled }),
        signal: controller.signal,
      });
      const body = await readResponse(response);
      ++requestVersion.current;
      setStatus(body);
      setRefreshError('');
    } catch (err) {
      ++requestVersion.current;
      setStatus(null);
      setError(`Change not confirmed: ${err.message} Refresh status before assuming it took effect.`);
    } finally {
      clearTimeout(timeout);
      setBusy(false);
    }
  };

  const display = describeTradingControl(status, now);
  const writable = status?.write_configured && token.length >= 32 && !busy;
  return (
    <section aria-label="Real trading control" style={{
      border: '1px solid var(--border-color, #555)', borderRadius: '12px',
      padding: '1rem', marginBottom: '1.5rem',
    }}>
      <strong role="status" style={{ color: display.confirmed ? 'var(--text-primary)' : 'var(--warning, #eab308)' }}>
        {display.label}
      </strong>
      <p>
        Saved setting: {status && !status.error
          ? (status.live_entries_enabled ? 'new real buys ON' : 'new real buys OFF')
          : 'unknown'}.
        {' '}Existing real holdings still require real protective orders and exits.
        Orders already submitted to IBKR are not cancelled by this switch.
      </p>
      <p style={{ color: 'var(--text-secondary)' }}>
        Hypothetical buys, exits and returns remain separate in Shadow Research.
        Agent confirmation reports connectivity and the setting it read, not a guarantee that every protective order is healthy.
        {status?.agent?.last_seen && ` Last agent report: ${new Date(status.agent.last_seen).toLocaleString()}.`}
      </p>
      {status && !status.write_configured && (
        <p>Switch locked: configure the server's private TRADING_CONTROL_TOKEN first.</p>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem' }}>
        <label>
          Operator token{' '}
          <input type="password" value={token} autoComplete="off" spellCheck={false}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Not stored in your browser" />
        </label>
        <button type="button" disabled={!writable || status?.error || status?.live_entries_enabled === true}
          onClick={() => update(true)}>Enable new real buys</button>
        <button type="button" disabled={!writable}
          onClick={() => update(false)}>Disable new real buys</button>
      </div>
      {(error || refreshError || status?.error) && <p role="alert" style={{ color: 'var(--danger, #ef4444)' }}>{error || refreshError || status.error}</p>}
    </section>
  );
}
