#!/usr/bin/env node
import assert from 'node:assert/strict';
import {
  apiErrorText, captureCoverage, comparisonResult, equityDifference, finiteNumber, initialAccountView,
  maxReplayDays, pollReplay, portfolioMetrics, recordedSessions, replayValidation, resultSnapshotIssue, snapshotValidation,
} from '../src/lib/intradayReplay.js';

let passed = 0;
const failures = [];
function check(name, fn) {
  try { fn(); passed++; console.log(`  ok ${name}`); }
  catch (error) { failures.push(name); console.error(`  x ${name}: ${error.message}`); }
}
const sessions = [
  { session: '2026-09-30', frames: 2, complete_frames: 1, missing_quotes: 3 },
  { session: '2026-09-28', frames: 4, complete_frames: 4, missing_quotes: 0 },
  { session: '2026-09-29', frames: 0 },
];
const result = { baseline: { final_equity_net: 101000 }, variant: { final_equity_net: 101250 } };

check('raw evidence exports allow observed sessions without replayable quote frames', () => {
  const rows = [{ session: '2026-09-29', frames: 0, first_at: '2026-09-29T20:00:00Z',
    initial_snapshot: { complete: false } }];
  assert.deepEqual(recordedSessions(rows), []);
  assert.equal(recordedSessions(rows, true).length, 1);
  assert.equal(replayValidation(rows, '2026-09-29', '2026-09-29', 93, true), null);
  assert.match(replayValidation(rows, '2026-09-29', '2026-09-29'), /No recorded sessions/);
  assert.match(replayValidation(rows, '2026-09-28', '2026-09-29', 93, true), /Select both dates/);
});

