import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { experimentFields } from '../src/lib/calibrationResearch.js';
import {
  activityPageValid, activityRows, benchmarkView, calibrationTabs, campaignProgress,
  contributionRows, initialCampaign, metricDelta, recordedEquity, sortedTrials,
  riskMetricsView,
} from '../src/lib/calibrationDashboard.js';

const baseline = { name: 'baseline', status: 'modeled', settings_diff: [],
  summary: { net_profit: 0, equity_delta_vs_recorded_config_baseline: 0, max_sampled_drawdown_pct: 2 } };
const selected = { name: 'candidate', status: 'modeled',
  settings_diff: [{ field: 'exit_config.armed_exit_deadline_hours', original: 3.25, effective: 3 }],
  summary: { net_profit: 125, equity_delta_vs_recorded_config_baseline: 125,
    max_sampled_drawdown_pct: 1, all_ticker_equity_deltas: { AAA: 150, BBB: -25 } } };
const rejected = { name: 'rejected', status: 'rejected', reason: 'Missing price', summary: { net_profit: 9999 } };
const proposal = { id: 'campaign-1', title: 'Example campaign', status: 'evaluating', artifact: {
  frozen: { selection: { selected_name: 'candidate', training_trials: [baseline, selected],
    training: { sessions: ['2026-10-01'], observed_start: '2026-10-01T14:00:00Z', observed_end: '2026-10-01T20:00:00Z' } },
  search_trials: [baseline, selected, rejected] },
  evaluation_sessions: ['2026-10-05', '2026-10-06'], evaluation_end: '2026-10-06',
  evaluation_complete: false, last_evaluated_session: '2026-10-05',
  evaluation: { evaluation: { baseline, frozen_candidate: selected, holdout: { sessions: ['2026-10-05'] } } },
} };

assert.equal(calibrationTabs.length, 5);
assert.equal(benchmarkView(null), null);
assert.equal(benchmarkView(proposal).candidate.net_profit, 125);
assert.equal(benchmarkView(proposal, 'training').baseline.net_profit, 0);
const waiting = structuredClone(proposal); delete waiting.artifact.evaluation;
assert.equal(benchmarkView(waiting).candidate, null, 'Never substitute training for missing evaluation');
assert.equal(campaignProgress(waiting).completed, null, 'An inconsistent missing report is not zero progress');
delete waiting.artifact.last_evaluated_session;
assert.equal(campaignProgress(waiting).completed, 0);
assert.equal(campaignProgress({}), null);
assert.deepEqual(campaignProgress(proposal), { completed: 1, planned: 2, end: '2026-10-06', final: false });
const duplicated = structuredClone(proposal);
duplicated.artifact.evaluation.evaluation.holdout.sessions = ['2026-10-05', '2026-10-05', '2026-10-09'];
assert.equal(campaignProgress(duplicated).completed, 1);
const failed = structuredClone(proposal); failed.artifact.evaluation.evaluation.frozen_candidate = rejected;
assert.equal(benchmarkView(failed).candidate, null, 'Rejected trials must never become profits');
assert.equal(metricDelta(0, 125), 125);
for (const value of [null, undefined, NaN, Infinity, '10', false]) assert.equal(metricDelta(value, 0), null);
assert.deepEqual(contributionRows({ AAA: 150, BBB: -25 }), [{ ticker: 'AAA', delta: 150 }, { ticker: 'BBB', delta: -25 }]);
assert.equal(contributionRows({ AAA: null }), null);
assert.deepEqual(sortedTrials([rejected, baseline, selected]).map((row) => row.name), ['candidate', 'baseline', 'rejected']);
assert.deepEqual(sortedTrials([rejected, baseline, selected], 'max_sampled_drawdown_pct').map((row) => row.name), ['candidate', 'baseline', 'rejected']);
assert.equal(initialCampaign([{ id: 'idea', status: 'investigation_requested' }, { id: 'running', status: 'evaluating' }]), 'running');
assert.equal(initialCampaign([]), '');
const events = [
  { sequence: 3, kind: 'cycle', occurred_at: '2026-10-03T16:00:00Z',
    fills: [{ ticker: 'AAA', side: 'BUY', shares: 2, price: 100, commission: 0 }],
    decisions: [], equity_curve: [{ timestamp: '2026-10-03T16:00:00Z', equity: 10005 }] },
  { sequence: 2, kind: 'gap', occurred_at: '2026-10-03T15:00:00Z', fills: [], decisions: [], equity_curve: [] },
  { sequence: 1, kind: 'cycle', occurred_at: '2026-10-03T14:00:00Z', fills: [], decisions: [],
    equity_curve: [{ timestamp: '2026-10-03T14:00:00Z', equity: 10000 }] },
];
assert.equal(activityRows(events, 'fills').length, 2);
assert.equal(activityRows(events, 'fills', 'zzz')[0].action, 'GAP', 'Ticker filters must not hide missing coverage');
assert.equal(activityRows(events, 'fills', 'aa')[0].commission, 0);
assert.deepEqual(activityRows([{
  sequence: 4, kind: 'run_recovered', occurred_at: '2026-10-06T13:35:00Z',
  reason: 'Acquisition gap; failed run preserved', new_run_id: 'a'.repeat(64),
}], 'fills', 'zzz').map((row) => [row.action, row.reason]), [[
  'RUN RECOVERED', `Acquisition gap; failed run preserved Separate replacement run: ${'a'.repeat(64)}.`,
]]);
assert.deepEqual(recordedEquity(events).map((row) => row.equity), [10000, null, 10005]);
assert.deepEqual(events.map((row) => row.sequence), [3, 2, 1], 'Chart sorting must not mutate page order');
const page = { run_id: 'abc', through_sequence: 3, events, next_before_sequence: 1 };
assert.equal(activityPageValid(page, 'abc'), true);
assert.equal(activityPageValid(page, 'wrong'), false);
assert.equal(activityPageValid(page, 'abc', 4), false);
assert.equal(activityPageValid({ ...page, next_before_sequence: 2 }, 'abc'), false);
assert.equal(activityPageValid({ ...page, events: [...events].reverse() }, 'abc'), false);
assert.equal(activityPageValid({ ...page, events: [{ sequence: 3, kind: 'cycle' }] }, 'abc'), false);
assert.equal(activityPageValid({ run_id: 'abc', through_sequence: 0, events: [], next_before_sequence: null }, 'abc'), true);

