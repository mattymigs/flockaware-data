#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, readFile, rm, writeFile, copyFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';

const ROOT = resolve(process.cwd());
const STATES_DIR = join(ROOT, 'states');
const CHANGES_DIR = join(ROOT, 'changes');
const MANIFEST_PATH = join(ROOT, 'us_state_manifest.json');
const TEMP_ROOT = join(tmpdir(), `flockaware-us-${process.pid}-${Date.now()}`);
const TEMP_STATES_DIR = join(TEMP_ROOT, 'states');
const TEMP_CHANGES_DIR = join(TEMP_ROOT, 'changes');
const TEMP_MANIFEST_PATH = join(TEMP_ROOT, 'us_state_manifest.json');

const REQUEST_TIMEOUT_MS = 300_000;
const RETRIES_PER_ENDPOINT = 2;
const MIN_NATIONAL_CAMERA_COUNT = 40_000;
const MAX_ALLOWED_DROP_FRACTION = 0.35;
const BETWEEN_STATE_DELAY_MS = 700;

const OVERPASS_ENDPOINTS = [
  'https://overpass.deflock.org/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://overpass-api.de/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];

const USER_AGENT =
  'FlockAwareData/1.0 (+https://github.com/mattymigs/flockaware-data; mattmignone@gmail.com)';

const STATE_DEFINITIONS = [
  ['AL', 'Alabama', ['FL', 'GA', 'MS', 'TN']],
  ['AK', 'Alaska', []],
  ['AZ', 'Arizona', ['CA', 'CO', 'NM', 'NV', 'UT']],
  ['AR', 'Arkansas', ['LA', 'MO', 'MS', 'OK', 'TN', 'TX']],
  ['CA', 'California', ['AZ', 'NV', 'OR']],
  ['CO', 'Colorado', ['AZ', 'KS', 'NE', 'NM', 'OK', 'UT', 'WY']],
  ['CT', 'Connecticut', ['MA', 'NY', 'RI']],
  ['DE', 'Delaware', ['MD', 'NJ', 'PA']],
  ['DC', 'District of Columbia', ['MD', 'VA']],
  ['FL', 'Florida', ['AL', 'GA']],
  ['GA', 'Georgia', ['AL', 'FL', 'NC', 'SC', 'TN']],
  ['HI', 'Hawaii', []],
  ['ID', 'Idaho', ['MT', 'NV', 'OR', 'UT', 'WA', 'WY']],
  ['IL', 'Illinois', ['IA', 'IN', 'KY', 'MO', 'WI']],
  ['IN', 'Indiana', ['IL', 'KY', 'MI', 'OH']],
  ['IA', 'Iowa', ['IL', 'MN', 'MO', 'NE', 'SD', 'WI']],
  ['KS', 'Kansas', ['CO', 'MO', 'NE', 'OK']],
  ['KY', 'Kentucky', ['IL', 'IN', 'MO', 'OH', 'TN', 'VA', 'WV']],
  ['LA', 'Louisiana', ['AR', 'MS', 'TX']],
  ['ME', 'Maine', ['NH']],
  ['MD', 'Maryland', ['DC', 'DE', 'PA', 'VA', 'WV']],
  ['MA', 'Massachusetts', ['CT', 'NH', 'NY', 'RI', 'VT']],
  ['MI', 'Michigan', ['IN', 'OH', 'WI']],
  ['MN', 'Minnesota', ['IA', 'MI', 'ND', 'SD', 'WI']],
  ['MS', 'Mississippi', ['AL', 'AR', 'LA', 'TN']],
  ['MO', 'Missouri', ['AR', 'IA', 'IL', 'KS', 'KY', 'NE', 'OK', 'TN']],
  ['MT', 'Montana', ['ID', 'ND', 'SD', 'WY']],
  ['NE', 'Nebraska', ['CO', 'IA', 'KS', 'MO', 'SD', 'WY']],
  ['NV', 'Nevada', ['AZ', 'CA', 'ID', 'OR', 'UT']],
  ['NH', 'New Hampshire', ['ME', 'MA', 'VT']],
  ['NJ', 'New Jersey', ['DE', 'NY', 'PA']],
  ['NM', 'New Mexico', ['AZ', 'CO', 'OK', 'TX', 'UT']],
  ['NY', 'New York', ['CT', 'MA', 'NJ', 'PA', 'VT']],
  ['NC', 'North Carolina', ['GA', 'SC', 'TN', 'VA']],
  ['ND', 'North Dakota', ['MN', 'MT', 'SD']],
  ['OH', 'Ohio', ['IN', 'KY', 'MI', 'PA', 'WV']],
  ['OK', 'Oklahoma', ['AR', 'CO', 'KS', 'MO', 'NM', 'TX']],
  ['OR', 'Oregon', ['CA', 'ID', 'NV', 'WA']],
  ['PA', 'Pennsylvania', ['DE', 'MD', 'NJ', 'NY', 'OH', 'WV']],
  ['RI', 'Rhode Island', ['CT', 'MA']],
  ['SC', 'South Carolina', ['GA', 'NC']],
  ['SD', 'South Dakota', ['IA', 'MN', 'MT', 'ND', 'NE', 'WY']],
  ['TN', 'Tennessee', ['AL', 'AR', 'GA', 'KY', 'MO', 'MS', 'NC', 'VA']],
  ['TX', 'Texas', ['AR', 'LA', 'NM', 'OK']],
  ['UT', 'Utah', ['AZ', 'CO', 'ID', 'NM', 'NV', 'WY']],
  ['VT', 'Vermont', ['MA', 'NH', 'NY']],
  ['VA', 'Virginia', ['DC', 'KY', 'MD', 'NC', 'TN', 'WV']],
  ['WA', 'Washington', ['ID', 'OR']],
  ['WV', 'West Virginia', ['KY', 'MD', 'OH', 'PA', 'VA']],
  ['WI', 'Wisconsin', ['IA', 'IL', 'MI', 'MN']],
  ['WY', 'Wyoming', ['CO', 'ID', 'MT', 'NE', 'SD', 'UT']],
].map(([stateCode, stateName, adjacentStates]) => ({
  stateCode,
  stateName,
  adjacentStates: [...adjacentStates].sort(),
}));

const CARDINALS = {
  N: 0, NNE: 22.5, NE: 45, ENE: 67.5,
  E: 90, ESE: 112.5, SE: 135, SSE: 157.5,
  S: 180, SSW: 202.5, SW: 225, WSW: 247.5,
  W: 270, WNW: 292.5, NW: 315, NNW: 337.5,
  NORTH: 0, NORTHEAST: 45, EAST: 90, SOUTHEAST: 135,
  SOUTH: 180, SOUTHWEST: 225, WEST: 270, NORTHWEST: 315,
  NB: 0, EB: 90, SB: 180, WB: 270,
};

function selectedDefinitions() {
  const argument = process.argv.find((value) => value.startsWith('--states='));
  const requested = argument
    ? new Set(argument.slice('--states='.length).split(',').map((value) => value.trim().toUpperCase()).filter(Boolean))
    : null;

  if (!requested) return STATE_DEFINITIONS;

  const selected = STATE_DEFINITIONS.filter(({ stateCode }) => requested.has(stateCode));
  const missing = [...requested].filter(
    (stateCode) => !STATE_DEFINITIONS.some((definition) => definition.stateCode === stateCode)
  );
  if (missing.length > 0) {
    throw new Error(`Unknown state codes: ${missing.join(', ')}`);
  }
  return selected;
}

function queryFor(stateCode) {
  return `[out:json][timeout:260];\narea["ISO3166-2"="US-${stateCode}"]["admin_level"="4"]->.state;\n(\n  node["man_made"="surveillance"]["surveillance:type"="ALPR"](area.state);\n  way["man_made"="surveillance"]["surveillance:type"="ALPR"](area.state);\n);\nout body center meta;`;
}

function cleaned(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function normalizeVendor(value) {
  const raw = cleaned(value);
  if (!raw) return null;
  if (/flock/i.test(raw)) return 'Flock Safety';
  if (/motorola|vigilant/i.test(raw)) return 'Motorola / Vigilant';
  if (/rekor/i.test(raw)) return 'Rekor';
  if (/genetec/i.test(raw)) return 'Genetec';
  if (/axis/i.test(raw)) return 'Axis Communications';
  return raw;
}

function removeCountySuffix(value) {
  const raw = cleaned(value);
  return raw ? raw.replace(/\s+County$/i, '') : null;
}

function normalizeDegrees(value) {
  return ((value % 360) + 360) % 360;
}

function resolveDirectionToken(token) {
  const normalized = token.trim().toUpperCase();
  if (!normalized) return null;
  if (Object.hasOwn(CARDINALS, normalized)) return CARDINALS[normalized];

  const numeric = Number(normalized);
  if (Number.isFinite(numeric)) return normalizeDegrees(numeric);

  const dash = normalized.indexOf('-', 1);
  if (dash > 0) {
    const start = resolveDirectionToken(normalized.slice(0, dash));
    const end = resolveDirectionToken(normalized.slice(dash + 1));
    if (start !== null && end !== null) {
      const clockwiseArc = ((end - start) % 360 + 360) % 360;
      return normalizeDegrees(start + clockwiseArc / 2);
    }
  }

  return null;
}

function parseDirection(raw) {
  if (!raw) return { degrees: null, text: null };
  const first = raw.split(/[;,]/).map((item) => item.trim()).find(Boolean);
  if (!first) return { degrees: null, text: null };
  return { degrees: resolveDirectionToken(first), text: first };
}

function pointForElement(element) {
  if (Number.isFinite(element.lat) && Number.isFinite(element.lon)) {
    return { latitude: element.lat, longitude: element.lon };
  }
  if (Number.isFinite(element.center?.lat) && Number.isFinite(element.center?.lon)) {
    return { latitude: element.center.lat, longitude: element.center.lon };
  }
  return null;
}

function transformElement(element, stateCode) {
  const point = pointForElement(element);
  if (!point) return null;

  const tags = element.tags ?? {};
  if (tags.man_made !== 'surveillance' || tags['surveillance:type'] !== 'ALPR') return null;

  const direction = parseDirection(tags.direction || tags['camera:direction']);
  return {
    id: `osm-${element.type}-${element.id}`,
    name: cleaned(tags.name),
    latitude: point.latitude,
    longitude: point.longitude,
    vendor: normalizeVendor(tags.brand || tags.manufacturer),
    operatorName: cleaned(tags.operator),
    directionDegrees: direction.degrees,
    directionText: direction.text,
    municipality:
      cleaned(tags['addr:city']) ||
      cleaned(tags['is_in:city']) ||
      cleaned(tags['is_in:town']) ||
      cleaned(tags['is_in:village']) ||
      null,
    county: removeCountySuffix(tags['addr:county'] || tags['is_in:county']),
    surveillanceZone: cleaned(tags['surveillance:zone']),
    mountType: cleaned(tags['camera:mount']),
    startDate: cleaned(tags.start_date),
    reference: cleaned(tags.ref),
    osmType: element.type,
    osmId: element.id,
    osmVersion: Number.isInteger(element.version) ? element.version : null,
    osmTimestamp: cleaned(element.timestamp),
    sourceName: 'OpenStreetMap',
    sourceURL: `https://www.openstreetmap.org/${element.type}/${element.id}`,
    dataStatus: 'community_mapped',
    stateCode,
  };
}

function stableCameraPayload(cameras) {
  return JSON.stringify(cameras);
}

function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

function sleep(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

async function readJSON(path) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch {
    return null;
  }
}

async function queryEndpoint(endpoint, stateCode) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'User-Agent': USER_AGENT,
      },
      body: new URLSearchParams({ data: queryFor(stateCode) }),
      signal: controller.signal,
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();

    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error(`Non-JSON response: ${text.slice(0, 160).replace(/\s+/g, ' ')}`);
    }

    if (typeof payload.remark === 'string' && /timed out|runtime error|out of memory/i.test(payload.remark)) {
      throw new Error(payload.remark);
    }
    if (!Array.isArray(payload.elements)) {
      throw new Error('Response did not include an elements array');
    }

    return payload.elements;
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchStateElements(stateCode) {
  const errors = [];

  for (let endpointIndex = 0; endpointIndex < OVERPASS_ENDPOINTS.length; endpointIndex += 1) {
    const endpoint = OVERPASS_ENDPOINTS[endpointIndex];
    for (let attempt = 1; attempt <= RETRIES_PER_ENDPOINT; attempt += 1) {
      try {
        console.log(`[${stateCode}] ${endpoint} attempt ${attempt}/${RETRIES_PER_ENDPOINT}`);
        return await queryEndpoint(endpoint, stateCode);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        errors.push(`${endpoint}: ${message}`);
        console.warn(`[${stateCode}] ${message}`);
        await sleep(1_500 * attempt);
      }
    }
  }

  throw new Error(`[${stateCode}] all Overpass endpoints failed:\n${errors.join('\n')}`);
}

