/**
 * volatilityFit.js — UI mirror of the `volatility_fit()` / `est_days_to_lock()`
 * pair in scoring.py. Deliberate duplication: the backend module cannot be
 * imported into the bundle, so this file must be updated in lockstep with
 * scoring.py. frontend/scripts/verify-build.mjs guards the constants.
 *
 * The bot has NO profit target. It arms a profit lock at +5% and trails 1.5%
 * below the high-water mark thereafter, and its entry stop is 2.5 x ATR clamped
 * to a 10%-12% band. So:
 *   - "days to target" measures the +5% lock, not a +25% run the bot would
 *     never hold for.
 *   - Because the stop CAPS at 12%, room measured in the stock's own daily
 *     range SHRINKS as ATR rises. Past ~4.8%/day a position holds under 2.5 ATR
 *     of room and is routinely stopped out inside 1-2 sessions.
 *
 * See decisions/2026-08-24_ai-evaluator-volatility-fit.md.
 */

export const ATR_STOP_CAP_PCT = 4.8;
export const ATR_COMFORT_LOW = 1.5;
export const PROFIT_LOCK_PCT = 5.0;

export function estDaysToLock(atrPct) {
  const atr = Number(atrPct);
  if (!Number.isFinite(atr) || atr <= 0) return 999;
  return Math.round(PROFIT_LOCK_PCT / atr);
}

export function volatilityFit(atrPct) {
  const atr = Number(atrPct);
  if (!Number.isFinite(atr) || atr <= 0) {
    return { emoji: '❓', label: 'Unknown volatility', tone: 'unknown', color: 'var(--text-muted)' };
  }
  if (atr > ATR_STOP_CAP_PCT) {
    return {
      emoji: '⚠️',
      label: 'Too volatile — under 2.5 ATR of room inside the 12% stop cap',
      tone: 'bad',
      color: '#f43f5e',
    };
  }
  if (atr >= ATR_COMFORT_LOW) {
    return { emoji: '✅', label: 'Good volatility fit', tone: 'good', color: '#10b981' };
  }
  return {
    emoji: '⚠️',
    label: 'Quiet — may not reach the +5% lock before day-7 rotation',
    tone: 'warn',
    color: '#f59e0b',
  };
}
