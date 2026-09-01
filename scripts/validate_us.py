#!/usr/bin/env python3
"""Independently validate the complete FlockAware public data publication."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

EXPECTED_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}
MINIMUM_US_CAMERA_COUNT = 50000


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing required publication file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def count_where(cameras: list[dict[str, Any]], key: str, predicate) -> int:
    return sum(1 for camera in cameras if predicate(camera.get(key)))


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    manifest_path = root / "us_state_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_json(manifest_path)

    if manifest.get("schemaVersion") != 1:
        raise RuntimeError("Manifest schemaVersion must be 1")

    entries = manifest.get("states")
    if not isinstance(entries, list):
        raise RuntimeError("Manifest states must be an array")

    codes = [entry.get("stateCode") for entry in entries if isinstance(entry, dict)]
    if len(codes) != len(set(codes)):
        raise RuntimeError("Manifest contains duplicate state codes")
    if set(codes) != EXPECTED_CODES:
        missing = sorted(EXPECTED_CODES - set(codes))
        unexpected = sorted(set(codes) - EXPECTED_CODES)
        raise RuntimeError(
            f"Manifest jurisdiction mismatch; missing={missing}, unexpected={unexpected}"
        )

    total_camera_count = 0
    total_flock_count = 0
    total_file_size = 0
    global_ids: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Manifest contains a non-object state entry")

        code = entry["stateCode"]
        state_path = root / entry["datasetURL"]
        changes_path = root / entry["changesURL"]
        dataset_bytes = state_path.read_bytes()
        dataset = load_json(state_path)
        changes = load_json(changes_path)

        expected_hash = entry.get("sha256")
        actual_hash = hashlib.sha256(dataset_bytes).hexdigest()
        if expected_hash != actual_hash:
            raise RuntimeError(f"{code}: SHA-256 mismatch")

        if entry.get("fileSizeBytes") != len(dataset_bytes):
            raise RuntimeError(f"{code}: fileSizeBytes mismatch")

        metadata = dataset.get("metadata")
        cameras = dataset.get("cameras")
        if not isinstance(metadata, dict) or not isinstance(cameras, list):
            raise RuntimeError(f"{code}: invalid dataset shape")
        if metadata.get("schemaVersion") not in (1, 2):
            raise RuntimeError(f"{code}: unsupported dataset schema")
        if metadata.get("cameraCount") != len(cameras):
            raise RuntimeError(f"{code}: metadata camera count mismatch")
        if entry.get("cameraCount") != len(cameras):
            raise RuntimeError(f"{code}: manifest camera count mismatch")

        flock_count = count_where(
            cameras,
            "vendor",
            lambda value: isinstance(value, str) and "flock" in value.lower(),
        )
        direction_count = count_where(
            cameras,
            "directionDegrees",
            lambda value: isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value)),
        )
        operator_count = count_where(
            cameras,
            "operatorName",
            lambda value: isinstance(value, str) and bool(value.strip()),
        )

        expected_counts = {
            "flockCount": flock_count,
            "directionCount": direction_count,
            "operatorCount": operator_count,
        }
        for key, actual in expected_counts.items():
            if metadata.get(key) != actual or entry.get(key) != actual:
                raise RuntimeError(f"{code}: {key} mismatch")

        local_ids: set[str] = set()
        for camera in cameras:
            if not isinstance(camera, dict):
                raise RuntimeError(f"{code}: camera record is not an object")
            camera_id = camera.get("id")
            if not isinstance(camera_id, str) or not camera_id:
                raise RuntimeError(f"{code}: camera has no stable ID")
            if camera_id in local_ids:
                raise RuntimeError(f"{code}: duplicate camera ID {camera_id}")
            if camera_id in global_ids:
                raise RuntimeError(f"Camera ID appears in multiple states: {camera_id}")
            local_ids.add(camera_id)
            global_ids.add(camera_id)

            latitude = camera.get("latitude")
            longitude = camera.get("longitude")
            if (
                not isinstance(latitude, (int, float))
                or isinstance(latitude, bool)
                or not math.isfinite(float(latitude))
                or not -90 <= float(latitude) <= 90
            ):
                raise RuntimeError(f"{code}: invalid latitude for {camera_id}")
            if (
                not isinstance(longitude, (int, float))
                or isinstance(longitude, bool)
                or not math.isfinite(float(longitude))
                or not -180 <= float(longitude) <= 180
            ):
                raise RuntimeError(f"{code}: invalid longitude for {camera_id}")
            if camera.get("stateCode") not in (None, code):
                raise RuntimeError(f"{code}: stateCode mismatch for {camera_id}")

        if changes.get("stateCode") != code:
            raise RuntimeError(f"{code}: changes state code mismatch")
        if changes.get("toVersion") != entry.get("version"):
            raise RuntimeError(f"{code}: changes version mismatch")
        for key, collection_key in (
            ("addedCount", "added"),
            ("removedCount", "removed"),
            ("changedCount", "changed"),
        ):
            collection = changes.get(collection_key)
            if not isinstance(collection, list) or changes.get(key) != len(collection):
                raise RuntimeError(f"{code}: {key} mismatch")

        bounds = entry.get("bounds")
        if not isinstance(bounds, dict):
            raise RuntimeError(f"{code}: missing geographic bounds")
        if not (
            bounds.get("south") < bounds.get("north")
            and bounds.get("west") < bounds.get("east")
        ):
            raise RuntimeError(f"{code}: invalid geographic bounds")

        total_camera_count += len(cameras)
        total_flock_count += flock_count
        total_file_size += len(dataset_bytes)

    if total_camera_count < MINIMUM_US_CAMERA_COUNT:
        raise RuntimeError(
            f"Publication has only {total_camera_count:,} cameras; "
            f"minimum is {MINIMUM_US_CAMERA_COUNT:,}"
        )
    if manifest.get("totalCameraCount") != total_camera_count:
        raise RuntimeError("Manifest national camera total mismatch")
    if manifest.get("totalFlockCount") != total_flock_count:
        raise RuntimeError("Manifest national Flock total mismatch")
    if manifest.get("totalFileSizeBytes") != total_file_size:
        raise RuntimeError("Manifest national file-size total mismatch")

    print(
        f"Validated {total_camera_count:,} cameras across {len(entries)} jurisdictions, "
        f"{total_flock_count:,} Flock-tagged, "
        f"{total_file_size / 1_048_576:.1f} MiB."
    )
    print(f"Manifest bytes: {len(manifest_bytes):,}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {error}", file=sys.stderr)
        raise
