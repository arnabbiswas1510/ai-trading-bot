import React from 'react';
import { finiteNumber } from '../lib/intradayReplay.js';
import { riskMetricsView } from '../lib/calibrationDashboard.js';

const muted = { color: 'var(--text-muted)', fontSize: '0.85rem', lineHeight: 1.65 };
const rows = [
  ['sharpe', 'Sharpe ratio', 'ratio', 'Average daily return above the lagged Treasury cash proxy, divided by daily excess-return volatility; annualized.'],
  ['sortino', 'Sortino ratio', 'ratio', 'Excess return relative to downside deviation below the same Treasury reference; annualized.'],
  ['calmar', 'Calmar ratio (window estimate)', 'ratio', 'Extrapolated annualized geometric return divided by maximum drawdown on the same session-final series. Not the conventional 36-month Calmar history.'],
  ['annualized_volatility_pct', 'Annualized daily volatility', 'percent', 'Sample standard deviation of session-to-session returns, scaled by the square root of 252.'],
  ['max_sampled_drawdown_pct', 'Full-window sampled maximum drawdown', 'percent', 'Largest equity drop from a previous sampled peak, including the starting portfolio and intraday marks. Not an unobserved intrabar maximum.'],
  ['session_max_drawdown_pct', 'Session-final maximum drawdown', 'percent', 'Largest decline on the daily series used by Calmar; excludes the opening partial interval.'],
  ['annualized_return_pct', 'Annualized growth extrapolation', 'percent', 'Mathematical extrapolation over 252 return periods, not an observed annual return or a forecast. Short windows can produce extreme values.'],
  ['daily_window_return_pct', 'Compounded return over daily-metric window', 'percent', 'Growth from the first terminal session mark to the last; can differ from full-window profit.'],
  ['worst_day_pct', 'Worst sampled daily return', 'percent', 'Worst complete session-to-session return, including modeled costs.'],
  ['best_day_pct', 'Best sampled daily return', 'percent', 'Best complete session-to-session return, including modeled costs.'],
  ['var_95_pct', 'Historical daily loss threshold (95% VaR)', 'percent', 'Empirical lower-tail loss estimate. At least 20 daily returns; a negative value means even this historical tail was a gain. Not a guaranteed maximum loss.'],
  ['expected_shortfall_95_pct', 'Historical tail loss (95% expected shortfall)', 'percent', 'Average signed loss in the empirical worst 5% tail; negative means a gain. Sparse tails remain highly uncertain.'],
  ['max_sampled_underwater_days', 'Longest sampled time below a peak (calendar days)', 'ratio', 'Time from a previous equity peak until sampled recovery, or the window end if still unrecovered.'],
  ['profit_factor', 'Completed-position profit factor', 'ratio', 'Gross positive net outcomes divided by absolute negative net outcomes; partial sales are grouped, and no loss denominator is undefined.'],
  ['win_rate_pct', 'Completed-position win rate', 'percent', 'Positive grouped completed outcomes as a share of eligible completed outcomes; positions already open at the window start are excluded.'],
  ['expectancy_usd', 'Mean completed-position outcome', 'money', 'Average net dollars per grouped completed position opened within this window; not a forecast of the next trade.'],
];

function valueText(value, unit) {
  if (finiteNumber(value) === null) return 'Unavailable';
  if (unit === 'money') return value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
  return `${value.toFixed(2)}${unit === 'percent' ? '%' : ''}`;
}

function Measurement({ evidence, metric, unit }) {
  const value = evidence?.metrics?.[metric];
  const reason = evidence?.unavailable?.[metric] ?? evidence?.unavailable?.all;
  return <td>{valueText(value, unit)}
    {finiteNumber(value) === null && <p style={muted}>{reason || 'Required observations are not available.'}</p>}
  </td>;
}

