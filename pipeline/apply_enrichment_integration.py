#!/usr/bin/env python3

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


build_path = Path("pipeline/build_us_states.py")
text = build_path.read_text()

text = replace_once(
    text,
    "from typing import Any, Iterable\n\nimport shapefile  # pyshp\n",
    "from typing import Any, Iterable\n\nfrom enrichment import EnrichmentStats, enrich_cameras, has_rich_metadata\nimport shapefile  # pyshp\n",
    "build import insertion point",
)

text = replace_once(
    text,
    'MANIFEST_PATH = ROOT / "us_state_manifest.json"\n\nINDEX_SIDECAR_URL =',
    'MANIFEST_PATH = ROOT / "us_state_manifest.json"\nENRICHMENT_DIR = ROOT / "enrichment"\nALERTS_DIR = ROOT / "alerts"\nALERT_LATEST_PATH = ALERTS_DIR / "latest.json"\n\nINDEX_SIDECAR_URL =',
    "build path constants insertion point",
)

text = replace_once(
    text,
    '        "county": camera.get("county"),\n        "sourceURL": camera.get("sourceURL"),\n    }\n',
    '        "county": camera.get("county"),\n        "sourceURL": camera.get("sourceURL"),\n        "stateCode": camera.get("stateCode"),\n        "detailLevel": camera.get("detailLevel"),\n    }\n',
    "camera summary block",
)

helpers = '''def load_enrichment_overlay(state_code: str) -> dict[str, Any] | None:
    overlay = read_json(ENRICHMENT_DIR / f"{state_code}.json")
    if not isinstance(overlay, dict):
        return None
    if int(overlay.get("schemaVersion", 0)) != 1:
        print(f"{state_code}: ignoring unsupported enrichment schema")
        return None
    cameras = overlay.get("cameras")
    if not isinstance(cameras, list):
        print(f"{state_code}: ignoring malformed enrichment overlay")
        return None
    declared_count = overlay.get("cameraCount")
    if declared_count is not None and declared_count != len(cameras):
        print(f"{state_code}: ignoring enrichment count mismatch")
        return None
    return overlay


def stats_for_existing_dataset(dataset: dict[str, Any]) -> EnrichmentStats:
    cameras = dataset.get("cameras", [])
    enriched_count = sum(1 for camera in cameras if has_rich_metadata(camera))
    return EnrichmentStats(
        base_count=len(cameras),
        overlay_count=enriched_count,
        matched_count=enriched_count,
        enriched_count=enriched_count,
        basic_count=len(cameras) - enriched_count,
        unmatched_overlay_count=0,
    )


def alert_event(
    state_code: str,
    state_name: str,
    dataset_version: int,
    generated_at: str,
    camera: dict[str, Any],
) -> dict[str, Any]:
    camera_id = str(camera.get("id") or "")
    return {
        "eventId": f"{state_code}:v{dataset_version}:{camera_id}",
        "eventType": "camera_added",
        "stateCode": state_code,
        "stateName": state_name,
        "datasetVersion": dataset_version,
        "detectedAt": generated_at,
        "camera": camera,
    }


'''
text = replace_once(text, "def main() -> None:\n", helpers + "def main() -> None:\n", "main marker")

text = replace_once(
    text,
    '    previous_manifest = read_json(MANIFEST_PATH)\n    previous_entries = previous_entry_by_code(previous_manifest)\n\n    print("Downloading national position index…")\n',
    '    previous_manifest = read_json(MANIFEST_PATH)\n    previous_entries = previous_entry_by_code(previous_manifest)\n    latest_alert_events: list[dict[str, Any]] = []\n\n    print("Downloading national position index…")\n',
    "main initialization insertion point",
)

text = replace_once(
    text,
    '    staged_states = staging / "states"\n    staged_changes = staging / "changes"\n    staged_states.mkdir(parents=True)\n    staged_changes.mkdir(parents=True)\n',
    '    staged_states = staging / "states"\n    staged_changes = staging / "changes"\n    staged_alerts = staging / "alerts"\n    staged_states.mkdir(parents=True)\n    staged_changes.mkdir(parents=True)\n    staged_alerts.mkdir(parents=True)\n',
    "staging block",
)

