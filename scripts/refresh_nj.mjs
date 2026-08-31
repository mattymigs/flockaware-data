#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const STATE_CODE = 'NJ';
const STATE_NAME = 'New Jersey';
const STATE_PATH = resolve('states/NJ.json');
const CHANGE_PATH = resolve('changes/NJ.json');
const MANIFEST_PATH = resolve('us_state_manifest.json');

const MINIMUM_CAMERA_COUNT = 500;
const MAXIMUM_ALLOWED_DROP_FRACTION = 0.35;
const TIMEOUT_MS = 165_000;
const RETRIES_PER_ENDPOINT = 2;

const OVERPASS_ENDPOINTS = [
  'https://overpass.deflock.org/api/interpreter',
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];

const USER_AGENT =
  'FlockAware-Data/1.0 (+https://github.com/mattymigs/flockaware-data; mattmignone@gmail.com)';

const QUERY = `[out:json][timeout:150];
area["ISO3166-2"="US-NJ"]["admin_level"="4"]->.target;
(
  node["man_made"="surveillance"]["surveillance:type"="ALPR"](area.target);
  way["man_made"="surveillance"]["surveillance:type"="ALPR"](area.target);
);
out body center meta;`;

const CARDINALS = {
  N: 0, NNE: 22.5, NE: 45, ENE: 67.5,
  E: 90, ESE: 112.5, SE: 135, SSE: 157.5,
  S: 180, SSW: 202.5, SW: 225, WSW: 247.5,
  W: 270, WNW: 292.5, NW: 315, NNW: 337.5,
  NORTH: 0, NORTHEAST: 45, EAST: 90, SOUTHEAST: 135,
  SOUTH: 180, SOUTHWEST: 225, WEST: 270, NORTHWEST: 315,
  NB: 0, EB: 90, SB: 180, WB: 270,
};

function cleaned(value) {
  if (typeof value !== 'string') return null;
  const result = value.trim();
  return result.length > 0 ? result : null;
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

  const first = raw
    .split(/[;,]/)
    .map((item) => item.trim())
    .find(Boolean);

  if (!first) return { degrees: null, text: null };
  return {
    degrees: resolveDirectionToken(first),
    text: first,
  };
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

function pointForElement(element) {
  if (Number.isFinite(element.lat) && Number.isFinite(element.lon)) {
    return { latitude: element.lat, longitude: element.lon };
  }

  if (Number.isFinite(element.center?.lat) && Number.isFinite(element.center?.lon)) {
    return { latitude: element.center.lat, longitude: element.center.lon };
  }

  return null;
}

function transformElement(element) {
  const point = pointForElement(element);
  if (!point) return null;

  const tags = element.tags ?? {};
  if (tags.man_made !== 'surveillance' || tags['surveillance:type'] !== 'ALPR') {
    return null;
  }

  const direction = parseDirection(tags.direction || tags['camera:direction']);
  const osmType = cleaned(element.type) ?? 'node';
  const osmId = Number.isInteger(element.id) ? element.id : null;
  if (osmId === null) return null;

  return {
    id: `osm-${osmType}-${osmId}`,
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
    osmType,
    osmId,
    osmVersion: Number.isInteger(element.version) ? element.version : null,
    osmTimestamp: cleaned(element.timestamp),
    sourceName: 'OpenStreetMap',
    sourceURL: `https://www.openstreetmap.org/${osmType}/${osmId}`,
    dataStatus: 'community_mapped',

    // Preserved for future app intelligence. Current app builds safely ignore
    // unknown JSON fields until their model adopts them.
    stateCode: STATE_CODE,
    model: cleaned(tags.model),
    cameraType: cleaned(tags['camera:type']),
    powerSource: cleaned(tags.power_source || tags['surveillance:power']),
    imageURL: cleaned(tags.image),
    mapillaryKey: cleaned(tags.mapillary),
    website: cleaned(tags.website),
    description: cleaned(tags.description),
    note: cleaned(tags.note),
    street: cleaned(tags['addr:street']),
  };
}

async function sleep(milliseconds) {
  await new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

async function queryEndpoint(endpoint) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'User-Agent': USER_AGENT,
      },
      body: new URLSearchParams({ data: QUERY }),
      signal: controller.signal,
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`Non-JSON response: ${text.slice(0, 180).replace(/\s+/g, ' ')}`);
    }

    if (typeof data.remark === 'string' && /timed out|runtime error|out of memory/i.test(data.remark)) {
      throw new Error(data.remark);
    }
    if (!Array.isArray(data.elements)) {
      throw new Error('Response did not include an elements array');
    }

    return data.elements;
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchElements() {
  const errors = [];

  for (const endpoint of OVERPASS_ENDPOINTS) {
    for (let attempt = 1; attempt <= RETRIES_PER_ENDPOINT; attempt += 1) {
      try {
        console.log(`Querying ${endpoint} (attempt ${attempt}/${RETRIES_PER_ENDPOINT})...`);
        return await queryEndpoint(endpoint);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        errors.push(`${endpoint}: ${message}`);
        console.warn(`  ${message}`);
        if (attempt < RETRIES_PER_ENDPOINT) await sleep(1_500 * attempt);
      }
    }
  }

  throw new Error(`All Overpass endpoints failed:\n${errors.join('\n')}`);
}

