# FlockAware Data Platform

Public, machine-readable ALPR camera data used by **FlockAwareNJ+**, a Mignone Labs LLC application.

## What lives here

This repository is the app's remotely updated camera-data distribution layer. The private `FlockAwareNJ` repository contains the iOS app; this public repository contains the validated camera datasets, manifests, enrichment overlays, change records, alert events, source-processing code, and scheduled GitHub Actions.

The iPhone does **not** query DeFlock, Overpass, or Census services directly. GitHub Actions performs that upstream work, validates the result, and commits static publication files here. The app then downloads only the state files it needs and caches a last-known-good copy on the device.

## Published endpoints

- `us_state_manifest.json` — national totals, per-state versions, file sizes, checksums, state bounds, and URLs
- `states/STATE.json` — normalized camera records for each of the 50 states and District of Columbia
- `changes/STATE.json` — additions, removals, and material record changes since the state's previous version
- `alerts/latest.json` — compact, deduplicatable events for cameras newly indexed in the latest publication
- `enrichment/STATE.json` — direct OpenStreetMap metadata overlays used by the publication builder

## Coverage and enrichment model

FlockAware uses two complementary public inputs:

1. A compact DeFlock national position index provides broad United States camera coverage with stable state files.
2. Direct OpenStreetMap/Overpass records provide richer metadata where a record can be conservatively matched by location and compatible vendor data.

A successful enrichment can add fields such as bearing, OSM object ID and update time, source link, operator, municipality, county, surveillance zone, and mount type. The compact camera ID and coordinates remain stable so an enrichment-only change is not reported as a newly installed camera. Unmatched OSM overlay records are not silently appended to the compact base.

New Jersey retains its existing richer direct-OSM publication while the same enrichment process expands nationally.

## New-camera alert contract

Every validated state publication is compared with its prior version. A new-camera event is emitted only for a camera ID newly present in the broad published index; metadata enrichment of an existing ID does not generate an addition event.

`alerts/latest.json` is intended for:

- on-device checks when FlockAwareNJ+ opens or the user taps **Check Now**;
- a secure server worker that matches events against saved watch areas;
- future remote APNs/FCM push, email, and opted-in SMS delivery.

An event means **newly indexed in the public data**, not necessarily newly installed in the physical world. Delivery services must deduplicate by `eventId` and retain the last processed `publicationId`.

## Automated update model

1. GitHub Actions downloads the compact national index and official Census state boundaries.
2. Records are assigned to states and normalized.
3. Available direct-OSM overlays are conservatively matched and merged.
4. Integrity checks reject empty, partial, malformed, checksum-invalid, or suspiciously reduced results.
5. Each state file is compared with its previous version.
6. State change feeds, the compact alert feed, SHA-256 checksums, and the national manifest are generated.
7. Only a complete validated publication is committed.

The broad national refresh runs every six hours. Direct-OSM enrichment runs daily for priority states and can be run manually for any state or for the full country. A failed upstream request never replaces a valid last-known-good publication.

## Important limitation

A record in this repository is a mapped or indexed ALPR location, not an assertion that Mignone Labs LLC independently inspected or verified the installation. Public source data may be incomplete, duplicated, inaccurate, moved, removed, or outdated.

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
├── alerts/
├── enrichment/
├── pipeline/
├── scripts/
├── .github/workflows/
├── DATA_LICENSE.md
└── README.md
```

## Local validation

Python 3.12 and Node.js 22 or later:

```bash
python -m pip install -r pipeline/requirements.txt
PYTHONPATH=pipeline python -m unittest pipeline/test_enrichment.py
node --check scripts/refresh_osm_enrichment.mjs
python pipeline/build_us_states.py
python pipeline/validate_us_publication.py
```

Refresh selected OSM enrichment overlays:

```bash
node scripts/refresh_osm_enrichment.mjs --states=PA,NY,CT
```

Use `--all` for all 50 states plus the District of Columbia.