old_candidate = '''            # Preserve the enriched NJ dataset produced by refresh_nj.mjs. All
            # other states are built from the compact national index in phase 1.
            if state_code == "NJ" and old_dataset and old_dataset.get("cameras"):
                candidate = old_dataset
            else:
                occurrences: dict[tuple[int, int, int], int] = defaultdict(int)
                cameras: list[dict[str, Any]] = []
                for record in sorted(
                    assigned[state_code],
                    key=lambda item: (
                        -item.latitude_microdegrees,
                        item.longitude_microdegrees,
                        item.brand_id,
                    ),
                ):
                    key = (
                        record.latitude_microdegrees,
                        record.longitude_microdegrees,
                        record.brand_id,
                    )
                    occurrence = occurrences[key]
                    occurrences[key] += 1
                    cameras.append(camera_from_record(record, occurrence, state_code))

                flock_count, bearing_count, operator_count = dataset_counts(cameras)
                candidate = {
                    "metadata": {
                        "schemaVersion": 2,
                        "generatedAt": generated_at,
                        "jurisdiction": boundary.name,
                        "source": "OpenStreetMap via DeFlock public camera index",
                        "sourceURL": INDEX_SIDECAR_URL,
                        "attribution": "© OpenStreetMap contributors",
                        "license": "ODbL-1.0",
                        "cameraCount": len(cameras),
                        "flockCount": flock_count,
                        "directionCount": bearing_count,
                        "operatorCount": operator_count,
                        "isDemo": False,
                    },
                    "cameras": cameras,
                }
'''
new_candidate = '''            # Keep the existing rich New Jersey publication stable while the
            # same direct-OSM enrichment system is rolled out nationally.
            if state_code == "NJ" and old_dataset and old_dataset.get("cameras"):
                candidate = old_dataset
                enrichment_stats = stats_for_existing_dataset(candidate)
            else:
                occurrences: dict[tuple[int, int, int], int] = defaultdict(int)
                base_cameras: list[dict[str, Any]] = []
                for record in sorted(
                    assigned[state_code],
                    key=lambda item: (
                        -item.latitude_microdegrees,
                        item.longitude_microdegrees,
                        item.brand_id,
                    ),
                ):
                    key = (
                        record.latitude_microdegrees,
                        record.longitude_microdegrees,
                        record.brand_id,
                    )
                    occurrence = occurrences[key]
                    occurrences[key] += 1
                    base_cameras.append(camera_from_record(record, occurrence, state_code))

                overlay = load_enrichment_overlay(state_code)
                overlay_cameras = overlay.get("cameras", []) if overlay else []
                cameras, enrichment_stats = enrich_cameras(
                    base_cameras,
                    overlay_cameras,
                    overlay_source=overlay.get("source") if overlay else None,
                )

                flock_count, bearing_count, operator_count = dataset_counts(cameras)
                source = "OpenStreetMap via DeFlock public camera index"
                if enrichment_stats.matched_count > 0:
                    source += " + direct OSM metadata enrichment"
                candidate = {
                    "metadata": {
                        "schemaVersion": 2,
                        "generatedAt": generated_at,
                        "jurisdiction": boundary.name,
                        "source": source,
                        "sourceURL": INDEX_SIDECAR_URL,
                        "attribution": "© OpenStreetMap contributors",
                        "license": "ODbL-1.0",
                        "cameraCount": len(cameras),
                        "flockCount": flock_count,
                        "directionCount": bearing_count,
                        "operatorCount": operator_count,
                        "isDemo": False,
                        "enrichmentGeneratedAt": overlay.get("generatedAt") if overlay else None,
                        "enrichmentStats": enrichment_stats.as_dict(),
                    },
                    "cameras": cameras,
                }
'''
text = replace_once(text, old_candidate, new_candidate, "state candidate block")

text = replace_once(
    text,
    '                (staged_changes / f"{state_code}.json").write_bytes(compact_json_bytes(changes))\n            else:\n',
    '                (staged_changes / f"{state_code}.json").write_bytes(compact_json_bytes(changes))\n                if not changes.get("baseline"):\n                    latest_alert_events.extend(\n                        alert_event(state_code, boundary.name, version, generated_at, camera)\n                        for camera in changes.get("added", [])\n                    )\n            else:\n',
    "change alert insertion point",
)

text = replace_once(
    text,
    '                "operatorCount": operator_count,\n                "fileSizeBytes": len(dataset_bytes),\n',
    '                "operatorCount": operator_count,\n                "enrichedCount": enrichment_stats.enriched_count,\n                "basicCount": enrichment_stats.basic_count,\n                "enrichmentMatchCount": enrichment_stats.matched_count,\n                "fileSizeBytes": len(dataset_bytes),\n',
    "manifest enrichment insertion point",
)

text = replace_once(
    text,
    '            "unassignedSourceCount": len(unassigned),\n            "states": manifest_entries,\n        }\n',
    '            "unassignedSourceCount": len(unassigned),\n            "alertFeedURL": "alerts/latest.json",\n            "states": manifest_entries,\n        }\n\n        latest_alert_events.sort(key=lambda event: event["eventId"])\n        alert_feed = {\n            "schemaVersion": 1,\n            "publicationId": f"{sidecar.get(\'build\') or generated_at}:{generated_at}",\n            "generatedAt": generated_at,\n            "sourceBuild": sidecar.get("build"),\n            "eventCount": len(latest_alert_events),\n            "states": sorted({event["stateCode"] for event in latest_alert_events}),\n            "events": latest_alert_events,\n        }\n',
    "manifest alert feed insertion point",
)

text = replace_once(
    text,
    '        staged_manifest = staging / "us_state_manifest.json"\n        staged_manifest.write_bytes(compact_json_bytes(manifest))\n',
    '        staged_manifest = staging / "us_state_manifest.json"\n        staged_manifest.write_bytes(compact_json_bytes(manifest))\n        (staged_alerts / "latest.json").write_bytes(compact_json_bytes(alert_feed))\n',
    "staged manifest insertion point",
)