function buildDataset(elements, generatedAt) {
  const cameraMap = new Map();

  for (const element of elements) {
    const camera = transformElement(element);
    if (camera) cameraMap.set(camera.id, camera);
  }

  const cameras = [...cameraMap.values()].sort((a, b) => {
    if (a.latitude !== b.latitude) return b.latitude - a.latitude;
    if (a.longitude !== b.longitude) return a.longitude - b.longitude;
    return a.id.localeCompare(b.id);
  });

  const flockCount = cameras.filter((camera) => camera.vendor === 'Flock Safety').length;
  const directionCount = cameras.filter((camera) => camera.directionDegrees !== null).length;
  const operatorCount = cameras.filter((camera) => camera.operatorName !== null).length;

  return {
    metadata: {
      schemaVersion: 2,
      generatedAt,
      jurisdiction: STATE_NAME,
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
}

async function readJSON(path) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch {
    return null;
  }
}

function stableCameraPayload(dataset) {
  return JSON.stringify(dataset?.cameras ?? []);
}

function validateDataset(next, previous) {
  const { cameras, metadata } = next;

  if (cameras.length < MINIMUM_CAMERA_COUNT) {
    throw new Error(
      `Only ${cameras.length} NJ cameras were returned; minimum safe count is ${MINIMUM_CAMERA_COUNT}. Refusing publication.`
    );
  }

  if (previous?.cameras?.length) {
    const minimumRelativeCount = Math.floor(
      previous.cameras.length * (1 - MAXIMUM_ALLOWED_DROP_FRACTION)
    );
    const minimumAllowed = Math.max(MINIMUM_CAMERA_COUNT, minimumRelativeCount);
    if (cameras.length < minimumAllowed) {
      throw new Error(
        `Camera count dropped from ${previous.cameras.length} to ${cameras.length}; minimum allowed is ${minimumAllowed}. Refusing publication.`
      );
    }
  }

  const ids = new Set();
  for (const camera of cameras) {
    if (ids.has(camera.id)) throw new Error(`Duplicate camera ID: ${camera.id}`);
    ids.add(camera.id);

    if (!Number.isFinite(camera.latitude) || camera.latitude < -90 || camera.latitude > 90) {
      throw new Error(`Invalid latitude for ${camera.id}`);
    }
    if (!Number.isFinite(camera.longitude) || camera.longitude < -180 || camera.longitude > 180) {
      throw new Error(`Invalid longitude for ${camera.id}`);
    }
  }

  if (metadata.cameraCount !== cameras.length) {
    throw new Error(`Metadata camera count ${metadata.cameraCount} does not match ${cameras.length}`);
  }
  if (metadata.flockCount !== cameras.filter((camera) => camera.vendor === 'Flock Safety').length) {
    throw new Error('Metadata Flock count is inconsistent');
  }
  if (metadata.directionCount !== cameras.filter((camera) => camera.directionDegrees !== null).length) {
    throw new Error('Metadata direction count is inconsistent');
  }
}

function cameraSummary(camera) {
  return {
    id: camera.id,
    latitude: camera.latitude,
    longitude: camera.longitude,
    vendor: camera.vendor,
    operatorName: camera.operatorName,
    directionDegrees: camera.directionDegrees,
    municipality: camera.municipality,
    county: camera.county,
    sourceURL: camera.sourceURL,
  };
}

function buildChanges(previous, next, fromVersion, toVersion, generatedAt) {
  if (!previous?.cameras) {
    return {
      schemaVersion: 1,
      stateCode: STATE_CODE,
      stateName: STATE_NAME,
      generatedAt,
      fromVersion: null,
      toVersion,
      baseline: true,
      addedCount: 0,
      removedCount: 0,
      changedCount: 0,
      added: [],
      removed: [],
      changed: [],
    };
  }

  const oldByID = new Map(previous.cameras.map((camera) => [camera.id, camera]));
  const newByID = new Map(next.cameras.map((camera) => [camera.id, camera]));

  const added = [];
  const removed = [];
  const changed = [];

  for (const [id, camera] of newByID) {
    const oldCamera = oldByID.get(id);
    if (!oldCamera) {
      added.push(cameraSummary(camera));
      continue;
    }

    const fields = [
      'name', 'latitude', 'longitude', 'vendor', 'operatorName',
      'directionDegrees', 'directionText', 'municipality', 'county',
      'surveillanceZone', 'mountType', 'startDate', 'reference',
      'osmVersion', 'osmTimestamp', 'model', 'cameraType', 'powerSource',
      'imageURL', 'mapillaryKey', 'website', 'street',
    ].filter((field) => JSON.stringify(oldCamera[field] ?? null) !== JSON.stringify(camera[field] ?? null));

    if (fields.length > 0) {
      changed.push({
        id,
        fields,
        previous: cameraSummary(oldCamera),
        current: cameraSummary(camera),
      });
    }
  }

  for (const id of oldByID.keys()) {
    if (!newByID.has(id)) removed.push(id);
  }

  added.sort((a, b) => a.id.localeCompare(b.id));
  removed.sort();
  changed.sort((a, b) => a.id.localeCompare(b.id));

  return {
    schemaVersion: 1,
    stateCode: STATE_CODE,
    stateName: STATE_NAME,
    generatedAt,
    fromVersion,
    toVersion,
    baseline: false,
    addedCount: added.length,
    removedCount: removed.length,
    changedCount: changed.length,
    added,
    removed,
    changed,
  };
}

function sha256(text) {
  return createHash('sha256').update(text).digest('hex');
}

function stateBounds(cameras) {
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

async function writeJSON(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(value)}\n`, 'utf8');
}

async function main() {
  const previousDataset = await readJSON(STATE_PATH);
  const previousManifest = await readJSON(MANIFEST_PATH);
  const previousEntry = previousManifest?.states?.find((entry) => entry.stateCode === STATE_CODE) ?? null;

  const elements = await fetchElements();
  const generatedAt = new Date().toISOString();
  const nextDataset = buildDataset(elements, generatedAt);
  validateDataset(nextDataset, previousDataset);

  const changed = stableCameraPayload(previousDataset) !== stableCameraPayload(nextDataset);
  if (!changed && previousEntry) {
    console.log(`No normalized camera changes detected (${nextDataset.cameras.length} records).`);
    return;
  }

  const nextVersion = previousEntry ? previousEntry.version + 1 : 1;
  const datasetText = `${JSON.stringify(nextDataset)}\n`;
  const datasetHash = sha256(datasetText);
  const changes = buildChanges(
    previousDataset,
    nextDataset,
    previousEntry?.version ?? null,
    nextVersion,
    generatedAt
  );

  const stateEntry = {
    stateCode: STATE_CODE,
    stateName: STATE_NAME,
    version: nextVersion,
    generatedAt,
    datasetURL: 'states/NJ.json',
    changesURL: 'changes/NJ.json',
    sha256: datasetHash,
    cameraCount: nextDataset.metadata.cameraCount,
    flockCount: nextDataset.metadata.flockCount,
    directionCount: nextDataset.metadata.directionCount,
    operatorCount: nextDataset.metadata.operatorCount,
    fileSizeBytes: Buffer.byteLength(datasetText),
    adjacentStates: ['NY', 'PA', 'DE'],
    bounds: stateBounds(nextDataset.cameras),
  };

  const states = (previousManifest?.states ?? [])
    .filter((entry) => entry.stateCode !== STATE_CODE)
    .concat(stateEntry)
    .sort((a, b) => a.stateCode.localeCompare(b.stateCode));

  const manifest = {
    schemaVersion: 1,
    generatedAt,
    source: 'OpenStreetMap',
    attribution: '© OpenStreetMap contributors',
    license: 'ODbL-1.0',
    totalCameraCount: states.reduce((sum, entry) => sum + entry.cameraCount, 0),
    totalFlockCount: states.reduce((sum, entry) => sum + entry.flockCount, 0),
    states,
  };

  await mkdir(dirname(STATE_PATH), { recursive: true });
  await writeFile(STATE_PATH, datasetText, 'utf8');
  await writeJSON(CHANGE_PATH, changes);
  await writeJSON(MANIFEST_PATH, manifest);

  console.log(
    `Published ${nextDataset.metadata.cameraCount} ${STATE_CODE} ALPR records as version ${nextVersion}: ` +
      `${nextDataset.metadata.flockCount} Flock-tagged, ` +
      `${nextDataset.metadata.directionCount} with bearing; ` +
      `${changes.addedCount} added, ${changes.removedCount} removed, ${changes.changedCount} changed.`
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});
