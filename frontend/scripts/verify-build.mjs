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
  { feature: "Thesis Stop rule row",      string: "Thesis Stop" },
  { feature: "Kill-switch rule row",      string: "Early Loss Kill-switch" },
  { feature: "Power Hold rule row",       string: "Power Hold (8-week rule)" },
  { feature: "Plateau exit rule row",     string: "Plateau Exit" },
  { feature: "Rank & Replace rule row",   string: "Rank & Replace" },
  { feature: "Armed exit rule row",       string: "Armed Exit" },
  { feature: "Latch degradation warning", string: "DISARMED by an intraday poke above entry" },
  // Position Journey — phase / history / next step (added 2026-08-20)
  { feature: "Position Journey card",     string: "Position Journey" },
  { feature: "Journey history column",    string: "What has happened" },
  { feature: "Journey next column",       string: "What happens next" },
  // Market direction buy-gate banner (added 2026-08-22) — the dashboard must
  // show whether buys are actually permitted, not just the descriptive status.
  { feature: "Market buy-gate banner",    string: "data-market-gate" },
  { feature: "Buy gate label",            string: "Buy Gate:" },
];

// -- Source-level structural guards --------------------------------------------
// Some regressions have no fingerprint string because the fix was structural.
// These are checked against source, not the bundle.
const SOURCE_GUARDS = [
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
  if (guard.require && !guard.require.test(src)) {
    console.error(`  x  ${guard.feature} — required pattern missing: ${guard.require}`);
    failures.push({ feature: guard.feature, string: String(guard.require) });
    failed++;
    continue;
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
