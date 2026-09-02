#!/usr/bin/env python3
"""Independent integrity validation for the published U.S. camera datasets."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "us_state_manifest.json"
STATES_DIR = ROOT / "states"
CHANGES_DIR = ROOT / "changes"
EXPECTED_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}
MINIMUM_NATIONAL_CAMERA_COUNT = 50_000


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fail(message: str) -> None:
    raise RuntimeError(message)


def validate_camera(camera: dict[str, Any], state_code: str, seen: set[str]) -> None:
    camera_id = str(camera.get("id") or "")
    if not camera_id:
        fail(f"{state_code}: camera is missing an ID")
    if camera_id in seen:
        fail(f"{state_code}: duplicate camera ID {camera_id}")
    seen.add(camera_id)

    latitude = camera.get("latitude")
    longitude = camera.get("longitude")
    if not isinstance(latitude, (int, float)) or not math.isfinite(latitude) or not -90 <= latitude <= 90:
        fail(f"{state_code}: invalid latitude for {camera_id}")
    if not isinstance(longitude, (int, float)) or not math.isfinite(longitude) or not -180 <= longitude <= 180:
        fail(f"{state_code}: invalid longitude for {camera_id}")

    record_state = camera.get("stateCode")
    if record_state is not None and str(record_state).upper() != state_code:
        fail(f"{state_code}: {camera_id} declares state {record_state}")


def main() -> None:
    if not MANIFEST_PATH.exists():
        fail("us_state_manifest.json is missing")

    manifest = read_json(MANIFEST_PATH)
    if manifest.get("schemaVersion") != 1:
        fail(f"Unsupported manifest schema: {manifest.get('schemaVersion')}")

    entries = manifest.get("states")
    if not isinstance(entries, list):
        fail("Manifest states is not an array")

    by_code = {str(entry.get("stateCode", "")).upper(): entry for entry in entries}
    if set(by_code) != EXPECTED_CODES:
        missing = sorted(EXPECTED_CODES.difference(by_code))
        extra = sorted(set(by_code).difference(EXPECTED_CODES))
        fail(f"Manifest jurisdiction mismatch; missing={missing}, extra={extra}")

    total_cameras = 0
    total_flock = 0
    global_ids: set[str] = set()

    for state_code in sorted(EXPECTED_CODES):
        entry = by_code[state_code]
        dataset_path = STATES_DIR / f"{state_code}.json"
        changes_path = CHANGES_DIR / f"{state_code}.json"
        if not dataset_path.exists():
            fail(f"{state_code}: state dataset is missing")
        if not changes_path.exists():
            fail(f"{state_code}: change feed is missing")

        raw = dataset_path.read_bytes()
        if sha256_hex(raw) != str(entry.get("sha256", "")).lower():
            fail(f"{state_code}: SHA-256 mismatch")
        if len(raw) != entry.get("fileSizeBytes"):
            fail(f"{state_code}: file size mismatch")

        dataset = json.loads(raw)
        metadata = dataset.get("metadata")
        cameras = dataset.get("cameras")
        if not isinstance(metadata, dict) or not isinstance(cameras, list):
            fail(f"{state_code}: invalid dataset structure")
        if metadata.get("schemaVersion") not in (1, 2):
            fail(f"{state_code}: unsupported dataset schema")
        if metadata.get("cameraCount") != len(cameras):
            fail(f"{state_code}: metadata camera count mismatch")
        if entry.get("cameraCount") != len(cameras):
            fail(f"{state_code}: manifest camera count mismatch")

        state_ids: set[str] = set()
        flock_count = 0
        bearing_count = 0
        operator_count = 0
        for camera in cameras:
            validate_camera(camera, state_code, state_ids)
            # Global IDs from enriched datasets can be truly global; compact
            # state index IDs include coordinates and are also expected unique.
            if camera["id"] in global_ids:
                fail(f"Camera ID appears in multiple state files: {camera['id']}")
            global_ids.add(camera["id"])
            if "flock" in str(camera.get("vendor") or "").casefold():
                flock_count += 1
            if camera.get("directionDegrees") is not None:
                bearing_count += 1
            if str(camera.get("operatorName") or "").strip():
                operator_count += 1

        expected_counts = (
            entry.get("flockCount"),
            entry.get("directionCount"),
            entry.get("operatorCount"),
        )
        actual_counts = (flock_count, bearing_count, operator_count)
        if expected_counts != actual_counts:
            fail(f"{state_code}: manifest attribute counts mismatch {expected_counts} != {actual_counts}")
        metadata_counts = (
            metadata.get("flockCount"),
            metadata.get("directionCount"),
            metadata.get("operatorCount"),
        )
        if metadata_counts != actual_counts:
            fail(f"{state_code}: metadata attribute counts mismatch {metadata_counts} != {actual_counts}")

        bounds = entry.get("bounds") or {}
        required_bounds = ("north", "south", "east", "west")
        if any(not isinstance(bounds.get(key), (int, float)) for key in required_bounds):
            fail(f"{state_code}: invalid or missing state bounds")
        if bounds["north"] <= bounds["south"] or bounds["east"] <= bounds["west"]:
            fail(f"{state_code}: inverted state bounds")

        changes = read_json(changes_path)
        if changes.get("stateCode") != state_code:
            fail(f"{state_code}: change feed state mismatch")
        if changes.get("toVersion") != entry.get("version"):
            fail(f"{state_code}: change feed version mismatch")
        if changes.get("addedCount") != len(changes.get("added", [])):
            fail(f"{state_code}: change feed added count mismatch")
        if changes.get("removedCount") != len(changes.get("removed", [])):
            fail(f"{state_code}: change feed removed count mismatch")
        if changes.get("changedCount") != len(changes.get("changed", [])):
            fail(f"{state_code}: change feed changed count mismatch")

        total_cameras += len(cameras)
        total_flock += flock_count

    if total_cameras < MINIMUM_NATIONAL_CAMERA_COUNT:
        fail(f"National publication has only {total_cameras:,} cameras")
    if manifest.get("totalCameraCount") != total_cameras:
        fail("Manifest national camera total does not match state files")
    if manifest.get("totalFlockCount") != total_flock:
        fail("Manifest national Flock total does not match state files")

    print(
        f"Validated {total_cameras:,} cameras across 50 states + DC "
        f"({total_flock:,} Flock-tagged)."
    )


if __name__ == "__main__":
    main()
