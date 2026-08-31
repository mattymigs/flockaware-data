# FlockAware Data Platform

Public, machine-readable ALPR camera data used by **FlockAwareNJ+**, a Mignone Labs LLC application.

## Purpose

This repository is the app's remotely updated camera-data distribution layer. The iOS application code remains in the private `FlockAwareNJ` repository; this repository contains public data, manifests, change records, validation scripts, and automated refresh workflows.

## Published endpoints

Once the first refresh completes, the platform publishes:

- `us_state_manifest.json` — national manifest and per-state versions
- `states/NJ.json` — normalized New Jersey camera dataset
- `changes/NJ.json` — additions, removals, and material record changes since the previous published version

Additional state files will be added without changing the app's data contract.

## Update model

1. GitHub Actions queries the upstream public mapping source on a schedule.
2. Records are normalized and deduplicated by stable OpenStreetMap object ID.
3. Integrity checks reject empty, partial, malformed, or suspiciously reduced results.
4. The new state file is compared with the prior published file.
5. A versioned change record and SHA-256 checksum are generated.
6. Only validated changes are committed and published.

The app uses **remote current data → cached last-known-good data → bundled New Jersey fallback**. A failed refresh never replaces a valid published dataset.

## Important limitation

A record in this repository is a mapped ALPR location, not an assertion that Mignone Labs LLC independently inspected or verified the installation. OpenStreetMap is community maintained and may be incomplete, duplicated, inaccurate, moved, removed, or outdated. New-camera alerts mean **newly mapped in the published data**, not necessarily newly installed in the physical world.

## Attribution

Camera data derived from OpenStreetMap is distributed under the Open Database License (ODbL 1.0).

**© OpenStreetMap contributors**

See [DATA_LICENSE.md](DATA_LICENSE.md) for attribution, licensing, and provenance details.

## Repository layout

```text
flockaware-data/
├── us_state_manifest.json
├── states/
├── changes/
├── scripts/
├── .github/workflows/
├── DATA_LICENSE.md
└── README.md
```

## Local refresh

Node.js 22 or later:

```bash
node scripts/refresh_nj.mjs
```

The script writes files only when the normalized camera records actually change.
