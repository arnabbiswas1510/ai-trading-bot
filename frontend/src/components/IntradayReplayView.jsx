import React, { useEffect, useRef, useState } from 'react';
import { Activity, Download, FlaskConical, Play, RefreshCw, ShieldAlert } from 'lucide-react';
import {
  apiErrorText, captureCoverage, comparisonResult, equityDifference, finiteNumber, initialAccountView,
  maxReplayDays, pollReplay, portfolioMetrics, recordedSessions, replayValidation, resultSnapshotIssue, snapshotValidation,
} from '../lib/intradayReplay.js';

const muted = { color: 'var(--text-muted)', fontSize: '0.82rem', lineHeight: 1.6 };
const rowStyle = { display: 'flex', alignItems: 'center', gap: '0.65rem', flexWrap: 'wrap' };
const money = (value, signed = false) => finiteNumber(value) === null ? 'Unavailable'
  : new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', ...(signed ? { signDisplay: 'exceptZero' } : {}),
  }).format(value);
const count = (value) => finiteNumber(value) === null ? 'Unknown' : value.toLocaleString();
const timestamp = (value) => value && !Number.isNaN(Date.parse(value))
  ? new Date(value).toLocaleString() : 'Not recorded';

async function request(url, options = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 30000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch {
      throw new Error(`Research service returned an unreadable response (HTTP ${response.status}).`);
    }
    if (!response.ok) throw new Error(apiErrorText(data, `Research request failed (HTTP ${response.status}).`));
    return data;
  } catch (error) {
    if (error.name === 'AbortError' && !options.signal?.aborted) {
      throw new Error('Research request timed out. Refresh saved comparisons before retrying; the server may still be working.');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}

function delay(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, 3000);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

function InitialSnapshot({ snapshot }) {
  const issue = snapshotValidation(snapshot);
  if (issue) return <p role="alert" style={{ ...muted, color: 'var(--color-warn)' }}>{issue}</p>;
  return (
    <section aria-label="Actual recorded starting portfolio" style={{ margin: '1rem 0' }}>
      <h4>Actual recorded starting portfolio</h4>
      <p style={muted}>
        Snapshot: {timestamp(snapshot.observed_at)} (local time)
        {' · '}Starting equity: <strong>{money(snapshot.equity)}</strong>
        {' · '}Recorded cash: {money(snapshot.cash)}
        {' · '}{snapshot.positions.length} existing positions
      </p>
      {snapshot.positions.length > 0 ? (
        <div className="table-container"><table>
          <thead><tr><th>Ticker</th><th>Shares</th><th>Recorded entry price</th></tr></thead>
          <tbody>{snapshot.positions.map((position, index) => (
            <tr key={`${position.ticker}-${index}`}><td>{position.ticker ?? 'Unknown'}</td>
              <td>{count(position.shares)}</td><td>{money(position.buy_price)}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <p style={muted}>The complete account snapshot explicitly records no open positions; this is not a synthetic reset.</p>}
      <p style={muted}>
        {snapshot.prior_trades.length} prior trade records · {snapshot.cooldown_ledger.length} cooldown records
        {' · '}{snapshot.protective_orders.length} protective order records.
        Cooldown records retain previous sales that restrict re-entry; protective orders retain recorded broker protection.
        Counts do not certify that protection covers every position—the replay validates the snapshot.
      </p>
      <details>
        <summary>Starting positions, prior trades, cooldown state and protective orders</summary>
        <pre style={{ ...muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 320, overflow: 'auto' }}>
          {JSON.stringify(snapshot, null, 2)}
        </pre>
      </details>
    </section>
  );
}

function InitialStateSummary({ result }) {
  if (result.initial_snapshot) return <InitialSnapshot snapshot={result.initial_snapshot} />;
  const initial = initialAccountView(result);
  return (
    <section style={{ margin: '1rem 0' }} aria-label="Recorded initial account details">
      <h4>Actual recorded starting portfolio</h4>
      <p style={muted}>
        Snapshot: {timestamp(initial.timestamp)} (local time)
        <br />Recorded starting equity: <strong>{money(initial.equity)}</strong>
        {' · '}Recorded cash: {money(initial.cash)}
        {' · '}Existing positions: {count(initial.positionsCount)}
        {initial.account?.currency && <> · Account currency: {initial.account.currency}</>}
      </p>
      {typeof initial.summary === 'string' && <p style={muted}>{initial.summary}</p>}
      {initial.positions?.length > 0 && <div className="table-container"><table>
        <thead><tr><th>Starting ticker</th><th>Shares</th><th>Recorded entry price</th></tr></thead>
        <tbody>{initial.positions.map((position, index) => (
          <tr key={`${position.ticker}-${index}`}><td>{position.ticker ?? 'Unknown'}</td>
            <td>{count(position.shares)}</td><td>{money(position.buy_price)}</td></tr>
        ))}</tbody>
      </table></div>}
      {initial.positions?.length === 0 && <p style={muted}>The recorded account explicitly contained no open positions; the account was not reset for this replay.</p>}
      <details open>
        <summary>Recorded starting positions, prior trades and protection details</summary>
        <p style={muted}>Only supplied details are shown. Missing fields are unavailable, not empty account state.</p>
        <pre style={{ ...muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 320, overflow: 'auto' }}>
          {JSON.stringify({
            account: initial.account ?? 'Not supplied',
            starting_positions: initial.positions ?? 'Not supplied',
            initial_state_summary: initial.summary ?? 'Prior trade, cooldown and protective-order details not supplied in this result; export the validated dataset to inspect the complete starting state.',
          }, null, 2)}
        </pre>
      </details>
    </section>
  );
}

function Comparison({ result }) {
  const diff = equityDifference(result);
  const portfolios = [portfolioMetrics(result.baseline), portfolioMetrics(result.variant)];
  const metrics = [
    ['Actual starting equity', 'initialEquity', money],
    ['Final equity, net of modeled costs', 'equity', money],
    ['Net equity change since starting snapshot', 'profit', (value) => money(value, true)],
    ['Modeled commission', 'commission', money],
    ['Modeled slippage cost', 'slippage', money],
    ['Simulated fills', 'fills', count],
    ['Positions still open', 'openPositions', count],
    ['Fully closed positions (not partial sales)', 'closedPositions', count],
    ['Replayed sessions', 'sessions', count],
    ['Largest peak-to-trough equity decline', 'drawdown', (value) => finiteNumber(value) === null ? 'Unavailable' : `${value.toFixed(2)}%`],
  ];
  return (
    <section aria-label="Recorded comparison results" style={{ marginTop: '1.5rem' }}>
      <h4>Research comparison — not live account performance</h4>
      <InitialStateSummary result={result} />
      <p style={{ ...muted, margin: '0.5rem 0' }}>
        Baseline keeps the recorded D-grade AI veto; the variant removes only that veto.
        Ranking, other entry gates, sizing and exit rules stay unchanged.
      </p>
      <div className="table-container">
        <table>
          <thead><tr><th>Measure</th><th>Baseline: veto kept</th><th>Variant: veto removed</th></tr></thead>
          <tbody>{metrics.map(([label, key, format]) => (
            <tr key={key}><td>{label}</td>{portfolios.map((portfolio, index) => (
              <td key={index}>{format(portfolio[key])}</td>
            ))}</tr>
          ))}</tbody>
        </table>
      </div>
      <p style={{ marginTop: '1rem' }}>
        <strong>Net final equity difference (variant − baseline): {money(diff, true)}</strong>
      </p>
      <p style={muted}>
        Positive means removing the D-grade veto helped this recorded research portfolio; negative means it hurt.
        Final equity includes marked open positions, not just realized profit. Missing data is unavailable, never zero.
        A better comparison is not proof of future profitability. Research only—human approval is required for any live change.
      </p>
      {result.configuration_provenance && <p style={muted}>{result.configuration_provenance}</p>}
      {Array.isArray(result.caveats) && result.caveats.length > 0 && (
        <details style={{ marginTop: '0.75rem' }}>
          <summary>Replay assumptions and limitations</summary>
          <ul style={{ ...muted, paddingLeft: '1.3rem' }}>{result.caveats.map((item, index) => (
            <li key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
          ))}</ul>
        </details>
      )}
      <details style={{ marginTop: '0.75rem' }}>
        <summary>Recorded evidence, metrics and reproducibility</summary>
        <p style={muted}>Only supplied evidence is shown. Unreported metrics are not estimated.</p>
        <pre style={{ ...muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 360, overflow: 'auto' }}>
          {JSON.stringify({
            dataset_label: result.dataset_label ?? 'Not supplied',
            input_sha256: result.input_sha256 ?? 'Not supplied',
            evidence: result.evidence ?? 'Not supplied',
            metrics: result.metrics ?? 'Not supplied',
            effective_config: result.effective_config ?? 'Not supplied',
            initial_state_mode: result.initial_state_mode ?? 'Not supplied',
            comparison_sign: result.comparison_sign ?? 'Not supplied',
            recommendation: result.recommendation ?? 'Not supplied',
          }, null, 2)}
        </pre>
      </details>
    </section>
  );
}

export default function IntradayReplayView() {
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState('');
  const [refreshing, setRefreshing] = useState(true);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [run, setRun] = useState(null);
  const [runRange, setRunRange] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const statusController = useRef(null);
  const actionController = useRef(null);

  async function refresh() {
    statusController.current?.abort();
    const controller = new AbortController();
    statusController.current = controller;
    setRefreshing(true);
    try {
      const data = await request('/api/intraday/status', { signal: controller.signal });
      if (!Array.isArray(data?.sessions) || !Array.isArray(data?.runs)) throw new Error('Capture status is incomplete. No research result can be inferred.');
      if (controller.signal.aborted) return;
      setStatus(data);
      setStatusError('');
      const sessions = recordedSessions(data.sessions);
      const valid = (value) => sessions.some((row) => row.session === value);
      setStartDate((value) => valid(value) ? value : sessions[0]?.session ?? '');
      setEndDate((value) => valid(value) ? value : sessions.at(-1)?.session ?? '');
    } catch (err) {
      if (!controller.signal.aborted) setStatusError(err.message);
    } finally {
      if (!controller.signal.aborted) setRefreshing(false);
    }
  }

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => {
      clearInterval(timer);
      statusController.current?.abort();
      actionController.current?.abort();
    };
  }, []);

  const sessions = recordedSessions(status?.sessions);
  const coverage = captureCoverage(status?.sessions);
  const maxDays = maxReplayDays(status?.max_replay_days);
  const validation = replayValidation(sessions, startDate, endDate, maxDays);
  const result = comparisonResult(run);
  const snapshotIssue = result ? resultSnapshotIssue(result) : null;
  const selectedSnapshot = sessions.find((session) => session.session === startDate)?.initial_snapshot;
  const runs = Array.isArray(status?.runs) ? status.runs : [];

  async function loadRun(id = null) {
    if (!id && validation) { setError(validation); return; }
    actionController.current?.abort();
    const controller = new AbortController();
    actionController.current = controller;
    setBusy(true);
    setRun(null);
    const saved = id ? runs.find((item) => item.id === id) : null;
    setRunRange(id
      ? saved && { start: saved.start_date, end: saved.end_date }
      : { start: startDate, end: endDate });
    setError('');
    try {
      let data = await request(id ? `/api/intraday/runs/${encodeURIComponent(id)}` : '/api/intraday/replay', {
        signal: controller.signal,
        ...(id ? {} : {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start_date: startDate, end_date: endDate, compare_without_ai_veto: true }),
        }),
      });
      data = await pollReplay(data, {
        signal: controller.signal, wait: delay, onUpdate: setRun,
        fetchRun: (runId, signal) => request(`/api/intraday/runs/${encodeURIComponent(runId)}`, { signal }),
      });
      if (controller.signal.aborted) return;
      setRun(data);
      if (data.status === 'rejected' || data.status === 'failed') setError(apiErrorText(data.error, `Comparison ${data.status}: no financial result is available. Check capture inputs and retry or open the saved run.`));
      else if (!comparisonResult(data)) throw new Error('The saved run has no completed financial result. Missing results are not a zero return.');
      else if (resultSnapshotIssue(comparisonResult(data))) throw new Error(resultSnapshotIssue(comparisonResult(data)));
      refresh();
    } catch (err) {
      if (!controller.signal.aborted) { setError(err.message); refresh(); }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function exportCapture() {
    if (validation) return;
    const controller = new AbortController();
    actionController.current = controller;
    setBusy(true);
    setError('');
    try {
      const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
      const data = await request(`/api/intraday/export?${params}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `intraday-capture-${startDate}-${endDate}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="card" data-intraday-research>
      <div style={{ ...rowStyle, justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <h3 style={rowStyle}><FlaskConical size={20} color="var(--accent-primary)" />Recorded intraday research</h3>
        <button type="button" className="btn btn-secondary" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={14} />{refreshing ? 'Refreshing…' : 'Refresh captures'}
        </button>
      </div>
      <p style={muted}>
        Automatic collection and comparisons; human approval is required before any live rule changes.
        This view cannot apply live updates. Replays reproduce your actual existing portfolio at the recorded starting point,
        including prior trades, cooldown state and protective orders. Cash and holdings are not reset.
        After that point, the alternative paths are simulated using observed quote samples—not exact IBKR fills.
        Missing older snapshots or trade history are never invented.
      </p>

      {statusError && <p role="alert" style={{ color: 'var(--color-down)', marginTop: '1rem' }}>{statusError} {status && 'Previously loaded capture status may be stale.'}</p>}
      {!status && refreshing && <p role="status" style={muted}>Loading recorded capture status…</p>}
      {status && (
        <section aria-label="Capture health" style={{ margin: '1.25rem 0' }}>
          <h4 style={rowStyle}><Activity size={16} />Capture health</h4>
          <p style={muted}>
            Collection: <strong>{status.enabled === true ? 'Enabled' : status.enabled === false ? 'Disabled' : 'Unknown'}</strong>
            {' · '}Sample interval: {count(status.sample_seconds)} seconds
            {' · '}Retention: {count(status.retention_days)} days
            <br />Latest capture: {timestamp(status.latest_capture_at)} (your local time)
          </p>
          {status.latest_error && <p role="alert" style={{ color: 'var(--color-warn)' }}>
            Latest capture error: {apiErrorText(status.latest_error)}
            {status.latest_error_at && <> · {timestamp(status.latest_error_at)} (your local time)</>}
          </p>}
          {status.heartbeat_stale === true && <p role="alert" style={{ color: 'var(--color-warn)' }}>
            Capture heartbeat is stale: the recorder has not reported recently. Recent data may be missing even if no error was recorded.
          </p>}
          {finiteNumber(status.pending_events) > 0 && <p role="alert" style={{ color: 'var(--color-warn)' }}>
            {count(status.pending_events)} capture events are queued and not yet persisted. The newest recorded window may be incomplete.
          </p>}
          {finiteNumber(status.dropped_events) > 0 && <p role="alert" style={{ color: 'var(--color-warn)' }}>
            {count(status.dropped_events)} capture events were dropped. Review the gaps before interpreting comparisons; missing data is not a zero return.
          </p>}
          <p style={muted}>
            {coverage.sessions} recorded sessions spanning {coverage.calendarDays} calendar days
            {' · '}{count(coverage.completeFrames)} complete input frames
            {' · '}{count(coverage.missingQuotes)} missing quotes.
            A frame is one recorded set of inputs; complete frames do not guarantee an entire session is covered.
          </p>
          <div style={{ ...muted, marginTop: '0.75rem' }}>
            <strong>Data requirements — progress is collection, not proof of an edge</strong>
            <ul style={{ paddingLeft: '1.25rem' }}>
              <li>4–8 weeks: instrumentation checks — confirm capture completeness and replay consistency.</li>
              <li>3 months: exploratory comparisons, with enough independent sessions and trades.</li>
              <li>6–12 months: broader validation across different market conditions.</li>
            </ul>
            Current recorded span: {coverage.calendarDays} calendar days. Gaps and incomplete inputs still require review;
            reaching a date milestone does not establish profitability.
          </div>
          {sessions.length > 0 && (
            <details style={{ marginTop: '0.75rem' }}>
              <summary>Recorded session coverage ({sessions.length})</summary>
              <div className="table-container" style={{ maxHeight: 280, overflow: 'auto' }}>
                <table><thead><tr><th>Session (New York)</th><th>Frames</th><th>Complete</th><th>Missing quotes</th><th>First / last observation (local)</th></tr></thead>
                  <tbody>{sessions.map((session) => (
                    <tr key={session.session}><td>{session.session}</td><td>{count(session.frames)}</td>
                      <td>{count(session.complete_frames)}</td><td>{count(session.missing_quotes)}</td>
                      <td>{timestamp(session.first_at)} / {timestamp(session.last_at)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          )}
        </section>
      )}

      <form onSubmit={(event) => { event.preventDefault(); loadRun(); }} style={{ marginTop: '1.25rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '1rem' }}>
          {[['Start recorded session', 'ir-start', startDate, setStartDate], ['End recorded session', 'ir-end', endDate, setEndDate]].map(([label, id, value, setter]) => (
            <div className="form-group" key={id}>
              <label htmlFor={id}>{label}</label>
              <select id={id} className="form-control" value={value} onChange={(event) => setter(event.target.value)} disabled={busy || !sessions.length} required>
                {!sessions.length && <option value="">No recorded sessions</option>}
                {sessions.map((session) => <option key={session.session} value={session.session}>{session.session}</option>)}
              </select>
            </div>
          ))}
        </div>
        {validation && <p style={muted}>{validation}</p>}
        <p style={muted}>Only the D-grade AI-veto comparison is supported. Both paths inherit the same actual starting equity, positions, prior trades and protective orders. The server must validate a complete starting snapshot; missing state is rejected, never replaced by an empty portfolio.</p>
        <p style={muted}>Maximum {maxDays} calendar days per request. Evaluate the retained 12 months as multiple windows, not one continuous simulated portfolio. Each window starts from its own recorded account snapshot.</p>
        {selectedSnapshot ? <InitialSnapshot snapshot={selectedSnapshot} />
          : <p style={muted}>The complete starting account snapshot will be checked when the selected sessions are loaded. No financial results are shown without it.</p>}
        <div style={{ ...rowStyle, marginTop: '1rem' }}>
          <button type="submit" className="btn btn-primary" disabled={busy || Boolean(validation) || Boolean(statusError)}>
            {busy ? <div className="spinner" /> : <Play size={15} />}
            {busy ? 'Loading recorded research…' : 'Compare with / without D-grade veto'}
          </button>
          {status?.export_available === true && <button type="button" className="btn btn-secondary" onClick={exportCapture}
            disabled={busy || Boolean(validation) || Boolean(statusError)}><Download size={15} />Export recorded inputs</button>}
        </div>
      </form>
      {error && <p role="alert" style={{ ...rowStyle, color: 'var(--color-down)', marginTop: '1rem' }}><ShieldAlert size={17} />{error}</p>}
      {run?.id && <p style={{ ...muted, marginTop: '0.75rem' }}>Saved run: {String(run.id)} · {run.status ?? 'completed'}</p>}
      {busy && run?.status === 'running' && (
        <div style={rowStyle}>
          <p role="status" style={muted}>Checking the saved run every 3 seconds, for up to one hour.</p>
          <button type="button" className="btn btn-secondary" onClick={() => {
            actionController.current?.abort();
            setBusy(false);
            setError('Stopped waiting in this view only. The server comparison may continue; open the saved run to resume checking.');
            refresh();
          }}>Stop waiting</button>
        </div>
      )}
      {run && runRange && <p style={muted}>Displayed comparison sessions: {runRange.start} – {runRange.end} (New York dates).</p>}
      {result && snapshotIssue && !error && <p role="alert" style={{ color: 'var(--color-down)' }}>{snapshotIssue}</p>}
      {result && !snapshotIssue && <Comparison result={result} />}

      <section aria-label="Saved automatic comparisons" style={{ marginTop: '1.75rem' }}>
        <h4>Automatic and saved comparisons</h4>
        <p style={muted}>Running, completed, rejected and failed runs are listed for review. Status refreshes every 30 seconds; nothing here changes live trading.</p>
        {!runs.length ? <p style={muted}>{status ? 'No saved comparisons yet. Missing history is not a zero return.' : 'Saved comparison history is unavailable until status loads.'}</p> : (
          <div className="table-container">
            <table><thead><tr><th>Created (local)</th><th>Sessions</th><th>Status / error</th><th>Net equity difference</th><th>Review</th></tr></thead>
              <tbody>{runs.map((saved) => (
                <tr key={saved.id}>
                  <td>{timestamp(saved.created_at)}</td><td>{saved.start_date} – {saved.end_date}</td>
                  <td>{saved.status}{saved.error && <div style={muted}>{apiErrorText(saved.error)}</div>}</td>
                  <td>{saved.status === 'completed' && !resultSnapshotIssue(saved.summary)
                    ? money(equityDifference(saved.summary), true) : 'Unavailable'}</td>
                  <td><button type="button" className="btn btn-secondary" disabled={busy || !saved.id} onClick={() => loadRun(saved.id)}>View run</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
