export function describeTradingControl(status, now = Date.now()) {
  if (!status || status.error) {
    return { label: 'Trading status unavailable', confirmed: false };
  }
  const agent = status.agent;
  const lastSeen = Date.parse(agent?.last_seen);
  const age = now - lastSeen;
  if (!Number.isFinite(lastSeen) || age < -5000 || age > 90000) {
    return { label: 'Execution agent unconfirmed / stale', confirmed: false };
  }
  if (agent.observed_revision !== status.revision) {
    return { label: 'Setting saved; awaiting agent confirmation', confirmed: false };
  }
  if (agent.broker_connected !== true) {
    return { label: 'Broker disconnected; protection unconfirmed', confirmed: false };
  }
  return {
    label: status.live_entries_enabled
      ? 'ACTIVE: new real buys permitted'
      : 'INACTIVE: new real buys blocked (protect-only)',
    confirmed: true,
  };
}
