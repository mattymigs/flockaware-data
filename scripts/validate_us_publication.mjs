#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { readFile, readdir, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const ROOT = resolve(process.cwd());
const MANIFEST_PATH = join(ROOT, 'us_state_manifest.json');
const STATES_DIR = join(ROOT, 'states');
const CHANGES_DIR = join(ROOT, 'changes');
const EXPECTED_JURISDICTIONS = 51;
const MIN_NATIONAL_CAMERA_COUNT = 40_000;

function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

async function readJSON(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function validateEntry(entry) {
  const stateCode = entry.stateCode;
  const datasetPath = join(STATES_DIR, `${stateCode}.json`);
  const changesPath = join(CHANGES_DIR, `${stateCode}.json`);
  const datasetData = await readFile(datasetPath);
  const dataset = JSON.parse(datasetData.toString('utf8'));
  const changes = await readJSON(changesPath);

  assert(dataset.metadata.schemaVersion === 2, `[${stateCode}] unsupported dataset schema`);
  assert(Array.isArray(dataset.cameras), `[${stateCode}] cameras is not an array`);
  assert(dataset.metadata.cameraCount === dataset.cameras.length, `[${stateCode}] metadata count mismatch`);
  assert(entry.cameraCount === dataset.cameras.length, `[${stateCode}] manifest count mismatch`);
  assert(entry.fileSizeBytes === datasetData.length, `[${stateCode}] file size mismatch`);
  assert(entry.sha256 === sha256(datasetData), `[${stateCode}] SHA-256 mismatch`);

  const ids = new Set();
  let flockCount = 0;
  let directionCount = 0;
  let operatorCount = 0;

  for (const camera of dataset.cameras) {
    assert(typeof camera.id === 'string' && camera.id.length > 0, `[${stateCode}] camera missing ID`);
    assert(!ids.has(camera.id), `[${stateCode}] duplicate camera ID ${camera.id}`);
    ids.add(camera.id);

    assert(Number.isFinite(camera.latitude), `[${stateCode}] invalid latitude for ${camera.id}`);
    assert(Number.isFinite(camera.longitude), `[${stateCode}] invalid longitude for ${camera.id}`);
    assert(camera.latitude >= -90 && camera.latitude <= 90, `[${stateCode}] latitude out of range`);
    assert(camera.longitude >= -180 && camera.longitude <= 180, `[${stateCode}] longitude out of range`);
    assert(camera.stateCode === stateCode, `[${stateCode}] camera assigned to ${camera.stateCode}`);

    if (camera.vendor === 'Flock Safety') flockCount += 1;
    if (camera.directionDegrees !== null) directionCount += 1;
    if (camera.operatorName !== null) operatorCount += 1;
  }

  assert(entry.flockCount === flockCount, `[${stateCode}] Flock count mismatch`);
  assert(entry.directionCount === directionCount, `[${stateCode}] direction count mismatch`);
  assert(entry.operatorCount === operatorCount, `[${stateCode}] operator count mismatch`);
  assert(dataset.metadata.flockCount === flockCount, `[${stateCode}] dataset Flock count mismatch`);
  assert(dataset.metadata.directionCount === directionCount, `[${stateCode}] dataset direction count mismatch`);
  assert(dataset.metadata.operatorCount === operatorCount, `[${stateCode}] dataset operator count mismatch`);

  assert(changes.schemaVersion === 1, `[${stateCode}] unsupported changes schema`);
  assert(changes.stateCode === stateCode, `[${stateCode}] changes state mismatch`);
  assert(changes.toVersion === entry.version, `[${stateCode}] changes version mismatch`);
  assert(changes.addedCount === changes.added.length, `[${stateCode}] added count mismatch`);
  assert(changes.removedCount === changes.removed.length, `[${stateCode}] removed count mismatch`);
  assert(changes.changedCount === changes.changed.length, `[${stateCode}] changed count mismatch`);

  return { cameraCount: dataset.cameras.length, flockCount };
}

async function main() {
  const manifest = await readJSON(MANIFEST_PATH);
  assert(manifest.schemaVersion === 1, 'Unsupported manifest schema');
  assert(Array.isArray(manifest.states), 'Manifest states is not an array');

  const stateFiles = (await readdir(STATES_DIR)).filter((name) => name.endsWith('.json'));
  const changeFiles = (await readdir(CHANGES_DIR)).filter((name) => name.endsWith('.json'));
  assert(stateFiles.length === manifest.states.length, 'State file count does not match manifest');
  assert(changeFiles.length >= manifest.states.length, 'Change file count does not match manifest');

  const stateCodes = manifest.states.map((entry) => entry.stateCode);
  assert(new Set(stateCodes).size === stateCodes.length, 'Duplicate state entries in manifest');

  if (manifest.states.length === EXPECTED_JURISDICTIONS) {
    assert(
      manifest.totalCameraCount >= MIN_NATIONAL_CAMERA_COUNT,
      `National camera count below integrity floor: ${manifest.totalCameraCount}`
    );
  }

  let totalCameraCount = 0;
  let totalFlockCount = 0;
  for (const entry of manifest.states) {
    const result = await validateEntry(entry);
    totalCameraCount += result.cameraCount;
    totalFlockCount += result.flockCount;
  }

  assert(manifest.totalCameraCount === totalCameraCount, 'National camera total mismatch');
  assert(manifest.totalFlockCount === totalFlockCount, 'National Flock total mismatch');

  const manifestStats = await stat(MANIFEST_PATH);
  console.log(
    `Validated ${manifest.states.length} jurisdictions, ${totalCameraCount.toLocaleString()} cameras, ` +
      `${totalFlockCount.toLocaleString()} Flock-tagged (${manifestStats.size.toLocaleString()}-byte manifest)`
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});
