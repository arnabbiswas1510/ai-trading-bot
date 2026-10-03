import React, { useEffect, useRef, useState } from 'react';
import { Activity, Download, FlaskConical, RefreshCw } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import CalibrationResearchView from './CalibrationResearchView.jsx';
import CalibrationRiskMetrics from './CalibrationRiskMetrics.jsx';
import IntradayReplayView, { request } from './IntradayReplayView.jsx';
import { finiteNumber } from '../lib/intradayReplay.js';
import { experimentFields, heartbeatLabel, metricText } from '../lib/calibrationResearch.js';
import {
  activityPageValid, activityRows, benchmarkView, calibrationTabs, campaignProgress,
  initialCampaign, metricDelta, recordedEquity, sortedTrials,
} from '../lib/calibrationDashboard.js';

const muted = { color: 'var(--text-muted)', fontSize: '0.85rem', lineHeight: 1.65 };
const flex = { display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' };
const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '1rem' };
const time = (value) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString() : 'Not recorded';
const label = (value) => typeof value === 'string' ? value.replaceAll('_', ' ') : 'Unknown';
const money = (value) => metricText(value, true);
const count = (value) => metricText(value);
const signed = (value) => finiteNumber(value) === null ? 'Unavailable'
  : `${value > 0 ? '+' : ''}${money(value)}`;
const percent = (value) => finiteNumber(value) === null ? 'Unavailable' : `${value.toFixed(2)}%`;
const captureValid = (value) => Array.isArray(value?.collectors) && Array.isArray(value.sessions);
const shadowValid = (value) => Array.isArray(value?.runs) && Array.isArray(value.health) && Array.isArray(value.reports);
const inboxValid = (value) => Array.isArray(value?.proposals) && value.settings?.value
  && Object.hasOwn(value.settings.value, 'risk_policy');
const detailValid = (value) => value?.proposal && Array.isArray(value.events);

