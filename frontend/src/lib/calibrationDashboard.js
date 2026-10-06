import { finiteNumber } from './intradayReplay.js';

export const calibrationTabs = [
  ['overview', 'Overview & health'],
  ['benchmarks', 'Benchmark detail'],
  ['activity', 'Simulated activity'],
  ['inbox', 'Review & approve'],
  ['recorded', 'Recorded-input tools'],
];

export function campaignProgress(proposal) {
  const artifact = proposal?.artifact;
  const planned = artifact?.evaluation_sessions;
  if (!Array.isArray(planned) || !planned.length
      || planned.some((day) => typeof day !== 'string') || new Set(planned).size !== planned.length) return null;
  const observed = artifact.evaluation?.evaluation?.holdout?.sessions;
  const completed = Array.isArray(observed)
    ? new Set(observed.filter((day) => planned.includes(day))).size
    : artifact.last_evaluated_session == null ? 0 : null;
  return { completed, planned: planned.length, end: artifact.evaluation_end,
    final: artifact.evaluation_complete === true };
}

export function benchmarkView(proposal, phase = 'evaluation') {
  const frozen = proposal?.artifact?.frozen;
  const selection = frozen?.selection;
  if (!selection) return null;
  const report = proposal.artifact.evaluation?.evaluation;
  const trials = frozen.search_trials ?? selection.training_trials;
  const baseline = phase === 'training' ? selection.training_trials?.find((trial) => trial.name === 'baseline')
    : report?.baseline;
  const candidate = phase === 'training'
    ? selection.training_trials?.find((trial) => trial.name === selection.selected_name)
    : report?.frozen_candidate;
  const modeled = (trial) => trial?.status === 'modeled' && trial.summary ? trial.summary : null;
  return {
    baseline: modeled(baseline), candidate: modeled(candidate),
    selected: selection.selected_name,
    changes: candidate?.settings_diff ?? null,
    period: phase === 'training' ? selection.training : report?.holdout,
    trials: Array.isArray(trials) ? trials : null,
    warnings: [...(modeled(baseline)?.warnings ?? []), ...(modeled(candidate)?.warnings ?? []),
      ...(report?.limitations ?? selection.limitations ?? [])].filter((value) => typeof value === 'string'),
    contributions: contributionRows(modeled(candidate)?.all_ticker_equity_deltas),
  };
}

export function riskMetricsView(proposal, phase = 'evaluation') {
  const saved = proposal?.artifact?.risk_analytics?.[phase];
  if (!saved) return null;
  if (saved.status === 'unavailable') return { error: saved.error || 'Risk analytics unavailable.' };
  const selection = proposal?.artifact?.frozen?.selection;
  const benchmark = phase === 'training' ? selection : proposal?.artifact?.evaluation?.evaluation;
  const window = phase === 'training' ? benchmark?.training : benchmark?.holdout;
  if (!benchmark || ![saved.input_sha256, saved.benchmark_sha256, saved.selection_sha256]
    .every((value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value))
      || saved.phase !== phase || saved.selected_name !== selection?.selected_name
      || saved.input_sha256 !== window?.input_sha256
      || saved.benchmark_sha256 !== benchmark?.artifact_sha256
      || saved.selection_sha256 !== selection?.artifact_sha256
      || !saved.baseline?.metrics || !saved.candidate?.metrics) {
    return { error: 'Risk metrics do not match this campaign and data window; no comparison is shown.' };
  }
  return saved;
}

export function contributionRows(values) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) return null;
  if (Object.values(values).some((value) => finiteNumber(value) === null)) return null;
  return Object.entries(values).map(([ticker, delta]) => ({ ticker, delta }))
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || a.ticker.localeCompare(b.ticker));
}

export function metricDelta(baseline, candidate) {
  return finiteNumber(baseline) === null || finiteNumber(candidate) === null ? null : candidate - baseline;
}

export function sortedTrials(trials, key = 'equity_delta_vs_recorded_config_baseline') {
  if (!Array.isArray(trials)) return [];
  const score = (trial) => trial.status === 'modeled' ? finiteNumber(trial.summary?.[key]) : null;
  return [...trials].sort((a, b) => {
    const left = score(a), right = score(b);
    if (left === null) return right === null ? String(a.name).localeCompare(String(b.name)) : 1;
    if (right === null) return -1;
    const direction = key === 'max_sampled_drawdown_pct' ? 1 : -1;
    return direction * (left - right) || String(a.name).localeCompare(String(b.name));
  });
}

export function activityRows(events, type, ticker = '') {
  if (!Array.isArray(events)) return [];
  const filter = ticker.trim().toUpperCase();
  return events.flatMap((event) => {
    if (event.kind !== 'cycle') return [{
      sequence: event.sequence, timestamp: event.occurred_at, ticker: '',
      action: event.kind === 'gap' ? 'GAP' : event.kind.replaceAll('_', ' ').toUpperCase(),
      reason: (typeof event.reason === 'string' && event.reason
        ? event.reason : `Recorded ${event.kind}; this is not evidence of no trading.`)
        + (event.new_run_id ? ` Separate replacement run: ${event.new_run_id}.` : ''),
      key: `${event.sequence}-gap`,
    }];
    return (event[type] ?? []).map((value, index) => ({
      ...value, sequence: event.sequence, timestamp: value.timestamp ?? event.occurred_at,
      key: `${event.sequence}-${type}-${index}`,
    })).filter((value) => !filter || String(value.ticker ?? '').toUpperCase().includes(filter));
  });
}

export function recordedEquity(events) {
  if (!Array.isArray(events)) return [];
  return [...events].sort((a, b) => a.sequence - b.sequence).flatMap((event) => {
    if (event.kind !== 'cycle') return [{ timestamp: event.occurred_at, equity: null }];
    return (event.equity_curve ?? []).map((point) => ({
      timestamp: point.timestamp ?? event.occurred_at, equity: finiteNumber(point.equity),
    }));
  });
}

export function activityPageValid(value, runId, through = null) {
  return value?.run_id === runId && Number.isSafeInteger(value.through_sequence)
    && value.through_sequence >= 0 && (through === null || value.through_sequence === through)
    && Array.isArray(value.events) && value.events.every((event, index, rows) =>
      Number.isSafeInteger(event.sequence) && event.sequence > 0 && event.sequence <= value.through_sequence
      && (index === 0 || rows[index - 1].sequence > event.sequence)
      && typeof event.kind === 'string'
      && ['decisions', 'fills', 'equity_curve'].every((key) => Array.isArray(event[key])))
    && (value.next_before_sequence === null
      || (Number.isSafeInteger(value.next_before_sequence) && value.events.length > 0
        && value.next_before_sequence === value.events.at(-1).sequence));
}

export function initialCampaign(proposals) {
  if (!Array.isArray(proposals)) return '';
  return (proposals.find((item) => item.status === 'evaluating')
    ?? proposals.find((item) => item.status === 'ready')
    ?? proposals.find((item) => item.kind === 'parameter')
    ?? proposals[0])?.id ?? '';
}
