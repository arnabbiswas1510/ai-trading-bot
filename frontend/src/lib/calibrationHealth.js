export const healthLabels = {
  pass: 'PASS - evidence available',
  wait: 'WAIT - not complete',
  fail: 'FAIL - action needed',
  unknown: 'UNKNOWN - unable to verify',
};

export const healthScopes = {
  session: 'Selected trading session',
  current: 'Current state',
  next_session: 'Preparation for the next session',
};

export const healthCheckIds = [
  'screening-session-inputs', 'screening-next-session', 'trigger-freshness',
  'source-coverage', 'report-delivery', 'shadow-inputs', 'observer-worker',
  'shadow-worker', 'calibration-worker', 'calibration-policy', 'calibration-campaign',
];

function validDay(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function validTimestamp(value) {
  return typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

export function dailyHealthIssue(report, requestedDate = '') {
  if (!report || report.schema_version !== 1 || report.timezone !== 'America/New_York'
      || !validTimestamp(report.checked_at) || !validDay(report.session_date)
      || typeof report.session_complete !== 'boolean'
      || !Object.hasOwn(healthLabels, report.overall_status)
      || !Array.isArray(report.checks) || !report.checks.length
      || !Array.isArray(report.limitations)
      || report.limitations.some((value) => typeof value !== 'string')) {
    return 'Daily health evidence is incomplete or unreadable. No successful result is inferred.';
  }
  if (requestedDate && report.session_date !== requestedDate) {
    return 'The returned report is for a different session. Run the check again.';
  }
  const ids = new Set();
  for (const check of report.checks) {
    if (!check || typeof check.id !== 'string' || !check.id || ids.has(check.id)
        || typeof check.title !== 'string' || !check.title
        || !Object.hasOwn(healthLabels, check.status) || !Object.hasOwn(healthScopes, check.scope)
        || typeof check.summary !== 'string' || !check.summary
        || typeof check.next_step !== 'string'
        || (check.status !== 'pass' && !check.next_step.trim())
        || !(check.evidence_at === null || validTimestamp(check.evidence_at))) {
      return 'A health-check stage has missing or invalid evidence. No successful result is inferred.';
    }
    ids.add(check.id);
  }
  if (healthCheckIds.some((id) => !ids.has(id))) {
    return 'Required pipeline stages are missing from the report. No successful result is inferred.';
  }
  const statuses = new Set(report.checks.map((check) => check.status));
  const expected = statuses.has('fail') ? 'fail' : statuses.has('unknown') ? 'unknown'
    : statuses.has('wait') || !report.session_complete ? 'wait' : 'pass';
  return report.overall_status === expected ? null
    : 'The overall result contradicts its stage results. No successful result is inferred.';
}

export async function loadDailyHealth(requester, sessionDate = '', signal) {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : '';
  const report = await requester(`/api/calibration/daily-health${query}`,
    { method: 'GET', cache: 'no-store', signal }, 60000);
  const issue = dailyHealthIssue(report, sessionDate);
  if (issue) throw new Error(issue);
  return report;
}

export function healthTime(value) {
  if (!validTimestamp(value)) return 'Not recorded';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value));
}
