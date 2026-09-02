#!/usr/bin/env node

/**
 * Refresh direct OpenStreetMap metadata overlays.
 *
 * These files do not replace the compact national DeFlock index. They are
 * optional enrichment layers that the publication builder matches onto stable
 * compact camera IDs. A failed Overpass request therefore never removes broad
 * national coverage or turns a source refresh into a false new-camera alert.
 */

import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const ROOT = resolve(process.cwd());
const ENRICHMENT_DIR = resolve(ROOT, 'enrichment');
const REQUEST_TIMEOUT_MS = 210_000;
const RETRIES_PER_ENDPOINT = 2;
const MAX_ALLOWED_DROP_FRACTION = 0.35;
const BETWEEN_STATE_DELAY_MS = 900;

const OVERPASS_ENDPOINTS = [
  'https://overpass.deflock.org/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://overpass-api.de/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];

const USER_AGENT =
  'FlockAware-Data/1.0 (+https://github.com/mattymigs/flockaware-data; mattmignone@gmail.com)';

const PRIORITY_STATE_CODES = ['NJ', 'NY', 'PA', 'CT', 'DE', 'MD', 'VA', 'MA', 'RI'];

const STATE_DEFINITIONS = [
  ['AL', 'Alabama'], ['AK', 'Alaska'], ['AZ', 'Arizona'], ['AR', 'Arkansas'],
  ['CA', 'California'], ['CO', 'Colorado'], ['CT', 'Connecticut'], ['DE', 'Delaware'],
  ['DC', 'District of Columbia'], ['FL', 'Florida'], ['GA', 'Georgia'], ['HI', 'Hawaii'],
  ['ID', 'Idaho'], ['IL', 'Illinois'], ['IN', 'Indiana'], ['IA', 'Iowa'],
  ['KS', 'Kansas'], ['KY', 'Kentucky'], ['LA', 'Louisiana'], ['ME', 'Maine'],
  ['MD', 'Maryland'], ['MA', 'Massachusetts'], ['MI', 'Michigan'], ['MN', 'Minnesota'],
  ['MS', 'Mississippi'], ['MO', 'Missouri'], ['MT', 'Montana'], ['NE', 'Nebraska'],
  ['NV', 'Nevada'], ['NH', 'New Hampshire'], ['NJ', 'New Jersey'], ['NM', 'New Mexico'],
  ['NY', 'New York'], ['NC', 'North Carolina'], ['ND', 'North Dakota'], ['OH', 'Ohio'],
  ['OK', 'Oklahoma'], ['OR', 'Oregon'], ['PA', 'Pennsylvania'], ['RI', 'Rhode Island'],
  ['SC', 'South Carolina'], ['SD', 'South Dakota'], ['TN', 'Tennessee'], ['TX', 'Texas'],
  ['UT', 'Utah'], ['VT', 'Vermont'], ['VA', 'Virginia'], ['WA', 'Washington'],
  ['WV', 'West Virginia'], ['WI', 'Wisconsin'], ['WY', 'Wyoming'],
].map(([stateCode, stateName]) => ({ stateCode, stateName }));

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
  if (tags.man_made !== 'surveillance' || tags['surveillance:type'] !== 'ALPR') {
    return null;
  }

  const osmType = cleaned(element.type) ?? 'node';
  const osmId = Number.isInteger(element.id) ? element.id : null;
  if (osmId === null) return null;

  const direction = parseDirection(tags.direction || tags['camera:direction']);
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
    stateCode,
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

function queryFor(stateCode) {
  return `[out:json][timeout:190];\narea["ISO3166-2"="US-${stateCode}"]["admin_level"="4"]->.state;\n(\n  node["man_made"="surveillance"]["surveillance:type"="ALPR"](area.state);\n  way["man_made"="surveillance"]["surveillance:type"="ALPR"](area.state);\n);\nout body center meta;`;
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
  for (const endpoint of OVERPASS_ENDPOINTS) {
    for (let attempt = 1; attempt <= RETRIES_PER_ENDPOINT; attempt += 1) {
      try {
        console.log(`[${stateCode}] ${endpoint} attempt ${attempt}/${RETRIES_PER_ENDPOINT}`);
        return await queryEndpoint(endpoint, stateCode);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        errors.push(`${endpoint}: ${message}`);
        console.warn(`[${stateCode}] ${message}`);
        if (attempt < RETRIES_PER_ENDPOINT) await sleep(1_500 * attempt);
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

function selectedDefinitions() {
  const allRequested = process.argv.includes('--all');
  const statesArgument = process.argv.find((value) => value.startsWith('--states='));
  const codes = allRequested
    ? new Set(STATE_DEFINITIONS.map(({ stateCode }) => stateCode))
    : statesArgument
      ? new Set(statesArgument.slice('--states='.length).split(',').map((value) => value.trim().toUpperCase()).filter(Boolean))
      : new Set(PRIORITY_STATE_CODES);

  const definitions = STATE_DEFINITIONS.filter(({ stateCode }) => codes.has(stateCode));
  const unknown = [...codes].filter(
    (code) => !STATE_DEFINITIONS.some(({ stateCode }) => stateCode === code)
  );
  if (unknown.length > 0) throw new Error(`Unknown state codes: ${unknown.join(', ')}`);
  return definitions;
}

async function writeAtomicJSON(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}-${Date.now()}`;
  await writeFile(temporary, `${JSON.stringify(value)}\n`, 'utf8');
  await rename(temporary, path);
}

async function refreshState({ stateCode, stateName }) {
  const destination = resolve(ENRICHMENT_DIR, `${stateCode}.json`);
  const previous = await readJSON(destination);
  let elements;
  try {
    elements = await fetchStateElements(stateCode);
  } catch (error) {
    if (previous?.cameras?.length >= 0) {
      console.warn(`[${stateCode}] preserving last-known-good enrichment after fetch failure`);
      return { stateCode, changed: false, retained: true, count: previous.cameras.length };
    }
    throw error;
  }

  const cameras = normalizeElements(elements, stateCode);
  if (previous?.cameras?.length > 25) {
    const minimumAllowed = Math.floor(previous.cameras.length * (1 - MAX_ALLOWED_DROP_FRACTION));
    if (cameras.length < minimumAllowed) {
      throw new Error(
        `[${stateCode}] suspicious enrichment drop ${previous.cameras.length} -> ${cameras.length}`
      );
    }
  }

  const next = {
    schemaVersion: 1,
    stateCode,
    stateName,
    generatedAt: new Date().toISOString(),
    source: 'OpenStreetMap Overpass',
    sourceURL: 'https://www.openstreetmap.org',
    cameraCount: cameras.length,
    cameras,
  };
  const unchanged = previous && JSON.stringify(previous.cameras) === JSON.stringify(cameras);
  if (unchanged) {
    console.log(`[${stateCode}] enrichment unchanged (${cameras.length.toLocaleString()} records)`);
    return { stateCode, changed: false, retained: false, count: cameras.length };
  }

  await writeAtomicJSON(destination, next);
  console.log(`[${stateCode}] wrote ${cameras.length.toLocaleString()} rich OSM records`);
  return { stateCode, changed: true, retained: false, count: cameras.length };
}

async function main() {
  const definitions = selectedDefinitions();
  await mkdir(ENRICHMENT_DIR, { recursive: true });

  let refreshed = 0;
  let retained = 0;
  let total = 0;
  let failures = 0;

  for (let index = 0; index < definitions.length; index += 1) {
    const definition = definitions[index];
    console.log(`\n=== ${definition.stateName} (${definition.stateCode}) ${index + 1}/${definitions.length} ===`);
    try {
      const result = await refreshState(definition);
      if (result.changed) refreshed += 1;
      if (result.retained) retained += 1;
      total += result.count;
    } catch (error) {
      failures += 1;
      console.error(error instanceof Error ? error.message : String(error));
    }
    if (index + 1 < definitions.length) await sleep(BETWEEN_STATE_DELAY_MS);
  }

  console.log(
    `\nOSM enrichment complete: ${refreshed} updated, ${retained} retained, ` +
      `${failures} failed, ${total.toLocaleString()} records represented.`
  );
  if (failures === definitions.length) process.exitCode = 1;
}

main().catch(async (error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  await rm(resolve(ROOT, '.osm-enrichment-tmp'), { recursive: true, force: true });
  process.exitCode = 1;
});
