#!/usr/bin/env python3
"""Build validated per-state FlockAware camera datasets from public OSM-derived data."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import shapefile  # pyshp
    from shapely.geometry import Point, shape as shapely_shape
    from shapely.prepared import prep
except ImportError as exc:  # pragma: no cover - dependency check for CI logs
    raise SystemExit(
        "Missing pipeline dependency. Run: python3 -m pip install shapely pyshp"
    ) from exc


SOURCE_URL = os.environ.get(
    "FLOCKAWARE_SOURCE_URL",
    "https://data.dontgetflocked.com/cameras.geojson.gz",
)
BOUNDARY_URL = os.environ.get(
    "FLOCKAWARE_BOUNDARY_URL",
    "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_500k.zip",
)
USER_AGENT = (
    "FlockAware-Data/2.0 "
    "(+https://github.com/mattymigs/flockaware-data; mattmignone@gmail.com)"
)
MINIMUM_US_CAMERA_COUNT = int(os.environ.get("FLOCKAWARE_MIN_US_CAMERAS", "50000"))
MAXIMUM_ALLOWED_DROP_FRACTION = float(
    os.environ.get("FLOCKAWARE_MAX_DROP_FRACTION", "0.35")
)
MAXIMUM_UNASSIGNED_FRACTION = float(
    os.environ.get("FLOCKAWARE_MAX_UNASSIGNED_FRACTION", "0.01")
)

STATE_NAMES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}

ADJACENT_STATES: dict[str, list[str]] = {
    "AL": ["FL", "GA", "MS", "TN"],
    "AK": [],
    "AZ": ["CA", "CO", "NM", "NV", "UT"],
    "AR": ["LA", "MO", "MS", "OK", "TN", "TX"],
    "CA": ["AZ", "NV", "OR"],
    "CO": ["AZ", "KS", "NE", "NM", "OK", "UT", "WY"],
    "CT": ["MA", "NY", "RI"],
    "DE": ["MD", "NJ", "PA"],
    "FL": ["AL", "GA"],
    "GA": ["AL", "FL", "NC", "SC", "TN"],
    "HI": [],
    "ID": ["MT", "NV", "OR", "UT", "WA", "WY"],
    "IL": ["IA", "IN", "KY", "MO", "WI"],
    "IN": ["IL", "KY", "MI", "OH"],
    "IA": ["IL", "MN", "MO", "NE", "SD", "WI"],
    "KS": ["CO", "MO", "NE", "OK"],
    "KY": ["IL", "IN", "MO", "OH", "TN", "VA", "WV"],
    "LA": ["AR", "MS", "TX"],
    "ME": ["NH"],
    "MD": ["DC", "DE", "PA", "VA", "WV"],
    "MA": ["CT", "NH", "NY", "RI", "VT"],
    "MI": ["IN", "OH", "WI"],
    "MN": ["IA", "ND", "SD", "WI"],
    "MS": ["AL", "AR", "LA", "TN"],
    "MO": ["AR", "IA", "IL", "KS", "KY", "NE", "OK", "TN"],
    "MT": ["ID", "ND", "SD", "WY"],
    "NE": ["CO", "IA", "KS", "MO", "SD", "WY"],
    "NV": ["AZ", "CA", "ID", "OR", "UT"],
    "NH": ["MA", "ME", "VT"],
    "NJ": ["DE", "NY", "PA"],
    "NM": ["AZ", "CO", "OK", "TX", "UT"],
    "NY": ["CT", "MA", "NJ", "PA", "VT"],
    "NC": ["GA", "SC", "TN", "VA"],
    "ND": ["MN", "MT", "SD"],
    "OH": ["IN", "KY", "MI", "PA", "WV"],
    "OK": ["AR", "CO", "KS", "MO", "NM", "TX"],
    "OR": ["CA", "ID", "NV", "WA"],
    "PA": ["DE", "MD", "NJ", "NY", "OH", "WV"],
    "RI": ["CT", "MA"],
    "SC": ["GA", "NC"],
    "SD": ["IA", "MN", "MT", "ND", "NE", "WY"],
    "TN": ["AL", "AR", "GA", "KY", "MO", "MS", "NC", "VA"],
    "TX": ["AR", "LA", "NM", "OK"],
    "UT": ["AZ", "CO", "ID", "NM", "NV", "WY"],
    "VT": ["MA", "NH", "NY"],
    "VA": ["DC", "KY", "MD", "NC", "TN", "WV"],
    "WA": ["ID", "OR"],
    "WV": ["KY", "MD", "OH", "PA", "VA"],
    "WI": ["IA", "IL", "MI", "MN"],
    "WY": ["CO", "ID", "MT", "NE", "SD", "UT"],
    "DC": ["MD", "VA"],
}


@dataclass(frozen=True)
class StateBoundary:
    code: str
    name: str
    geometry: Any
    prepared: Any
    bounds: tuple[float, float, float, float]

    def bbox_contains(self, longitude: float, latitude: float, padding: float = 0.0) -> bool:
        minimum_x, minimum_y, maximum_x, maximum_y = self.bounds
        return (
            minimum_x - padding <= longitude <= maximum_x + padding
            and minimum_y - padding <= latitude <= maximum_y + padding
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def request_bytes(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/geo+json, application/zip, application/octet-stream",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        if not payload:
            raise RuntimeError(f"Empty response from {url}")
        content_encoding = (response.headers.get("Content-Encoding") or "").lower()
        if content_encoding == "gzip" and not payload.startswith(b"\x1f\x8b"):
            return payload
        return payload


def decode_possible_gzip(payload: bytes) -> bytes:
    if payload.startswith(b"\x1f\x8b"):
        return gzip.decompress(payload)
    return payload


def load_source_geojson(url: str) -> dict[str, Any]:
    print(f"Downloading public ALPR source: {url}")
    payload = decode_possible_gzip(request_bytes(url))
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        preview = payload[:240].decode("utf-8", errors="replace").replace("\n", " ")
        raise RuntimeError(f"Source was not valid JSON: {preview}") from exc

    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise RuntimeError("Source is not a GeoJSON FeatureCollection")
    return document


def load_state_boundaries(url: str) -> dict[str, StateBoundary]:
    print(f"Downloading Census state boundaries: {url}")
    payload = request_bytes(url)

    with tempfile.TemporaryDirectory(prefix="flockaware-boundaries-") as temporary_directory:
        zip_path = Path(temporary_directory) / "states.zip"
        zip_path.write_bytes(payload)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temporary_directory)

        shapefiles = list(Path(temporary_directory).glob("*.shp"))
        if len(shapefiles) != 1:
            raise RuntimeError(f"Expected one state shapefile, found {len(shapefiles)}")

        reader = shapefile.Reader(str(shapefiles[0]))
        fields = [field[0] for field in reader.fields[1:]]
        boundaries: dict[str, StateBoundary] = {}

        for shape_record in reader.iterShapeRecords():
            record = dict(zip(fields, shape_record.record))
            code = str(record.get("STUSPS", "")).upper()
            if code not in STATE_NAMES:
                continue

            geometry = shapely_shape(shape_record.shape.__geo_interface__)
            boundaries[code] = StateBoundary(
                code=code,
                name=STATE_NAMES[code],
                geometry=geometry,
                prepared=prep(geometry),
                bounds=tuple(float(value) for value in geometry.bounds),
            )

    missing = sorted(set(STATE_NAMES) - set(boundaries))
    if missing:
        raise RuntimeError(f"Census boundaries missing jurisdictions: {', '.join(missing)}")
    return boundaries


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            candidate = float(value.strip())
        except ValueError:
            return None
        return candidate if math.isfinite(candidate) else None
    return None


def clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def normalize_vendor(value: Any) -> str | None:
    raw = clean_string(value)
    if raw is None:
        return None
    lowered = raw.lower()
    if "flock" in lowered:
        return "Flock Safety"
    if "motorola" in lowered or "vigilant" in lowered:
        return "Motorola / Vigilant"
    if "rekor" in lowered:
        return "Rekor"
    if "genetec" in lowered:
        return "Genetec"
    if "axis" in lowered:
        return "Axis Communications"
    return raw


def feature_coordinate(feature: dict[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    longitude = finite_number(coordinates[0])
    latitude = finite_number(coordinates[1])
    if longitude is None or latitude is None:
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def assign_state(
    longitude: float,
    latitude: float,
    boundaries: dict[str, StateBoundary],
) -> str | None:
    point = Point(longitude, latitude)
    candidates = [
        boundary
        for boundary in boundaries.values()
        if boundary.bbox_contains(longitude, latitude)
    ]

    for boundary in candidates:
        if boundary.prepared.contains(point) or boundary.geometry.covers(point):
            return boundary.code

    nearby = [
        boundary
        for boundary in boundaries.values()
        if boundary.bbox_contains(longitude, latitude, padding=0.05)
    ]
    if not nearby:
        return None

    nearest = min(nearby, key=lambda boundary: boundary.geometry.distance(point))
    return nearest.code if nearest.geometry.distance(point) <= 0.05 else None


def normalized_camera(
    feature: dict[str, Any],
    state_code: str,
    longitude: float,
    latitude: float,
) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    osm_type = clean_string(properties.get("osmType")) or "node"
    osm_id_value = properties.get("osmId")
    osm_id = int(osm_id_value) if isinstance(osm_id_value, (int, float)) else None

    if osm_id is not None:
        camera_id = f"osm-{osm_type}-{osm_id}"
        source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    else:
        fallback = f"{latitude:.7f}|{longitude:.7f}|{properties.get('brand', '')}"
        camera_id = f"derived-{hashlib.sha256(fallback.encode()).hexdigest()[:24]}"
        source_url = None

    direction = finite_number(properties.get("direction"))
    direction_text = clean_string(properties.get("directionCardinal"))
    if direction_text is None and direction is not None:
        direction_text = f"{direction:g}"

    camera = {
        "id": camera_id,
        "name": clean_string(properties.get("name")),
        "latitude": latitude,
        "longitude": longitude,
        "vendor": normalize_vendor(properties.get("brand") or properties.get("manufacturer")),
        "operatorName": clean_string(properties.get("operator")),
        "directionDegrees": direction,
        "directionText": direction_text,
        "municipality": clean_string(properties.get("municipality") or properties.get("city")),
        "county": clean_string(properties.get("county")),
        "surveillanceZone": clean_string(
            properties.get("surveillanceZone") or properties.get("surveillance:zone")
        ),
        "mountType": clean_string(properties.get("mountType") or properties.get("camera:mount")),
        "startDate": clean_string(properties.get("startDate") or properties.get("start_date")),
        "reference": clean_string(properties.get("ref")),
        "osmType": osm_type,
        "osmId": osm_id,
        "osmVersion": int(properties["osmVersion"])
        if isinstance(properties.get("osmVersion"), (int, float))
        else None,
        "osmTimestamp": clean_string(properties.get("osmTimestamp")),
        "sourceName": "OpenStreetMap via DeFlock public data",
        "sourceURL": source_url,
        "dataStatus": "community_mapped",
        "stateCode": state_code,
        "model": clean_string(properties.get("model")),
        "cameraType": clean_string(properties.get("cameraType") or properties.get("camera:type")),
        "powerSource": clean_string(
            properties.get("powerSource")
            or properties.get("power_source")
            or properties.get("surveillance:power")
        ),
        "imageURL": clean_string(properties.get("imageURL") or properties.get("image")),
        "mapillaryKey": clean_string(properties.get("mapillaryKey") or properties.get("mapillary")),
        "website": clean_string(properties.get("website")),
        "description": clean_string(properties.get("description")),
        "note": clean_string(properties.get("note")),
        "street": clean_string(properties.get("street") or properties.get("addr:street")),
    }
    return camera


def remove_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: remove_none_values(child)
            for key, child in value.items()
            if child is not None
        }
    if isinstance(value, list):
        return [remove_none_values(child) for child in value]
    return value


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(remove_none_values(value), separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def camera_summary(camera: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "latitude",
        "longitude",
        "vendor",
        "operatorName",
        "directionDegrees",
        "municipality",
        "county",
        "sourceURL",
    )
    return {field: camera.get(field) for field in fields if camera.get(field) is not None}


def build_changes(
    state_code: str,
    previous: dict[str, Any] | None,
    current_cameras: list[dict[str, Any]],
    from_version: int | None,
    to_version: int,
    generated_at: str,
    baseline: bool,
) -> dict[str, Any]:
    if baseline or not previous:
        return {
            "schemaVersion": 1,
            "stateCode": state_code,
            "stateName": STATE_NAMES[state_code],
            "generatedAt": generated_at,
            "fromVersion": from_version,
            "toVersion": to_version,
            "baseline": True,
            "addedCount": 0,
            "removedCount": 0,
            "changedCount": 0,
            "added": [],
            "removed": [],
            "changed": [],
        }

    old_by_id = {camera["id"]: camera for camera in previous.get("cameras", [])}
    new_by_id = {camera["id"]: camera for camera in current_cameras}
    added: list[dict[str, Any]] = []
    removed: list[str] = []
    changed: list[dict[str, Any]] = []

    watched_fields = (
        "name",
        "latitude",
        "longitude",
        "vendor",
        "operatorName",
        "directionDegrees",
        "directionText",
        "municipality",
        "county",
        "surveillanceZone",
        "mountType",
        "startDate",
        "reference",
        "osmVersion",
        "osmTimestamp",
        "model",
        "cameraType",
        "powerSource",
        "imageURL",
        "mapillaryKey",
        "website",
        "street",
    )

    for camera_id, camera in new_by_id.items():
        old_camera = old_by_id.get(camera_id)
        if old_camera is None:
            added.append(camera_summary(camera))
            continue
        fields = [
            field
            for field in watched_fields
            if old_camera.get(field) != camera.get(field)
        ]
        if fields:
            changed.append(
                {
                    "id": camera_id,
                    "fields": fields,
                    "previous": camera_summary(old_camera),
                    "current": camera_summary(camera),
                }
            )

    for camera_id in old_by_id:
        if camera_id not in new_by_id:
            removed.append(camera_id)

    added.sort(key=lambda camera: camera["id"])
    removed.sort()
    changed.sort(key=lambda camera: camera["id"])

    return {
        "schemaVersion": 1,
        "stateCode": state_code,
        "stateName": STATE_NAMES[state_code],
        "generatedAt": generated_at,
        "fromVersion": from_version,
        "toVersion": to_version,
        "baseline": False,
        "addedCount": len(added),
        "removedCount": len(removed),
        "changedCount": len(changed),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def stable_camera_bytes(cameras: Iterable[dict[str, Any]]) -> bytes:
    return json_bytes(list(cameras))


def state_metadata(
    state_code: str,
    generated_at: str,
    cameras: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "jurisdiction": STATE_NAMES[state_code],
        "source": "OpenStreetMap via DeFlock public data",
        "sourceURL": "https://www.openstreetmap.org",
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0",
        "cameraCount": len(cameras),
        "flockCount": sum(camera.get("vendor") == "Flock Safety" for camera in cameras),
        "directionCount": sum(camera.get("directionDegrees") is not None for camera in cameras),
        "operatorCount": sum(camera.get("operatorName") is not None for camera in cameras),
        "isDemo": False,
    }


def validate_input_count(
    source_feature_count: int,
    assigned_count: int,
    unassigned_count: int,
    previous_manifest: dict[str, Any] | None,
) -> None:
    if source_feature_count < MINIMUM_US_CAMERA_COUNT:
        raise RuntimeError(
            f"Source contained only {source_feature_count:,} US cameras; "
            f"minimum safe count is {MINIMUM_US_CAMERA_COUNT:,}"
        )
    if assigned_count < MINIMUM_US_CAMERA_COUNT:
        raise RuntimeError(
            f"Only {assigned_count:,} cameras were assigned to 50 states + DC; "
            f"minimum safe count is {MINIMUM_US_CAMERA_COUNT:,}"
        )

    unassigned_fraction = unassigned_count / max(1, source_feature_count)
    if unassigned_fraction > MAXIMUM_UNASSIGNED_FRACTION:
        raise RuntimeError(
            f"{unassigned_count:,} cameras ({unassigned_fraction:.2%}) could not be assigned "
            f"to a state; maximum is {MAXIMUM_UNASSIGNED_FRACTION:.2%}"
        )

    previous_total = int((previous_manifest or {}).get("totalCameraCount") or 0)
    if previous_total > 0:
        minimum_relative = math.floor(
            previous_total * (1 - MAXIMUM_ALLOWED_DROP_FRACTION)
        )
        if assigned_count < max(MINIMUM_US_CAMERA_COUNT, minimum_relative):
            raise RuntimeError(
                f"National count dropped from {previous_total:,} to {assigned_count:,}; "
                "refusing publication"
            )


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--boundary-url", default=BOUNDARY_URL)
    parser.add_argument("--root", default=".")
    arguments = parser.parse_args()

    root = Path(arguments.root).resolve()
    states_directory = root / "states"
    changes_directory = root / "changes"
    manifest_path = root / "us_state_manifest.json"

    previous_manifest = load_json(manifest_path)
    previous_entries = {
        entry.get("stateCode"): entry
        for entry in (previous_manifest or {}).get("states", [])
        if isinstance(entry, dict) and entry.get("stateCode")
    }
    baseline_migration = len(previous_entries) < len(STATE_NAMES)

    source = load_source_geojson(arguments.source_url)
    boundaries = load_state_boundaries(arguments.boundary_url)

    cameras_by_state: dict[str, list[dict[str, Any]]] = {
        code: [] for code in STATE_NAMES
    }
    invalid_features = 0
    unassigned_features = 0

    for index, feature in enumerate(source["features"], start=1):
        if not isinstance(feature, dict):
            invalid_features += 1
            continue
        coordinate = feature_coordinate(feature)
        if coordinate is None:
            invalid_features += 1
            continue
        longitude, latitude = coordinate
        state_code = assign_state(longitude, latitude, boundaries)
        if state_code is None:
            unassigned_features += 1
            continue
        cameras_by_state[state_code].append(
            normalized_camera(feature, state_code, longitude, latitude)
        )
        if index % 10000 == 0:
            print(f"Assigned {index:,} of {len(source['features']):,} source features...")

    for state_code, cameras in cameras_by_state.items():
        unique = {camera["id"]: camera for camera in cameras}
        cameras_by_state[state_code] = sorted(
            unique.values(),
            key=lambda camera: (
                -camera["latitude"],
                camera["longitude"],
                camera["id"],
            ),
        )

    assigned_count = sum(len(cameras) for cameras in cameras_by_state.values())
    validate_input_count(
        source_feature_count=len(source["features"]),
        assigned_count=assigned_count,
        unassigned_count=unassigned_features,
        previous_manifest=previous_manifest,
    )

    print(
        f"Assigned {assigned_count:,} cameras to 50 states + DC "
        f"({unassigned_features:,} unassigned, {invalid_features:,} invalid)."
    )

    generated_at = utc_now()
    manifest_entries: list[dict[str, Any]] = []
    any_changes = False

    for state_code in sorted(STATE_NAMES):
        state_path = states_directory / f"{state_code}.json"
        change_path = changes_directory / f"{state_code}.json"
        previous_dataset = load_json(state_path)
        previous_entry = previous_entries.get(state_code)
        cameras = cameras_by_state[state_code]
        unchanged = (
            previous_dataset is not None
            and stable_camera_bytes(previous_dataset.get("cameras", []))
            == stable_camera_bytes(cameras)
        )

        if unchanged:
            dataset = previous_dataset
            dataset_bytes = state_path.read_bytes()
            version = int((previous_entry or {}).get("version") or 1)
            state_generated_at = str(
                (previous_entry or {}).get("generatedAt")
                or dataset.get("metadata", {}).get("generatedAt")
                or generated_at
            )
            changes = load_json(change_path) or build_changes(
                state_code=state_code,
                previous=previous_dataset,
                current_cameras=cameras,
                from_version=version,
                to_version=version,
                generated_at=state_generated_at,
                baseline=True,
            )
        else:
            any_changes = True
            old_version = int((previous_entry or {}).get("version") or 0)
            version = old_version + 1 if old_version > 0 else 1
            state_generated_at = generated_at
            dataset = {
                "metadata": state_metadata(state_code, state_generated_at, cameras),
                "cameras": cameras,
            }
            dataset_bytes = json_bytes(dataset)
            changes = build_changes(
                state_code=state_code,
                previous=previous_dataset,
                current_cameras=cameras,
                from_version=old_version or None,
                to_version=version,
                generated_at=state_generated_at,
                baseline=baseline_migration or previous_dataset is None,
            )
            write_atomic(state_path, dataset_bytes)
            write_atomic(change_path, json_bytes(changes))

        metadata = dataset["metadata"]
        boundary = boundaries[state_code]
        manifest_entries.append(
            {
                "stateCode": state_code,
                "stateName": STATE_NAMES[state_code],
                "version": version,
                "generatedAt": state_generated_at,
                "datasetURL": f"states/{state_code}.json",
                "changesURL": f"changes/{state_code}.json",
                "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                "cameraCount": metadata["cameraCount"],
                "flockCount": metadata["flockCount"],
                "directionCount": metadata["directionCount"],
                "operatorCount": metadata["operatorCount"],
                "fileSizeBytes": len(dataset_bytes),
                "adjacentStates": ADJACENT_STATES[state_code],
                "bounds": {
                    "north": boundary.bounds[3],
                    "south": boundary.bounds[1],
                    "east": boundary.bounds[2],
                    "west": boundary.bounds[0],
                },
            }
        )

    total_camera_count = sum(entry["cameraCount"] for entry in manifest_entries)
    total_flock_count = sum(entry["flockCount"] for entry in manifest_entries)
    total_file_size = sum(entry["fileSizeBytes"] for entry in manifest_entries)

    if not any_changes and previous_manifest is not None:
        print("No camera changes detected. Existing publication remains current.")
        return

    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "source": "OpenStreetMap via DeFlock public data",
        "sourceURL": arguments.source_url,
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0",
        "totalCameraCount": total_camera_count,
        "totalFlockCount": total_flock_count,
        "totalFileSizeBytes": total_file_size,
        "states": manifest_entries,
    }
    write_atomic(manifest_path, json_bytes(manifest))

    print(
        f"Published {total_camera_count:,} cameras across {len(manifest_entries)} "
        f"jurisdictions ({total_flock_count:,} Flock-tagged; "
        f"{total_file_size / 1_048_576:.1f} MiB uncompressed)."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {error}", file=sys.stderr)
        raise