check('recorded dates require real frames and are sorted without mutating status', () => {
  assert.deepEqual(recordedSessions(sessions).map((row) => row.session), ['2026-09-28', '2026-09-30']);
  assert.equal(sessions[0].session, '2026-09-30');
  assert.deepEqual(recordedSessions(null), []);
});
check('research only submits actual recorded dates in order; capital is not user-selected', () => {
  assert.equal(replayValidation(sessions, '2026-09-28', '2026-09-30'), null);
  assert.match(replayValidation(sessions, '2026-09-29', '2026-09-30'), /recorded sessions/);
  assert.match(replayValidation(sessions, '2026-09-30', '2026-09-28'), /on or after/);
  assert.match(replayValidation([], '', ''), /No recorded sessions/);
  const dates = ['2026-01-01', '2026-04-03', '2026-04-04'].map((session) => ({ session, frames: 1 }));
  assert.equal(replayValidation(dates, '2026-01-01', '2026-04-03'), null);
  assert.match(replayValidation(dates, '2026-01-01', '2026-04-04'), /93 calendar days/);
  assert.equal(replayValidation(dates, '2026-01-01', '2026-04-04', 94), null);
  assert.match(replayValidation(sessions, '2026-09-28', '2026-09-30', 2), /2 calendar days/);
  assert.equal(maxReplayDays(31), 31);
  for (const value of [undefined, null, 0, -1, NaN, Infinity, '20', 1.5]) assert.equal(maxReplayDays(value), 93);
});
check('missing and nonnumeric metrics never become zero', () => {
  for (const missing of [null, undefined, '', '100', false, NaN, Infinity]) assert.equal(finiteNumber(missing), null);
  assert.equal(finiteNumber(0), 0);
  assert.deepEqual(portfolioMetrics(null), { initialEquity: null, equity: null, profit: null, commission: null, slippage: null, fills: null, openPositions: null, drawdown: null, closedPositions: null, sessions: null });
  assert.equal(portfolioMetrics({ fills: [], open_positions: [] }).fills, 0);
  assert.equal(portfolioMetrics({ fills: [], open_positions: [] }).openPositions, 0);
});
check('signed comparison means variant minus baseline, including losing variants', () => {
  assert.equal(equityDifference(result), 250);
  assert.equal(equityDifference({ baseline: result.variant, variant: result.baseline }), -250);
  assert.equal(equityDifference({ baseline: { final_equity_net: 0 }, variant: { final_equity_net: 0 } }), 0);
  assert.equal(equityDifference({ net_final_equity_difference: 999, baseline: result.baseline }), null);
});
check('rejected and running runs never expose financial results', () => {
  assert.equal(comparisonResult({ status: 'rejected', result }), null);
  assert.equal(comparisonResult({ status: 'running', result }), null);
  assert.equal(comparisonResult({ status: 'failed', result }), null);
  assert.equal(comparisonResult({ status: 'completed', result }), result);
  assert.equal(comparisonResult(result), result);
  assert.equal(comparisonResult({ status: 'completed', result: null }), null);
});
check('capture coverage measures calendar span and preserves unknown quote counts', () => {
  assert.deepEqual(captureCoverage(sessions), { sessions: 2, calendarDays: 3, completeFrames: 5, missingQuotes: 3 });
  assert.equal(captureCoverage([{ session: '2026-09-30', frames: 1 }]).missingQuotes, null);
  assert.equal(captureCoverage([]).completeFrames, null);
});
check('API details render readable text, including FastAPI validation arrays', () => {
  assert.equal(apiErrorText({ detail: 'Missing quote: ABC' }), 'Missing quote: ABC');
  assert.equal(apiErrorText({ detail: [{ msg: 'Capital must be positive' }, { msg: 'Missing date' }] }), 'Capital must be positive; Missing date');
  assert.equal(apiErrorText({ error: { message: 'Capture gap' } }), 'Capture gap');
  assert.equal(apiErrorText({}, 'Unavailable'), 'Unavailable');
});
check('actual starting snapshot is mandatory, with explicit history and protection state', () => {
  const snapshot = {
    complete: true, equity: 100000, cash: 20000, positions: [{ ticker: 'ABC', shares: 100 }],
    prior_trades: [{ ticker: 'XYZ' }], cooldown_ledger: [], protective_orders: [{ ticker: 'ABC' }],
  };
  assert.equal(snapshotValidation(snapshot), null);
  assert.match(snapshotValidation(null), /will not be substituted/);
  assert.match(snapshotValidation({ ...snapshot, complete: false }), /incomplete/);
  assert.match(snapshotValidation({ ...snapshot, equity: null }), /synthetic capital/);
  for (const key of ['positions', 'prior_trades', 'cooldown_ledger', 'protective_orders']) {
    assert.match(snapshotValidation({ ...snapshot, [key]: undefined }), /missing/);
  }
  assert.equal(snapshotValidation({ ...snapshot, positions: [], protective_orders: [], cash: 100000 }), null);
  assert.match(replayValidation([{ ...sessions[1], initial_snapshot: { complete: false } }], '2026-09-28', '2026-09-28'), /incomplete/);
});
check('optional initial-state summaries do not invent missing metadata or accept known incomplete state', () => {
  assert.equal(resultSnapshotIssue({ initial_state_summary: { cash: 2000 } }), null);
  assert.equal(resultSnapshotIssue(result), null);
  assert.match(resultSnapshotIssue({ initial_state_summary: { complete: false } }), /incomplete/);
  assert.match(resultSnapshotIssue({ initial_snapshot: { complete: false } }), /incomplete/);
  assert.match(resultSnapshotIssue({ effective_config: { scope: { initial_state: 'flat_no_recent_sales' } } }), /synthetic empty portfolio/);
  assert.match(resultSnapshotIssue({ initial_state_mode: 'synthetic' }), /does not start/);
  assert.equal(resultSnapshotIssue({ initial_state_mode: 'recorded_actual_portfolio' }), null);
});
check('schema2 recorded account and position fields display without optional summary', () => {
  const positions = [{ ticker: 'ABC', shares: 15, buy_price: 100 }];
  const account = { net_liquidation: 120000, positions_value: 45000, currency: 'USD' };
  const initial = initialAccountView({
    effective_initial_account: account, effective_initial_cash: 75000,
    initial_snapshot_at: '2026-09-30T13:30:00Z',
    initial_state_summary: 'One recorded position; protective-order anchor is a sampled assumption.',
    baseline: { initial_equity: 120000, initial_positions: positions },
  });
  assert.equal(initial.equity, 120000);
  assert.equal(initial.cash, 75000);
  assert.equal(initial.positions, positions);
  assert.equal(initial.positionsCount, 1);
  assert.equal(initial.timestamp, '2026-09-30T13:30:00Z');
  assert.match(initial.summary, /sampled assumption/);
  assert.equal(initialAccountView({}).positionsCount, null);
  assert.equal(initialAccountView({}).equity, null);
  const metrics = portfolioMetrics({ initial_equity: 120000, closed_position_count: 2, sessions_count: 5, max_drawdown_pct: 3.25 });
  assert.equal(metrics.closedPositions, 2);
  assert.equal(metrics.sessions, 5);
  assert.equal(metrics.drawdown, 3.25);
  assert.equal(portfolioMetrics({ closed_positions: 3 }).closedPositions, 3);
  assert.equal(portfolioMetrics({ closed_positions: [], closed_position_count: 2 }).closedPositions, 2);
});
async function asyncCheck(name, fn) {
  try { await fn(); passed++; console.log(`  ok ${name}`); }
  catch (error) { failures.push(name); console.error(`  x ${name}: ${error.message}`); }
}
await asyncCheck('HTTP202 running response polls by saved ID until completed', async () => {
  let calls = 0;
  const updates = [];
  const completed = { id: 'run-1', status: 'completed', result };
  assert.equal(await pollReplay({ id: 'run-1', status: 'running' }, {
    signal: new AbortController().signal, wait: async () => {}, onUpdate: (run) => updates.push(run.status),
    fetchRun: async (id) => { assert.equal(id, 'run-1'); return ++calls === 2 ? completed : { id, status: 'running' }; },
  }), completed);
  assert.equal(calls, 2);
  assert.deepEqual(updates, ['running', 'running']);
});
await asyncCheck('polling returns rejected/failed and propagates retrieval errors', async () => {
  for (const status of ['rejected', 'failed']) {
    const run = await pollReplay({ id: 'run', status: 'running' }, {
      signal: new AbortController().signal, wait: async () => {}, onUpdate: () => {},
      fetchRun: async () => ({ status, error: 'Incomplete account' }),
    });
    assert.equal(run.status, status);
  }
  await assert.rejects(pollReplay({ id: 'run', status: 'running' }, {
    signal: new AbortController().signal, wait: async () => {}, onUpdate: () => {},
    fetchRun: async () => { throw new Error('Polling service unavailable'); },
  }), /Polling service unavailable/);
});
await asyncCheck('polling stops at one hour or when aborted without fetching again', async () => {
  let clock = 0;
  let calls = 0;
  await assert.rejects(pollReplay({ id: 'run', status: 'running' }, {
    signal: new AbortController().signal, now: () => clock,
    wait: async () => { clock += 3600000; }, onUpdate: () => {},
    fetchRun: async () => { calls++; },
  }), /one hour/);
  assert.equal(calls, 0);
  const controller = new AbortController();
  await assert.rejects(pollReplay({ id: 'run', status: 'running' }, {
    signal: controller.signal, wait: async () => controller.abort(), onUpdate: () => {},
    fetchRun: async () => { calls++; },
  }), { name: 'AbortError' });
  assert.equal(calls, 0);
});
console.log(`\nIntraday replay: ${passed} passed / ${failures.length} failed\n`);
if (failures.length) process.exit(1);
