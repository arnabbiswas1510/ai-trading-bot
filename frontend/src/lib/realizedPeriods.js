/**
 * Calendar bucketing for realised P&L.
 *
 * Realised P&L is attributed to the date the position was CLOSED (`sell_date`),
 * not the date it was opened. A trade bought in August and sold this Monday is
 * this week's result — that is what "realised" means, and it is the only
 * attribution under which the four buckets sum back to the all-time total.
 *
 * Boundaries are computed in the browser's local timezone, deliberately. The
 * user reads this dashboard from one place and means "the Monday I lived
 * through". Anchoring to America/New_York would be defensible for a market
 * calendar, but these are accounting buckets, not trading sessions, and a
 * UTC-anchored week silently shifts Sunday-evening trades into the wrong bucket.
 *
 * Weeks start MONDAY. JavaScript's getDay() is Sunday-indexed (0=Sun), so the
 * shift below maps Sunday onto offset 6 rather than 0 — without it, every
 * Sunday would be treated as the start of the coming week instead of the tail
 * of the one that just ended.
 */

import { netPnL, isCommissionComplete } from './commissions.js';

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

/** Most recent Monday at 00:00 local, inclusive of `d` when `d` is a Monday. */
export function startOfWeek(d) {
  const day = startOfDay(d);
  const offset = (day.getDay() + 6) % 7;   // Mon=0 … Sun=6
  day.setDate(day.getDate() - offset);
  return day;
}

export const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);

const addDays = (d, n) => {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
};

const addMonths = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);

/**
 * The four reporting windows, as half-open intervals [start, end).
 *
 * Half-open matters: `thisWeek.end` is the start of next week rather than "now",
 * so a trade closing later today still lands in the bucket. Using `now` as the
 * end would make the figure quietly change meaning depending on when the page
 * was loaded.
 */
export function periodBounds(now = new Date()) {
  const weekStart = startOfWeek(now);
  const monthStart = startOfMonth(now);
  return [
    { key: 'thisWeek',  label: 'This Week',  sub: 'Mon to date', start: weekStart,              end: addDays(weekStart, 7) },
    { key: 'lastWeek',  label: 'Last Week',  sub: 'Mon–Sun',     start: addDays(weekStart, -7), end: weekStart },
    { key: 'thisMonth', label: 'This Month', sub: 'MTD',         start: monthStart,             end: addMonths(monthStart, 1) },
    { key: 'lastMonth', label: 'Last Month', sub: 'full month',  start: addMonths(monthStart, -1), end: monthStart },
  ];
}

const sellTime = (trade) => {
  if (!trade?.sell_date) return null;
  const t = new Date(trade.sell_date).getTime();
  return Number.isNaN(t) ? null : t;
};

/**
 * Realised net P&L per window.
 *
 * Each bucket reports `complete: false` when any contributing trade is missing
 * commission data, so a provisional total is never presented as final. An empty
 * bucket is `complete: true` with `total: 0` — nothing unknown is in it.
 */
export function realizedByPeriod(trades = [], now = new Date()) {
  return periodBounds(now).map((p) => {
    const from = p.start.getTime();
    const to = p.end.getTime();
    const inWindow = trades.filter((t) => {
      const ts = sellTime(t);
      return ts !== null && ts >= from && ts < to;
    });
    return {
      ...p,
      count: inWindow.length,
      total: inWindow.reduce((sum, t) => sum + netPnL(t), 0),
      complete: inWindow.every(isCommissionComplete),
    };
  });
}

/**
 * Gross / commission / net split for a set of trades.
 *
 * `feeLegsMissing` is the honest measure of how provisional the net is: it
 * counts individual legs, not trades, because a trade with a buy fee but no
 * sell fee is still only half-known.
 */
export function realizedBreakdown(trades = []) {
  let gross = 0;
  let commission = 0;
  let feeLegsMissing = 0;
  let feeLegsTotal = 0;

  for (const t of trades) {
    gross += t?.profit_loss ?? 0;
    commission += t?.total_commission ?? 0;
    for (const leg of [t?.buy_commission, t?.sell_commission]) {
      feeLegsTotal += 1;
      if (leg === null || leg === undefined) feeLegsMissing += 1;
    }
  }

  return {
    gross,
    commission,
    net: gross - commission,
    feeLegsTotal,
    feeLegsMissing,
    complete: feeLegsTotal > 0 && feeLegsMissing === 0,
  };
}
