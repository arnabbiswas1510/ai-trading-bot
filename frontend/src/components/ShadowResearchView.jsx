import React, { useEffect, useRef, useState } from 'react';
import { Download, RefreshCw, ShieldAlert } from 'lucide-react';
import { request } from './IntradayReplayView.jsx';

const muted = { color: 'var(--text-muted)', fontSize: '0.82rem', lineHeight: 1.6 };
const dollars = (value) => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : 'Unavailable';

export default function ShadowResearchView() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [runId, setRunId] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [exporting, setExporting] = useState(false);
  const statusRequest = useRef(null);
  const exportRequest = useRef(null);

  async function refresh() {
    statusRequest.current?.abort();
    const controller = new AbortController();
    statusRequest.current = controller;
    try {
      const data = await request('/api/intraday/shadow/status', { signal: controller.signal });
      if (!Array.isArray(data.runs) || !Array.isArray(data.health) || !Array.isArray(data.reports)) {
        throw new Error('Shadow research status is incomplete; no portfolio result can be inferred.');
      }
      if (controller.signal.aborted) return;
      setStatus(data);
      setRunId((old) => data.runs.some((run) => run.id === old) ? old : data.runs[0]?.id ?? '');
      setError('');
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => {
      clearInterval(timer);
      statusRequest.current?.abort();
      exportRequest.current?.abort();
    };
  }, []);

  async function exportDataset(event) {
    event.preventDefault();
    exportRequest.current?.abort();
    const controller = new AbortController();
    exportRequest.current = controller;
    setExporting(true);
    setError('');
    try {
      const params = new URLSearchParams({ run_id: runId, start_date: start, end_date: end });
      const data = await request(`/api/intraday/shadow/export?${params}`, { signal: controller.signal }, 300000);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `shadow-decisions-${start}-${end}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message);
    } finally {
      if (!controller.signal.aborted) setExporting(false);
    }
  }

  return (
    <section className="card" aria-label="Decision-only portfolio and research reports">
      <h3>Decision-only portfolio — hypothetical, not live trading</h3>
      <p style={muted}>
        The shadow worker records would-be buys, holds and sells and keeps its own portfolio across restarts.
        Only simulated fills change this portfolio. It cannot place orders, change live settings, or approve restarting trading.
        Missing inputs and unsupported positions block the simulation rather than becoming invented trades.
      </p>
      <button type="button" className="btn btn-secondary" onClick={refresh}>
        <RefreshCw size={14} /> Refresh shadow status
      </button>
      {error && <p role="alert" style={{ color: 'var(--color-warn)' }}><ShieldAlert size={16} /> {error}</p>}
      {status && <>
        {status.health.length === 0 && <p role="alert" style={muted}>No shadow worker heartbeat has been recorded.</p>}
        {status.health.map((health) => <p key={health.id} role={health.heartbeat_stale || health.last_error ? 'alert' : undefined} style={muted}>
          <strong>{health.status}</strong>{health.heartbeat_stale ? ' — heartbeat is stale' : ''}
          {' · '}Last decision: {health.last_cycle_at ?? 'Not recorded'}
          {health.last_error && ` · ${health.last_error}`}
        </p>)}
        {status.portfolio ? <>
          <p style={muted}>
            <strong>Hypothetical equity: {dollars(status.portfolio.equity)}</strong>
            {' · '}Cash: {dollars(status.portfolio.cash)}
            {' · '}Commissions: {dollars(status.portfolio.commission)}
            {' · '}As of: {status.portfolio.last_timestamp ?? 'Unavailable'}
          </p>
          <details open>
            <summary>Hypothetical holdings — latest run only</summary>
            <pre style={{ ...muted, whiteSpace: 'pre-wrap', maxHeight: 250, overflow: 'auto' }}>
              {JSON.stringify(status.portfolio.positions, null, 2)}
            </pre>
          </details>
        </> : <p style={muted}>No hypothetical portfolio checkpoint is available; no balance is inferred.</p>}
        <details open>
          <summary>Shadow worker health and progress</summary>
          <pre style={{ ...muted, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>
            {JSON.stringify(status.health, null, 2)}
          </pre>
        </details>
        <details>
          <summary>Recorded hypothetical portfolio runs ({status.runs.length})</summary>
          <pre style={{ ...muted, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>
            {JSON.stringify(status.runs, null, 2)}
          </pre>
        </details>
        <h4>Daily summaries and weekly research reports</h4>
        <p style={muted}>
          Scheduled messages use the configured Telegram recipients, with persistent GitHub incidents for failures.
          These are automated reports, not daily interactive conversations with an assistant.
        </p>
        {status.reports.length === 0 && <p style={muted}>No scheduled reports have been saved yet. That does not establish healthy collection.</p>}
        {status.reports.map((report) => <details key={report.id}>
          <summary>{report.report_kind} · {report.period_start} to {report.period_end} — {report.status ?? 'Recorded'}</summary>
          {report.body && <p style={{ ...muted, whiteSpace: 'pre-wrap' }}>{report.body}</p>}
          <pre style={{ ...muted, whiteSpace: 'pre-wrap', maxHeight: 350, overflow: 'auto' }}>
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>)}
      </>}
      <form onSubmit={exportDataset} style={{ marginTop: '1rem' }}>
        <h4>Export labelled shadow decision inputs for calibration</h4>
        <p style={muted}>
          Select complete sessions from one run. The export must reproduce its hypothetical starting checkpoint
          from the original actual-account seed; gaps or missing predecessor evidence are rejected.
          Use an earlier export for selection and a separate later export for holdout evaluation.
        </p>
        <div className="form-group">
          <label htmlFor="shadow-run">Hypothetical portfolio run</label>
          <select id="shadow-run" className="form-control" value={runId} onChange={(e) => setRunId(e.target.value)} required>
            <option value="">Select a recorded run</option>
            {(status?.runs ?? []).map((run) => <option key={run.id} value={run.id}>{run.id} — {run.status}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="shadow-start">First session (New York date)</label>
          <input id="shadow-start" className="form-control" type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
        </div>
        <div className="form-group">
          <label htmlFor="shadow-end">Last session (New York date)</label>
          <input id="shadow-end" className="form-control" type="date" min={start || undefined} value={end} onChange={(e) => setEnd(e.target.value)} required />
        </div>
        <button type="submit" className="btn btn-secondary" disabled={exporting || !runId || !start || !end || end < start}>
          <Download size={14} /> {exporting ? 'Validating shadow inputs…' : 'Export shadow decision inputs'}
        </button>
      </form>
    </section>
  );
}
