import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const manifestPath = path.join(root, "us_state_manifest.json");
const statisticsDirectory = path.join(root, "statistics");
const historyPath = path.join(statisticsDirectory, "history.json");

if (!fs.existsSync(manifestPath)) {
  throw new Error("us_state_manifest.json is required before statistics can be recorded");
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const states = Array.isArray(manifest.states) ? manifest.states : [];

const summedCameraCount = states.reduce((total, state) => total + Number(state.cameraCount || 0), 0);
const summedFlockCount = states.reduce((total, state) => total + Number(state.flockCount || 0), 0);

const stateValues = Object.fromEntries(
  states
    .filter((state) => typeof state.stateCode === "string" && state.stateCode.trim())
    .map((state) => [
      state.stateCode.trim().toUpperCase(),
      {
        stateName: state.stateName || state.stateCode,
        cameraCount: Number(state.cameraCount || 0),
        flockCount: Number(state.flockCount || 0),
      },
    ])
);

const snapshot = {
  capturedAt: manifest.generatedAt || new Date().toISOString(),
  sourceBuild: manifest.sourceBuild || manifest.generatedAt || null,
  national: {
    cameraCount: Number.isFinite(manifest.totalCameraCount)
      ? Number(manifest.totalCameraCount)
      : summedCameraCount,
    flockCount: Number.isFinite(manifest.totalFlockCount)
      ? Number(manifest.totalFlockCount)
      : summedFlockCount,
    jurisdictionCount: states.length,
  },
  states: stateValues,
};

let history = {
  schemaVersion: 1,
  updatedAt: null,
  snapshots: [],
};

if (fs.existsSync(historyPath)) {
  const decoded = JSON.parse(fs.readFileSync(historyPath, "utf8"));
  if (decoded.schemaVersion !== 1 || !Array.isArray(decoded.snapshots)) {
    throw new Error("statistics/history.json has an unsupported schema");
  }
  history = decoded;
}

const signature = (value) =>
  JSON.stringify({
    sourceBuild: value.sourceBuild,
    national: value.national,
    states: Object.keys(value.states || {})
      .sort()
      .map((code) => [code, value.states[code].cameraCount, value.states[code].flockCount]),
  });

const snapshotSignature = signature(snapshot);
const lastSnapshot = history.snapshots.at(-1);

if (lastSnapshot && signature(lastSnapshot) === snapshotSignature) {
  console.log("Statistics unchanged; existing latest snapshot is already current.");
  process.exit(0);
}

history.snapshots.push(snapshot);
history.snapshots.sort((a, b) => String(a.capturedAt).localeCompare(String(b.capturedAt)));

// More than enough history for twice-weekly national publication plus NJ refreshes.
if (history.snapshots.length > 1000) {
  history.snapshots = history.snapshots.slice(-1000);
}

history.updatedAt = new Date().toISOString();
fs.mkdirSync(statisticsDirectory, { recursive: true });
fs.writeFileSync(historyPath, `${JSON.stringify(history, null, 2)}\n`, "utf8");

console.log(
  `Recorded statistics snapshot: ${snapshot.national.cameraCount} ALPR / ${snapshot.national.flockCount} Flock across ${snapshot.national.jurisdictionCount} jurisdictions.`
);
