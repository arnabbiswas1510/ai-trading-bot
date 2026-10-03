import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { experimentFields } from '../src/lib/calibrationResearch.js';
import {
  activityPageValid, activityRows, benchmarkView, calibrationTabs, campaignProgress,
  contributionRows, initialCampaign, metricDelta, recordedEquity, sortedTrials,
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
  'ResponsiveContainer', 'BarChart', 'CartesianGrid', 'XAxis', 'YAxis', 'Tooltip', 'ReferenceLine', 'Bar'];
// Hooks reference the imported useState binding, supplied without adding a testing dependency.
const renderSource = code.replace(/\buseState\(/g, 'React.useState(');
const { Benchmarks: RenderBenchmarks, Overview } = new Function(...names, `${renderSource}; return { Benchmarks, Overview };`)(
  React, experimentFields, benchmarkView, campaignProgress, metricDelta, sortedTrials,
  (v) => typeof v === 'number' && Number.isFinite(v) ? v : null,
  (v, cash) => typeof v === 'number' && Number.isFinite(v) ? cash ? `$${v.toFixed(2)}` : String(v) : 'Unavailable',
  () => '', ...Array(12).fill(mockComponent),
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
console.log('Calibration dashboard: missing-data, comparison, progress, pagination, navigation and rendered-panel checks passed.');
