/**
 * verify-build.mjs
 *
 * Post-build integrity check. Runs automatically after `npm run build`.
 * Greps the compiled Vite bundle for known feature fingerprints — literal
 * strings (API paths, UI labels) that survive JS minification.
 *
 * If any fingerprint is missing the script exits with code 1, which:
 *   - Fails the local `npm run build` command
 *   - Fails the Docker `RUN npm run build` layer ? build rejected at CI
 *   - Prevents a partial/stale bundle from ever being pushed to ghcr.io
 *
 * HOW TO ADD A FINGERPRINT:
 *   Pick a literal string from the new feature (preferably an API path or
 *   a unique UI label). Add it to FEATURE_FINGERPRINTS with a description.
 *   Avoid variable names — they get mangled by the minifier.
 */

import { readFileSync, readdirSync } from "fs";
import { join, resolve } from "path";

const DIST_DIR = resolve(new URL(".", import.meta.url).pathname, "../dist/assets");

// -- Feature fingerprints ------------------------------------------------------
// Each entry: { feature, string }
// `string` must be a literal that appears verbatim in the compiled JS bundle.
// Good choices: API URL fragments, unique UI text, error messages.
const FEATURE_FINGERPRINTS = [
  // Core portfolio table
  { feature: "Portfolio table",           string: "Lifecycle / Tiers" },
  // EMA-21 exit logic (present since early versions)
  { feature: "EMA-21 exit label",         string: "EMA-21 Exit" },
  // 3-Tier Plateau Rotation (added 2026-07-16)
  { feature: "Approve rotation API path", string: "approve-rotation" },
  { feature: "Dismiss rotation API path", string: "dismiss-rotation" },
  { feature: "Tier 1 label",              string: "Tier 1" },
  { feature: "Tier 2 label",              string: "Tier 2" },
  { feature: "Tier 3 label",              string: "Tier 3" },
  { feature: "Plateau Health card",       string: "data-plateau-health-card" },
  // Entry Conviction scorecard
  { feature: "Entry Conviction card",     string: "Entry Conviction" },
  // Position lifecycle / risk tier visibility (added 2026-08-14)
  { feature: "Risk Rule Ladder card",     string: "data-risk-ladder" },
  { feature: "Risk ladder heading",       string: "Risk Rule Ladder" },
  // The Prove-It Stop replaced five rules on 2026-09-04. Both phase labels are
  // fingerprinted because a build that ships only one of them means the phase
  // branch was tree-shaken or the mirror in positionRules.js went stale.
  { feature: "Prove-It rule row",         string: "Prove-It Stop (" },
  { feature: "Prove-It Phase 1 label",    string: "Phase 1 — unproven" },
  { feature: "Prove-It Phase 2 label",    string: "Phase 2 — proven" },
  { feature: "Give-back floor copy",      string: "give-back floor" },
  { feature: "Power Hold rule row",       string: "Power Hold (8-week rule)" },
  { feature: "Rank & Replace rule row",   string: "Rank & Replace" },
  { feature: "Armed exit rule row",       string: "Armed Exit" },
  // Retired-rule labels must survive forever: trade history still contains
  // their sell_reason strings and the exit panel has to keep resolving them.
  { feature: "Retired rule labels kept",  string: "Early Loss Kill-switch" },
  { feature: "Latch degradation warning", string: "DISARMED by an intraday poke above entry" },
  // A missing add_ibkr_position_values.sql migration silently turns Invested
  // Portfolio Value into cost basis and Unrealized P&L into $0.00. The warning
  // is the only thing distinguishing that from a genuinely flat book.
  { feature: "Cost-basis fallback warning", string: "data-unsynced-warning" },
  { feature: "Fallback warning copy",       string: "have not been marked from IBKR" },
  // Position Journey — phase / history / next step (added 2026-08-20)
  { feature: "Position Journey card",     string: "Position Journey" },
  { feature: "Journey history column",    string: "What has happened" },
  { feature: "Journey next column",       string: "What happens next" },
  // Market direction buy-gate banner (added 2026-08-22) — the dashboard must
  // show whether buys are actually permitted, not just the descriptive status.
  { feature: "Market buy-gate banner",    string: "data-market-gate" },
  { feature: "Buy gate label",            string: "Buy Gate:" },
  // Exit detail panel (added 2026-08-23) — the exit reason column is only a
  // label; the breakdown behind it is what makes an exit auditable.
  { feature: "Exit detail panel",         string: "data-exit-detail" },
  { feature: "Exit executor attribution", string: "Sold by " },
  { feature: "Exit unrecorded disclosure", string: "Not recorded for this exit" },
  { feature: "Exit verbatim reason",      string: "Exit reason as stored" },
  // IBKR-sourced position values (added 2026-09-03). Without these the price
  // column silently reverts to looking live while showing cost basis, which is
  // the exact ambiguity decisions/2026-09-03_ibkr-sourced-position-values.md
  // exists to remove.
  { feature: "IBKR price provenance",     string: "FMP estimate — not broker" },
  { feature: "Cost-basis provenance",     string: "Cost basis — no quote" },
  { feature: "IBKR price as-of stamp",    string: "as of " },
];

