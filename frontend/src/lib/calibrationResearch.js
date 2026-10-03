import { finiteNumber } from './intradayReplay.js';

export const experimentFields = [
  ['decision_config.min_trigger_score', 'Breakout score floor (0–100)', 0, 100],
  ['decision_config.min_pre_breakout_score', 'Pre-breakout score floor (0–100)', 0, 100],
  ['decision_config.min_relaxed_trigger_score', 'Relaxed score floor (0–100)', 0, 100],
  ['decision_config.min_vol_surge_gate', 'Volume surge minimum (multiple)', 0, 10],
  ['decision_config.max_pivot_extension', 'Maximum extension above pivot (fraction; 0.05 = 5%)', 0, 0.2],
  ['decision_config.max_pivot_breakdown', 'Maximum breakdown below pivot (fraction)', 0, 0.2],
  ['decision_config.max_pre_breakout_pivot_dist', 'Maximum pre-breakout pivot distance (fraction)', 0, 0.2],
  ['exit_config.armed_exit_deadline_hours', 'Armed exit deadline (hours)', 0.25, 24],
  ['exit_config.scale_out_trigger_pct', 'Partial sale gain trigger (fraction)', 0.005, 0.5],
  ['exit_config.scale_out_fraction', 'Fraction of position sold at partial sale', 0.01, 0.99],
  ['exit_config.scale_out_enabled', 'Partial sale rule enabled — investigation approval required', 'boolean'],
  ['disable_ai_veto', 'Remove AI D-grade veto — investigation approval required', 'boolean'],
];

export function experimentPayload(name, field, value) {
  if (typeof name !== 'string' || !name.trim() || name !== name.trim() || name.length > 80 || name === 'baseline') {
    throw new Error('Use a unique experiment name, 1–80 characters, not “baseline”, without surrounding spaces.');
  }
  const definition = experimentFields.find(([key]) => key === field);
  if (!definition) throw new Error('Choose a supported experiment field.');
  const [, title, min, max] = definition;
  let parsed;
  if (min === 'boolean') {
    if (value !== 'true' && value !== 'false') throw new Error('Choose true or false for this rule experiment.');
    parsed = value === 'true';
    if (field === 'disable_ai_veto' && !parsed) throw new Error('Keeping the recorded veto is not a changed experiment.');
  } else parsed = bounded(value, title, min, max);
  if (!field.includes('.')) return { name, [field]: parsed };
  const [group, key] = field.split('.');
  return { name, [group]: { [key]: parsed } };
}

export const riskFields = [
  ['min_completed_positions', 'Minimum completed positions', 1, null, true],
  ['min_distinct_sessions', 'Minimum distinct sessions', 2, null, true],
  ['min_improvement_usd', 'Minimum improvement ($)', 0, null, false, true],
  ['max_drawdown_increase_pp', 'Maximum drawdown increase (percentage points)', 0],
  ['max_worst_loss_increase_usd', 'Maximum worst-position loss increase ($)', 0],
  ['min_positive_tickers', 'Minimum tickers with positive improvement', 1, null, true],
  ['max_largest_contributor_fraction', 'Maximum fraction of improvement from one ticker (0–1)', 0, 1, false, true],
];

export function parseFiniteInput(value, label = 'Value') {
  if ((typeof value !== 'string' && typeof value !== 'number') || String(value).trim() === '') {
    throw new Error(`${label} is required; an empty value is not zero.`);
  }
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`${label} must be a finite number.`);
  return number;
}

function bounded(value, label, min, max, integer = false, exclusiveMin = false) {
  const number = parseFiniteInput(value, label);
  if ((integer && !Number.isInteger(number)) || (exclusiveMin ? number <= min : number < min)
      || (max != null && number > max)) throw new Error(`${label} is outside its allowed range.`);
  return number;
}