function normalizeElements(elements, stateCode) {
  const byID = new Map();
  for (const element of elements) {
    const camera = transformElement(element, stateCode);
    if (camera) byID.set(camera.id, camera);
  }

  return [...byID.values()].sort((a, b) => {
    if (a.latitude !== b.latitude) return b.latitude - a.latitude;
    if (a.longitude !== b.longitude) return a.longitude - b.longitude;
    return a.id.localeCompare(b.id);
  });
}

function validateCoordinates(cameras, stateCode) {
  const ids = new Set();
  for (const camera of cameras) {
    if (ids.has(camera.id)) throw new Error(`[${stateCode}] duplicate ID ${camera.id}`);
    ids.add(camera.id);

    if (!Number.isFinite(camera.latitude) || camera.latitude < -90 || camera.latitude > 90 ||
        !Number.isFinite(camera.longitude) || camera.longitude < -180 || camera.longitude > 180) {
      throw new Error(`[${stateCode}] invalid coordinate for ${camera.id}`);
    }
  }
}

function boundsFor(cameras) {
  if (cameras.length === 0) return null;
  return cameras.reduce(
    (bounds, camera) => ({
      north: Math.max(bounds.north, camera.latitude),
      south: Math.min(bounds.south, camera.latitude),
      east: Math.max(bounds.east, camera.longitude),
      west: Math.min(bounds.west, camera.longitude),
    }),
    { north: -90, south: 90, east: -180, west: 180 }
  );
}

