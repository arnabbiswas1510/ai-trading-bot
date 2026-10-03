import assert from 'node:assert/strict';
import { buildLifecycle, evaluatePositionRules, positionExitConfig, RULES_CONFIG, STATE } from '../src/lib/positionRules.js';

const base = {
  ticker: 'TEST', buy_price: 100, current_price: 104, hwm_price: 104.25, highest_unrealized_pct: 4.25,
  buy_date: '2026-09-25', shares: 10, closed_above_entry: true, stop_loss_pct: 0.07,
};
const config = {
  armed_exit_deadline_hours: 6, scale_out_enabled: true, scale_out_trigger_pct: 0.045, scale_out_fraction: 0.275,
};
const evaluate = (pos) => evaluatePositionRules(pos, 5, 1, 7, 1);
const rule = (pos, id) => evaluate(pos).rules.find((entry) => entry.id === id);
const pos = { ...base, strategy_exit_config: config };
assert.equal(rule(pos, 'scale_out').state, STATE.WATCH);
assert.match(rule(pos, 'scale_out').headline, /triggers at \+4\.5%/);
assert.match(rule(pos, 'scale_out').detail, /Books 27\.5%/);
assert.equal(rule(pos, 'scale_out').window, 'Peak ≥ +4.5%, once');
assert.match(rule(pos, 'armed_exit').detail, /deadline is 6h/);
assert.equal(RULES_CONFIG.SCALE_OUT_TRIGGER_PCT, 0.04);
assert.equal(RULES_CONFIG.ARMED_EXIT_DEADLINE_HOURS, 3.25);
assert.equal(rule({ ...pos, highest_unrealized_pct: 4.5 }, 'scale_out').state, STATE.TRIGGERED);

const off = { ...pos, strategy_exit_config: { ...config, scale_out_enabled: false }, highest_unrealized_pct: 50 };
assert.equal(rule(off, 'scale_out').state, STATE.OFF);
assert.equal(rule(off, 'scale_out').window, 'Disabled');
assert.doesNotMatch(rule(off, 'scale_out').headline, /next cycle|triggers at/);
assert.equal(rule({ ...off, scaled_out: true }, 'scale_out').state, STATE.OFF);
assert.match(rule({ ...pos, scaled_out: true }, 'scale_out').detail, /historical fraction is not inferred/);
assert.doesNotMatch(rule({ ...pos, scaled_out: true }, 'scale_out').headline, /27\.5%/);

const legacy = rule(base, 'scale_out');
assert.equal(legacy.state, STATE.TRIGGERED);
assert.match(legacy.detail, /Legacy dashboard defaults/);
assert.match(legacy.detail, /Books 33%/);
assert.match(rule(base, 'armed_exit').detail, /deadline is 3\.25h/);
for (const value of [null, undefined, [], {}, 'invalid', { ...config, scale_out_enabled: 'false' },
  { ...config, scale_out_trigger_pct: '' }, { ...config, scale_out_trigger_pct: NaN },
  { ...config, scale_out_trigger_pct: 0.6 }, { ...config, scale_out_fraction: 1 },
  { ...config, scale_out_fraction: '0.33' }, { ...config, scale_out_fraction: Infinity }]) {
  const invalid = rule({ ...base, strategy_exit_config: value }, 'scale_out');
  assert.equal(invalid.state, STATE.DEGRADED);
  assert.equal(invalid.window, 'Unavailable');
  assert.doesNotMatch(invalid.detail, /Books 33%|Legacy dashboard defaults/);
}
for (const value of [null, '', '6', NaN, Infinity, 0, 25]) {
  const invalid = { ...pos, strategy_exit_config: { ...config, armed_exit_deadline_hours: value } };
  assert.equal(rule(invalid, 'armed_exit').state, STATE.DEGRADED);
  assert.match(rule(invalid, 'armed_exit').detail, /missing or invalid/);
  assert.doesNotMatch(rule(invalid, 'armed_exit').detail, /3\.25h/);
  assert.equal(rule(invalid, 'scale_out').state, STATE.WATCH);
}

const armed = { ...pos, exit_armed: true, exit_armed_at: new Date(Date.now() - 3600000).toISOString() };
assert.match(rule(armed, 'armed_exit').headline, /5\.0h until/);
const journey = buildLifecycle(armed, evaluate(armed), 5, 1);
assert.match(journey.next.find((entry) => entry.key === 'armed_resolution').detail, /5\.0h away/);
assert.match(journey.next.find((entry) => entry.key === 'armed_resolution').detail, /effective exit settings/);
for (const invalid of [
  { ...armed, strategy_exit_config: { ...config, armed_exit_deadline_hours: null } },
  { ...armed, exit_armed_at: 'invalid' },
  { ...armed, exit_armed_at: null },
]) {
  assert.match(rule(invalid, 'armed_exit').headline, /deadline unavailable/);
  assert.doesNotMatch(rule(invalid, 'armed_exit').headline, /deadline reached/);
  const next = buildLifecycle(invalid, evaluate(invalid), 5, 1).next.find((entry) => entry.key === 'armed_resolution');
  assert.match(next.detail, /deadline is unavailable/);
  assert.doesNotMatch(next.detail, /deadline has passed|NaN/);
}
assert.equal(positionExitConfig(pos).scaleTrigger, 0.045);
assert.equal(positionExitConfig(base).scaleTrigger, 0.04);
assert.deepEqual(config, {
  armed_exit_deadline_hours: 6, scale_out_enabled: true, scale_out_trigger_pct: 0.045, scale_out_fraction: 0.275,
});
console.log('Runtime exit-rule overrides, disabled scale-out, legacy labels and invalid-value assertions passed.');
