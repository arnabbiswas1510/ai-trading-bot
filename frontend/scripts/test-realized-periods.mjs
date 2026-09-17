#!/usr/bin/env node
/**
 * test-realized-periods.mjs — assertions for calendar bucketing of realised P&L.
 *
 * Follows the `test-exit-details.mjs` idiom: zero-dependency node script wired
 * into `npm run build`, so a boundary regression fails the build.
 *
 * Date bucketing is the classic source of silent, plausible-looking wrong
 * numbers — an off-by-one week boundary does not throw, it just quietly moves
 * money between two cards. Every boundary below is asserted explicitly.
 */
import {
  startOfWeek, startOfMonth, periodBounds, realizedByPeriod, realizedBreakdown,
} from '../src/lib/realizedPeriods.js';

let passed = 0;
const failures = [];

function check(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok ${name}`);
  } catch (err) {
    failures.push({ name, message: err.message });
    console.error(`  x  ${name} — ${err.message}`);
  }
}

function eq(actual, expected, what) {
  if (actual !== expected) {
    throw new Error(`${what || 'value'}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// Local-midnight constructor. Using `new Date('2026-09-17')` would parse as UTC
// and shift the day for anyone west of Greenwich — exactly the bug these tests
// exist to catch, so the fixtures must not contain it.
const local = (y, m, d, h = 12) => new Date(y, m - 1, d, h);

console.log('\n  Realised period bucketing\n');

// ── Week boundary ────────────────────────────────────────────────────────────
check('a Wednesday resolves to the Monday of that week', () => {
  eq(ymd(startOfWeek(local(2026, 9, 16))), '2026-09-14');
});

check('a Monday is its own week start, not the previous week', () => {
  eq(ymd(startOfWeek(local(2026, 9, 14))), '2026-09-14');
});

// The Sunday case is the whole reason for the (getDay()+6)%7 shift. With a naive
// Sunday-indexed calculation this returns the NEXT day, pushing every Sunday
// trade into the following week.
check('a Sunday belongs to the week that is ending, not the one starting', () => {
  eq(ymd(startOfWeek(local(2026, 9, 20))), '2026-09-14');
});

check('week start is local midnight, not midday', () => {
  const s = startOfWeek(local(2026, 9, 16, 23));
  eq(s.getHours(), 0, 'hours');
  eq(s.getMinutes(), 0, 'minutes');
});

// ── Month boundary, including the year wrap ──────────────────────────────────
check('month start is the 1st at local midnight', () => {
  eq(ymd(startOfMonth(local(2026, 9, 17))), '2026-09-01');
});

check('last month crosses the year boundary correctly', () => {
  const [, , , lastMonth] = periodBounds(local(2027, 1, 10));
  eq(ymd(lastMonth.start), '2026-12-01', 'lastMonth.start');
  eq(ymd(lastMonth.end), '2027-01-01', 'lastMonth.end');
});

// ── Windows are half-open, and adjacent ones do not overlap ──────────────────
check('last week ends exactly where this week begins', () => {
  const [thisWeek, lastWeek] = periodBounds(local(2026, 9, 17));
  eq(lastWeek.end.getTime(), thisWeek.start.getTime());
});

check('this week runs a full 7 days, not up to "now"', () => {
  const [thisWeek] = periodBounds(local(2026, 9, 17));
  eq((thisWeek.end - thisWeek.start) / 86400000, 7);
});

// ── Bucketing ────────────────────────────────────────────────────────────────
const trade = (sell_date, pnl, fees = true) => ({
  sell_date,
  profit_loss: pnl,
  net_profit_loss: pnl,
  buy_commission: fees ? 1 : null,
  sell_commission: fees ? 1 : null,
  commission_complete: fees,
});

check('trades land in the correct week bucket', () => {
  const now = local(2026, 9, 17);              // Thursday; week began Mon 14th
  const trades = [
    trade('2026-09-15T15:00:00', 100),          // this week
    trade('2026-09-17T10:00:00', 50),           // this week
    trade('2026-09-11T15:00:00', -30),          // last week
    trade('2026-08-20T15:00:00', 999),          // last month
  ];
  const [thisWeek, lastWeek, thisMonth, lastMonth] = realizedByPeriod(trades, now);
  eq(thisWeek.total, 150, 'thisWeek');
  eq(thisWeek.count, 2, 'thisWeek.count');
  eq(lastWeek.total, -30, 'lastWeek');
  eq(thisMonth.total, 120, 'thisMonth');        // 100 + 50 - 30, all September
  eq(lastMonth.total, 999, 'lastMonth');
});

// A Monday trade is the boundary value: it must be in THIS week and absent from
// last week. Getting this wrong double-counts or drops a whole day.
check('a Monday-close sits in this week and not last week', () => {
  const now = local(2026, 9, 17);
  const [thisWeek, lastWeek] = realizedByPeriod([trade('2026-09-14T09:31:00', 42)], now);
  eq(thisWeek.total, 42, 'thisWeek');
  eq(lastWeek.total, 0, 'lastWeek');
  eq(lastWeek.count, 0, 'lastWeek.count');
});

check('an empty bucket is complete, not provisional', () => {
  const [thisWeek] = realizedByPeriod([], local(2026, 9, 17));
  eq(thisWeek.total, 0, 'total');
  eq(thisWeek.complete, true, 'complete');
});

check('one fee-less trade makes its whole bucket provisional', () => {
  const now = local(2026, 9, 17);
  const [thisWeek] = realizedByPeriod(
    [trade('2026-09-15T15:00:00', 100), trade('2026-09-16T15:00:00', 10, false)], now,
  );
  eq(thisWeek.complete, false);
});

check('a null or unparseable sell_date is excluded rather than counted as now', () => {
  const now = local(2026, 9, 17);
  const rows = [{ ...trade('2026-09-15T15:00:00', 100), sell_date: null },
                { ...trade('2026-09-15T15:00:00', 100), sell_date: 'not-a-date' }];
  const [thisWeek] = realizedByPeriod(rows, now);
  eq(thisWeek.count, 0);
});

// ── Gross / fee / net split ──────────────────────────────────────────────────
check('breakdown separates gross, commission and net', () => {
  const b = realizedBreakdown([
    { profit_loss: 100, total_commission: 2, buy_commission: 1, sell_commission: 1 },
    { profit_loss: -50, total_commission: 1, buy_commission: null, sell_commission: 1 },
  ]);
  eq(b.gross, 50, 'gross');
  eq(b.commission, 3, 'commission');
  eq(b.net, 47, 'net');
  eq(b.feeLegsTotal, 4, 'feeLegsTotal');
  eq(b.feeLegsMissing, 1, 'feeLegsMissing');
  eq(b.complete, false, 'complete');
});

// The live defect this whole change exists to fix: before the backend passed the
// commission columns through, every trade looked fee-complete with zero fees and
// the dashboard reported the GROSS figure as though it were net.
check('a missing fee leg is never treated as a zero fee', () => {
  const b = realizedBreakdown([{ profit_loss: 100, buy_commission: null, sell_commission: null }]);
  eq(b.commission, 0, 'commission');
  eq(b.complete, false, 'complete');
  eq(b.feeLegsMissing, 2, 'feeLegsMissing');
});

// ── Result ───────────────────────────────────────────────────────────────────
console.log(`\n    ${passed} passed / ${failures.length} failed\n`);
if (failures.length > 0) {
  console.error('  REALISED PERIOD TESTS FAILED:');
  for (const f of failures) console.error(`     • ${f.name}: ${f.message}`);
  process.exit(1);
}
