import React, { useEffect, useId, useRef, useState } from 'react';
import { Download, FlaskConical, RefreshCw } from 'lucide-react';
import { request } from './IntradayReplayView.jsx';
import {
  actionPayload, deploymentDownload, deploymentEligibility, experimentFields, experimentPayload, heartbeatLabel, metricText, riskFields, settingsPayload,
} from '../lib/calibrationResearch.js';

const muted = { color: 'var(--text-muted)', fontSize: '0.82rem', lineHeight: 1.6 };
const row = { display: 'flex', gap: '0.65rem', flexWrap: 'wrap', alignItems: 'center' };
const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '1rem' };
const text = (value) => value == null ? 'Unavailable'
  : typeof value === 'object' ? JSON.stringify(value) : String(value);
const label = (value) => String(value).replaceAll('_', ' ');
const date = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'Unavailable';

function EvidenceFields({ value }) {
  if (!value || typeof value !== 'object') return <p style={muted}>{text(value)}</p>;
  return <dl style={muted}>{Object.entries(value).map(([key, item]) => <div key={key} style={{ marginBottom: '0.5rem' }}>
    <dt><strong>{label(key)}</strong></dt>
    <dd style={{ marginLeft: '1rem', overflowWrap: 'anywhere' }}>
      {item != null && typeof item === 'object' ? <details><summary>Show recorded evidence</summary><EvidenceFields value={item} /></details> : text(item)}
    </dd>
  </div>)}</dl>;
}

function SettingsDiff({ value }) {
  if (Array.isArray(value) && value.length === 0) return <p style={muted}>This record explicitly contains no settings changes.</p>;
  if (!value || (!Array.isArray(value) && !Object.keys(value).length)) {
    return <p style={muted}>Exact old/new settings are unavailable for this record.</p>;
  }
  if (!Array.isArray(value)) return <EvidenceFields value={value} />;
  return <div className="table-container"><table><thead><tr><th>Parameter</th><th>Recorded old value</th><th>Proposed new value</th></tr></thead>
    <tbody>{value.map((change, index) => <tr key={index}><th scope="row">{change.field ?? 'Unavailable'}</th>
      <td>{text(change.original)}</td><td>{text(change.effective)}</td></tr>)}</tbody></table></div>;
}

function Metrics({ baseline, candidate }) {
  const fields = [
    ['net_profit', 'Simulated net profit', true],
    ['final_equity_net', 'Simulated final equity, net of costs', true],
    ['equity_delta_vs_recorded_config_baseline', 'Improvement over recorded configuration', true],
    ['max_sampled_drawdown_pct', 'Largest sampled decline from peak (%)'],
    ['n_completed_positions', 'Completed positions'],
    ['n_distinct_sessions', 'Distinct market sessions'],
  ];
  return <div className="table-container"><table>
    <thead><tr><th>Evaluation evidence</th><th>Recorded configuration</th><th>Frozen candidate</th></tr></thead>
    <tbody>{fields.map(([key, title, money]) => <tr key={key}>
      <th scope="row">{title}</th><td>{metricText(baseline?.[key], money)}</td><td>{metricText(candidate?.[key], money)}</td>
    </tr>)}</tbody>
  </table></div>;
}

