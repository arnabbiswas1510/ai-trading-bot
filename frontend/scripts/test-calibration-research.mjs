import assert from 'node:assert/strict';
import {
  actionPayload, deploymentDownload, deploymentEligibility, experimentPayload, heartbeatLabel, metricText, parseFiniteInput,
  settingsPayload,
} from '../src/lib/calibrationResearch.js';

const policy = {
  min_completed_positions: 10, min_distinct_sessions: 5, min_improvement_usd: 100,
  max_drawdown_increase_pp: 0, max_worst_loss_increase_usd: 0,
  min_positive_tickers: 3, max_largest_contributor_fraction: 0.5,
};
const settings = { revision: 7, value: {
  enabled: true, training_sessions: 5, evaluation_sessions: 5, max_candidates: 16, risk_policy: policy,
} };
const proposal = { id: 'p1', status: 'ready', revision: 4, artifact_sha256: 'a'.repeat(64),
  artifact: { evaluation_complete: true, settings_revision: 7,
    frozen: { settings: settings.value, risk_policy_sha256: 'c'.repeat(64) },
    evaluation: { eligibility: { eligible: true, reasons: [] } } } };

assert.equal(deploymentEligibility(proposal, settings).eligible, true);
for (const value of [undefined, null, false, 'true', 1]) {
  assert.equal(deploymentEligibility({ ...proposal,
    artifact: { ...proposal.artifact, evaluation: { eligibility: { eligible: value } } } }, settings).eligible, false);
}
for (const bad of [null, {}, { ...proposal, status: 'evaluating' }, { ...proposal, status: 'approved' },
  { ...proposal, artifact_sha256: '' }, { ...proposal, revision: undefined },
  { ...proposal, artifact: { ...proposal.artifact, evaluation_complete: false } },
  { ...proposal, artifact: { ...proposal.artifact, evaluation_complete: undefined } },
  { ...proposal, artifact: { ...proposal.artifact, evaluation_complete: 'true' } },
  { ...proposal, artifact: { ...proposal.artifact, settings_revision: undefined } },
  { ...proposal, artifact: { ...proposal.artifact, settings_revision: 6 } },
  { ...proposal, artifact: { ...proposal.artifact, frozen: { ...proposal.artifact.frozen, risk_policy_sha256: undefined } } },
  { ...proposal, artifact: { ...proposal.artifact, frozen: {} } },
  { ...proposal, artifact: { ...proposal.artifact, frozen: { settings: settings.value,
    selection: { frozen_experiment: { disable_ai_veto: true } } } } },
  { ...proposal, artifact: { ...proposal.artifact, frozen: { settings: { ...settings.value, risk_policy: null } } } },
  { ...proposal, artifact: { ...proposal.artifact, evaluation: { eligibility: { eligible: true, reasons: ['risk worsened'] } } } }]) {
  assert.equal(deploymentEligibility(bad, settings).eligible, false);
  assert.throws(() => actionPayload(bad, 'approve_deployment', '', settings));
}
for (const bad of [null, {}, { ...settings, revision: undefined },
  { ...settings, value: { ...settings.value, risk_policy: null } },
  { ...settings, value: { ...settings.value, risk_policy: { ...policy, min_improvement_usd: 101 } } },
  { ...settings, value: { ...settings.value, risk_policy: {} } }]) {
  assert.equal(deploymentEligibility(proposal, bad).eligible, false);
}
for (const bad of ['', '   ', null, undefined, false, [], {}, NaN, Infinity, 'NaN', '-Infinity']) {
  assert.throws(() => parseFiniteInput(bad));
}
assert.equal(parseFiniteInput('0'), 0);
assert.equal(parseFiniteInput('0.005'), 0.005);
assert.equal(metricText(null, true), 'Unavailable');
assert.equal(metricText(NaN), 'Unavailable');
assert.equal(metricText('0'), 'Unavailable');
assert.equal(metricText(0, true), '$0.00');
assert.deepEqual(settingsPayload(settings.value, 7), { expected_revision: 7, value: settings.value });
assert.equal(settingsPayload({ ...settings.value, risk_policy: null }, 7).value.risk_policy, null);
for (const delta of [
  { training_sessions: 0 }, { training_sessions: 41 }, { training_sessions: 1.5 },
  { training_sessions: 40, evaluation_sessions: 21 }, { evaluation_sessions: '' },
  { max_candidates: 33 }, { enabled: 'true' }, { risk_policy: {} },
  { risk_policy: { ...policy, max_largest_contributor_fraction: 0 } },
  { risk_policy: { ...policy, max_largest_contributor_fraction: 1.1 } },
  { risk_policy: { ...policy, min_improvement_usd: 0 } },
  { risk_policy: { ...policy, min_distinct_sessions: 1 } },
  { risk_policy: { ...policy, max_drawdown_increase_pp: -1 } },
]) assert.throws(() => settingsPayload({ ...settings.value, ...delta }, 7));
assert.throws(() => settingsPayload(settings.value, '7'));
assert.deepEqual(actionPayload(proposal, 'approve_deployment', 'Reviewed evidence', settings), {
  expected_revision: 4, action: 'approve_deployment', note: 'Reviewed evidence',
  artifact_sha256: 'a'.repeat(64), expected_policy_revision: 7,
});
assert.equal(actionPayload({ ...proposal, revision: 9, artifact_sha256: 'b'.repeat(64),
  artifact: { ...proposal.artifact, settings_revision: 11 } },
  'approve_deployment', '', { ...settings, revision: 11 }).expected_policy_revision, 11);