function changedFields(previous, next) {
  const ignored = new Set(['osmTimestamp']);
  const keys = new Set([...Object.keys(previous ?? {}), ...Object.keys(next ?? {})]);
  return [...keys]
    .filter((key) => !ignored.has(key))
    .filter((key) => JSON.stringify(previous?.[key] ?? null) !== JSON.stringify(next?.[key] ?? null))
    .sort();
}

function buildChangeSet({ stateCode, stateName, oldDataset, oldVersion, newDataset, newVersion, generatedAt }) {
  if (!oldDataset) {
    return {
      schemaVersion: 1,
      stateCode,
      stateName,
      generatedAt,
      fromVersion: null,
      toVersion: newVersion,
      baseline: true,
      addedCount: 0,
      removedCount: 0,
      changedCount: 0,
      added: [],
      removed: [],
      changed: [],
    };
  }

  const oldByID = new Map(oldDataset.cameras.map((camera) => [camera.id, camera]));
  const newByID = new Map(newDataset.cameras.map((camera) => [camera.id, camera]));
  const added = [...newByID.keys()].filter((id) => !oldByID.has(id)).sort();
  const removed = [...oldByID.keys()].filter((id) => !newByID.has(id)).sort();
  const changed = [];

  for (const [id, nextCamera] of newByID) {
    const previousCamera = oldByID.get(id);
    if (!previousCamera) continue;
    const fields = changedFields(previousCamera, nextCamera);
    if (fields.length > 0) changed.push({ id, fields });
  }
  changed.sort((a, b) => a.id.localeCompare(b.id));

  return {
    schemaVersion: 1,
    stateCode,
    stateName,
    generatedAt,
    fromVersion: oldVersion,
    toVersion: newVersion,
    baseline: false,
    addedCount: added.length,
    removedCount: removed.length,
    changedCount: changed.length,
    added,
    removed,
    changed,
  };
}

