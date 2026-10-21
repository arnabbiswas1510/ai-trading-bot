import React, { useEffect, useRef, useState } from 'react';
import { request } from './IntradayReplayView.jsx';
import { dailyHealthIssue, healthLabels, healthScopes, healthTime, loadDailyHealth } from '../lib/calibrationHealth.js';

const muted = { color: 'var(--text-muted)', fontSize: '0.85rem', lineHeight: 1.65 };
const colors = {
  pass: 'var(--color-success, #15803d)',
  wait: 'var(--text-muted)',
  fail: 'var(--color-danger, #dc2626)',
  unknown: 'var(--color-warn, #b45309)',
};

export function DailyHealthReport({ report }) {
  const issue = dailyHealthIssue(report);
  if (issue) return <p role="alert">{issue}</p>;
  return <div aria-live="polite">
    <h4 style={{ color: colors[report.overall_status] }}>{healthLabels[report.overall_status]}</h4>
    <p>Trading session: <strong>{report.session_date}</strong>.
      {' '}Checked {healthTime(report.checked_at)} New York time.
      {!report.session_complete && ' This trading session has not finished.'}</p>
    <p style={muted}>This is a saved-on-screen snapshot, not continuous monitoring. Run it again for fresh evidence.
      Current worker health and preparation for the next session do not certify the selected day.</p>
    <div className="table-container"><table>
      <thead><tr><th>Stage / scope</th><th>Result</th><th>Evidence</th><th>Next step</th></tr></thead>
      <tbody>{report.checks.map((check) => <tr key={check.id}>
        <th scope="row">{check.title}<br /><span style={muted}>{healthScopes[check.scope]}</span></th>
        <td style={{ color: colors[check.status] }}>{healthLabels[check.status]}</td>
        <td>{check.summary}<br /><span style={muted}>Evidence time: {healthTime(check.evidence_at)} (New York)</span></td>
        <td>{check.next_step || 'No action requested by this stage.'}</td>
      </tr>)}</tbody>
    </table></div>
    {report.limitations.map((item, index) => <p key={index} style={muted}>{item}</p>)}
  </div>;
}

export default function DailyCalibrationHealth() {
  const [sessionDate, setSessionDate] = useState('');
  const [state, setState] = useState({ loading: false, report: null, error: '' });
  const active = useRef(null);
  useEffect(() => () => active.current?.abort(), []);

  async function runCheck() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setState({ loading: true, report: null, error: '' });
    try {
      const report = await loadDailyHealth(request, sessionDate, controller.signal);
      if (!controller.signal.aborted) setState({ loading: false, report, error: '' });
    } catch (error) {
      if (!controller.signal.aborted) setState({
        loading: false, report: null,
        error: `Daily health check unavailable: ${error.message} No successful result is confirmed.`,
      });
    } finally {
      if (active.current === controller) active.current = null;
    }
  }

  return <section className="card" aria-label="Daily calibration health check">
    <h3>Daily calibration health check</h3>
    <p style={muted}>Run after the market closes and the daily screener finishes.
      This checks operational evidence, not profitability, and never enables live buys or changes settings.
      Waiting for enough research data is different from a failed pipeline.</p>
    <div style={{ display: 'flex', alignItems: 'end', flexWrap: 'wrap', gap: '0.75rem' }}>
      <label>Session date (New York)
        <input type="date" className="form-control" value={sessionDate} disabled={state.loading}
          aria-describedby="daily-health-date-help" onChange={(event) => {
            setSessionDate(event.target.value);
            setState({ loading: false, report: null, error: '' });
          }} />
      </label>
      <button type="button" className="btn btn-primary" disabled={state.loading} onClick={runCheck}>
        {state.loading ? 'Checking daily evidence...' : 'Run daily health check'}
      </button>
    </div>
    <p id="daily-health-date-help" style={muted}>Leave the date blank for the latest completed NYSE session.
      Holidays, weekends and early closes follow the exchange calendar. All report times are New York time.</p>
    {state.loading && <p role="status">Reading each stage. A previous result is not reused while this check runs.</p>}
    {state.error && <p role="alert" style={{ color: colors.unknown }}>{state.error}</p>}
    {!state.loading && !state.error && !state.report
      && <p style={muted}>Not checked yet. A recent heartbeat alone does not mean the day succeeded.</p>}
    {state.report && <DailyHealthReport report={state.report} />}
  </section>;
}
