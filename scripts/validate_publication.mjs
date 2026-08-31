#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const MANIFEST_PATH = resolve('us_state_manifest.json');

function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

function fail(message) {
  throw new Error(message);
}

function validCoordinate(camera) {
  return Number.isFinite(camera.latitude) &&
    Number.isFinite(camera.longitude) &&
    camera.latitude >= -90 && camera.latitude <= 90 &&
    camera.longitude >= -180 && camera.longitude <= 180;
}

async function main() {
  const manifest = JSON.parse(await readFile(MANIFEST_PATH, 'utf8'));

  if (manifest.schemaVersion !== 1) fail(`Unsupported manifest schema: ${manifest.schemaVersion}`);
  if (!Array.isArray(manifest.states) || manifest.states.length === 0) fail('Manifest has no states');

  const stateCodes = new Set();
  let totalCameraCount = 0;
  let totalFlockCount = 0;

  for (const entry of manifest.states) {
    if (!/^[A-Z]{2}$/.test(entry.stateCode)) fail(`Invalid state code: ${entry.stateCode}`);
    if (stateCodes.has(entry.stateCode)) fail(`Duplicate manifest state: ${entry.stateCode}`);
    stateCodes.add(entry.stateCode);

    if (!Number.isInteger(entry.version) || entry.version < 1) {
      fail(`Invalid version for ${entry.stateCode}: ${entry.version}`);
    }

    const datasetPath = resolve(entry.datasetURL);
    const datasetData = await readFile(datasetPath);
    const actualHash = sha256(datasetData);
    if (actualHash !== entry.sha256) {
      fail(`SHA-256 mismatch for ${entry.stateCode}: ${actualHash} != ${entry.sha256}`);
    }

    if (entry.fileSizeBytes !== datasetData.byteLength) {
      fail(`File-size mismatch for ${entry.stateCode}: ${datasetData.byteLength} != ${entry.fileSizeBytes}`);
    }

    const dataset = JSON.parse(datasetData.toString('utf8'));
    if (!dataset.metadata || !Array.isArray(dataset.cameras)) {
      fail(`Malformed dataset for ${entry.stateCode}`);
    }

    if (dataset.metadata.cameraCount !== dataset.cameras.length) {
      fail(`Dataset metadata count mismatch for ${entry.stateCode}`);
    }
    if (entry.cameraCount !== dataset.cameras.length) {
      fail(`Manifest count mismatch for ${entry.stateCode}`);
    }

    const ids = new Set();
    for (const camera of dataset.cameras) {
      if (typeof camera.id !== 'string' || camera.id.length === 0) {
        fail(`Missing camera ID in ${entry.stateCode}`);
      }
      if (ids.has(camera.id)) fail(`Duplicate camera ID ${camera.id} in ${entry.stateCode}`);
      ids.add(camera.id);
      if (!validCoordinate(camera)) fail(`Invalid coordinate for ${camera.id}`);
    }

    const flockCount = dataset.cameras.filter(
      (camera) => typeof camera.vendor === 'string' && /flock/i.test(camera.vendor)
    ).length;
    const directionCount = dataset.cameras.filter(
      (camera) => Number.isFinite(camera.directionDegrees)
    ).length;
    const operatorCount = dataset.cameras.filter(
      (camera) => typeof camera.operatorName === 'string' && camera.operatorName.trim().length > 0
    ).length;

    if (flockCount !== entry.flockCount || flockCount !== dataset.metadata.flockCount) {
      fail(`Flock count mismatch for ${entry.stateCode}`);
    }
    if (directionCount !== entry.directionCount || directionCount !== dataset.metadata.directionCount) {
      fail(`Direction count mismatch for ${entry.stateCode}`);
    }
    if (operatorCount !== entry.operatorCount || operatorCount !== dataset.metadata.operatorCount) {
      fail(`Operator count mismatch for ${entry.stateCode}`);
    }

    if (entry.changesURL) {
      const changes = JSON.parse(await readFile(resolve(entry.changesURL), 'utf8'));
      if (changes.stateCode !== entry.stateCode) fail(`Change-file state mismatch for ${entry.stateCode}`);
      if (changes.toVersion !== entry.version) fail(`Change-file version mismatch for ${entry.stateCode}`);
      if (changes.addedCount !== changes.added.length) fail(`Added count mismatch for ${entry.stateCode}`);
      if (changes.removedCount !== changes.removed.length) fail(`Removed count mismatch for ${entry.stateCode}`);
      if (changes.changedCount !== changes.changed.length) fail(`Changed count mismatch for ${entry.stateCode}`);
    }

    totalCameraCount += entry.cameraCount;
    totalFlockCount += entry.flockCount;
  }

  if (manifest.totalCameraCount !== totalCameraCount) {
    fail(`National camera total mismatch: ${manifest.totalCameraCount} != ${totalCameraCount}`);
  }
  if (manifest.totalFlockCount !== totalFlockCount) {
    fail(`National Flock total mismatch: ${manifest.totalFlockCount} != ${totalFlockCount}`);
  }

  console.log(
    `Publication valid: ${manifest.states.length} state(s), ` +
      `${totalCameraCount} cameras, ${totalFlockCount} Flock-tagged.`
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});
