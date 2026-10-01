import assert from 'node:assert/strict';
import { describeTradingControl } from '../src/lib/tradingControl.js';

const now = Date.parse('2026-10-01T15:00:00Z');
const status = {
  live_entries_enabled: false,
  revision: 3,
  agent: { last_seen: '2026-10-01T14:59:50Z', observed_revision: 3, broker_connected: true },
};
assert.match(describeTradingControl(status, now).label, /^INACTIVE/);
assert.match(describeTradingControl({ ...status, live_entries_enabled: true }, now).label, /^ACTIVE/);
for (const value of [
  null,
  { ...status, error: 'control unavailable' },
  { ...status, agent: null },
  { ...status, revision: 4 },
  { ...status, agent: { ...status.agent, broker_connected: false } },
  { ...status, agent: { ...status.agent, last_seen: '2026-10-01T14:00:00Z' } },
  { ...status, agent: { ...status.agent, last_seen: '2026-10-01T16:00:00Z' } },
]) {
  assert.equal(describeTradingControl(value, now).confirmed, false);
}
assert.equal(describeTradingControl(status, now + 91000).confirmed, false);
console.log('Trading-control status assertions passed.');