// -- Source-level structural guards --------------------------------------------
// Some regressions have no fingerprint string because the fix was structural.
// These are checked against source, not the bundle.
const SOURCE_GUARDS = [
  {
    feature: "Volatility fit is a band, not a speed ramp (ADR 2026-08-24)",
    file: "../src/lib/volatilityFit.js",
    // The AI rubric used to rank on EstDaysTo25% = 25/ATR, a monotonic rescaling
    // of ATR that made it an unbounded preference for volatility. Measured at
    // -9.5pp CAGR over 2,315 offline signals with a clean dose-response.
    // The cap is anchored to the 12% entry-stop clamp: above 4.8%/day a position
    // holds under 2.5 ATR of room. Changing these must go through the ADR.
    require: [/ATR_STOP_CAP_PCT = 4\.8/, /PROFIT_LOCK_PCT = 5\.0/],
    // A +25% target does not exist anywhere in the bot.
    forbid: /25\s*\/\s*atr|25\.0\s*\*/i,
  },
  {
    feature: "Dashboard pace measures the +5% lock, not a phantom +25% goal",
    file: "../src/components/DashboardView.jsx",
    require: [/estDaysToLock/, /PROFIT_LOCK_PCT/],
    forbid: /toward \+25% goal|more to reach \+25%|Est\.&nbsp;\+25%/,
  },
  {
    feature: "Sidebar nav items are <button>, not <div> (iOS tap fix, 2026-08-22)",
    file: "../src/App.jsx",
    // A clickable <div> has no activation behaviour, so iOS Safari does not
    // dispatch a synthetic click reliably — the left nav appeared frozen on iOS
    // while working on Android. Reverting to <div onClick> reintroduces that.
    forbid: /<div[^>]*className=\{`nav-item/,
    require: /<button[\s\S]{0,120}className=\{`nav-item/,
  },
  {
    feature: "Sidebar uses dvh fallback for iOS toolbar (2026-08-22)",
    file: "../src/index.css",
    require: /height:\s*100dvh/,
  },
  {
    feature: "Exit reasons are not fabricated from return % (2026-08-23)",
    file: "../src/lib/exitDetails.js",
    // The root cause of the old bug was that classification took the return
    // percentage as an argument and branched on it: any reconciled exit was
    // labelled a +25% profit target above +24% and a -7% stop below it. Both
    // numbers were false — the fixed profit target was removed from the bot
    // entirely, and the stop is a dynamic 8.25-10% trail that tightens to 1.5%
    // after the +5% lock — so a trade that closed at -2.77% was shown to the
    // operator as a -7% stop loss.
    //
    // Guarding the invariant rather than the strings: pinning classifyExit to a
    // single reason-string parameter means the classifier structurally cannot
    // see the P&L, so it cannot invent a rule from it. (deriveTradeMath does
    // legitimately read profit_loss — that is arithmetic on the fill, not
    // classification, which is exactly the distinction that was missing before.)
    // Prose in the two view components may still quote the old labels when
    // explaining the fix, which is why this check is structural and lives here.
    forbid: /pctReturn/,
    require: /export function classifyExit\(rawReason\)/,
  },
  {
    feature: "Exit classification is not duplicated per view (2026-08-23)",
    file: "../src/components/DashboardView.jsx",
    // DashboardView and TradesView each carried their own getCleanExitReason
    // and the two silently drifted apart. Both must import the shared module.
    forbid: /function getCleanExitReason/,
    require: /from '\.\.\/lib\/exitDetails'/,
  },
  {
    feature: "Trade History uses the shared exit classifier (2026-08-23)",
    file: "../src/components/TradesView.jsx",
    forbid: /getCleanExitReason\s*=\s*\(/,
    require: /from '\.\.\/lib\/exitDetails'/,
  },
];

// -- Load all JS bundle files --------------------------------------------------
let bundleText = "";
try {
  const jsFiles = readdirSync(DIST_DIR).filter((f) => f.endsWith(".js"));
  if (jsFiles.length === 0) {
    console.error("?  verify-build: No JS files found in dist/assets/");
    console.error("    Did `vite build` run successfully?");
    process.exit(1);
  }
  for (const f of jsFiles) {
    bundleText += readFileSync(join(DIST_DIR, f), "utf-8");
  }
  console.log(`\n??  verify-build: checking ${jsFiles.length} bundle file(s)...`);
} catch (err) {
  console.error(`?  verify-build: Could not read dist/assets/ — ${err.message}`);
  process.exit(1);
}

// -- Run checks ----------------------------------------------------------------
let passed = 0;
let failed = 0;
const failures = [];

for (const { feature, string } of FEATURE_FINGERPRINTS) {
  if (bundleText.includes(string)) {
    console.log(`  ?  ${feature}`);
    passed++;
  } else {
    console.error(`  ?  ${feature} — "${string}" not found in bundle`);
    failures.push({ feature, string });
    failed++;
  }
}

console.log(`\n    ${passed} passed / ${failed} failed\n`);

// -- Source guards -------------------------------------------------------------
const SRC_ROOT = resolve(new URL(".", import.meta.url).pathname, ".");
for (const guard of SOURCE_GUARDS) {
  let src;
  try {
    src = readFileSync(resolve(SRC_ROOT, guard.file), "utf-8");
  } catch (err) {
    console.error(`  x  ${guard.feature} — cannot read ${guard.file}: ${err.message}`);
    failures.push({ feature: guard.feature, string: guard.file });
    failed++;
    continue;
  }
  if (guard.forbid && guard.forbid.test(src)) {
    console.error(`  x  ${guard.feature} — forbidden pattern present: ${guard.forbid}`);
    failures.push({ feature: guard.feature, string: String(guard.forbid) });
    failed++;
    continue;
  }
  if (guard.require) {
    // `require` accepts a single regex or an array of regexes that must all match.
    const required = Array.isArray(guard.require) ? guard.require : [guard.require];
    const missing = required.find((re) => !re.test(src));
    if (missing) {
      console.error(`  x  ${guard.feature} — required pattern missing: ${missing}`);
      failures.push({ feature: guard.feature, string: String(missing) });
      failed++;
      continue;
    }
  }
  console.log(`  ok ${guard.feature}`);
  passed++;
}

console.log(`\n    total: ${passed} passed / ${failed} failed\n`);
if (failed > 0) {
  console.error("????????????????????????????????????????????????????????????");
  console.error("?  BUILD REJECTED — missing features in compiled bundle:");
  for (const { feature, string } of failures) {
    console.error(`     • ${feature}: expected "${string}"`);
  }
  console.error("");
  console.error("   This usually means a JSX file was not saved before building,");
  console.error("   or the Vite build used a stale Docker cache layer.");
  console.error("   Fix: ensure all changes are committed and rebuild with --no-cache.");
  console.error("????????????????????????????????????????????????????????????");
  process.exit(1);
}

console.log("?  verify-build: all feature fingerprints present. Bundle is valid.\n");