text = replace_once(
    text,
    '        STATES_DIR.mkdir(parents=True, exist_ok=True)\n        CHANGES_DIR.mkdir(parents=True, exist_ok=True)\n',
    '        STATES_DIR.mkdir(parents=True, exist_ok=True)\n        CHANGES_DIR.mkdir(parents=True, exist_ok=True)\n        ALERTS_DIR.mkdir(parents=True, exist_ok=True)\n',
    "publication directories insertion point",
)

text = replace_once(
    text,
    '        for path in staged_changes.glob("*.json"):\n            os.replace(path, CHANGES_DIR / path.name)\n        os.replace(staged_manifest, MANIFEST_PATH)\n',
    '        for path in staged_changes.glob("*.json"):\n            os.replace(path, CHANGES_DIR / path.name)\n        os.replace(staged_alerts / "latest.json", ALERT_LATEST_PATH)\n        os.replace(staged_manifest, MANIFEST_PATH)\n',
    "alert publication insertion point",
)

text = replace_once(
    text,
    '            f"({total_flock:,} Flock-tagged)."\n        )\n',
    '            f"({total_flock:,} Flock-tagged; "\n            f"{len(latest_alert_events):,} new-camera alert events)."\n        )\n',
    "publication summary insertion point",
)

build_path.write_text(text)

validator_path = Path("pipeline/validate_us_publication.py")
validator = validator_path.read_text()
validator = replace_once(
    validator,
    'CHANGES_DIR = ROOT / "changes"\nEXPECTED_CODES = {\n',
    'CHANGES_DIR = ROOT / "changes"\nALERTS_DIR = ROOT / "alerts"\nEXPECTED_CODES = {\n',
    "validator constants insertion point",
)
validator = replace_once(
    validator,
    '    record_state = camera.get("stateCode")\n    if record_state is not None and str(record_state).upper() != state_code:\n        fail(f"{state_code}: {camera_id} declares state {record_state}")\n',
    '    record_state = camera.get("stateCode")\n    if record_state is not None and str(record_state).upper() != state_code:\n        fail(f"{state_code}: {camera_id} declares state {record_state}")\n\n    detail_level = camera.get("detailLevel")\n    if detail_level is not None and detail_level not in {"basic", "enriched"}:\n        fail(f"{state_code}: {camera_id} has invalid detailLevel {detail_level}")\n',
    "validator camera detail insertion point",
)
validator = replace_once(
    validator,
    '        if metadata_counts != actual_counts:\n            fail(f"{state_code}: metadata attribute counts mismatch {metadata_counts} != {actual_counts}")\n\n        bounds = entry.get("bounds") or {}\n',
    '        if metadata_counts != actual_counts:\n            fail(f"{state_code}: metadata attribute counts mismatch {metadata_counts} != {actual_counts}")\n\n        enriched_count = sum(1 for camera in cameras if camera.get("detailLevel") == "enriched")\n        if entry.get("enrichedCount") is not None:\n            if entry.get("enrichedCount") != enriched_count:\n                fail(f"{state_code}: manifest enriched count mismatch")\n            if entry.get("basicCount") != len(cameras) - enriched_count:\n                fail(f"{state_code}: manifest basic count mismatch")\n\n        bounds = entry.get("bounds") or {}\n',
    "validator enrichment count insertion point",
)
validator = replace_once(
    validator,
    '    if manifest.get("totalFlockCount") != total_flock:\n        fail("Manifest national Flock total does not match state files")\n\n    print(\n',
    '    if manifest.get("totalFlockCount") != total_flock:\n        fail("Manifest national Flock total does not match state files")\n\n    alert_feed_url = manifest.get("alertFeedURL")\n    if alert_feed_url:\n        alert_path = ROOT / str(alert_feed_url)\n        if not alert_path.exists():\n            fail("Manifest alert feed is missing")\n        alert_feed = read_json(alert_path)\n        if alert_feed.get("schemaVersion") != 1:\n            fail("Unsupported new-camera alert feed schema")\n        events = alert_feed.get("events")\n        if not isinstance(events, list) or alert_feed.get("eventCount") != len(events):\n            fail("New-camera alert feed event count mismatch")\n        event_ids: set[str] = set()\n        for event in events:\n            event_id = str(event.get("eventId") or "")\n            if not event_id or event_id in event_ids:\n                fail(f"Invalid or duplicate new-camera event ID: {event_id!r}")\n            event_ids.add(event_id)\n            state_code = str(event.get("stateCode") or "").upper()\n            if state_code not in EXPECTED_CODES:\n                fail(f"New-camera event has invalid state: {state_code}")\n            camera = event.get("camera")\n            if not isinstance(camera, dict):\n                fail(f"New-camera event {event_id} has no camera summary")\n            latitude = camera.get("latitude")\n            longitude = camera.get("longitude")\n            if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):\n                fail(f"New-camera event {event_id} has invalid coordinates")\n\n    print(\n',
    "validator alert feed insertion point",
)
validator_path.write_text(validator)