async function prepareState(definition, existingManifest) {
  const { stateCode, stateName, adjacentStates } = definition;
  const previousDatasetPath = join(STATES_DIR, `${stateCode}.json`);
  const previousChangesPath = join(CHANGES_DIR, `${stateCode}.json`);
  const oldDataset = await readJSON(previousDatasetPath);
  const oldEntry = existingManifest?.states?.find((entry) => entry.stateCode === stateCode) ?? null;

  let elements;
  try {
    elements = await fetchStateElements(stateCode);
  } catch (error) {
    if (!oldDataset || !oldEntry) throw error;
    console.warn(`[${stateCode}] retaining last-known-good publication after fetch failure`);
    await copyFile(previousDatasetPath, join(TEMP_STATES_DIR, `${stateCode}.json`));
    if (await readJSON(previousChangesPath)) {
      await copyFile(previousChangesPath, join(TEMP_CHANGES_DIR, `${stateCode}.json`));
    }
    return { entry: oldEntry, changed: false, retained: true };
  }

  const cameras = normalizeElements(elements, stateCode);
  validateCoordinates(cameras, stateCode);

  if (oldDataset?.cameras?.length > 25) {
    const minimumAllowed = Math.floor(oldDataset.cameras.length * (1 - MAX_ALLOWED_DROP_FRACTION));
    if (cameras.length < minimumAllowed) {
      throw new Error(
        `[${stateCode}] suspicious count drop ${oldDataset.cameras.length} -> ${cameras.length}; publication aborted`
      );
    }
  }

  const unchanged = oldDataset && stableCameraPayload(oldDataset.cameras) === stableCameraPayload(cameras);
  if (unchanged && oldEntry) {
    await copyFile(previousDatasetPath, join(TEMP_STATES_DIR, `${stateCode}.json`));
    if (await readJSON(previousChangesPath)) {
      await copyFile(previousChangesPath, join(TEMP_CHANGES_DIR, `${stateCode}.json`));
    } else {
      const noChange = buildChangeSet({
        stateCode,
        stateName,
        oldDataset,
        oldVersion: oldEntry.version,
        newDataset: oldDataset,
        newVersion: oldEntry.version,
        generatedAt: oldEntry.generatedAt,
      });
      await writeFile(join(TEMP_CHANGES_DIR, `${stateCode}.json`), `${JSON.stringify(noChange)}\n`);
    }
    console.log(`[${stateCode}] unchanged (${cameras.length.toLocaleString()} cameras)`);
    return { entry: oldEntry, changed: false, retained: false };
  }

  const generatedAt = new Date().toISOString();
  const version = (oldEntry?.version ?? 0) + 1;
  const flockCount = cameras.filter((camera) => camera.vendor === 'Flock Safety').length;
  const directionCount = cameras.filter((camera) => camera.directionDegrees !== null).length;
  const operatorCount = cameras.filter((camera) => camera.operatorName !== null).length;

  const dataset = {
    metadata: {
      schemaVersion: 2,
      generatedAt,
      jurisdiction: stateName,
      source: 'OpenStreetMap',
      sourceURL: 'https://www.openstreetmap.org',
      attribution: '© OpenStreetMap contributors',
      license: 'ODbL-1.0',
      cameraCount: cameras.length,
      flockCount,
      directionCount,
      operatorCount,
      isDemo: false,
    },
    cameras,
  };

  const datasetData = Buffer.from(`${JSON.stringify(dataset)}\n`, 'utf8');
  const entry = {
    stateCode,
    stateName,
    version,
    generatedAt,
    datasetURL: `states/${stateCode}.json`,
    changesURL: `changes/${stateCode}.json`,
    sha256: sha256(datasetData),
    cameraCount: cameras.length,
    flockCount,
    directionCount,
    operatorCount,
    fileSizeBytes: datasetData.length,
    adjacentStates,
    bounds: boundsFor(cameras),
  };

  const changes = buildChangeSet({
    stateCode,
    stateName,
    oldDataset,
    oldVersion: oldEntry?.version ?? null,
    newDataset: dataset,
    newVersion: version,
    generatedAt,
  });

  await writeFile(join(TEMP_STATES_DIR, `${stateCode}.json`), datasetData);
  await writeFile(join(TEMP_CHANGES_DIR, `${stateCode}.json`), `${JSON.stringify(changes)}\n`);

  console.log(
    `[${stateCode}] v${version}: ${cameras.length.toLocaleString()} cameras, ` +
      `${flockCount.toLocaleString()} Flock, ${directionCount.toLocaleString()} with bearing`
  );
  return { entry, changed: true, retained: false };
}