assert.deepEqual(actionPayload(proposal, 'comment', 'Do not deploy', settings), {
  expected_revision: 4, action: 'comment', note: 'Do not deploy',
});
const experiment = experimentPayload('score_test', 'decision_config.min_trigger_score', '70');
assert.deepEqual(experiment, { name: 'score_test', decision_config: { min_trigger_score: 70 } });
assert.deepEqual(actionPayload({ revision: 0 }, 'request_experiment', 'New independent request', settings, experiment), {
  expected_revision: 0, action: 'request_experiment', note: 'New independent request', experiment,
});
assert.deepEqual(actionPayload({ revision: 0 }, 'request_experiment', 'Investigate a new rule before data exists', settings), {
  expected_revision: 0, action: 'request_experiment', note: 'Investigate a new rule before data exists',
});
assert.deepEqual(actionPayload(proposal, 'request_experiment', 'Test only', settings, experiment), {
  expected_revision: 4, action: 'request_experiment', note: 'Test only', experiment,
});
assert.deepEqual(experimentPayload('no_scale_out', 'exit_config.scale_out_enabled', 'false'),
  { name: 'no_scale_out', exit_config: { scale_out_enabled: false } });
assert.deepEqual(experimentPayload('no_veto', 'disable_ai_veto', 'true'), { name: 'no_veto', disable_ai_veto: true });
for (const args of [
  ['', 'decision_config.min_trigger_score', '70'],
  ['baseline', 'decision_config.min_trigger_score', '70'],
  [' score ', 'decision_config.min_trigger_score', '70'],
  ['score', 'decision_config.min_trigger_score', ''],
  ['score', 'decision_config.min_trigger_score', '101'],
  ['score', 'decision_config.min_trigger_score', 'NaN'],
  ['score', 'exit_config.scale_out_enabled', '0'],
  ['score', 'disable_ai_veto', 'false'],
  ['score', 'live_entries_enabled', 'true'],
]) assert.throws(() => experimentPayload(...args));
assert.throws(() => actionPayload(proposal, 'request_experiment', '   ', settings));
assert.deepEqual(actionPayload(proposal, 'request_experiment', 'Investigate a new rule; needs engineering', settings), {
  expected_revision: 4, action: 'request_experiment', note: 'Investigate a new rule; needs engineering',
});
const now = Date.parse('2026-10-03T12:00:00Z');
assert.match(heartbeatLabel(null, now), /No research worker/);
assert.match(heartbeatLabel({ status: 'waiting', last_seen_at: '2026-10-03T11:59:00Z' }, now), /recent/);
assert.match(heartbeatLabel({ status: 'waiting', last_seen_at: '2026-10-03T11:00:00Z' }, now), /stale/);
assert.match(heartbeatLabel({ status: 'waiting', last_seen_at: 'invalid' }, now), /unavailable/);
const artifact = { filename: 'strategy.patch', patch: 'diff --git a/a b/a', manifest: { proposal_id: 'p1', proposal_revision: 2 },
  overlay: 'MIN_TRIGGER_SCORE=70\n', sha256: 'b'.repeat(64), notes: ['Approval checked before apply'], token: 'must-not-export' };
const download = deploymentDownload(artifact, proposal);
assert.equal(download.filename, 'approved-strategy-p1-r2.json');
assert.deepEqual(Object.keys(JSON.parse(download.content)), ['filename', 'patch', 'manifest', 'overlay', 'sha256', 'notes']);
assert.equal(JSON.parse(download.content).patch, artifact.patch);
assert.equal(JSON.parse(download.content).overlay, artifact.overlay);
assert.equal(download.content.includes('must-not-export'), false);
for (const bad of [null, {}, { ...artifact, overlay: undefined }, { ...artifact, overlay: {} }, { ...artifact, sha256: '' },
  { ...artifact, manifest: [] }, { ...artifact, manifest: { proposal_id: 'p1' } }, { ...artifact, patch: '' }]) {
  assert.throws(() => deploymentDownload(bad, proposal));
}
console.log('Calibration research parsing, approval bindings, risk gates, experiment and heartbeat assertions passed.');