export default function CalibrationRiskMetrics({ proposal, phase }) {
  const evidence = riskMetricsView(proposal, phase);
  if (!evidence) return <section aria-label="Risk-adjusted calibration metrics">
    <h4>Risk-adjusted metrics</h4>
    <p style={muted}>No recorded risk analytics for this window yet. Older artifacts are not retroactively rewritten.
      New worker results include daily-return and Treasury evidence; unavailable ratios are never inferred from aggregate profit.</p>
  </section>;
  if (evidence.error) return <section aria-label="Risk-adjusted calibration metrics">
    <h4>Risk-adjusted metrics unavailable</h4><p role="alert">{evidence.error}</p>
  </section>;
  const reference = evidence.reference_snapshot;
  const warnings = [...new Set([...(evidence.baseline.warnings ?? []), ...(evidence.candidate.warnings ?? []),
    ...(reference?.warnings ?? [])])];
  return <section aria-label="Risk-adjusted calibration metrics" data-calibration-risk-metrics>
    <h4>Risk-adjusted metrics: recorded rules versus frozen candidate</h4>
    <p style={muted}>One observation per terminal recorded session, not per intraday tick or per trade.
      The opening partial interval is excluded. Five sessions therefore provide only four daily returns.
      Standard square-root-of-time scaling does not correct for serial correlation between days.
      These diagnostics do not change automatic selection or approval requirements.</p>
    <p><strong>Annualized ratios are estimates, not calibrated guarantees.</strong> Short windows are exploratory;
      zero volatility or zero drawdown makes the corresponding ratio undefined, not infinite.</p>
    <p style={muted}>Recorded rules: {evidence.baseline.sample?.sessions ?? 'Unavailable'} session marks /
      {' '}{evidence.baseline.sample?.daily_returns ?? 'Unavailable'} daily returns.
      {' '}Candidate: {evidence.candidate.sample?.sessions ?? 'Unavailable'} session marks /
      {' '}{evidence.candidate.sample?.daily_returns ?? 'Unavailable'} daily returns.</p>
    <p style={muted}>Risk-free reference: {reference?.source ?? 'Unavailable'}.
      {' '}Retrieved: {reference?.retrieved_at ?? 'Unavailable'}.
      Historical three-month yields approximate cash accrual; they are not realized Treasury investment returns.</p>
    {reference?.status !== 'available' && <p role="alert">Treasury data unavailable: {reference?.error ?? 'No reference snapshot'}.
      Sharpe and Sortino do not silently fall back to 0%.</p>}
    {evidence.retry_error && <p role="alert">Automatic diagnostic retry stopped: {evidence.retry_error}</p>}
    {proposal.artifact.risk_analytics.retry_pending && <p style={muted}>
      Missing Treasury data is queued for a later worker cycle while research is enabled.
      Approved, deferred and rejected proposals are not automatically rewritten.</p>}
    <div className="table-container"><table>
      <thead><tr><th>Metric and interpretation</th><th>Recorded rules</th><th>Frozen candidate</th></tr></thead>
      <tbody>{rows.map(([metric, title, unit, meaning]) => <tr key={metric}>
        <th scope="row">{title}<p style={{ ...muted, fontWeight: 400 }}>{meaning}</p></th>
        <Measurement evidence={evidence.baseline} metric={metric} unit={unit} />
        <Measurement evidence={evidence.candidate} metric={metric} unit={unit} />
      </tr>)}</tbody>
    </table></div>
    {warnings.map((warning) => <p key={warning} style={muted}>Caution: {warning}</p>)}
    <details><summary>Calculation assumptions and reproducible daily evidence</summary>
      <p style={muted}>Inspect the exact sampled timestamps, daily returns, rate dates and frozen source hashes.
        Daily metrics and full-window metrics use different explicitly labelled starting points.
        The complete evidence is included in the campaign JSON download.</p>
      <pre style={{ ...muted, maxHeight: 380, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {JSON.stringify({ phase: evidence.phase, input_sha256: evidence.input_sha256,
          analytics_fingerprint: evidence.analytics_fingerprint, reference_snapshot: reference,
          baseline: evidence.baseline, candidate: evidence.candidate }, null, 2)}
      </pre>
    </details>
  </section>;
}