const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
assert.ok(app.indexOf("if (currentView === 'calibration')") < app.indexOf('if (dataLoading)'));
assert.ok(app.indexOf("if (currentView === 'calibration')") < app.indexOf('if (dataError)'));
assert.match(app, /setCurrentView\('calibration'\)/);
const inbox = readFileSync(new URL('../src/components/CalibrationResearchView.jsx', import.meta.url), 'utf8');
assert.match(inbox, /\[focusProposalId, focusRequest\]/);
assert.match(inbox, /setDownloadDetails\(\{ proposalId: proposal\.id,/,
  'Downloaded evidence must stay bound to the requested proposal, not a changed selection');

// Compile the actual JSX with the existing build tool and render its read-only panels.
const source = readFileSync(new URL('../src/components/CalibrationDashboard.jsx', import.meta.url), 'utf8');
assert.match(source, /hidden=\{tab !== 'inbox'\}/);
assert.match(source, /inboxOpened && <CalibrationResearchView/);
assert.match(source, /value=\{effectiveId\} disabled=\{inboxBusy\}/);
const withoutImports = source.replace(/^import[\s\S]*?;\n/gm, '').replace('export default function', 'function');
const { code } = await transformWithEsbuild(withoutImports, 'CalibrationDashboard.jsx', { loader: 'jsx', jsx: 'transform' });
const mockComponent = () => null;
const names = ['React', 'experimentFields', 'benchmarkView', 'campaignProgress', 'metricDelta', 'sortedTrials',
  'finiteNumber', 'metricText', 'heartbeatLabel', 'Activity', 'Download', 'FlaskConical', 'RefreshCw',
  'ResponsiveContainer', 'BarChart', 'CartesianGrid', 'XAxis', 'YAxis', 'Tooltip', 'ReferenceLine', 'Bar', 'CalibrationRiskMetrics'];
// Hooks reference the imported useState binding, supplied without adding a testing dependency.
const renderSource = code.replace(/\buseState\(/g, 'React.useState(');
const { Benchmarks: RenderBenchmarks, Overview } = new Function(...names, `${renderSource}; return { Benchmarks, Overview };`)(
  React, experimentFields, benchmarkView, campaignProgress, metricDelta, sortedTrials,
  (v) => typeof v === 'number' && Number.isFinite(v) ? v : null,
  (v, cash) => typeof v === 'number' && Number.isFinite(v) ? cash ? `$${v.toFixed(2)}` : String(v) : 'Unavailable',
  () => '', ...Array(13).fill(mockComponent),
);
const resource = { data: { proposal }, loading: false, error: '', updated: '2026-10-03T16:00:00Z' };
const html = renderToStaticMarkup(React.createElement(RenderBenchmarks, { resource, onReview() {} }));
assert.match(html, /125\.00/);
assert.match(html, /Provisional or waiting/);
assert.match(html, /Missing price/);
assert.doesNotMatch(html, /9999/);
assert.match(html, /armed_exit_deadline_hours/);
assert.match(html, /Armed exit deadline \(hours\)/);
const missingHtml = renderToStaticMarkup(React.createElement(RenderBenchmarks, { resource: { ...resource, data: { proposal: waiting } }, onReview() {} }));
assert.match(missingHtml, /comparable modeled pair is not available/);
const errorResource = { data: null, error: 'Service offline', loading: false, updated: null };
const errorHtml = renderToStaticMarkup(React.createElement(Overview, {
  capture: errorResource, shadow: errorResource, inbox: errorResource, detail: errorResource, onReview() {},
}));
assert.match(errorHtml, /Service offline/);
assert.match(errorHtml, /Unavailable/);
assert.doesNotMatch(errorHtml, /\$0\.00/);
const recoveryHtml = renderToStaticMarkup(React.createElement(Overview, {
  capture: errorResource, inbox: errorResource, detail: errorResource, onReview() {},
  shadow: { data: { health: [], reports: [], runs: [{
    status: 'running', seed_at: '2026-10-06T13:35:00Z', earliest_full_session: '2026-10-07',
    recovery: { previous_run_id: 'b'.repeat(64), mode: 'automatic' },
  }] }, loading: false, error: '' },
}));
assert.match(recoveryHtml, /Earliest eligible full session: 2026-10-07/);
assert.match(recoveryHtml, /not proof that a complete day was recorded/);
assert.match(recoveryHtml, /Automatic replacement of run/);
assert.match(recoveryHtml, /Separate runs are not combined/);
assert.equal(riskMetricsView(proposal), null);
const riskProposal = structuredClone(proposal);
riskProposal.artifact.frozen.selection.artifact_sha256 = 'a'.repeat(64);
riskProposal.artifact.evaluation.evaluation.artifact_sha256 = 'b'.repeat(64);
riskProposal.artifact.evaluation.evaluation.holdout.input_sha256 = 'c'.repeat(64);
riskProposal.artifact.risk_analytics = { evaluation: {
  phase: 'evaluation', status: 'available', selected_name: 'candidate',
  selection_sha256: 'a'.repeat(64), benchmark_sha256: 'b'.repeat(64), input_sha256: 'c'.repeat(64),
  reference_snapshot: { status: 'available', source: 'US Treasury 3-month', error: null, observations: [] },
  baseline: { metrics: { sharpe: null, calmar: null, max_sampled_drawdown_pct: 0 },
    unavailable: { sharpe: 'Zero excess-return variance', calmar: 'Zero drawdown' }, sample: { sessions: 5, daily_returns: 4 } },
  candidate: { metrics: { sharpe: 1.25, calmar: 2, max_sampled_drawdown_pct: 2 }, sample: { sessions: 5, daily_returns: 4 },
    warnings: ['Short sample: exploratory only'] },
} };
assert.equal(riskMetricsView(riskProposal).candidate.metrics.sharpe, 1.25);
assert.equal(riskMetricsView(riskProposal, 'training'), null, 'Do not show evaluation ratios in the training tab');
const wrongRisk = structuredClone(riskProposal);
wrongRisk.artifact.risk_analytics.evaluation.input_sha256 = 'wrong-window';
assert.match(riskMetricsView(wrongRisk).error, /do not match/);
const riskSource = readFileSync(new URL('../src/components/CalibrationRiskMetrics.jsx', import.meta.url), 'utf8');
const riskCode = (await transformWithEsbuild(riskSource.replace(/^import[\s\S]*?;\n/gm, '')
  .replace('export default function', 'function'), 'CalibrationRiskMetrics.jsx', { loader: 'jsx', jsx: 'transform' })).code;
const RiskMetrics = new Function('React', 'finiteNumber', 'riskMetricsView', `${riskCode};return CalibrationRiskMetrics;`)(
  React, (value) => typeof value === 'number' && Number.isFinite(value) ? value : null, riskMetricsView);
const riskHtml = renderToStaticMarkup(React.createElement(RiskMetrics, { proposal: riskProposal, phase: 'evaluation' }));
assert.match(riskHtml, /Sharpe ratio/);
assert.match(riskHtml, /Calmar ratio/);
assert.match(riskHtml, /Zero excess-return variance/);
assert.match(riskHtml, /Zero drawdown/);
assert.match(riskHtml, /0\.00%/);
assert.match(riskHtml, /1\.25/);
assert.match(riskHtml, /Short sample/);
const offlineRisk = structuredClone(riskProposal);
offlineRisk.artifact.risk_analytics.evaluation.reference_snapshot.status = 'unavailable';
offlineRisk.artifact.risk_analytics.evaluation.reference_snapshot.error = 'Historical rates missing';
offlineRisk.artifact.risk_analytics.evaluation.candidate.metrics.sharpe = null;
const offlineHtml = renderToStaticMarkup(React.createElement(RiskMetrics, { proposal: offlineRisk, phase: 'evaluation' }));
assert.match(offlineHtml, /Historical rates missing/);
assert.match(offlineHtml, /do not silently fall back/);
const oldRiskHtml = renderToStaticMarkup(React.createElement(RiskMetrics, { proposal, phase: 'training' }));
assert.match(oldRiskHtml, /Older artifacts are not retroactively rewritten/);
console.log('Calibration dashboard: missing-data, comparison, progress, pagination, navigation and rendered-panel checks passed.');