function ProposalEvidence({ proposal }) {
  const artifact = proposal.artifact ?? {};
  const frozen = artifact.frozen ?? {};
  const selection = frozen.selection ?? {};
  const evaluation = artifact.evaluation?.evaluation ?? {};
  const trials = frozen.search_trials ?? selection.training_trials;
  const candidate = evaluation.frozen_candidate?.summary;
  const baseline = evaluation.baseline?.summary;
  return <>
    <h4>Simulation evidence — not live results</h4>
    <p style={muted}>Training selects a candidate. Evaluation tests that frozen candidate on separate later sessions.
      Missing evidence is unavailable, never a zero result. Small samples and profits concentrated in one ticker are not proof of a repeatable improvement.</p>
    <p role={artifact.evaluation_complete === true ? undefined : 'status'} style={muted}>
      <strong>{artifact.evaluation_complete === true ? 'Final predeclared evaluation window complete.'
        : 'Provisional or waiting — evaluation window is not complete; not approvable.'}</strong>
      {' '}Training: {artifact.training_start ?? 'Unavailable'} to {artifact.training_end ?? 'Unavailable'}.
      {' '}Predeclared evaluation: {artifact.evaluation_start ?? 'Unavailable'} to {artifact.evaluation_end ?? 'Unavailable'}.
      {' '}Last evaluated session: {artifact.last_evaluated_session ?? 'Unavailable'}.
    </p>
    <details open><summary>Recorded data periods and provenance</summary>
      <EvidenceFields value={Object.fromEntries(Object.entries(artifact).filter(([key]) => !['frozen', 'evaluation'].includes(key)))} />
      <EvidenceFields value={{ training_period: selection.training, evaluation_period: evaluation.holdout }} />
    </details>
    <p><strong>Frozen candidate:</strong> {selection.frozen_experiment?.name ?? 'Unavailable'}</p>
    <h4>Exact proposed settings — old and new values</h4>
    <SettingsDiff value={selection.settings_diff ?? selection.frozen_experiment?.settings_diff
      ?? selection.training_trials?.find((trial) => trial.name === selection.frozen_experiment?.name)?.settings_diff} />
    <Metrics baseline={baseline} candidate={candidate} />
    <details open><summary>Risk checks and failure reasons</summary>
      <p style={muted}>Eligibility: {artifact.evaluation?.eligibility?.eligible === true ? 'Passed recorded checks' : 'Not approved by recorded checks'}.
        A pass does not deploy or enable buys.</p>
      <EvidenceFields value={artifact.evaluation?.eligibility ?? null} />
    </details>
    <details><summary>Largest contributors and per-position attribution</summary>
      <p style={muted}>These records show whether the improvement depends on a few trades rather than being spread across tickers.</p>
      <EvidenceFields value={{ largest_delta_contributors: candidate?.largest_delta_contributors,
        position_attribution: candidate?.position_attribution }} />
    </details>
    <details open><summary>Warnings and hypotheses</summary>
      <EvidenceFields value={{ baseline_warnings: baseline?.warnings, candidate_warnings: candidate?.warnings,
        limitations: evaluation.limitations ?? selection.limitations,
        training_hypotheses: frozen.hypotheses, evaluation_hypotheses: artifact.evaluation?.hypotheses }} />
    </details>
    <details><summary>All attempted experiments, including rejected experiments ({Array.isArray(trials) ? trials.length : 'unavailable'})</summary>
      {!Array.isArray(trials) || trials.length === 0 ? <p style={muted}>No trial records available.</p>
        : trials.map((trial, index) => <details key={index}>
          <summary>{trial.name ?? trial.experiment?.name ?? `Trial ${index + 1}`} — {trial.status ?? trial.reason ?? 'Recorded'}</summary>
          <SettingsDiff value={trial.settings_diff} /><EvidenceFields value={trial} />
        </details>)}
    </details>
    <details><summary>Complete immutable artifact (audit copy)</summary>
      <pre style={{ ...muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 350, overflow: 'auto' }}>{JSON.stringify(artifact, null, 2)}</pre>
    </details>
  </>;
}

