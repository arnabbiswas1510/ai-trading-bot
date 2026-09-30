export function recordedSessions(sessions) {
  if (!Array.isArray(sessions)) return [];
  return sessions.filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row?.session)
    && finiteNumber(row.frames) > 0).sort((a, b) => a.session.localeCompare(b.session));
}

export function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function maxReplayDays(value) {
  return Number.isInteger(value) && value > 0 ? value : 93;
}

export function replayValidation(sessions, start, end, maxDays) {
  const dates = new Set(recordedSessions(sessions).map((row) => row.session));
  if (!dates.size) return 'No recorded sessions yet. Capture must collect point-in-time inputs first.';
  if (!dates.has(start) || !dates.has(end)) return 'Select both dates from recorded sessions.';
  if (start > end) return 'End session must be on or after the start session.';
  const limit = maxReplayDays(maxDays);
  if ((Date.parse(end) - Date.parse(start)) / 86400000 + 1 > limit) {
    return `Each comparison is limited to ${limit} calendar days to bound worker memory. Evaluate the retained 12 months as multiple windows, each starting from its own actual recorded account snapshot.`;
  }
  const selected = sessions.find((row) => row.session === start);
  if (selected?.initial_snapshot != null) return snapshotValidation(selected.initial_snapshot);
  return null;
}

export function snapshotValidation(snapshot) {
  if (!snapshot || snapshot.complete !== true) {
    return 'Actual starting portfolio snapshot is missing or incomplete. Capture existing positions, prior trades, cooldown state and protective orders before replaying; an empty portfolio will not be substituted.';
  }
  if (finiteNumber(snapshot.equity) === null || finiteNumber(snapshot.cash) === null) {
    return 'Starting equity or cash was not recorded. Replay cannot replace the account with synthetic capital.';
  }
  for (const key of ['positions', 'prior_trades', 'cooldown_ledger', 'protective_orders']) {
    if (!Array.isArray(snapshot[key])) return `Starting snapshot is missing ${key.replaceAll('_', ' ')}. No financial comparison is available.`;
  }
  return null;
}

export function resultSnapshotIssue(result) {
  if (result?.initial_state_mode && result.initial_state_mode !== 'recorded_actual_portfolio') {
    return 'This run does not start from the recorded actual portfolio. No financial comparison is available.';
  }
  if (result?.initial_snapshot != null) return snapshotValidation(result.initial_snapshot);
  if (result?.initial_state_summary?.complete === false) return 'The actual starting account snapshot is incomplete. No financial comparison is available.';
  if (result?.effective_config?.scope?.initial_state === 'flat_no_recent_sales') {
    return 'This saved run used a synthetic empty portfolio, not your recorded existing account. Run a new actual-portfolio comparison.';
  }
  // A summary is optional presentation metadata; the backend validates the full
  // initial state before it can mark an actual-account replay completed.
  return null;
}

export function initialAccountView(result) {
  const summary = result?.initial_state_summary;
  const account = result?.effective_initial_account;
  const positions = result?.baseline?.initial_positions ?? summary?.positions;
  return {
    equity: finiteNumber(account?.net_liquidation ?? result?.baseline?.initial_equity ?? summary?.equity ?? summary?.initial_equity),
    cash: finiteNumber(result?.effective_initial_cash ?? summary?.cash),
    positions: Array.isArray(positions) ? positions : null,
    positionsCount: Array.isArray(positions) ? positions.length : finiteNumber(summary?.positions_count),
    timestamp: result?.initial_snapshot_at ?? summary?.observed_at,
    account,
    summary,
  };
}

export async function pollReplay(initial, { fetchRun, wait, onUpdate, signal, now = Date.now }) {
  const deadline = now() + 3600000;
  let run = initial;
  while (run.status === 'running' && now() < deadline) {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    if (!run.id) throw new Error('Running comparison has no saved run ID. Refresh the comparison list.');
    onUpdate(run);
    await wait(signal);
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    if (now() >= deadline) break;
    run = await fetchRun(run.id, signal);
  }
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (run.status === 'running') {
    throw new Error('Comparison is still running after one hour. Automatic waiting has stopped; the server may continue. Open the saved run to check again.');
  }
  return run;
}

export function apiErrorText(value, fallback = 'The request failed.') {
  if (typeof value === 'string' && value.trim()) return value;
  if (Array.isArray(value)) return value.map((item) => apiErrorText(item, '')).filter(Boolean).join('; ') || fallback;
  if (value && typeof value === 'object') {
    if (value.detail != null) return apiErrorText(value.detail, fallback);
    if (value.msg != null) return apiErrorText(value.msg, fallback);
    if (value.error != null) return apiErrorText(value.error, fallback);
    if (value.message != null) return apiErrorText(value.message, fallback);
  }
  return fallback;
}

export function comparisonResult(response) {
  if (!response || (response.status && response.status !== 'completed')) return null;
  const result = response.result ?? response;
  return result && typeof result === 'object' && (result.baseline || result.variant) ? result : null;
}

export function equityDifference(result) {
  const baseline = finiteNumber(result?.baseline?.final_equity_net);
  const variant = finiteNumber(result?.variant?.final_equity_net);
  // Never turn missing marks into a zero-value portfolio or a profitable comparison.
  return baseline === null || variant === null ? null : variant - baseline;
}

export function portfolioMetrics(portfolio) {
  return {
    initialEquity: finiteNumber(portfolio?.initial_equity),
    equity: finiteNumber(portfolio?.final_equity_net),
    profit: finiteNumber(portfolio?.net_profit),
    commission: finiteNumber(portfolio?.commission),
    slippage: finiteNumber(portfolio?.slippage_cost),
    fills: Array.isArray(portfolio?.fills) ? portfolio.fills.length : null,
    openPositions: Array.isArray(portfolio?.open_positions) ? portfolio.open_positions.length : null,
    drawdown: finiteNumber(portfolio?.max_drawdown_pct),
    closedPositions: finiteNumber(portfolio?.closed_positions) ?? finiteNumber(portfolio?.closed_position_count),
    sessions: finiteNumber(portfolio?.sessions_count),
  };
}

export function captureCoverage(sessions) {
  const rows = recordedSessions(sessions);
  if (!rows.length) return { sessions: 0, calendarDays: 0, completeFrames: null, missingQuotes: null };
  const sum = (key) => rows.every((row) => finiteNumber(row[key]) !== null)
    ? rows.reduce((total, row) => total + row[key], 0) : null;
  return {
    sessions: rows.length,
    calendarDays: Math.floor((Date.parse(rows.at(-1).session) - Date.parse(rows[0].session)) / 86400000) + 1,
    completeFrames: sum('complete_frames'),
    missingQuotes: sum('missing_quotes'),
  };
}
