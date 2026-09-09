#!/usr/bin/env node

import { spawnSync } from 'node:child_process';

const ALL_STATES = [
  'AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME',
  'MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI',
  'SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY',
];

function requestedStates() {
  const argument = process.argv.find((value) => value.startsWith('--states='));
  if (!argument) return ALL_STATES;
  const requested = argument
    .slice('--states='.length)
    .split(',')
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);

  const unknown = requested.filter((code) => !ALL_STATES.includes(code));
  if (unknown.length > 0) {
    throw new Error(`Unknown state codes: ${unknown.join(', ')}`);
  }
  return [...new Set(requested)];
}

const states = requestedStates();
const failures = [];

console.log(`Resilient enrichment pass for ${states.length} jurisdiction(s): ${states.join(', ')}`);

for (const state of states) {
  console.log(`\n=== Enriching ${state} ===`);
  const result = spawnSync(process.execPath, ['scripts/refresh_us.mjs', `--states=${state}`], {
    stdio: 'inherit',
    env: process.env,
  });

  if (result.status !== 0) {
    failures.push(state);
    console.warn(`[${state}] enrichment failed; retaining the previously published state dataset and continuing`);
  }
}

if (failures.length > 0) {
  console.warn(`\nCompleted with retained last-known-good data for: ${failures.join(', ')}`);
} else {
  console.log('\nAll requested jurisdictions enriched successfully.');
}

// A state-level enrichment failure is intentionally non-fatal. The publication
// validator that follows this script remains authoritative for repository-wide
// integrity and will still fail the workflow if the combined publication is bad.
