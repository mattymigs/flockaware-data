#!/usr/bin/env python3
"""Build the FlockAware 50-state + District of Columbia camera publication.

The source is the public compact U.S. camera-position index maintained by the
DeFlock data project. Records are assigned to official Census state polygons,
normalized into the FlockAware camera schema, validated, split by state, and
published with stable per-state versions and change feeds.

New Jersey keeps its richer state file when one is already present, because the
NJ-specific pipeline includes OSM IDs, bearing, operator, and other metadata not
present in the compact national index.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from enrichment import EnrichmentStats, enrich_cameras, has_rich_metadata
import shapefile  # pyshp
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
STATES_DIR = ROOT / "states"
CHANGES_DIR = ROOT / "changes"
MANIFEST_PATH = ROOT / "us_state_manifest.json"
ENRICHMENT_DIR = ROOT / "enrichment"
ALERTS_DIR = ROOT / "alerts"
ALERT_LATEST_PATH = ALERTS_DIR / "latest.json"

INDEX_SIDECAR_URL = "https://tiles.dontgetflocked.com/cameras-us-hourly-index.json"
INDEX_BINARY_URL = "https://tiles.dontgetflocked.com/cameras-us-hourly-index.bin"
CENSUS_BOUNDARY_URLS = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip",
    "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_20m.zip",
)

USER_AGENT = (
    "FlockAware-Data/1.0 "
    "(+https://github.com/mattymigs/flockaware-data; mattmignone@gmail.com)"
)
REQUEST_TIMEOUT_SECONDS = 90
MINIMUM_NATIONAL_CAMERA_COUNT = 50_000
MAXIMUM_ALLOWED_DROP_FRACTION = 0.35
MAXIMUM_UNASSIGNED_FRACTION = 0.02
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

STATE_ADJACENCY: dict[str, list[str]] = {
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
    fips: str
    geometry: BaseGeometry
    prepared: Any
    west: float
    south: float
    east: float
    north: float

    def bbox_contains(self, lon: float, lat: float, padding: float = 0.0) -> bool:
        return (
            self.west - padding <= lon <= self.east + padding
            and self.south - padding <= lat <= self.north + padding
        )


@dataclass(frozen=True)
class IndexRecord:
    latitude_microdegrees: int
    longitude_microdegrees: int
    brand_id: int
    brand: str

    @property
    def latitude(self) -> float:
        return self.latitude_microdegrees / 1_000_000.0

    @property
    def longitude(self) -> float:
        return self.longitude_microdegrees / 1_000_000.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetch_bytes(url: str, *, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, application/octet-stream, */*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                data = response.read()
                if not data:
                    raise RuntimeError(f"Empty response from {url}")
                return data
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            last_error = error
            print(f"Fetch attempt {attempt}/{attempts} failed for {url}: {error}")
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def fetch_first(urls: Iterable[str]) -> bytes:
    failures: list[str] = []
    for url in urls:
        try:
            return fetch_bytes(url)
        except Exception as error:  # noqa: BLE001 - aggregate fallback diagnostics
            failures.append(f"{url}: {error}")
    raise RuntimeError("All boundary downloads failed:\n" + "\n".join(failures))


def parse_index(sidecar_data: bytes, binary_data: bytes) -> tuple[dict[str, Any], list[IndexRecord]]:
    sidecar = json.loads(sidecar_data)
    if len(binary_data) < 16:
        raise RuntimeError("National position index is truncated")
    if binary_data[:4] != b"FHIX":
        raise RuntimeError("National position index magic is invalid")

    version, count, _reserved = struct.unpack_from("<III", binary_data, 4)
    if version != 1:
        raise RuntimeError(f"Unsupported national position index version: {version}")
    if count != int(sidecar.get("count", -1)):
        raise RuntimeError(f"Position index count mismatch: binary={count}, sidecar={sidecar.get('count')}")
    if count < MINIMUM_NATIONAL_CAMERA_COUNT:
        raise RuntimeError(f"National position index contains only {count:,} cameras")

    expected_size = 16 + count * 9
    if len(binary_data) < expected_size:
        raise RuntimeError(
            f"Position index is incomplete: expected at least {expected_size:,} bytes, received {len(binary_data):,}"
        )

    latitude_offset = 16
    longitude_offset = latitude_offset + count * 4
    brand_offset = longitude_offset + count * 4
    brands = [str(value).strip() for value in sidecar.get("brands", [])]

    records: list[IndexRecord] = []
    append = records.append
    for index in range(count):
        latitude = struct.unpack_from("<i", binary_data, latitude_offset + index * 4)[0]
        longitude = struct.unpack_from("<i", binary_data, longitude_offset + index * 4)[0]
        brand_id = binary_data[brand_offset + index]
        brand = brands[brand_id] if brand_id < len(brands) else ""
        append(IndexRecord(latitude, longitude, brand_id, brand))

    return sidecar, records


def load_boundaries(zip_data: bytes) -> dict[str, StateBoundary]:
    with tempfile.TemporaryDirectory(prefix="flockaware-boundaries-") as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / "states.zip"
        archive_path.write_bytes(zip_data)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(temp_dir)

        shapefiles = list(temp_dir.glob("*.shp"))
        if not shapefiles:
            raise RuntimeError("Census state archive did not contain a shapefile")

        reader = shapefile.Reader(str(shapefiles[0]))
        field_names = [field[0] for field in reader.fields[1:]]
        boundaries: dict[str, StateBoundary] = {}

        for shape_record in reader.iterShapeRecords():
            attributes = dict(zip(field_names, shape_record.record, strict=False))
            code = str(attributes.get("STUSPS", "")).strip().upper()
            if code not in STATE_CODES:
                continue

            geometry = shape(shape_record.shape.__geo_interface__)
            if geometry.is_empty:
                raise RuntimeError(f"Census geometry for {code} is empty")
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            west, south, east, north = geometry.bounds
            boundaries[code] = StateBoundary(
                code=code,
                name=str(attributes.get("NAME", code)).strip(),
                fips=str(attributes.get("STATEFP", "")).strip(),
                geometry=geometry,
                prepared=prep(geometry),
                west=west,
                south=south,
                east=east,
                north=north,
            )

    missing = sorted(STATE_CODES.difference(boundaries))
    if missing:
        raise RuntimeError(f"Census boundary file is missing: {', '.join(missing)}")
    return boundaries


def assign_state(record: IndexRecord, boundaries: dict[str, StateBoundary]) -> str | None:
    lon = record.longitude
    lat = record.latitude
    point = Point(lon, lat)

    candidates = [boundary for boundary in boundaries.values() if boundary.bbox_contains(lon, lat)]
    for boundary in candidates:
        if boundary.prepared.contains(point) or boundary.geometry.touches(point):
            return boundary.code

    # Simplified Census coastlines can leave a valid road/intersection point a
    # short distance outside the polygon. Assign only when it is very close to a
    # state boundary; never use a broad nearest-state fallback.
    nearby = [boundary for boundary in boundaries.values() if boundary.bbox_contains(lon, lat, padding=0.08)]
    if nearby:
        nearest = min(nearby, key=lambda item: item.geometry.distance(point))
        if nearest.geometry.distance(point) <= 0.03:
            return nearest.code
    return None


def normalize_vendor(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    lowered = raw.casefold()
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


def camera_from_record(record: IndexRecord, occurrence: int, state_code: str) -> dict[str, Any]:
    vendor = normalize_vendor(record.brand)
    camera_id = (
        f"index-{record.latitude_microdegrees}-{record.longitude_microdegrees}-"
        f"{record.brand_id}-{occurrence}"
    )
    return {
        "id": camera_id,
        "name": None,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "vendor": vendor,
        "operatorName": None,
        "directionDegrees": None,
        "directionText": None,
        "municipality": None,
        "county": None,
        "surveillanceZone": None,
        "mountType": None,
        "startDate": None,
        "reference": None,
        "osmType": None,
        "osmId": None,
        "osmVersion": None,
        "osmTimestamp": None,
        "sourceName": "OpenStreetMap",
        "sourceURL": None,
        "dataStatus": "community_mapped",
        "stateCode": state_code,
    }


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def compact_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def stable_camera_payload(dataset: dict[str, Any] | None) -> bytes:
    return compact_json_bytes((dataset or {}).get("cameras", []))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def camera_summary(camera: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": camera.get("id"),
        "latitude": camera.get("latitude"),
        "longitude": camera.get("longitude"),
        "vendor": camera.get("vendor"),
        "operatorName": camera.get("operatorName"),
        "directionDegrees": camera.get("directionDegrees"),
        "municipality": camera.get("municipality"),
        "county": camera.get("county"),
        "sourceURL": camera.get("sourceURL"),
        "stateCode": camera.get("stateCode"),
        "detailLevel": camera.get("detailLevel"),
    }


def build_changes(
    state_code: str,
    state_name: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    from_version: int | None,
    to_version: int,
    generated_at: str,
) -> dict[str, Any]:
    if previous is None:
        return {
            "schemaVersion": 1,
            "stateCode": state_code,
            "stateName": state_name,
            "generatedAt": generated_at,
            "fromVersion": None,
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
    new_by_id = {camera["id"]: camera for camera in current.get("cameras", [])}
    added = [camera_summary(camera) for key, camera in new_by_id.items() if key not in old_by_id]
    removed = sorted(key for key in old_by_id if key not in new_by_id)
    tracked_fields = (
        "name", "latitude", "longitude", "vendor", "operatorName",
        "directionDegrees", "directionText", "municipality", "county",
        "surveillanceZone", "mountType", "startDate", "reference",
        "osmVersion", "osmTimestamp", "sourceURL",
    )
    changed: list[dict[str, Any]] = []
    for camera_id in sorted(new_by_id.keys() & old_by_id.keys()):
        old = old_by_id[camera_id]
        new = new_by_id[camera_id]
        fields = [field for field in tracked_fields if old.get(field) != new.get(field)]
        if fields:
            changed.append({
                "id": camera_id,
                "fields": fields,
                "previous": camera_summary(old),
                "current": camera_summary(new),
            })

    added.sort(key=lambda item: str(item["id"]))
    return {
        "schemaVersion": 1,
        "stateCode": state_code,
        "stateName": state_name,
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


def dataset_counts(cameras: list[dict[str, Any]]) -> tuple[int, int, int]:
    flock = sum(1 for camera in cameras if "flock" in str(camera.get("vendor") or "").casefold())
    bearing = sum(1 for camera in cameras if camera.get("directionDegrees") is not None)
    operator = sum(1 for camera in cameras if str(camera.get("operatorName") or "").strip())
    return flock, bearing, operator


def validate_state_dataset(dataset: dict[str, Any], state_code: str) -> None:
    cameras = dataset.get("cameras")
    metadata = dataset.get("metadata")
    if not isinstance(cameras, list) or not isinstance(metadata, dict):
        raise RuntimeError(f"{state_code}: invalid dataset structure")
    if metadata.get("cameraCount") != len(cameras):
        raise RuntimeError(f"{state_code}: camera count mismatch")

    ids: set[str] = set()
    for camera in cameras:
        camera_id = str(camera.get("id") or "")
        if not camera_id or camera_id in ids:
            raise RuntimeError(f"{state_code}: missing or duplicate camera ID {camera_id!r}")
        ids.add(camera_id)
        lat = camera.get("latitude")
        lon = camera.get("longitude")
        if not isinstance(lat, (int, float)) or not math.isfinite(lat) or not -90 <= lat <= 90:
            raise RuntimeError(f"{state_code}: invalid latitude for {camera_id}")
        if not isinstance(lon, (int, float)) or not math.isfinite(lon) or not -180 <= lon <= 180:
            raise RuntimeError(f"{state_code}: invalid longitude for {camera_id}")

    flock, bearing, operator = dataset_counts(cameras)
    expected = (metadata.get("flockCount"), metadata.get("directionCount"), metadata.get("operatorCount"))
    if expected != (flock, bearing, operator):
        raise RuntimeError(f"{state_code}: metadata attribute counts are inconsistent")


def previous_entry_by_code(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("stateCode", "")).upper(): entry
        for entry in (manifest or {}).get("states", [])
        if entry.get("stateCode")
    }


def load_enrichment_overlay(state_code: str) -> dict[str, Any] | None:
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


def main() -> None:
    generated_at = utc_now()
    previous_manifest = read_json(MANIFEST_PATH)
    previous_entries = previous_entry_by_code(previous_manifest)
    latest_alert_events: list[dict[str, Any]] = []

    print("Downloading national position index…")
    sidecar_data = fetch_bytes(INDEX_SIDECAR_URL)
    binary_data = fetch_bytes(INDEX_BINARY_URL)
    sidecar, records = parse_index(sidecar_data, binary_data)
    print(f"National source build {sidecar.get('build')}: {len(records):,} records")

    if previous_manifest:
        previous_total = int(previous_manifest.get("totalCameraCount", 0))
        minimum_relative = math.floor(previous_total * (1 - MAXIMUM_ALLOWED_DROP_FRACTION))
        if previous_total and len(records) < max(MINIMUM_NATIONAL_CAMERA_COUNT, minimum_relative):
            raise RuntimeError(
                f"National source count dropped from {previous_total:,} to {len(records):,}; refusing publication"
            )

    print("Downloading official Census state boundaries…")
    boundaries = load_boundaries(fetch_first(CENSUS_BOUNDARY_URLS))

    assigned: dict[str, list[IndexRecord]] = {code: [] for code in STATE_CODES}
    unassigned: list[IndexRecord] = []
    for index, record in enumerate(records, start=1):
        state_code = assign_state(record, boundaries)
        if state_code is None:
            unassigned.append(record)
        else:
            assigned[state_code].append(record)
        if index % 20_000 == 0:
            print(f"Assigned {index:,}/{len(records):,} records…")

    unassigned_fraction = len(unassigned) / max(1, len(records))
    if unassigned_fraction > MAXIMUM_UNASSIGNED_FRACTION:
        raise RuntimeError(
            f"Unable to assign {len(unassigned):,} of {len(records):,} records "
            f"({unassigned_fraction:.2%}); refusing publication"
        )
    print(f"Assigned {len(records) - len(unassigned):,} records; ignored {len(unassigned):,} outside 50 states/DC")

    staging = Path(tempfile.mkdtemp(prefix="flockaware-us-publication-"))
    staged_states = staging / "states"
    staged_changes = staging / "changes"
    staged_alerts = staging / "alerts"
    staged_states.mkdir(parents=True)
    staged_changes.mkdir(parents=True)
    staged_alerts.mkdir(parents=True)

    any_camera_changes = False
    manifest_entries: list[dict[str, Any]] = []

    try:
        for state_code in sorted(STATE_CODES):
            boundary = boundaries[state_code]
            old_path = STATES_DIR / f"{state_code}.json"
            old_dataset = read_json(old_path)
            old_entry = previous_entries.get(state_code)

            # Keep the existing rich New Jersey publication stable while the
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

            validate_state_dataset(candidate, state_code)
            cameras_changed = old_dataset is None or stable_camera_payload(candidate) != stable_camera_payload(old_dataset)
            if cameras_changed:
                any_camera_changes = True
                version = int((old_entry or {}).get("version", 0)) + 1
                candidate["metadata"]["generatedAt"] = generated_at
                dataset = candidate
            else:
                version = int((old_entry or {}).get("version", 1))
                dataset = old_dataset

            dataset_bytes = compact_json_bytes(dataset)
            (staged_states / f"{state_code}.json").write_bytes(dataset_bytes)

            if cameras_changed:
                changes = build_changes(
                    state_code,
                    boundary.name,
                    old_dataset,
                    dataset,
                    int(old_entry["version"]) if old_entry and "version" in old_entry else None,
                    version,
                    generated_at,
                )
                (staged_changes / f"{state_code}.json").write_bytes(compact_json_bytes(changes))
                if not changes.get("baseline"):
                    latest_alert_events.extend(
                        alert_event(state_code, boundary.name, version, generated_at, camera)
                        for camera in changes.get("added", [])
                    )
            else:
                old_change_path = CHANGES_DIR / f"{state_code}.json"
                if old_change_path.exists():
                    shutil.copy2(old_change_path, staged_changes / f"{state_code}.json")
                else:
                    changes = build_changes(
                        state_code,
                        boundary.name,
                        None,
                        dataset,
                        None,
                        version,
                        generated_at,
                    )
                    (staged_changes / f"{state_code}.json").write_bytes(compact_json_bytes(changes))

            cameras = dataset["cameras"]
            flock_count, bearing_count, operator_count = dataset_counts(cameras)
            manifest_entries.append({
                "stateCode": state_code,
                "stateName": boundary.name,
                "fips": boundary.fips,
                "version": version,
                "generatedAt": dataset["metadata"].get("generatedAt", generated_at),
                "datasetURL": f"states/{state_code}.json",
                "changesURL": f"changes/{state_code}.json",
                "sha256": sha256_hex(dataset_bytes),
                "cameraCount": len(cameras),
                "flockCount": flock_count,
                "directionCount": bearing_count,
                "operatorCount": operator_count,
                "enrichedCount": enrichment_stats.enriched_count,
                "basicCount": enrichment_stats.basic_count,
                "enrichmentMatchCount": enrichment_stats.matched_count,
                "fileSizeBytes": len(dataset_bytes),
                "adjacentStates": STATE_ADJACENCY[state_code],
                "bounds": {
                    "north": boundary.north,
                    "south": boundary.south,
                    "east": boundary.east,
                    "west": boundary.west,
                },
            })

        total_count = sum(entry["cameraCount"] for entry in manifest_entries)
        total_flock = sum(entry["flockCount"] for entry in manifest_entries)
        manifest = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "sourceBuild": sidecar.get("build"),
            "source": "OpenStreetMap via DeFlock public camera index",
            "sourceURL": INDEX_SIDECAR_URL,
            "attribution": "© OpenStreetMap contributors",
            "license": "ODbL-1.0",
            "totalCameraCount": total_count,
            "totalFlockCount": total_flock,
            "unassignedSourceCount": len(unassigned),
            "alertFeedURL": "alerts/latest.json",
            "states": manifest_entries,
        }

        latest_alert_events.sort(key=lambda event: event["eventId"])
        alert_feed = {
            "schemaVersion": 1,
            "publicationId": f"{sidecar.get('build') or generated_at}:{generated_at}",
            "generatedAt": generated_at,
            "sourceBuild": sidecar.get("build"),
            "eventCount": len(latest_alert_events),
            "states": sorted({event["stateCode"] for event in latest_alert_events}),
            "events": latest_alert_events,
        }

        # If source data and every state payload are unchanged, preserve the old
        # manifest timestamp and avoid needless repository churn.
        if not any_camera_changes and previous_manifest:
            print("No state camera changes detected; publication remains unchanged.")
            return

        staged_manifest = staging / "us_state_manifest.json"
        staged_manifest.write_bytes(compact_json_bytes(manifest))
        (staged_alerts / "latest.json").write_bytes(compact_json_bytes(alert_feed))

        # Atomic-ish repository replacement: all files are fully built and
        # validated in staging before they are copied into the publication tree.
        STATES_DIR.mkdir(parents=True, exist_ok=True)
        CHANGES_DIR.mkdir(parents=True, exist_ok=True)
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        for path in staged_states.glob("*.json"):
            os.replace(path, STATES_DIR / path.name)
        for path in staged_changes.glob("*.json"):
            os.replace(path, CHANGES_DIR / path.name)
        os.replace(staged_alerts / "latest.json", ALERT_LATEST_PATH)
        os.replace(staged_manifest, MANIFEST_PATH)

        print(
            f"Published {total_count:,} camera records across 50 states + DC "
            f"({total_flock:,} Flock-tagged; "
            f"{len(latest_alert_events):,} new-camera alert events)."
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