function revision(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label} is unavailable. Reload before writing.`);
  return value;
}

export function settingsPayload(draft, expectedRevision) {
  const training = bounded(draft.training_sessions, 'Training sessions', 1, 40, true);
  const evaluation = bounded(draft.evaluation_sessions, 'Evaluation sessions', 1, 40, true);
  if (training + evaluation > 60) throw new Error('Training and evaluation together must not exceed 60 sessions.');
  const policy = draft.risk_policy === null ? null : Object.fromEntries(riskFields.map(
    ([key, label, min, max, integer, exclusive]) => [
      key, bounded(draft.risk_policy?.[key], label, min, max, integer, exclusive),
    ],
  ));
  if (typeof draft.enabled !== 'boolean') throw new Error('Research enabled must be true or false.');
  return {
    expected_revision: revision(expectedRevision, 'Settings revision'),
    value: {
      enabled: draft.enabled,
      training_sessions: training,
      evaluation_sessions: evaluation,
      max_candidates: bounded(draft.max_candidates, 'Maximum candidates', 1, 32, true),
      risk_policy: policy,
    },
  };
}

export function deploymentEligibility(proposal, settings) {
  const reasons = [];
  if (proposal?.status !== 'ready') reasons.push('The proposal must be ready for review.');
  if (proposal?.artifact?.evaluation_complete !== true) {
    reasons.push('The predeclared evaluation window is not complete; provisional results cannot be approved.');
  }
  if (proposal?.artifact?.frozen?.selection?.frozen_experiment?.disable_ai_veto === true) {
    reasons.push('Removing the AI D-grade veto is supported for approved research only, not for deployment artifacts.');
  }
  if (!Number.isInteger(proposal?.artifact?.settings_revision)
      || proposal.artifact.settings_revision !== settings?.revision) {
    reasons.push('The frozen research settings revision differs from the current revision or is unavailable. Start a new campaign before approval.');
  }
  if (!/^[a-f0-9]{64}$/i.test(proposal?.artifact?.frozen?.risk_policy_sha256 ?? '')) {
    reasons.push('The frozen risk-policy fingerprint is unavailable.');
  }
  const eligibility = proposal?.artifact?.evaluation?.eligibility;
  if (eligibility?.eligible !== true) reasons.push('Evaluation has not explicitly passed the risk policy.');
  if (Array.isArray(eligibility?.reasons)) reasons.push(...eligibility.reasons);
  try {
    if (settings?.value?.risk_policy == null) throw new Error('Risk policy is UNSET; exploratory results cannot be approved.');
    const current = settingsPayload(settings.value, settings.revision).value.risk_policy;
    const frozenSettings = proposal?.artifact?.frozen?.settings;
    if (!frozenSettings || frozenSettings.risk_policy == null) {
      throw new Error('The frozen risk policy is unavailable; approval requires the policy used to freeze this experiment.');
    }
    const frozen = settingsPayload(frozenSettings, settings.revision).value.risk_policy;
    if (JSON.stringify(frozen) !== JSON.stringify(current)) {
      throw new Error('The risk policy changed after this experiment was frozen. New research under the current policy is required.');
    }
  } catch (error) { reasons.push(error.message); }
  if (!/^[a-f0-9]{64}$/i.test(proposal?.artifact_sha256 ?? '')) reasons.push('Exact artifact SHA-256 is unavailable.');
  if (!Number.isInteger(proposal?.revision) || proposal.revision < 0) reasons.push('Proposal revision is unavailable.');
  return { eligible: reasons.length === 0, reasons };
}

export function actionPayload(proposal, action, note, settings, experiment) {
  const payload = { expected_revision: revision(proposal?.revision, 'Proposal revision'), action, note };
  if (action === 'approve_deployment') {
    const eligibility = deploymentEligibility(proposal, settings);
    if (!eligibility.eligible) throw new Error(eligibility.reasons.join(' '));
    payload.artifact_sha256 = proposal.artifact_sha256;
    payload.expected_policy_revision = revision(settings.revision, 'Risk policy revision');
  }
  if (action === 'request_experiment') {
    if (experiment) payload.experiment = experiment;
    else if (typeof note !== 'string' || !note.trim()) {
      throw new Error('Describe the new-rule investigation or choose a structured experiment. Empty requests are not allowed.');
    }
  }
  return payload;
}

export function heartbeatLabel(health, now = Date.now()) {
  if (!health) return 'No research worker heartbeat recorded.';
  const age = now - Date.parse(health.last_seen_at);
  if (!Number.isFinite(age) || age < -60000 || age > 15 * 60000) {
    return `${health.status ?? 'Unknown'} — heartbeat stale or unavailable (over 15 minutes).`;
  }
  return `${health.status ?? 'Unknown'} — recent heartbeat.`;
}

export function metricText(value, money = false) {
  if (finiteNumber(value) === null) return 'Unavailable';
  return money ? value.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
    : value.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

export function deploymentDownload(result, proposal) {
  if (typeof result?.patch !== 'string' || !result.patch || typeof result.filename !== 'string'
      || !result.manifest || typeof result.manifest !== 'object' || Array.isArray(result.manifest)
      || typeof result.overlay !== 'string' || !result.overlay
      || !/^[a-f0-9]{64}$/i.test(result.sha256 ?? '')) {
    throw new Error('Deployment response is incomplete; no artifact downloaded.');
  }
  const version = revision(result.manifest.proposal_revision, 'Immutable approval revision');
  if (typeof proposal?.id !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(proposal.id)) {
    throw new Error('Deployment proposal identifier is unavailable.');
  }
  return {
    filename: `approved-strategy-${proposal.id}-r${version}.json`,
    content: JSON.stringify({
      filename: result.filename, patch: result.patch, manifest: result.manifest,
      overlay: result.overlay, sha256: result.sha256, notes: result.notes,
    }, null, 2),
  };
}
