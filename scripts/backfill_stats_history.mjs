import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const historyPath = path.join(root, "statistics", "history.json");
const maxCommits = 40;

const history = JSON.parse(fs.readFileSync(historyPath, "utf8"));
if (history.schemaVersion !== 1 || !Array.isArray(history.snapshots)) {
  throw new Error("statistics/history.json has an unsupported schema");
}

const commitList = execFileSync(
  "git",
  ["log", `-${maxCommits}`, "--format=%H", "--", "us_state_manifest.json"],
  { encoding: "utf8" }
)
  .trim()
  .split(/\s+/)
  .filter(Boolean)
  .reverse();

const stateValues = (manifest) =>
  Object.fromEntries(
    (manifest.states || [])
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

const makeSnapshot = (manifest, commit) => {
  const states = Array.isArray(manifest.states) ? manifest.states : [];
  const summedCameraCount = states.reduce((total, state) => total + Number(state.cameraCount || 0), 0);
  const summedFlockCount = states.reduce((total, state) => total + Number(state.flockCount || 0), 0);

  return {
    capturedAt: manifest.generatedAt || new Date().toISOString(),
    sourceBuild: manifest.sourceBuild || commit,
    national: {
      cameraCount: Number.isFinite(manifest.totalCameraCount)
        ? Number(manifest.totalCameraCount)
        : summedCameraCount,
      flockCount: Number.isFinite(manifest.totalFlockCount)
        ? Number(manifest.totalFlockCount)
        : summedFlockCount,
      jurisdictionCount: states.length,
    },
    states: stateValues(manifest),
  };
};

const signature = (snapshot) =>
  JSON.stringify({
    national: snapshot.national,
    states: Object.keys(snapshot.states || {})
      .sort()
      .map((code) => [code, snapshot.states[code].cameraCount, snapshot.states[code].flockCount]),
  });

const snapshotsByTimestamp = new Map(
  history.snapshots.map((snapshot) => [snapshot.capturedAt, snapshot])
);

for (const commit of commitList) {
  let raw;
  try {
    raw = execFileSync("git", ["show", `${commit}:us_state_manifest.json`], {
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
    });
  } catch {
    continue;
  }

  let manifest;
  try {
    manifest = JSON.parse(raw);
  } catch {
    continue;
  }

  if (!manifest.generatedAt || !Array.isArray(manifest.states) || manifest.states.length === 0) {
    continue;
  }

  snapshotsByTimestamp.set(manifest.generatedAt, makeSnapshot(manifest, commit));
}

const sorted = [...snapshotsByTimestamp.values()].sort((a, b) =>
  String(a.capturedAt).localeCompare(String(b.capturedAt))
);

// Remove consecutive duplicate publications while keeping real rises and drops.
const deduped = [];
for (const snapshot of sorted) {
  if (deduped.length === 0 || signature(deduped.at(-1)) !== signature(snapshot)) {
    deduped.push(snapshot);
  }
}

history.snapshots = deduped.slice(-1000);
history.updatedAt = new Date().toISOString();
fs.writeFileSync(historyPath, `${JSON.stringify(history, null, 2)}\n`, "utf8");

console.log(`Backfilled ${history.snapshots.length} verified publication snapshots.`);
