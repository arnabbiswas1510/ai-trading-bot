#!/usr/bin/env node
/**
 * test-exit-details.mjs — assertions for the shared exit classifier.
 *
 * The frontend has no test framework, and adding one for a single module is
 * not worth the dependency. This follows the existing `verify-build.mjs`
 * idiom: a zero-dependency node script wired into `npm run build`, so a
 * regression fails the build rather than reaching the dashboard.
 *
 * Every case below is drawn from a real `sell_reason` string in
 * `trade_history`, not invented, so the fixtures stay honest about what the
 * agent actually writes.
 */
import {
  classifyExit, extractReasonFacts, unrecordedFields, deriveTradeMath, holdDays,
} from "../src/lib/exitDetails.js";

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
    throw new Error(`${what || "value"}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

console.log("\n  Exit classifier\n");

// ── The regression this module exists to prevent ─────────────────────────────
// PTGX really closed at -2.77% on a broker trailing stop. The previous UI
// labelled it "Stop Loss (-7%)" because it branched on the return percentage
// and anything under +24% fell into the stop bucket. The -7% was invented, and
// so was the +25% profit target above it — that rule no longer exists in the bot.
check("broker trail exit is not labelled with an invented stop percentage", () => {
  const c = classifyExit("Trailing stop (IBKR GTC TRAIL order)");
  eq(c.label, "Trailing Stop", "label");
  if (/\d+(\.\d+)?%/.test(c.label)) {
    throw new Error(`label quotes a percentage it cannot know: "${c.label}"`);
  }
});

check("no exit is ever attributed to a profit target", () => {
  const reasons = [
    "Trailing stop (IBKR GTC TRAIL order)",
    "Order filled",
    "reconciled",
    "Manual close in IBKR (reconciled) — PRICE_UNCERTAIN",
  ];
  for (const r of reasons) {
    const c = classifyExit(r);
    if (/profit target/i.test(c.label) || /profit target/i.test(c.what)) {
      throw new Error(`"${r}" was attributed to a removed profit-target rule`);
    }
  }
});

check("classification is identical regardless of the trade's P&L", () => {
  // The old helper took pctReturn and branched on it; this asserts the
  // signature change actually removed that coupling.
  const a = classifyExit("Trailing stop (IBKR GTC TRAIL order)");
  const b = classifyExit("Trailing stop (IBKR GTC TRAIL order)");
  eq(a.label, b.label, "label");
  eq(classifyExit.length, 1, "classifyExit arity");
});

// ── Executor attribution ─────────────────────────────────────────────────────
check("IBKR resting order attributes to the broker, not the bot", () => {
  eq(classifyExit("Trailing stop (IBKR GTC TRAIL order)").executor.key, "BROKER");
});

check("agent rules attribute to the bot", () => {
  eq(classifyExit("Intraday Loss Minimiser — Day 3 universal rule, selling on 0.5% pullback from intraday high $247.99 (current $245.81)").executor.key, "BOT");
  eq(classifyExit("Rank & Replace Swap (Day 7+) — replaced with superior breakout APH (New trigger: 79 vs held Mₜ=41.9)").executor.key, "BOT");
});

check("human closes attribute to manual", () => {
  eq(classifyExit("Manual close in IBKR (reconciled) — PRICE_UNCERTAIN").executor.key, "MANUAL");
  eq(classifyExit("manual_rotation").executor.key, "MANUAL");
});

check("an empty reason is reported as unrecorded, not guessed", () => {
  const c = classifyExit(null);
  eq(c.label, "Unrecorded");
  eq(c.executor.key, "UNKNOWN");
});

// ── Rule precedence ──────────────────────────────────────────────────────────
// The generic trailing-stop pattern sits near the end of the table on purpose:
// several named agent rules mention a trailing stop in passing and must not be
// swallowed by it.
check("named agent rules win over the generic trailing-stop pattern", () => {
  eq(classifyExit("Early Dollar Stop breached; trailing stop not yet armed").label, "Early Dollar Stop");
  eq(classifyExit("Thesis stop hit before the trailing stop could arm").label, "Thesis Stop");
  eq(classifyExit("Early loss kill-switch fired ahead of the trailing stop").label, "Early Loss Kill-switch");
});

check("specific patterns win over the substrings they contain", () => {
  eq(classifyExit("Early Dollar Stop").label, "Early Dollar Stop");
});

// ── Fact extraction ──────────────────────────────────────────────────────────
check("intraday minimiser facts are read back out of the reason text", () => {
  const facts = extractReasonFacts(
    "Intraday Loss Minimiser — Day 3 verdict FAIL, selling on 0.5% pullback from intraday high $357.54 (current $355.27)"
  );
  const byLabel = Object.fromEntries(facts.map((f) => [f.label, f.value]));
  eq(byLabel["Intraday high"], 357.54);
  eq(byLabel["Price when sold"], 355.27);
  eq(byLabel["Day of hold"], 3);
  eq(byLabel["Day-3 verdict"], "FAIL");
  const pullback = byLabel["Pullback from high"];
  if (!(pullback < 0 && pullback > -1)) {
    throw new Error(`pullback should be a small negative percent, got ${pullback}`);
  }
});

check("rank & replace facts include both scores and the replacement", () => {
  const facts = extractReasonFacts(
    "Rank & Replace Swap (Day 7+) — replaced with superior breakout APH (New trigger: 79 vs held Mₜ=41.9)"
  );
  const byLabel = Object.fromEntries(facts.map((f) => [f.label, f.value]));
  eq(byLabel["Rotated into"], "APH");
  eq(byLabel["New trigger score"], 79);
  eq(byLabel["Held momentum score"], 41.9);
});

check("a bare broker reason yields no facts rather than fabricated ones", () => {
  eq(extractReasonFacts("Trailing stop (IBKR GTC TRAIL order)").length, 0);
});

// ── Round-trip with the agent's own output ───────────────────────────────────
// This exact string is what _exit_context_suffix() in execution_agent.py now
// appends. If either side's format drifts, this fails rather than silently
// degrading the panel back to "not recorded".
const AGENT_TRAIL_REASON =
  "Trailing stop (IBKR GTC TRAIL order) — trail 10.00%, HWM $52.02 set 2026-08-21, " +
  "implied trigger $46.82, day 2 of hold, peak +4.30%, armed at $51.70 (profit lock +5%), power hold active";

check("the agent's recorded risk state parses back out in full", () => {
  const byLabel = Object.fromEntries(extractReasonFacts(AGENT_TRAIL_REASON).map((f) => [f.label, f.value]));
  eq(byLabel["Trail in force"], 10, "trail");
  eq(byLabel["High-water mark"], 52.02, "hwm");
  eq(byLabel["Peak set on"], "2026-08-21", "hwm date");
  eq(byLabel["Implied trigger"], 46.82, "trigger");
  eq(byLabel["Day of hold"], 2, "day");
  eq(byLabel["Peak unrealised"], 4.3, "peak");
  eq(byLabel["Exit armed at"], 51.7, "armed price");
  eq(byLabel["Power hold"], "Active", "power hold");
});

check("context in the reason does not change who gets the credit", () => {
  const c = classifyExit(AGENT_TRAIL_REASON);
  eq(c.label, "Trailing Stop", "label");
  eq(c.executor.key, "BROKER", "executor");
});

check("a fully recorded broker exit reports nothing as missing", () => {
  const trade = {
    exit_reason: AGENT_TRAIL_REASON,
    buy_date: "2026-08-19T13:46:04Z",
    sell_date: "2026-08-21T15:30:00Z",
  };
  eq(unrecordedFields(trade, classifyExit(trade.exit_reason)).length, 0);
});

// ── Honest disclosure of missing data ────────────────────────────────────────
check("broker exits declare the trail numbers as unrecorded", () => {
  const trade = {
    exit_reason: "Trailing stop (IBKR GTC TRAIL order)",
    buy_date: "2026-08-13T13:45:50Z",
    sell_date: "2026-08-18T00:00:00Z",
  };
  const missing = unrecordedFields(trade, classifyExit(trade.exit_reason));
  const joined = missing.join(" | ").toLowerCase();
  for (const term of ["trail percentage", "high-water mark", "trigger price"]) {
    if (!joined.includes(term)) throw new Error(`missing disclosure of "${term}"`);
  }
});

check("agent exits with a real timestamp declare nothing missing", () => {
  const trade = {
    exit_reason: "Rank & Replace Swap (Day 7+) — replaced with superior breakout APH (New trigger: 79 vs held Mₜ=41.9)",
    buy_date: "2026-07-30T14:03:43Z",
    sell_date: "2026-08-18T19:48:00Z",
  };
  eq(unrecordedFields(trade, classifyExit(trade.exit_reason)).length, 0);
});

check("a PRICE_UNCERTAIN row flags its own price as unverified", () => {
  const trade = {
    exit_reason: "Manual close in IBKR (reconciled) — PRICE_UNCERTAIN",
    buy_date: "2026-07-31T14:31:02Z",
    sell_date: null,
  };
  const c = classifyExit(trade.exit_reason);
  eq(c.priceUncertain, true, "priceUncertain");
  const joined = unrecordedFields(trade, c).join(" | ");
  if (!/placeholder/i.test(joined)) throw new Error("did not flag the placeholder price");
  if (!/timestamp/i.test(joined)) throw new Error("did not flag the missing exit timestamp");
});

// ── Hold duration ────────────────────────────────────────────────────────────
// FROG was bought at 13:45 and stopped out the same afternoon, but the
// reconcile path stamps the sell as date-only midnight — so the raw timestamps
// put the exit 13 hours BEFORE the entry.
check("a same-day broker exit reads as 0 days, not as negative or unknown", () => {
  eq(holdDays({ buy_date: "2026-08-18T13:45:50Z", sell_date: "2026-08-18T00:00:00Z" }), 0);
  eq(holdDays({ buy_date: "2026-07-27T13:30:54Z", sell_date: "2026-07-27T00:00:00Z" }), 0);
});

check("a date-only exit counts calendar days, not truncated elapsed time", () => {
  // Buy 07-14 14:03 -> sell stamped 07-23 00:00. Elapsed is 8.4 days, but the
  // record only knows the two dates, and those are 9 days apart.
  eq(holdDays({ buy_date: "2026-07-14T14:03:43Z", sell_date: "2026-07-23T00:00:00Z" }), 9);
});

check("a missing exit date yields null rather than a bogus duration", () => {
  eq(holdDays({ buy_date: "2026-07-31T14:31:02Z", sell_date: null }), null);
});

// ── Derived arithmetic ───────────────────────────────────────────────────────
check("trade math is derived from the fills, not copied from the P&L column", () => {
  const m = deriveTradeMath({
    shares: 10, buy_price: 100, sell_price: 90,
    profit_loss: -100, percent_return: -10,
    buy_date: "2026-08-01T14:00:00Z", sell_date: "2026-08-05T15:00:00Z",
  });
  eq(m.costBasis, 1000, "costBasis");
  eq(m.proceeds, 900, "proceeds");
  eq(m.perShare, -10, "perShare");
  eq(m.days, 4, "days");
});

check("absent prices degrade to null instead of NaN", () => {
  const m = deriveTradeMath({ shares: null, buy_price: null, sell_price: null });
  eq(m.costBasis, null, "costBasis");
  eq(m.proceeds, null, "proceeds");
  eq(m.perShare, null, "perShare");
});

// ── Result ───────────────────────────────────────────────────────────────────
console.log(`\n    ${passed} passed / ${failures.length} failed\n`);
if (failures.length > 0) {
  console.error("  EXIT CLASSIFIER TESTS FAILED:");
  for (const f of failures) console.error(`     • ${f.name}: ${f.message}`);
  process.exit(1);
}