function useResearch(url, refreshKey, valid) {
  const [state, setState] = useState({ url: null, data: null, error: '', loading: true, updated: null });
  useEffect(() => {
    let alive = true, pending = false;
    const controller = new AbortController();
    setState({ url, data: null, error: '', loading: Boolean(url), updated: null });
    async function load() {
      if (!url || pending) return;
      pending = true;
      try {
        const value = await request(url, { signal: controller.signal }, 20000);
        if (!valid(value)) throw new Error('Research response is incomplete; no result is inferred.');
        if (alive) setState({ url, data: value, error: '', loading: false, updated: new Date().toISOString() });
      } catch (error) {
        if (alive) setState((old) => ({ ...old, data: null, error: error.message, loading: false }));
      } finally { pending = false; }
    }
    load();
    const timer = setInterval(load, 30000);
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, [url, refreshKey, valid]);
  return state.url === url ? state : { data: null, error: '', loading: Boolean(url), updated: null };
}

function ResourceState({ resource, name }) {
  if (resource.error) return <p role="alert" style={{ ...muted, color: 'var(--color-warn)' }}>
    <strong>{name} unavailable:</strong> {resource.error} Missing evidence is not a zero result.
  </p>;
  if (resource.loading) return <p role="status" style={muted}>Loading {name.toLowerCase()}…</p>;
  return <p style={muted}>{name} fetched: {time(resource.updated)}. All times are shown in your local timezone.</p>;
}

function Stat({ title, value, detail }) {
  return <div style={{ padding: '1rem', border: '1px solid var(--border-color)', borderRadius: 10 }}>
    <div style={muted}>{title}</div><strong style={{ fontSize: '1.3rem' }}>{value}</strong>
    {detail && <p style={{ ...muted, marginBottom: 0 }}>{detail}</p>}
  </div>;
}

function downloadEvidence(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Campaign({ proposal, onReview }) {
  const progress = campaignProgress(proposal);
  const artifact = proposal?.artifact;
  if (!artifact?.frozen) return <p style={muted}>No frozen campaign selected. A request is not yet a measured experiment.</p>;
  return <section aria-label="Frozen campaign progress">
    <h4>{proposal.title} — {label(proposal.status)}</h4>
    <p style={muted}>Training selects a candidate from earlier sessions. Evaluation tests that unchanged candidate on later observations.</p>
    <ol style={{ ...muted, paddingLeft: '1.5rem' }}>
      <li>Training: {artifact.training_start} through {artifact.training_end}.</li>
      <li>Candidate frozen: {time(artifact.frozen_at)}.</li>
      <li>Reserved evaluation: {artifact.evaluation_start} through {artifact.evaluation_end}.</li>
      <li>Human review: {proposal.status === 'approved' ? 'Artifact approved; deployment is NOT confirmed by this record.'
        : 'Separate approval is required; interim gains cannot end the experiment early.'}</li>
    </ol>
    {progress && <div style={flex}>
      {progress.completed !== null && <progress aria-label="Evaluated reserved sessions" value={progress.completed} max={progress.planned} />}
      <span>{count(progress.completed)} / {progress.planned} reserved sessions evaluated.
        {' '}{progress.final ? 'Fixed evaluation complete.' : 'Not final.'}</span>
    </div>}
    <p style={muted}>Last evaluated session: {artifact.last_evaluated_session ?? 'None recorded'}.
      A missing session or a source restart can block the campaign; elapsed time is not evidence.</p>
    <button type="button" className="btn btn-secondary" onClick={onReview}>Review this proposal and leave feedback</button>
  </section>;
}

function Overview({ capture, shadow, inbox, detail, onReview }) {
  const collectors = capture.data?.collectors;
  const portfolio = shadow.data?.portfolio;
  const health = inbox.data?.health;
  return <>
    <section className="card">
      <h3>Is the calibration process actually progressing?</h3>
      <p style={muted}>A heartbeat proves a process is responding, not that usable decisions or simulated trades have been recorded.
        The stages below are independent; one successful service does not certify the others.</p>
      <div style={grid}>
        <Stat title="Captured dates — not certified training sessions"
          value={capture.data ? capture.data.sessions.length : 'Unavailable'} detail="Startup/error records do not count as completed decisions." />
        <Stat title="Simulated fully closed positions" value={count(portfolio?.closed_positions)}
          detail={portfolio ? `Latest reference run only; snapshot ${time(portfolio.last_timestamp)}.` : 'No hypothetical checkpoint available.'} />
        <Stat title="Research worker" value={label(health?.status)} detail={heartbeatLabel(health)} />
        <Stat title="Recent proposals awaiting your decision" value={inbox.data
          ? inbox.data.proposals.filter((item) => ['ready', 'investigation_requested'].includes(item.status)).length : 'Unavailable'}
          detail="Artifact reviews and new investigation requests; select a proposal above to open its review." />
        <Stat title="Deployment-review risk policy" value={!inbox.data ? 'Unavailable'
          : inbox.data.settings.value.risk_policy == null ? 'Unset — exploratory only' : 'Explicit policy configured'}
          detail="Your policy must be set before a new evaluation campaign; setting it later cannot qualify old results." />
      </div>
      <ResourceState resource={capture} name="Collection health" />
      {collectors && <div className="table-container"><table>
        <thead><tr><th>Collector</th><th>Heartbeat</th><th>Last persisted output</th><th>Pending / dropped records</th><th>Last error</th></tr></thead>
        <tbody>{collectors.map((item) => <tr key={item.id}><th scope="row">{item.id}</th>
          <td>{item.heartbeat_stale === false ? 'Recent' : 'Stale / unavailable'}<br />{time(item.last_seen_at)}</td>
          <td>{time(item.latest_capture_at)}</td><td>{count(item.pending_events)} / {count(item.dropped_events)}</td>
          <td>{item.latest_error || 'None reported'}</td></tr>)}</tbody>
      </table>{!collectors.length && <p style={muted}>No collector health records.</p>}</div>}
      <ResourceState resource={shadow} name="Simulation health" />
      {shadow.data?.health.map((item) => <p key={item.id} style={muted}>
        <strong>{item.id}: {label(item.status)}</strong> · {item.heartbeat_stale === false ? 'Heartbeat recent' : 'Stale / unavailable heartbeat'}
        {' · '}Last decision cycle: {time(item.last_cycle_at)} · Last persisted: {time(item.last_persisted_at)}
        {item.last_error && <><br />Blocker: {item.last_error}</>}
      </p>)}
      {shadow.data?.health.length === 0 && <p style={muted}>No shadow-worker heartbeat recorded.</p>}
      <ResourceState resource={inbox} name="Research status" />
      {health?.last_error && <p role="alert">Research blocker: {health.last_error}</p>}
      {health?.metrics?.reason && <p style={muted}>{health.metrics.reason}</p>}
      {inbox.data?.settings.value.enabled === false && <p role="status">Automatic research is disabled in the research settings.</p>}
    </section>
    <section className="card"><h3>Selected campaign</h3>
      <ResourceState resource={detail} name="Campaign evidence" />
      <Campaign proposal={detail.data?.proposal} onReview={onReview} />
    </section>
    <section className="card"><h3>Latest saved research reports</h3>
      <ResourceState resource={shadow} name="Saved reports" />
      <p style={muted}>Daily and weekly windows can overlap. Do not add their profit figures together.</p>
      {shadow.data?.reports.length === 0 && <p style={muted}>No saved reports. This is not evidence of zero activity.</p>}
      {shadow.data?.reports.map((report) => <details key={report.id} style={{ marginBottom: '0.75rem' }}>
        <summary>{report.title ?? report.id} · {label(report.status)} · {time(report.created_at)}</summary>
        <pre style={{ ...muted, maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {typeof report.body === 'string' ? report.body : JSON.stringify(report, null, 2)}
        </pre>
      </details>)}
    </section>
  </>;
}

function Benchmarks({ resource, onReview }) {
  const [phase, setPhase] = useState('evaluation');
  const [sort, setSort] = useState('equity_delta_vs_recorded_config_baseline');
  const proposal = resource.data?.proposal;
  const view = benchmarkView(proposal, phase);
  const eligibility = proposal?.artifact?.evaluation?.eligibility;
  const policy = proposal?.artifact?.frozen?.settings?.risk_policy;
  const riskRows = [
    ['baseline_completed_positions', 'Reference fully closed positions', 'min_completed_positions', 'At least', count],
    ['candidate_completed_positions', 'Candidate fully closed positions', 'min_completed_positions', 'At least', count],
    ['distinct_sessions', 'Distinct evaluation dates', 'min_distinct_sessions', 'At least', count],
    ['net_improvement_usd', 'After-cost improvement', 'min_improvement_usd', 'At least', money],
    ['drawdown_increase_pp', 'Extra peak-to-trough decline (percentage points)', 'max_drawdown_increase_pp', 'At most', count],
    ['worst_loss_increase_usd', 'Increase in worst completed-position loss', 'max_worst_loss_increase_usd', 'At most', money],
    ['positive_tickers', 'Stocks contributing a positive improvement', 'min_positive_tickers', 'At least', count],
    ['largest_contributor_fraction', 'Largest stock share of positive improvements', 'max_largest_contributor_fraction', 'At most',
      (value) => finiteNumber(value) === null ? 'Unavailable' : percent(value * 100)],
  ];
  const metrics = [
    ['net_profit', 'After-cost equity change', money, money],
    ['final_equity_net', 'Final equity including open holdings', money, money],
    ['max_sampled_drawdown_pct', 'Largest sampled decline from peak', percent, (value) => `${count(value)} percentage points`],
    ['commission', 'Modeled commission', money, money],
    ['slippage_cost', 'Modeled slippage', money, money],
    ['n_completed_positions', 'Fully closed positions (not partial sales)', count, count],
    ['n_distinct_sessions', 'Distinct recorded sessions', count, count],
    ['open_position_count', 'Positions still open', count, count],
  ];
  const chart = view?.baseline && view.candidate
    && [view.baseline.net_profit, view.candidate.net_profit].every((value) => finiteNumber(value) !== null)
    ? [{ name: 'Recorded rules', profit: view.baseline.net_profit }, { name: 'Frozen candidate', profit: view.candidate.net_profit }] : [];
  return <section className="card" aria-label="Detailed calibration benchmarks">
    <h3>Recorded rules versus the frozen candidate</h3>
    <p style={muted}>The baseline is the recorded reference strategy, not your real-account return or a market index.
      Both sides use the same starting portfolio, period and modeled costs. Positive dollar differences favor the candidate;
      a positive drawdown difference means a worse decline from the portfolio peak.</p>
    <ResourceState resource={resource} name="Benchmark evidence" />
    <div style={flex}>{[['evaluation', 'Future evaluation'], ['training', 'Training search']].map(([value, title]) =>
      <button key={value} type="button" className={`btn ${phase === value ? 'btn-primary' : 'btn-secondary'}`}
        aria-pressed={phase === value} onClick={() => setPhase(value)}>{title}</button>)}</div>
    {!view && <p style={muted}>Choose a frozen campaign above. Unstarted investigations have no benchmark.</p>}
    {view && <>
      <p><strong>{phase === 'training' ? 'Selection data — NOT independent validation.'
        : proposal.artifact.evaluation_complete ? 'Final reserved evaluation results.' : 'Provisional or waiting — NOT a deployment recommendation.'}</strong></p>
      <p style={muted}>Candidate: {view.selected}. Observed period: {time(view.period?.observed_start)} through {time(view.period?.observed_end)}.</p>
      <p style={{ ...muted, overflowWrap: 'anywhere' }}>Source run: {proposal.artifact.run_id ?? 'Unavailable'}.
        {' '}Starting checkpoint: {time(view.period?.snapshot_at)}.
        {' '}Input SHA-256 (content fingerprint): {view.period?.input_sha256 ?? 'Unavailable'}.</p>
      {!view.baseline || !view.candidate ? <p role="status">A comparable modeled pair is not available for this period yet.</p> : <>
        <div className="table-container"><table>
          <thead><tr><th>Measure</th><th>Recorded rules</th><th>Frozen candidate</th><th>Candidate minus recorded rules</th></tr></thead>
          <tbody>{metrics.map(([key, title, format, deltaFormat]) => <tr key={key}><th scope="row">{title}</th>
            <td>{format(view.baseline[key])}</td><td>{format(view.candidate[key])}</td>
            <td>{metricDelta(view.baseline[key], view.candidate[key]) === null ? 'Unavailable'
              : deltaFormat(metricDelta(view.baseline[key], view.candidate[key]))}</td></tr>)}</tbody>
        </table></div>
        {chart.length > 0 && <div role="img" aria-label="After-cost equity change comparison; exact values are in the preceding table" style={{ height: 240, marginTop: 16 }}>
          <ResponsiveContainer><BarChart data={chart}><CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" /><YAxis /><Tooltip formatter={money} /><ReferenceLine y={0} />
            <Bar dataKey="profit" name="After-cost equity change (USD)" fill="var(--accent-primary)" />
          </BarChart></ResponsiveContainer>
        </div>}
      </>}
      <CalibrationRiskMetrics proposal={proposal} phase={phase} />
      <h4>Exact settings under comparison</h4>
      {Array.isArray(view.changes) ? <div className="table-container"><table>
        <thead><tr><th>Parameter</th><th>Recorded value</th><th>Candidate value</th></tr></thead>
        <tbody>{view.changes.map((change) => <tr key={change.field}><th scope="row">{change.field}</th>
          <td>{String(change.original)}</td><td>{String(change.effective)}
            <p style={muted}>{experimentFields.find(([field]) => field === change.field)?.[1]}</p></td></tr>)}</tbody>
      </table>{view.changes.length === 0 && <p style={muted}>No parameter change was selected.</p>}</div> : <p style={muted}>Settings differences unavailable.</p>}
      <h4>Is one stock driving the improvement?</h4>
      <p style={muted}>Ticker contributions include open-position marks and costs. Removing contributors is an arithmetic sensitivity check,
        not a simulation of replacement trades.</p>
      <div style={grid}>
        <Stat title="Candidate improvement" value={signed(view.candidate?.equity_delta_vs_recorded_config_baseline)} />
        <Stat title="Improvement excluding largest positive ticker" value={signed(view.candidate?.attribution_only_excluding_top1_delta)} />
        <Stat title="Improvement excluding top three positive tickers" value={signed(view.candidate?.attribution_only_excluding_top3_delta)} />
      </div>
      {view.contributions ? <div className="table-container"><table>
        <thead><tr><th>Ticker</th><th>Contribution to candidate improvement</th></tr></thead>
        <tbody>{view.contributions.map((item) => <tr key={item.ticker}><th scope="row">{item.ticker}</th><td>{signed(item.delta)}</td></tr>)}</tbody>
      </table></div> : <p style={muted}>Ticker attribution unavailable.</p>}
      {phase === 'evaluation' && <>
        <h4>Frozen risk-policy checks</h4>
        <p style={muted}>These are the limits fixed when this candidate was selected, not any settings edited since then.
          {policy == null && ' No policy was frozen: this campaign is exploratory and cannot qualify for deployment.'}</p>
        <div style={grid}>
          <Stat title="Reference worst completed-position loss" value={money(eligibility?.metrics?.baseline_worst_loss_usd)} />
          <Stat title="Candidate worst completed-position loss" value={money(eligibility?.metrics?.candidate_worst_loss_usd)} />
        </div>
        <div className="table-container"><table>
          <thead><tr><th>Requirement</th><th>Measured</th><th>Frozen limit</th></tr></thead>
          <tbody>{riskRows.map(([metric, title, field, direction, format]) => <tr key={metric}><th scope="row">{title}</th>
            <td>{format(eligibility?.metrics?.[metric])}</td><td>{policy ? `${direction} ${format(policy[field])}` : 'Not configured'}</td></tr>)}</tbody>
        </table></div>
        {eligibility?.reasons?.map((reason) => <p key={reason} style={muted}>Recorded blocker: {reason}</p>)}
        {eligibility?.warnings?.map((warning) => <p key={warning} style={muted}>Recorded warning: {warning}</p>)}
        <p style={muted}>The review inbox checks current artifact, policy and deployment eligibility again.
          Passing these numeric measurements alone is not approval.</p>
      </>}
      <h4>Training search — every recorded attempt</h4>
      <p style={muted}>Only the frozen winner receives future evaluation. Sorting this table does not change the selection or run another experiment.</p>
      <label style={flex}>Sort attempts <select className="form-control" value={sort} onChange={(event) => setSort(event.target.value)}>
        <option value="equity_delta_vs_recorded_config_baseline">Training improvement, highest first</option>
        <option value="max_sampled_drawdown_pct">Training drawdown, lowest first</option>
      </select></label>
      <div className="table-container"><table><thead><tr><th>Attempt</th><th>Status</th><th>Training improvement</th><th>Drawdown</th><th>Reason / selection</th></tr></thead>
        <tbody>{sortedTrials(view.trials, sort).map((trial) => <tr key={trial.name}><th scope="row">{trial.name}</th>
          <td>{label(trial.status)}</td><td>{signed(trial.status === 'modeled' ? trial.summary?.equity_delta_vs_recorded_config_baseline : null)}</td>
          <td>{percent(trial.status === 'modeled' ? trial.summary?.max_sampled_drawdown_pct : null)}</td>
          <td>{trial.reason ?? (trial.name === view.selected ? 'Frozen selection' : 'Not selected')}</td></tr>)}</tbody>
      </table>{!view.trials && <p style={muted}>Attempt records unavailable.</p>}</div>
      <details open><summary>Recorded limitations and sample warnings</summary>
        {[...new Set(view.warnings)].map((warning) => <p key={warning} style={muted}>{warning}</p>)}
        <p style={muted}>A higher modeled profit is not proof of future profitability. Read all risk checks before any approval.</p>
      </details>
      <div style={flex}><button type="button" className="btn btn-primary" onClick={onReview}>Open this proposal in the review inbox</button>
        <button type="button" className="btn btn-secondary" onClick={() => downloadEvidence(`calibration-evidence-${proposal.id}.json`, resource.data)}>
          <Download size={16} /> Download private recorded evidence</button></div>
    </>}
  </section>;
}

function SimulatedActivity({ shadow }) {
  const [runId, setRunId] = useState('');
  const [page, setPage] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [type, setType] = useState('fills');
  const [ticker, setTicker] = useState('');
  const controller = useRef(null);
  const sourceRuns = shadow.data?.runs;
  const selected = runId || sourceRuns?.[0]?.id || '';
  async function load(older = false) {
    if (!selected) return;
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setBusy(true); setError('');
    const through = older ? page.through_sequence : null;
    const params = new URLSearchParams({ limit: '50' });
    if (older) {
      params.set('through_sequence', through);
      params.set('before_sequence', page.next_before_sequence);
    }
    try {
      const value = await request(`/api/intraday/shadow/runs/${encodeURIComponent(selected)}/activity?${params}`, { signal: current.signal });
      if (!activityPageValid(value, selected, through)) throw new Error('Simulation activity response is incomplete or changed its source window.');
      if (!current.signal.aborted) setPage(value);
    } catch (err) {
      if (!current.signal.aborted) { setError(err.message); setPage(null); }
    } finally { if (!current.signal.aborted) setBusy(false); }
  }
  useEffect(() => {
    setPage(null); setError(''); load();
    return () => controller.current?.abort();
  }, [selected]);
  const rows = activityRows(page?.events, type, ticker);
  const curve = recordedEquity(page?.events);
  const portfolio = page?.portfolio;
  return <section className="card" aria-label="Recorded simulated activity">
    <h3><Activity size={18} /> Simulated trades and decisions</h3>
    <p style={muted}>This is the recorded reference shadow portfolio, not real orders and not the frozen challenger.
      Each page shows up to 50 stored cycles; a cycle can contain many fills or no fills.
      Older pages keep the same published boundary so new records cannot shift pagination.</p>
    <ResourceState resource={shadow} name="Available simulation runs" />
    <div style={flex}>
      <label>Source run <select className="form-control" value={selected} onChange={(event) => setRunId(event.target.value)}>
        {selected && !sourceRuns?.some((run) => run.id === selected) && <option value={selected}>Previously selected run: {selected}</option>}
        {!sourceRuns?.length && <option value="">No recorded runs</option>}
        {sourceRuns?.map((run) => <option key={run.id} value={run.id}>{time(run.created_at)} · {label(run.status)} · {run.id.slice(0, 12)}</option>)}
      </select></label>
      <button type="button" className="btn btn-secondary" disabled={!selected || busy} onClick={() => load()}>Refresh newest page</button>
      <button type="button" className="btn btn-secondary" disabled={!page?.next_before_sequence || busy} onClick={() => load(true)}>Older cycles</button>
    </div>
    {busy && <p role="status">Loading recorded simulation activity…</p>}
    {error && <p role="alert">Simulation activity unavailable: {error}. No empty portfolio is inferred.</p>}
    {page?.run_id === selected && <>
      <p style={muted}>Run status: {label(page.run_status)} · Fixed published boundary: {page.through_sequence}.
        {' '}Showing cycle sequences {page.events.at(-1)?.sequence ?? 'none'}–{page.events[0]?.sequence ?? 'none'}.
        {' '}{page.next_before_sequence === null ? 'No older stored cycles found.' : 'More history exists; this page is not a lifetime total.'}</p>
      <p style={{ ...muted, overflowWrap: 'anywhere' }}>Exact source run: {page.run_id}</p>
      {page.coverage_warning && <p role="alert">{page.coverage_warning}</p>}
      <div style={grid}><Stat title="Reference shadow equity" value={money(portfolio?.equity)} detail={`Checkpoint as of ${time(portfolio?.last_timestamp)}`} />
        <Stat title="Reference shadow cash" value={money(portfolio?.cash)} />
        <Stat title="Fully closed simulated positions" value={count(portfolio?.closed_positions)} detail="Checkpoint cumulative counter, not fills on this page." />
      </div>
      <h4>Open simulated positions at the published checkpoint</h4>
      {portfolio?.positions && typeof portfolio.positions === 'object' ? <div className="table-container"><table>
        <thead><tr><th>Ticker</th><th>Shares</th><th>Entry price</th><th>Entry date</th></tr></thead>
        <tbody>{Object.entries(portfolio.positions).map(([symbol, position]) => <tr key={symbol}><th scope="row">{position.ticker ?? symbol}</th>
          <td>{count(position.shares)}</td><td>{money(position.buy_price)}</td><td>{position.buy_date ?? 'Unavailable'}</td></tr>)}</tbody>
      </table>{Object.keys(portfolio.positions).length === 0 && <p style={muted}>This recorded checkpoint explicitly contains no open positions.</p>}</div>
        : <p style={muted}>No verified holdings snapshot available.</p>}
      <h4>Recorded equity marks on this page only</h4>
      <p style={muted}>Not a full-history return curve. No candidate curve is invented from summary metrics; gaps remain gaps.</p>
      {curve.some((point) => point.equity !== null) ? <div style={{ height: 220 }} role="img" aria-label="Recorded reference shadow equity marks on the displayed page">
        <ResponsiveContainer><LineChart data={curve}><CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="timestamp" tickFormatter={(value) => time(value)} hide /><YAxis domain={['auto', 'auto']} />
          <Tooltip labelFormatter={time} formatter={money} /><Line dataKey="equity" name="Simulated equity (USD)" stroke="var(--accent-primary)" dot={false} connectNulls={false} />
        </LineChart></ResponsiveContainer>
      </div> : <p style={muted}>No recorded equity marks on this page.</p>}
      <div style={flex}>
        <label>Activity <select className="form-control" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="fills">Simulated fills</option><option value="decisions">Buy, hold, skip and exit decisions</option>
        </select></label>
        <label>Filter ticker <input className="form-control" value={ticker} onChange={(event) => setTicker(event.target.value)} placeholder="All tickers" /></label>
      </div>
      <div className="table-container"><table><thead><tr><th>Time / cycle</th><th>Ticker</th><th>Action</th><th>Shares</th><th>Fill price</th><th>Commission</th><th>Recorded reason</th></tr></thead>
        <tbody>{rows.map((item) => <tr key={item.key}><td>{time(item.timestamp)}<br />#{item.sequence}</td><th scope="row">{item.ticker || '—'}</th>
          <td>{item.side ?? item.action ?? 'Unavailable'}</td><td>{count(item.shares)}</td>
          <td>{type === 'fills' ? money(item.price) : 'Not a fill'}</td><td>{type === 'fills' ? money(item.commission) : 'Not a fill'}</td>
          <td>{item.reason ?? 'Reason not recorded'}</td></tr>)}</tbody>
      </table>{rows.length === 0 && <p style={muted}>No matching {type} on this page. Other pages may contain activity.</p>}</div>
      <details><summary>Exact recorded page (includes equity marks)</summary><pre style={{ ...muted, maxHeight: 280, overflow: 'auto' }}>{JSON.stringify(page, null, 2)}</pre></details>
      <button type="button" className="btn btn-secondary" onClick={() => downloadEvidence(`shadow-activity-${selected}-${page.events[0]?.sequence ?? 0}.json`, page)}>
        <Download size={16} /> Download private activity page</button>
    </>}
  </section>;
}

export default function CalibrationDashboard() {
  const [tab, setTab] = useState('overview');
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [inboxOpened, setInboxOpened] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const [inboxBusy, setInboxBusy] = useState(false);
  const capture = useResearch('/api/intraday/status', refresh, captureValid);
  const shadow = useResearch('/api/intraday/shadow/status', refresh, shadowValid);
  const inbox = useResearch('/api/calibration', refresh, inboxValid);
  const effectiveId = selectedId || initialCampaign(inbox.data?.proposals);
  useEffect(() => {
    if (!selectedId && effectiveId) setSelectedId(effectiveId);
  }, [effectiveId, selectedId]);
  const detail = useResearch(effectiveId ? `/api/calibration/proposals/${encodeURIComponent(effectiveId)}` : null, refresh, detailValid);
  const review = () => { setInboxOpened(true); setFocusRequest((value) => value + 1); setTab('inbox'); };
  function chooseTab(next) { if (next === 'inbox') setInboxOpened(true); setTab(next); }
  return <div data-calibration-dashboard>
    <section className="card">
      <div style={{ ...flex, justifyContent: 'space-between' }}><h2><FlaskConical size={22} /> Calibration command center</h2>
        <button type="button" className="btn btn-secondary" onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={16} /> Refresh evidence</button></div>
      <p style={muted}>One place to follow collection, hypothetical trading, parameter research and your decisions.
        These are simulations, not real-account performance. Nothing on this dashboard enables live buys.
        Health and campaign summaries refresh every 30 seconds; activity pages stay fixed until you refresh them.</p>
      <label style={flex}>Campaign / investigation
        <select className="form-control" value={effectiveId} disabled={inboxBusy} onChange={(event) => setSelectedId(event.target.value)}>
          {effectiveId && !inbox.data?.proposals.some((item) => item.id === effectiveId)
            && <option value={effectiveId}>Previously selected proposal: {effectiveId}</option>}
          {!inbox.data?.proposals.length && <option value="">No recorded proposals</option>}
          {inbox.data?.proposals.map((item) => <option key={item.id} value={item.id}>{item.title} · {label(item.status)} · {time(item.created_at)}</option>)}
        </select>
      </label>
      <p style={muted}>Showing up to 50 recent proposals and 20 source runs. Campaign dollar results are separate experiments; do not sum them.</p>
      <div role="tablist" aria-label="Calibration visibility tabs" style={{ ...flex, marginTop: '1rem' }}>
        {calibrationTabs.map(([key, title]) => <button key={key} type="button" role="tab" id={`cal-tab-${key}`}
          aria-selected={tab === key} aria-controls={`cal-panel-${key}`}
          tabIndex={tab === key ? 0 : -1}
          className={`btn ${tab === key ? 'btn-primary' : 'btn-secondary'}`} onClick={() => chooseTab(key)}
          onKeyDown={(event) => {
            const current = calibrationTabs.findIndex(([value]) => value === key);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? calibrationTabs.length - 1
              : event.key === 'ArrowRight' ? (current + 1) % calibrationTabs.length
                : event.key === 'ArrowLeft' ? (current + calibrationTabs.length - 1) % calibrationTabs.length : null;
            if (next === null) return;
            event.preventDefault();
            const nextKey = calibrationTabs[next][0]; chooseTab(nextKey);
            document.getElementById(`cal-tab-${nextKey}`)?.focus();
          }}>{title}</button>)}
      </div>
    </section>
    {calibrationTabs.filter(([key]) => key !== 'inbox').map(([key]) => <div key={key} role="tabpanel"
      id={`cal-panel-${key}`} aria-labelledby={`cal-tab-${key}`} hidden={tab !== key}>
      {tab === key && key === 'overview' && <Overview capture={capture} shadow={shadow} inbox={inbox} detail={detail} onReview={review} />}
      {tab === key && key === 'benchmarks' && <Benchmarks resource={detail} onReview={review} />}
      {tab === key && key === 'activity' && <SimulatedActivity shadow={shadow} />}
      {tab === key && key === 'recorded' && <IntradayReplayView />}
    </div>)}
    <div role="tabpanel" id="cal-panel-inbox" aria-labelledby="cal-tab-inbox" hidden={tab !== 'inbox'}>
      {inboxOpened && <CalibrationResearchView focusProposalId={effectiveId} focusRequest={focusRequest}
        onSelectProposal={setSelectedId} onBusyChange={setInboxBusy} />}
    </div>
  </div>;
}