function FollowupExperiment({ authorized, busy, onRequest, linked = true }) {
  const inputId = useId();
  const [name, setName] = useState('');
  const [field, setField] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const definition = experimentFields.find(([key]) => key === field);
  const rule = definition?.[2] === 'boolean';
  return <details><summary>{linked ? 'Request a linked, structured follow-up experiment' : 'Request a new structured experiment'}</summary>
    <form onSubmit={(event) => {
      event.preventDefault(); setError('');
      try { onRequest(experimentPayload(name, field, value)); } catch (err) { setError(err.message); }
    }}>
      <p style={muted}>Choose one supported change and its exact value. Numeric requests can enter research automatically.
        Rule toggles create a permission request and cannot run until you approve that child experiment. Comments never become executable instructions.</p>
      <div className="form-group"><label htmlFor={`${inputId}-name`}>Experiment name</label>
        <input id={`${inputId}-name`} className="form-control" maxLength={80} required value={name} onChange={(event) => setName(event.target.value)} /></div>
      <div className="form-group"><label htmlFor={`${inputId}-field`}>Parameter or rule</label>
        <select id={`${inputId}-field`} className="form-control" required value={field} onChange={(event) => { setField(event.target.value); setValue(''); }}>
          <option value="">Choose a supported change</option>
          {experimentFields.map(([key, title]) => <option key={key} value={key}>{title}</option>)}
        </select></div>
      <div className="form-group"><label htmlFor={`${inputId}-value`}>Exact requested value</label>
        {rule ? <select id={`${inputId}-value`} className="form-control" required value={value} onChange={(event) => setValue(event.target.value)}>
          <option value="">Choose explicitly</option><option value="true">True</option><option value="false">False</option>
        </select> : <input id={`${inputId}-value`} className="form-control" type="number" step="any" min={definition?.[2]} max={definition?.[3]}
          required value={value} onChange={(event) => setValue(event.target.value)} />}</div>
      {rule && <p role="alert">This changes a rule, not just a number. Investigation approval is required before it can be simulated.</p>}
      {field === 'disable_ai_veto' && <p style={muted}>Removing the D-grade veto can be investigated, but the deployment artifact builder does not support deploying that change.</p>}
      {error && <p role="alert">{error}</p>}
      <button type="submit" className="btn btn-secondary" disabled={!authorized || busy || !definition}>
        {busy ? 'Saving…' : rule ? 'Request rule investigation — approval required' : 'Request numeric experiment'}</button>
    </form>
  </details>;
}

function ResearchSettings({ settings, authorized, busy, onSave }) {
  const [draft, setDraft] = useState(null);
  const [expectedRevision, setExpectedRevision] = useState(null);
  const [error, setError] = useState('');
  const value = draft ?? settings?.value;
  if (!value) return <p style={muted}>Research settings unavailable.</p>;
  function change(key, next) {
    setExpectedRevision((old) => old ?? settings.revision);
    setDraft((old) => ({ ...(old ?? settings.value), [key]: next }));
  }
  async function save(event) {
    event.preventDefault();
    setError('');
    try {
      const payload = settingsPayload(value, expectedRevision ?? settings.revision);
      if (await onSave(payload)) { setDraft(null); setExpectedRevision(null); }
    } catch (err) { setError(err.message); }
  }
  return <details><summary>Research settings and explicit risk policy (revision {settings.revision})</summary>
    <form onSubmit={save}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
      <p style={muted}>Only numeric parameter research is autonomous. Broader rule experiments require prior investigation approval.
        Saving these settings changes research only, never live risk rules or permission to buy.</p>
      <label style={row}><input type="checkbox" checked={value.enabled === true}
        onChange={(event) => change('enabled', event.target.checked)} />Research enabled</label>
      <div style={grid}>{[
        ['training_sessions', 'Training sessions (1–40)', 40],
        ['evaluation_sessions', 'Later evaluation sessions (1–40)', 40],
        ['max_candidates', 'Maximum attempted candidates (1–32)', 32],
      ].map(([key, title, max]) => <div className="form-group" key={key}>
        <label htmlFor={`cal-${key}`}>{title}</label>
        <input id={`cal-${key}`} className="form-control" type="number" min="1" max={max} step="1"
          value={value[key] ?? ''} onChange={(event) => change(key, event.target.value)} required />
      </div>)}</div>
      <p style={muted}>Training + evaluation cannot exceed 60 sessions.</p>
      <label style={row}><input type="checkbox" checked={value.risk_policy != null}
        onChange={(event) => change('risk_policy', event.target.checked ? {} : null)} />Set explicit deployment-review risk policy</label>
      <p style={muted}>{value.risk_policy == null
        ? 'UNSET — exploratory mode only. No deployment artifact approval is possible.'
        : 'Fill every threshold yourself; no sample-size or profit threshold is assumed. Zero maximum increases mean no worse drawdown and no worse worst-position loss. One percentage point means, for example, a decline rising from 5% to 6%.'}</p>
      {value.risk_policy != null && <div style={grid}>{riskFields.map(([key, title, min, max, integer]) => <div className="form-group" key={key}>
        <label htmlFor={`cal-risk-${key}`}>{title}</label>
        <input id={`cal-risk-${key}`} className="form-control" type="number" step={integer ? '1' : 'any'} min={min} max={max ?? undefined}
          value={value.risk_policy[key] ?? ''} required
          onChange={(event) => change('risk_policy', { ...value.risk_policy, [key]: event.target.value })} />
      </div>)}</div>}
      {draft && expectedRevision !== settings.revision && <p role="alert">Settings changed while you were editing. Your draft is preserved; discard it and review the latest values before saving.</p>}
      {error && <p role="alert">{error}</p>}
      <div style={row}><button className="btn btn-primary" disabled={!authorized || busy || (draft && expectedRevision !== settings.revision)}>
        {busy ? 'Saving…' : 'Save research settings and policy'}</button>
        {draft && <button type="button" className="btn btn-secondary" onClick={() => {
          if (window.confirm('Discard your unsaved research settings?')) { setDraft(null); setExpectedRevision(null); }
        }}>Discard draft and load current settings</button>}</div>
      </fieldset>
    </form>
  </details>;
}