async function copyUnselectedExistingStates(selectedCodes, existingManifest, entries) {
  for (const entry of existingManifest?.states ?? []) {
    if (selectedCodes.has(entry.stateCode)) continue;
    const statePath = join(STATES_DIR, `${entry.stateCode}.json`);
    const changesPath = join(CHANGES_DIR, `${entry.stateCode}.json`);
    if (!(await readJSON(statePath))) continue;

    await copyFile(statePath, join(TEMP_STATES_DIR, `${entry.stateCode}.json`));
    if (await readJSON(changesPath)) {
      await copyFile(changesPath, join(TEMP_CHANGES_DIR, `${entry.stateCode}.json`));
    }
    entries.push(entry);
  }
}

async function publish(entries) {
  entries.sort((a, b) => a.stateCode.localeCompare(b.stateCode));
  const totalCameraCount = entries.reduce((total, entry) => total + entry.cameraCount, 0);
  const totalFlockCount = entries.reduce((total, entry) => total + entry.flockCount, 0);

  if (entries.length === STATE_DEFINITIONS.length && totalCameraCount < MIN_NATIONAL_CAMERA_COUNT) {
    throw new Error(
      `National integrity floor failed: ${totalCameraCount.toLocaleString()} < ${MIN_NATIONAL_CAMERA_COUNT.toLocaleString()}`
    );
  }

  const manifest = {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    source: 'OpenStreetMap',
    attribution: '© OpenStreetMap contributors',
    license: 'ODbL-1.0',
    totalCameraCount,
    totalFlockCount,
    states: entries,
  };
  await writeFile(TEMP_MANIFEST_PATH, `${JSON.stringify(manifest)}\n`);

  await mkdir(STATES_DIR, { recursive: true });
  await mkdir(CHANGES_DIR, { recursive: true });

  for (const entry of entries) {
    await copyFile(
      join(TEMP_STATES_DIR, `${entry.stateCode}.json`),
      join(STATES_DIR, `${entry.stateCode}.json`)
    );
    const tempChanges = join(TEMP_CHANGES_DIR, `${entry.stateCode}.json`);
    if (await readJSON(tempChanges)) {
      await copyFile(tempChanges, join(CHANGES_DIR, `${entry.stateCode}.json`));
    }
  }
  await copyFile(TEMP_MANIFEST_PATH, MANIFEST_PATH);

  console.log(
    `Published ${entries.length} jurisdictions, ${totalCameraCount.toLocaleString()} cameras, ` +
      `${totalFlockCount.toLocaleString()} Flock-tagged`
  );
}

async function main() {
  const definitions = selectedDefinitions();
  const selectedCodes = new Set(definitions.map(({ stateCode }) => stateCode));
  const existingManifest = await readJSON(MANIFEST_PATH);
  const entries = [];

  await rm(TEMP_ROOT, { recursive: true, force: true });
  await mkdir(TEMP_STATES_DIR, { recursive: true });
  await mkdir(TEMP_CHANGES_DIR, { recursive: true });

  try {
    let index = 0;
    for (const definition of definitions) {
      index += 1;
      console.log(`\n=== ${definition.stateName} (${definition.stateCode}) ${index}/${definitions.length} ===`);
      const result = await prepareState(definition, existingManifest);
      entries.push(result.entry);
      if (index < definitions.length) await sleep(BETWEEN_STATE_DELAY_MS);
    }

    await copyUnselectedExistingStates(selectedCodes, existingManifest, entries);
    await publish(entries);
  } finally {
    await rm(TEMP_ROOT, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});
