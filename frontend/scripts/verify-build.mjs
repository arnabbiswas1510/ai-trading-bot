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
  { feature: "Portfolio table",           string: "Plateau Days" },
  // EMA-21 exit logic (present since early versions)
  { feature: "EMA-21 exit label",         string: "EMA-21 Exit" },
  // 3-Tier Plateau Rotation (added 2026-07-16)
  { feature: "Approve rotation API path", string: "approve-rotation" },
  { feature: "Dismiss rotation API path", string: "dismiss-rotation" },
  { feature: "Tier 1 label",              string: "Tier 1" },
  { feature: "Tier 2 label",              string: "Tier 2" },
  { feature: "Tier 3 label",              string: "Tier 3" },
  { feature: "Plateau Health card",       string: "Tier 3 auto-rotate" },
  // Entry Conviction scorecard
  { feature: "Entry Conviction card",     string: "Entry Conviction" },
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