export default function CalibrationResearchView({ focusProposalId = '', focusRequest = 0, onSelectProposal, onBusyChange } = {}) {
  const [inbox, setInbox] = useState(null);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [token, setToken] = useState('');
  const [notes, setNotes] = useState({});
  const [newRequestNote, setNewRequestNote] = useState('');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState('');
  const [updatedAt, setUpdatedAt] = useState(null);
  const [downloadDetails, setDownloadDetails] = useState(null);
  const inboxController = useRef(null);
  const detailController = useRef(null);
  const mutationController = useRef(null);
  const mounted = useRef(false);
  const currentId = useRef('');
  const proposal = detail?.proposal?.id === selectedId ? detail.proposal : null;
  const authorized = inbox?.write_configured === true && Boolean(token.trim());
  const eligibility = deploymentEligibility(proposal, inbox?.settings);

  useEffect(() => {
    if (focusProposalId) setSelectedId(focusProposalId);
  }, [focusProposalId, focusRequest]);
  useEffect(() => { onBusyChange?.(Boolean(busy)); }, [busy, onBusyChange]);

  async function refreshInbox() {
    inboxController.current?.abort();
    const controller = new AbortController();
    inboxController.current = controller;
    try {
      const data = await request('/api/calibration', { signal: controller.signal });
      if (!Array.isArray(data.proposals) || !data.settings) throw new Error('Research inbox response is incomplete.');
      if (!controller.signal.aborted && mounted.current) {
        setInbox(data); setUpdatedAt(new Date().toISOString()); setLoadError('');
      }
    } catch (err) { if (!controller.signal.aborted && mounted.current) setLoadError(err.message); }
  }
  async function refreshDetail(id) {
    detailController.current?.abort();
    if (!id) return;
    const controller = new AbortController();
    detailController.current = controller;
    try {
      const data = await request(`/api/calibration/proposals/${encodeURIComponent(id)}`, { signal: controller.signal });
      if (!data.proposal || !Array.isArray(data.events)) throw new Error('Proposal history is incomplete.');
      if (!controller.signal.aborted && mounted.current && currentId.current === id) setDetail(data);
    } catch (err) { if (!controller.signal.aborted && mounted.current) setLoadError(err.message); }
  }
  useEffect(() => {
    mounted.current = true;
    refreshInbox();
    const timer = setInterval(refreshInbox, 30000);
    return () => {
      mounted.current = false; clearInterval(timer);
      inboxController.current?.abort(); detailController.current?.abort(); mutationController.current?.abort();
    };
  }, []);
  useEffect(() => {
    currentId.current = selectedId;
    setDetail(null);
    refreshDetail(selectedId);
    const timer = setInterval(() => refreshDetail(selectedId), 30000);
    return () => { clearInterval(timer); detailController.current?.abort(); };
  }, [selectedId]);

  async function mutate(path, method, payload, busyLabel) {
    if (busy || !authorized) return false;
    const controller = new AbortController();
    mutationController.current = controller;
    setBusy(busyLabel); setError(''); setMessage('');
    try {
      const result = await request(path, {
        method, signal: controller.signal, headers: { Authorization: `Bearer ${token.trim()}`, 'Content-Type': 'application/json' },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      });
      if (controller.signal.aborted || !mounted.current) return false;
      if (method === 'GET') {
        const download = deploymentDownload(result, proposal);
        const url = URL.createObjectURL(new Blob([download.content], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url; link.download = download.filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setDownloadDetails({ proposalId: proposal.id, filename: download.filename,
          sha256: result.sha256, manifest: result.manifest, notes: result.notes });
        setMessage(`Downloaded ${download.filename}. Patch-text SHA-256: ${result.sha256}. Use the authenticated operator script from your own machine. No live setting changed.`);
      } else setMessage('Saved to the durable research history. No live deployment or buy permission changed.');
      await Promise.all([refreshInbox(), refreshDetail(currentId.current)]);
      return true;
    } catch (err) {
      if (!controller.signal.aborted && mounted.current) {
        setError(err.status === 409 ? 'This review is stale: the proposal, artifact or risk policy changed. Reloaded current evidence; review it again before retrying. Your note and token are preserved.' : err.message);
        if (err.status === 409) await Promise.all([refreshInbox(), refreshDetail(currentId.current)]);
      }
      return false;
    } finally { if (!controller.signal.aborted && mounted.current) setBusy(''); }
  }
  async function act(action, experiment) {
    try {
      const payload = actionPayload(proposal, action, notes[selectedId] ?? '', inbox?.settings, experiment);
      if (action === 'approve_deployment' && !window.confirm(
        `Approve exactly this artifact?\n${proposal.artifact_sha256}\nProposal revision ${proposal.revision}; risk policy revision ${inbox.settings.revision}.\nThis does not deploy or enable buys. You must separately apply and push the downloaded artifact using the operator script.`,
      )) return;
      if (action === 'approve_investigation' && !window.confirm('Approve this rule experiment for simulation only? It will not change live rules or enable buys.')) return;
      await mutate(`/api/calibration/proposals/${encodeURIComponent(selectedId)}/actions`, 'POST', payload, action);
    } catch (err) { setError(err.message); }
  }
  async function createRequest(experiment) {
    try {
      const payload = actionPayload({ revision: 0 }, 'request_experiment', newRequestNote, inbox?.settings, experiment);
      await mutate('/api/calibration/experiments', 'POST', payload, 'new_request');
    } catch (err) { setError(err.message); }
  }

  return <section className="card" aria-label="Calibration research inbox" data-calibration-inbox>
    <h3><FlaskConical size={20} /> Research inbox — simulated proposals</h3>
    <p style={muted}>Autonomous numeric research can propose changes, not deploy them. Broader rule experiments need your prior permission.
      Telegram notifications point to durable evidence and decisions here. Artifact approval is separate from investigation approval.
      No action here enables live buys.</p>
    <div style={row}><button type="button" className="btn btn-secondary" onClick={() => {
      refreshInbox(); refreshDetail(selectedId);
    }} disabled={Boolean(busy)}><RefreshCw size={14} /> Refresh research inbox</button>
      <span style={muted}>Polls every 30 seconds · Last refreshed: {date(updatedAt)}</span></div>
    {loadError && <p role="alert" style={{ color: 'var(--color-warn)' }}>Research service unavailable: {loadError}. Previously shown evidence may be stale.</p>}
    {error && <p role="alert" style={{ color: 'var(--color-warn)' }}>{error}</p>}
    {message && <p role="status" style={{ ...muted, overflowWrap: 'anywhere' }}>{message}</p>}
    <div className="form-group">
      <label htmlFor="cal-token">Operator token (memory only; retained between Calibration tabs, cleared when leaving this page)</label>
      <input id="cal-token" className="form-control" type="password" autoComplete="off" value={token}
        onChange={(event) => setToken(event.target.value)} />
      <button type="button" className="btn btn-secondary" onClick={() => setToken('')} disabled={!token}>Clear token</button>
    </div>
    {inbox?.write_configured === false && <p role="alert">Research writes are not configured on the server. Read-only review remains available.</p>}
    <h4>Start a research conversation</h4>
    <p style={muted}>Request an experiment now, even before any research campaign or completed data window exists.
      Requests are saved immediately; simulation waits for suitable data. These requests are independent; use a selected proposal below for linked follow-ups.</p>
    <div className="form-group">
      <label htmlFor="cal-new-request-note">New request context or proposed rule (saved as feedback, not executable text)</label>
      <textarea id="cal-new-request-note" className="form-control" rows={3} value={newRequestNote}
        onChange={(event) => setNewRequestNote(event.target.value)} />
    </div>
    <FollowupExperiment linked={false} authorized={authorized} busy={Boolean(busy)} onRequest={createRequest} />
    <p style={muted}>For a new rule outside the supported fields, describe it above and request an investigation.
      Prior approval is required, and an unsupported rule remains blocked for engineering; the description never runs as code.</p>
    <button type="button" className="btn btn-secondary"
      disabled={!authorized || Boolean(busy) || !newRequestNote.trim()} onClick={() => createRequest()}>
      {busy === 'new_request' ? 'Saving request…' : 'Request new independent rule investigation'}
    </button>
    {!inbox ? <p style={muted}>{loadError ? 'Research status could not be loaded.' : 'Loading research status…'}</p> : <>
      <p style={muted}><strong>{heartbeatLabel(inbox.health)}</strong> Last seen: {date(inbox.health?.last_seen_at)}.
        {inbox.health?.last_error && ` Last worker error: ${inbox.health.last_error}`}</p>
      <details><summary>Worker progress and blocking evidence</summary><EvidenceFields value={inbox.health?.metrics} /></details>
      <ResearchSettings settings={inbox.settings} authorized={authorized} busy={Boolean(busy)}
        onSave={(payload) => mutate('/api/calibration/settings', 'PUT', payload, 'settings')} />
      <h4>Saved proposals ({inbox.proposals.length})</h4>
      {!inbox.proposals.length && <p style={muted}>No research yet. This is not a successful evaluation.
        {inbox.health?.status === 'blocked' ? ' The worker is blocked; inspect its error and evidence above.'
          : inbox.settings.value.enabled ? ' Waiting for complete research sessions or a worker cycle.' : ' Research is paused.'}</p>}
      <div style={row}>{inbox.proposals.map((item) => <button type="button" key={item.id} className="btn btn-secondary"
        aria-pressed={selectedId === item.id} disabled={Boolean(busy)} onClick={() => {
          setSelectedId(item.id); onSelectProposal?.(item.id);
        }}>
        {item.title ?? item.id} · {label(item.status)} · {item.kind === 'rule' ? 'Rule experiment' : 'Parameters'}
      </button>)}</div>
    </>}
    {selectedId && !proposal && <p style={muted}>Loading selected proposal…</p>}
    {proposal && <article style={{ marginTop: '1.5rem' }}>
      <h4>{proposal.title} — {label(proposal.status)}</h4>
      <p style={muted}>Proposal {proposal.id} · revision {proposal.revision} · Created {date(proposal.created_at)}
        {' · '}Updated {date(proposal.updated_at)}{proposal.parent_id && ` · Follow-up to ${proposal.parent_id}`}</p>
      <p style={{ ...muted, overflowWrap: 'anywhere' }}>Exact artifact SHA-256 (content fingerprint): {proposal.artifact_sha256 ?? 'Unavailable'}</p>
      <ProposalEvidence proposal={proposal} />
      {proposal.kind === 'rule' && <p role="alert">Rule toggles require investigation approval before simulation. Investigation approval is not deployment approval.</p>}
      <details open><summary>Requested experiment</summary><EvidenceFields value={proposal.request} /></details>
      <FollowupExperiment key={proposal.id} authorized={authorized} busy={Boolean(busy)}
        onRequest={(experiment) => act('request_experiment', experiment)} />
      <div className="form-group">
        <label htmlFor="cal-note">Durable feedback / reason (a note is not executable code)</label>
        <textarea id="cal-note" className="form-control" rows={3} value={notes[selectedId] ?? ''}
          onChange={(event) => setNotes((old) => ({ ...old, [selectedId]: event.target.value }))} />
      </div>
      <div style={row}>{[
        ['comment', 'Save feedback'], ['reject', 'Reject'], ['defer', 'Defer'], ['resume', 'Resume research'],
        ['revoke', 'Revoke approval'],
      ].map(([action, title]) => <button type="button" className="btn btn-secondary" key={action}
        disabled={!authorized || Boolean(busy) || (action === 'comment' && !(notes[selectedId] ?? '').trim())
          || (action === 'resume' && proposal.status !== 'deferred')
          || (action === 'revoke' && proposal.status !== 'approved')
          || (['reject', 'defer'].includes(action) && ['approved', 'rejected'].includes(proposal.status))
          || (action === 'defer' && proposal.status === 'deferred')}
        onClick={() => act(action)}>{busy === action ? 'Saving…' : title}</button>)}
        <button type="button" className="btn btn-secondary" disabled={!authorized || Boolean(busy) || proposal.status !== 'investigation_requested'}
          onClick={() => act('approve_investigation')}>{busy === 'approve_investigation' ? 'Saving…' : 'Approve experiment only'}</button>
      </div>
      <p style={muted}>Have an idea outside the supported parameter list? Describe it in the feedback above, then request an investigation.
        This records a new-rule permission request only. Approving it marks engineering support as needed; the text is never executed or converted into a live rule.</p>
      <button type="button" className="btn btn-secondary"
        disabled={!authorized || Boolean(busy) || !(notes[selectedId] ?? '').trim()}
        onClick={() => act('request_experiment')}>
        {busy === 'request_experiment' ? 'Saving…' : 'Request new-rule investigation — engineering required'}
      </button>
      <h4>Exact deployment artifact approval</h4>
      <p style={muted}>Review all evidence and the exact fingerprint before approval. Download the complete JSON artifact, containing its patch and approval manifest.
        Use the operator script from an authorized clean checkout on your own machine; it checks current approval before applying.
        This dashboard never automatically deploys or enables buys.</p>
      {!eligibility.eligible && <ul style={muted}>{eligibility.reasons.map((reason, index) => <li key={index}>{text(reason)}</li>)}</ul>}
      <div style={row}>
        <button type="button" className="btn btn-primary"
          disabled={!authorized || Boolean(busy) || !eligibility.eligible || Boolean(loadError) || inbox?.deployment_mode !== 'operator_artifact'}
          onClick={() => act('approve_deployment')}>{busy === 'approve_deployment' ? 'Saving…' : 'Approve artifact — does not deploy or enable buys'}</button>
        <button type="button" className="btn btn-secondary"
          disabled={!authorized || Boolean(busy) || proposal.status !== 'approved'}
          onClick={() => mutate(`/api/calibration/proposals/${encodeURIComponent(selectedId)}/deployment`, 'GET', undefined, 'download')}>
          <Download size={14} />{busy === 'download' ? 'Preparing artifact…' : 'Download approved deployment artifact (JSON)'}</button>
      </div>
      {downloadDetails?.proposalId === proposal.id && <details open><summary>Downloaded artifact manifest and operator instructions</summary>
        <p style={muted}>On your authorized machine, replace the dashboard address below with your dashboard HTTPS address.
          The script authenticates with your existing operator token; never put that token in a URL.
          Plaintext LAN addresses are rejected; an HTTP localhost address through your SSH tunnel is also supported.
          Without <code>--apply-and-push</code>, the script applies locally only. Include the flag only when you explicitly intend to push and deploy.</p>
        <pre style={{ ...muted, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {`python3 scripts/apply_calibration_artifact.py ${downloadDetails.filename} --dashboard-url https://YOUR-DASHBOARD --apply-and-push`}
        </pre>
        <EvidenceFields value={{ filename: downloadDetails.filename, patch_text_sha256: downloadDetails.sha256,
          manifest: downloadDetails.manifest, notes: downloadDetails.notes }} />
      </details>}
      <details open><summary>Decision history ({detail.events.length})</summary>
        {!detail.events.length && <p style={muted}>No decisions recorded yet.</p>}
        <ol>{detail.events.map((event, index) => <li key={index} style={muted}>
          <strong>{label(event.event)} · {date(event.created_at)}</strong>
          <p style={{ whiteSpace: 'pre-wrap' }}>{event.note || 'No note recorded.'}</p>
          {event.data && <details><summary>Decision binding and evidence</summary><EvidenceFields value={event.data} /></details>}
        </li>)}</ol>
      </details>
    </article>}
  </section>;
}
